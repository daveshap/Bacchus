import hashlib
from io import BytesIO
import json
import math
import struct
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import wave

from PIL import Image

from bacchus import audio, captions as caption_generator, planning, render, styles, visual_review
from bacchus.library import LibraryError
from bacchus.workflow import Studio, WorkflowError, subtitles


SCRIPT = (
    "A cup sits on the table beside the window as the afternoon light moves slowly across the room.\n"
    "The cup catches the light."
)


def two_scenes():
    return [
        {"startLine": 1, "endLine": 1, "layout": "title", "motion": "fade",
         "title": "In a patch of light", "caption": "", "artId": "", "artBrief": ""},
        {"startLine": 2, "endLine": 2, "layout": "illustration", "motion": "push-in",
         "title": "A cup on the table", "caption": "The light catches the rim.",
         "artId": "cup", "artBrief": "A brown ceramic cup in side view."},
    ]


def jpeg(color):
    with BytesIO() as buffer:
        Image.new("RGB", (160, 112), color).save(buffer, format="JPEG")
        return buffer.getvalue()


def wav_recording():
    with BytesIO() as buffer:
        with wave.open(buffer, "wb") as recording:
            recording.setparams((1, 2, 16000, 0, "NONE", "PCM"))
            recording.writeframes(b"\x00\x01" * 32000)
        return buffer.getvalue()


def wav_tone(frequency, seconds):
    rate = 16_000
    samples = [round(10_000 * math.sin(index * math.tau * frequency / rate)) for index in range(round(seconds * rate))]
    with BytesIO() as buffer:
        with wave.open(buffer, "wb") as recording:
            recording.setparams((1, 2, rate, 0, "NONE", "PCM"))
            recording.writeframes(struct.pack(f"<{len(samples)}h", *samples))
        return buffer.getvalue()


def word_timings(audio_hash):
    words = []
    for start, line in enumerate(SCRIPT.splitlines()):
        tokens = line.split()
        for index, text in enumerate(tokens):
            words.append({
                "text": text,
                "start": round(start + index / len(tokens), 6),
                "end": round(start + (index + 1) / len(tokens), 6),
            })
    return json.dumps({"audio": {"sha256": audio_hash}, "duration": 2, "words": words}).encode()


@unittest.skipUnless(audio.audio_available(), "FFmpeg is required")
class StudioWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="bacchus-test-")
        self.addCleanup(temporary.cleanup)
        self.studio = Studio(temporary.name)
        self.project = self.studio.create("A patch of light")
        self.project_id = self.project["id"]
        self.addCleanup(self.wait_for_jobs)
        self.project = self.studio.upload_audio(
            self.project_id, self.project["revision"], "recording.wav", wav_recording(),
        )

    def wait_for_jobs(self):
        for job in tuple(self.studio.jobs):
            job.join(30)
            self.assertFalse(job.is_alive(), "The export worker did not finish.")

    def change(self, action, **body):
        self.project = self.studio.change(
            self.project_id, action, {"revision": self.project["revision"], **body},
        )
        return self.project

    def lock_input(self):
        if not self.project.get("transcript"):
            self.project = self.studio.import_timings(
                self.project_id, self.project["revision"], word_timings(self.project["audio"]["originalHash"]),
            )
        self.change("input", script=SCRIPT, intention="Observe a detail of an everyday object.", confirmTiming=True)

    def clear_style_effects(self):
        for effect in self.project["mix"]["effects"]:
            self.project = self.studio.remove_effect(self.project_id, effect["id"], {"revision": self.project["revision"]})

    def approve_dry_mix(self):
        self.clear_style_effects()
        self.change("mix", musicMode="none", musicGainDb=-24, effects=[])
        self.prepare_and_approve_mix()

    def prepare_and_approve_mix(self):
        self.change("mix/prepare")
        self.change("mix/approve", reviewed=True)

    def record_check(self, scope, check, passed=True):
        self.project = self.studio.record_review(
            self.project_id, scope, check["id"],
            {"revision": self.project["revision"], "passed": passed, "reason": "Review regression fixture."},
        )

    def review_checks(self, scope):
        key = "assetChecks" if scope == "assets" else "animationChecks"
        for check in self.project[key]:
            proof = self.studio.review_proof(self.project_id, scope, check["id"])
            self.assertTrue(proof.read_bytes().startswith(b"\x89PNG"))
            self.record_check(scope, check)

    def prepare_animation(self):
        self.change("assets/checks")
        self.review_checks("assets")
        self.change("animation/prepare")

    def pass_animation_review(self):
        self.change("qc")
        self.review_checks("animation")
        self.change("candidate/accept")

    def plan_with_art(self):
        self.lock_input()
        self.change("plan", scenes=two_scenes())
        self.change("plan/approve")
        self.upload_art("tan")
        self.approve_art()

    def test_chart_requires_source_evidence_before_plan_can_pass(self):
        self.lock_input()
        scenes = two_scenes()
        scenes[0].update(layout="layered", motion="still", layers=[{
            "id": "made-up", "kind": "bar-chart", "box": {"x": 0, "y": 0, "w": 100, "h": 100},
            "text": "", "artId": "", "artBrief": "", "unit": "million",
            "items": [{"label": "cup", "value": 90}],
            "enter": {"line": 0, "word": 0, "effect": "show", "duration": 0},
            "leave": {"line": 0, "word": 0},
        }])
        self.change("plan", scenes=scenes)
        with self.assertRaisesRegex(WorkflowError, "could not match 'cup' to 90 million"):
            self.change("plan/approve")
        self.assertFalse(self.studio.get(self.project_id)["plan"]["approved"])

    def test_project_can_compose_original_music_and_keep_uploaded_effects(self):
        self.assertEqual(self.project["musicPreference"]["effectiveChoice"], "original")
        self.plan_with_art()
        self.assertIsNone(self.project["mix"]["music"])
        self.upload_mix("effects", "chime.wav", wav_tone(660, 0.3))
        effects = [track["id"] for track in self.project["mix"]["effects"]]
        self.change("mix/music-choice", choice="custom", style="warm")
        self.change("mix/prepare")
        music = self.project["mix"]["music"]
        self.assertEqual(music["arrangement"]["style"], "warm")
        self.assertEqual(music["arrangement"]["duration"], 2)
        self.assertEqual([track["id"] for track in self.project["mix"]["effects"]], effects)
        self.change("mix/approve", reviewed=True)
        self.change("mix/music-choice", choice="generic", style=None)
        self.assertNotIn("arrangement", self.project["mix"]["music"])
        self.assertFalse(self.project["mix"]["approved"])

    def upload_art(self, color):
        self.project = self.studio.upload_art(
            self.project_id, self.project["revision"], "cup", "cup.jpg", jpeg(color),
        )

    def approve_art(self):
        self.project = self.studio.approve_art(
            self.project_id, "cup", {"revision": self.project["revision"]},
        )

    def publish_art(self):
        self.project = self.studio.publish_art(
            self.project_id, "cup", {"revision": self.project["revision"]},
        )

    def upload_mix(self, kind, name, raw):
        self.project = self.studio.upload_mix(
            self.project_id, self.project["revision"], kind, name, raw,
        )

    def test_claude_findings_require_operator_resolution(self):
        self.plan_with_art()
        self.change("assets/checks")
        [proof] = self.project["assetChecks"]
        call = {"revision": self.project["revision"]}
        advice = {"findings": [{"id": "behavior-in-use", "status": "unclear", "evidence": "Static panel."}]}
        with (
            patch.object(visual_review, "available", return_value=True),
            patch.object(visual_review, "review", return_value=advice) as reviewer,
        ):
            self.project = self.studio.advise_review(self.project_id, "assets", proof["id"], call)
            self.studio.advise_review(self.project_id, "assets", proof["id"], {"revision": self.project["revision"]})
        reviewer.assert_called_once()
        scope, brief, image = reviewer.call_args.args
        self.assertEqual(scope, "asset")
        self.assertIn("The cup catches the light.", brief)
        self.assertTrue(image.startswith(b"\x89PNG"))
        self.assertEqual(self.project["assetChecks"][0]["status"], "ready")
        with self.assertRaisesRegex(WorkflowError, "resolved"):
            self.studio.record_review(self.project_id, "assets", proof["id"],
                                      {"revision": self.project["revision"], "passed": True, "reason": ""})
        self.record_check("assets", proof)
        self.assertTrue(self.project["assetReviewReady"])

    def test_transcribing_leaves_editable_draft(self):
        empty = self.studio.create("Missing recording")
        with self.assertRaisesRegex(WorkflowError, "(?i)recording|audio|narration"):
            self.studio.change(
                empty["id"], "input",
                {"revision": empty["revision"], "script": SCRIPT, "intention": "A test."},
            )
        with self.assertRaisesRegex(WorkflowError, "recording-aligned (word )?cue"):
            self.change("input", script=SCRIPT, intention="", confirmTiming=True)
        original_audio_hash = self.project["audio"]["hash"]
        result = {"script": SCRIPT, "model": "gpt-transcribe", "timing": "estimated",
                  "requestId": "test", "usage": {"type": "duration", "seconds": 2}}
        with patch.object(audio, "transcriber_available", return_value=True), patch.object(
            audio, "transcribe", return_value=result,
        ) as transcribe:
            self.change("transcribe")
        recording_path = transcribe.call_args.args[0]
        self.assertEqual(recording_path.name, f"{original_audio_hash}.wav")
        self.assertEqual(self.project["transcript"]["script"], SCRIPT)
        self.assertEqual(self.project["transcript"]["timing"], "text-draft")
        self.assertIsNone(self.project["input"])
        self.assertIsNone(self.project["plan"])
        with self.assertRaisesRegex(WorkflowError, "recording-aligned (word )?cue"):
            self.change("input", script=SCRIPT, intention="", confirmTiming=True)
        self.assertEqual(self.project["audio"]["hash"], original_audio_hash)

    def test_imported_cues_follow_original_recording(self):
        original_hash = hashlib.sha256(wav_recording()).hexdigest()
        timing_bytes = json.dumps({
            "audio": {"sha256": original_hash}, "duration": 2,
            "words": [
                {"text": "Hello", "start": 0.41, "end": 0.94},
                {"text": "there.", "start": 1.31, "end": 1.78},
            ],
        }).encode()
        self.project = self.studio.import_timings(self.project_id, self.project["revision"], timing_bytes)
        with self.assertRaisesRegex(WorkflowError, "confirm"):
            self.change("input", script="Hello there.", intention="Keep the supplied timing.")
        self.change("input", script="Hello there.", intention="Keep the supplied timing.", confirmTiming=True)
        source = self.project["input"]
        self.assertEqual(source["timing"], "source-cues")
        self.assertEqual((source["lines"][0]["start"], source["lines"][0]["end"]), (0.41, 1.78))
        self.assertEqual([word["start"] for word in source["words"]], [0.41, 1.31])
        self.assertEqual(source["timingHash"], hashlib.sha256(timing_bytes).hexdigest())
        self.change("plan/starter")
        self.change("plan/approve")
        self.approve_dry_mix()
        self.prepare_animation()
        self.pass_animation_review()
        self.change("input/unlock")
        with self.assertRaisesRegex(WorkflowError, "Review recording-aligned word cues"):
            self.change("input", script="An edited cue.", intention="", confirmTiming=True)
        self.assertIsNone(self.project["input"])
        self.assertFalse(self.project["accepted"])

    def test_saved_text_first_project_reopens_under_current_intake_rules(self):
        sample = self.studio.create("An early cut", demo=True)
        self.assertEqual(sample["captionReview"]["method"], "authored-lines-v1")
        self.assertEqual(self.studio._caption_data(sample)[0], subtitles(sample))
        identifier = sample["id"]
        path = self.studio.root / identifier / "project.json"
        previous = json.loads(path.read_bytes())
        previous.pop("mix")
        previous["legacy"] = True
        previous["input"]["timing"] = "estimated"
        previous["input"].pop("words")
        path.write_text(json.dumps(previous), encoding="utf-8")

        opened = self.studio.get(identifier)
        self.assertEqual(opened["captionReview"]["method"], "legacy-lines-v1")
        self.assertEqual(self.studio._caption_data(opened)[0], subtitles(opened))
        self.assertEqual(opened["input"]["script"], sample["input"]["script"])
        self.assertTrue(opened["plan"]["approved"])
        self.assertTrue(opened["requiresAudio"])
        with self.assertRaisesRegex(WorkflowError, "recording-aligned"):
            self.studio.preview(identifier, 1)
        with self.assertRaisesRegex(WorkflowError, "recording-aligned"):
            self.studio.change(identifier, "animation/prepare", {"revision": opened["revision"]})
        unlocked = self.studio.change(identifier, "input/unlock", {"revision": opened["revision"]})
        with self.assertRaisesRegex(WorkflowError, "Import the narration recording"):
            self.studio.change(identifier, "input", {
                "revision": unlocked["revision"], "script": SCRIPT, "intention": "",
            })

    def test_gate_order_and_plan_invalidation(self):
        with self.assertRaisesRegex(WorkflowError, "Lock the reviewed narration cues"):
            self.change("plan", scenes=two_scenes())
        stale_revision = self.project["revision"]
        self.lock_input()
        locked = self.project["input"]["hash"]
        with self.assertRaisesRegex(WorkflowError, "project changed"):
            self.studio.change(self.project_id, "input/unlock", {"revision": stale_revision})
        with self.assertRaisesRegex(WorkflowError, "Unlock the narration"):
            self.lock_input()
        incomplete = two_scenes()[:1]
        with self.assertRaisesRegex(WorkflowError, "cover lines"):
            self.change("plan", scenes=incomplete)
        self.change("plan", scenes=two_scenes())
        with self.assertRaisesRegex(WorkflowError, "Approve the scene plan"):
            self.change("assets/checks")
        self.change("plan/approve")
        with self.assertRaisesRegex(WorkflowError, "Upload and approve"):
            self.change("assets/checks")
        self.assertEqual([item["id"] for item in self.project["missing"]], ["cup"])
        self.assertIn("A brown ceramic cup", self.project["artPrompt"])
        self.upload_art("tan")
        self.approve_art()
        self.approve_dry_mix()
        with self.assertRaisesRegex(WorkflowError, "Review and pass the planned artwork uses"):
            self.change("animation/prepare")
        with self.assertRaisesRegex(WorkflowError, "Review and pass the planned artwork uses"):
            self.studio.preview(self.project_id, 0.5)
        self.change("assets/checks")
        self.assertEqual(self.project["assetChecks"][0]["status"], "ready")
        with self.assertRaisesRegex(WorkflowError, "Review and pass the planned artwork uses"):
            self.change("animation/prepare")
        self.review_checks("assets")
        original_asset_proof = self.project["assetChecks"][0]["id"]
        with self.assertRaisesRegex(WorkflowError, "Prepare the animation"):
            self.studio.preview(self.project_id, 0.5)
        with self.assertRaisesRegex(WorkflowError, "Prepare the animation"):
            self.change("qc")
        self.change("animation/prepare")
        self.assertTrue(self.studio.preview(self.project_id, 0.5).startswith(b"\x89PNG"))
        with self.assertRaisesRegex(WorkflowError, "technical animation checks"):
            self.change("candidate/accept")
        self.change("qc")
        with self.assertRaisesRegex(WorkflowError, "Review and pass the scenes and transitions"):
            self.change("candidate/accept")
        with self.assertRaisesRegex(WorkflowError, "Accept the current candidate"):
            self.change("export")
        self.record_check("animation", self.project["animationChecks"][0], passed=False)
        self.assertEqual(self.project["animationChecks"][0]["status"], "failed")
        with self.assertRaisesRegex(WorkflowError, "Review and pass the scenes and transitions"):
            self.change("candidate/accept")
        self.review_checks("animation")
        self.change("candidate/accept")
        self.assertTrue(self.project["accepted"])
        edited = two_scenes()
        edited[0]["title"] = "A changed title"
        self.change("plan", scenes=edited)
        self.assertEqual(self.project["input"]["hash"], locked)
        self.assertFalse(self.project["plan"]["approved"])
        self.assertFalse(self.project["accepted"])
        self.assertIsNone(self.project["qc"])
        self.change("plan/approve")
        self.assertEqual(self.project["assetChecks"][0]["id"], original_asset_proof)
        self.assertEqual(self.project["assetChecks"][0]["status"], "passed")
        self.assertFalse(self.project["animationPrepared"])
        self.change("input/unlock")
        self.assertIsNone(self.project["input"])
        self.assertIsNone(self.project["plan"])

    def test_art_normalization_fingerprint_and_revocation(self):
        self.plan_with_art()
        self.approve_dry_mix()
        self.prepare_animation()
        original = self.project["assets"][0]
        path, content_type = self.studio.media(self.project_id, "art", original["hash"])
        self.assertEqual(content_type, "image/png")
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), original["hash"])
        with Image.open(path) as art:
            self.assertEqual((art.format, art.mode, art.size), ("PNG", "RGBA", (160, 112)))
        with Image.open(BytesIO(self.studio.preview(self.project_id, 1.5))) as frame:
            self.assertEqual(frame.size, (render.WIDTH, render.HEIGHT))
        self.pass_animation_review()
        self.upload_art("brown")
        self.assertTrue(self.project["plan"]["approved"])
        self.assertFalse(self.project["assets"][0]["approved"])
        self.assertNotEqual(self.project["assets"][0]["hash"], original["hash"])
        self.assertFalse(self.project["accepted"])
        self.assertIsNone(self.project["qc"])
        with self.assertRaisesRegex(WorkflowError, "Upload and approve"):
            self.change("qc")

    def test_tampered_proofs_block_acceptance_and_export(self):
        self.plan_with_art()
        self.approve_dry_mix()
        self.prepare_animation()
        self.change("qc")
        self.review_checks("animation")
        check_id = self.project["animationChecks"][0]["id"]
        self.studio.review_proof(self.project_id, "animation", check_id).write_bytes(b"changed animation evidence")
        with self.assertRaisesRegex(WorkflowError, "valid proof"):
            self.studio.review_proof(self.project_id, "animation", check_id)
        with self.assertRaisesRegex(WorkflowError, "Review and pass the scenes and transitions"):
            self.change("candidate/accept")
        self.change("qc")
        self.assertEqual([item["status"] for item in self.project["animationChecks"]], ["ready", "passed"])
        self.record_check("animation", self.project["animationChecks"][0])
        self.change("candidate/accept")
        check_id = self.project["assetChecks"][0]["id"]
        self.studio.review_proof(self.project_id, "assets", check_id).write_bytes(b"changed artwork evidence")
        with self.assertRaisesRegex(WorkflowError, "Review and pass the planned artwork uses"):
            self.change("export")

    def test_planner_cannot_move_the_gates(self):
        self.plan_with_art()
        self.approve_dry_mix()
        self.prepare_animation()
        self.pass_animation_review()
        current_plan = self.project["plan"]
        invalid = two_scenes()
        invalid[0]["approved"] = True
        proposed = two_scenes()
        proposed[0]["title"] = "How the light falls"
        responses = [(invalid, {"usage": {}}), (proposed, {"usage": {}})]
        with patch.object(planning, "planner_available", return_value=True), patch.object(
            planning, "request_plan", side_effect=responses,
        ) as request:
            with self.assertRaisesRegex(WorkflowError, "Expected these fields"):
                self.change("plan/generate")
            self.project = self.studio.get(self.project_id)
            self.assertEqual(self.project["plan"], current_plan)
            self.assertTrue(self.project["accepted"])
            self.assertIsNone(self.project["busy"])
            self.change("plan/generate")
            payload = request.call_args.args[0]
        self.assertEqual(set(payload), {"model", "max_tokens", "system", "messages", "output_config"})
        self.assertEqual(self.project["plannerCalls"], 2)
        self.assertEqual(self.project["plan"]["source"], "claude")
        self.assertFalse(self.project["accepted"])
        self.assertFalse(self.project["plan"]["approved"])
        self.assertIsNone(self.project["qc"])

    def test_style_pins_the_guide_applies_audio_preferences_and_keeps_its_library_separate(self):
        guide = self.studio.styles.create({
            "name": "Receipt illustrations", "libraryName": "Receipt artwork",
            "guide": "# Use a paper backdrop", "visualPreset": "studio-ink",
            "answers": {**styles.ANSWER_DEFAULTS, "music": False, "soundEffects": True,
                        "effectsNotes": "  Keep each transition subtle.\n"},
        })
        self.project = self.studio.create("A patch of light", style_id=guide["id"], style_revision=1)
        self.project_id = self.project["id"]
        self.project = self.studio.upload_audio(self.project_id, self.project["revision"], "recording.wav", wav_recording())
        self.plan_with_art()
        self.assertEqual(self.project["mix"]["musicMode"], "none")
        self.assertEqual(len(self.project["mix"]["effects"]), 1)
        self.change("assets/checks")
        self.review_checks("assets")
        self.publish_art()
        self.assertEqual(self.studio.style_view(guide)["assetCount"], 1)
        asset = self.project["assets"][0]
        self.assertEqual(self.studio.library_for("professional-corporate").list_assets(), [])
        other = self.studio.create("Corporate video", style_id="professional-corporate")
        with self.assertRaises(LibraryError):
            self.studio.pin_library(other["id"], {
                "revision": other["revision"], "libraryId": asset["libraryId"], "version": asset["libraryVersion"], "alias": "cup",
            })
        self.studio.styles.update(guide["id"], 1, {"guide": "A new guide"})
        pinned = self.studio.get(self.project_id)["styleSnapshot"]
        self.assertEqual((pinned["revision"], pinned["guide"]), (1, "# Use a paper backdrop"))
        self.assertEqual(pinned["answers"]["effectsNotes"], "  Keep each transition subtle.\n")
        later = self.studio.create("Another lesson", style_id=guide["id"])
        self.assertEqual(later["styleSnapshot"]["revision"], 2)

    def test_library_pin_keeps_the_selected_version_across_projects(self):
        self.plan_with_art()
        self.change("assets/checks")
        self.review_checks("assets")
        self.publish_art()
        original = self.project["assets"][0]
        self.assertEqual(original["libraryVersion"], 1)
        self.upload_art("brown")
        self.approve_art()
        self.change("assets/checks")
        self.review_checks("assets")
        self.publish_art()
        updated = self.project["assets"][0]
        self.assertEqual(updated["libraryVersion"], 2)
        self.assertEqual(updated["libraryId"], original["libraryId"])
        second = self.studio.create("Another cup")
        pin = {"revision": second["revision"], "libraryId": original["libraryId"], "version": 1, "alias": "old-cup"}
        second = self.studio.pin_library(second["id"], pin)
        pin["revision"] = second["revision"]
        self.assertEqual(self.studio.pin_library(second["id"], pin), second)
        with self.assertRaisesRegex(WorkflowError, "already used"):
            self.studio.pin_library(second["id"], {**pin, "version": 2})
        reloaded = Studio(self.studio.root).get(second["id"])
        pinned = reloaded["assets"][0]
        self.assertTrue(pinned["approved"])
        self.assertEqual((pinned["libraryVersion"], pinned["hash"]), (1, original["hash"]))
        path, _ = self.studio.media(second["id"], "art", pinned["hash"])
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), original["hash"])

        library = self.studio.library
        metadata = library.root / "entries" / f"{original['libraryId']}.json"
        metadata.write_bytes(b"broken library entry")
        reopened = Studio(self.studio.root)
        damaged = reopened.get(second["id"])["assets"][0]
        self.assertIn("libraryWarning", damaged)
        self.assertEqual(damaged["hash"], original["hash"])
        self.assertTrue(path.is_file())
        reopened.library.quarantine(original["libraryId"])
        quarantined = reopened.get(second["id"])["assets"][0]
        self.assertIn("in quarantine", quarantined["libraryWarning"])

    def test_art_only_joins_library_after_passed_use_and_flagged_version_stays_quarantined(self):
        self.plan_with_art()
        self.assertNotIn("libraryId", self.project["assets"][0])
        self.assertEqual(self.studio.library.list_assets(), [])
        self.change("assets/checks")
        check = self.project["assetChecks"][0]
        self.record_check("assets", check, passed=False)
        with self.assertRaisesRegex(WorkflowError, "Pass every current planned use"):
            self.publish_art()
        self.assertEqual(self.studio.library.list_assets(), [])
        self.record_check("assets", check)
        self.publish_art()
        original = self.project["assets"][0]
        self.assertTrue(original["libraryQualified"])
        self.assertEqual((self.project["assetChecks"][0]["id"], self.project["assetChecks"][0]["status"]),
                         (check["id"], "passed"))

        self.project = self.studio.report_art_issue(self.project_id, "cup", {
            "revision": self.project["revision"], "kind": "defective", "reason": "The rim is cut off.",
        })
        self.assertEqual(self.project["missing"][0]["issue"]["kind"], "defective")
        self.assertIn("The rim is cut off.", self.project["artPrompt"])
        with self.assertRaisesRegex(WorkflowError, "same flagged artwork"):
            self.upload_art("tan")
        second = self.studio.create("Needs a cup")
        pin = {"revision": second["revision"], "libraryId": original["libraryId"], "version": 1, "alias": "cup"}
        with self.assertRaisesRegex(LibraryError, "open artwork issue"):
            self.studio.pin_library(second["id"], pin)
        self.upload_art("brown")
        self.assertEqual(self.project["assetIssues"][0]["replacementHash"], self.project["assets"][0]["hash"])
        self.approve_art()
        self.change("assets/checks")
        self.review_checks("assets")
        self.publish_art()
        replacement = self.project["assets"][0]
        self.assertEqual((replacement["libraryId"], replacement["libraryVersion"]), (original["libraryId"], 2))
        self.assertTrue(self.studio.pin_library(second["id"], {**pin, "version": 2})["assets"][0]["approved"])
        with self.assertRaisesRegex(LibraryError, "open artwork issue"):
            self.studio.library.get(original["libraryId"], 1)

    def test_planner_reuses_approved_art_from_another_project(self):
        self.plan_with_art()
        self.change("assets/checks")
        self.review_checks("assets")
        self.publish_art()
        original = self.project["assets"][0]
        second = self.studio.create("A cup in view")
        second = self.studio.upload_audio(second["id"], second["revision"], "narration.wav", wav_recording())
        second = self.studio.import_timings(second["id"], second["revision"], word_timings(second["audio"]["originalHash"]))
        second = self.studio.change(second["id"], "input", {
            "revision": second["revision"], "script": SCRIPT, "intention": "A ceramic cup.", "confirmTiming": True,
        })
        scenes = two_scenes()
        scenes[1].update(artId=original["libraryId"], artBrief="")
        with patch.object(planning, "planner_provider", return_value="anthropic"), patch.object(
            planning, "request_plan", return_value=(scenes, {"usage": {}}),
        ) as request:
            second = self.studio.change(second["id"], "plan/generate", {"revision": second["revision"]})
        context = json.loads(request.call_args.args[0]["messages"][0]["content"])
        self.assertIn(original["libraryId"], {asset["id"] for asset in context["availableAssets"]})
        self.assertFalse(second["plan"]["approved"])
        self.assertEqual(second["missing"], [])
        reused = second["assets"][0]
        self.assertEqual((reused["hash"], reused["libraryVersion"]), (original["hash"], 1))

    @unittest.skipUnless(render.render_available(), "FFmpeg and FFprobe are required")
    def test_music_and_effects_change_the_export_candidate(self):
        self.plan_with_art()
        self.clear_style_effects()
        self.change("assets/checks")
        self.review_checks("assets")
        with self.assertRaisesRegex(WorkflowError, "(?i)mix|music|composition"):
            self.change("animation/prepare")
        self.upload_mix("music", "score.wav", wav_tone(220, 0.5))
        self.upload_mix("effects", "chime.wav", wav_tone(660, 0.3))
        effect_id = self.project["mix"]["effects"][0]["id"]
        mix = {"musicMode": "track", "musicGainDb": -24,
               "effects": [{"id": effect_id, "start": 0.25, "gainDb": -9}]}
        self.change("mix", **mix)
        with self.assertRaisesRegex(WorkflowError, "(?i)mix|music|composition"):
            self.change("animation/prepare")
        with self.assertRaisesRegex(WorkflowError, "Prepare and approve the exact soundtrack"):
            self.change("mix/approve", reviewed=True)
        self.change("mix/prepare")
        with self.assertRaisesRegex(WorkflowError, "Review and confirm"):
            self.change("mix/approve", reviewed=False)
        first_preview = self.studio.mix_preview(self.project_id)
        first_mix_bytes = first_preview.read_bytes()
        first_mix_hash = hashlib.sha256(first_mix_bytes).hexdigest()
        self.assertEqual(self.project["mix"]["rendered"]["sha256"], first_mix_hash)
        self.assertEqual(self.project["mix"]["rendered"]["samples"], 96_000)
        self.change("mix/prepare")
        self.assertEqual(self.project["mix"]["rendered"]["sha256"], first_mix_hash)
        with wave.open(str(first_preview), "rb") as recording:
            self.assertAlmostEqual(recording.getnframes() / recording.getframerate(), 2, places=2)
        first_preview.write_bytes(b"tampered soundtrack")
        with self.assertRaisesRegex(WorkflowError, "fingerprint"):
            self.change("mix/approve", reviewed=True)
        self.prepare_and_approve_mix()
        first_preview = self.studio.mix_preview(self.project_id)
        first_mix_bytes = first_preview.read_bytes()
        first_mix_hash = hashlib.sha256(first_mix_bytes).hexdigest()
        self.assertEqual(self.project["mix"]["rendered"]["sha256"], first_mix_hash)
        self.change("animation/prepare")
        self.pass_animation_review()
        first_candidate = self.project["qc"]["candidateHash"]
        scene_proof_ids = [check["id"] for check in self.project["animationChecks"]]
        first_preview.write_bytes(b"tampered after approval")
        with self.assertRaisesRegex(WorkflowError, "fingerprint"):
            self.change("export")
        mix["effects"][0]["start"] = 0.75
        self.change("mix", **mix)
        self.assertFalse(self.project["accepted"])
        self.assertIsNone(self.project["mix"].get("rendered"))
        self.prepare_and_approve_mix()
        second_preview = self.studio.mix_preview(self.project_id)
        self.assertNotEqual(self.project["mix"]["rendered"]["sha256"], first_mix_hash)
        self.assertNotEqual(first_mix_bytes, second_preview.read_bytes())
        self.change("animation/prepare")
        self.change("qc")
        self.assertNotEqual(self.project["qc"]["candidateHash"], first_candidate)
        self.assertEqual([check["id"] for check in self.project["animationChecks"]], scene_proof_ids)
        self.assertTrue(all(check["status"] == "passed" for check in self.project["animationChecks"]))

    @unittest.skipUnless(render.render_available(), "FFmpeg and FFprobe are required")
    def test_verified_export_with_narration_captions_and_receipt(self):
        self.plan_with_art()
        self.approve_dry_mix()
        self.prepare_animation()
        clip = self.studio.motion_preview(self.project_id, {
            "revision": self.project["revision"], "start": 0.75, "end": 1.25,
        })
        self.assertEqual((clip["width"], clip["height"], clip["fps"], clip["frames"]), (1280, 720, 60, 30))
        self.assertTrue(clip["decodedAudio"])
        self.assertTrue(self.studio.motion_preview_file(self.project_id, clip["url"].rsplit("/", 1)[-1]).is_file())
        self.pass_animation_review()
        report = self.project["captionReview"]
        self.assertEqual(report["method"], "timed-words-v1")
        with patch.object(caption_generator, "generate", wraps=caption_generator.generate) as generate:
            self.assertEqual(self.studio.get(self.project_id)["captionReview"], report)
            generate.assert_not_called()
        self.change("export")
        self.wait_for_jobs()
        delivered = self.studio.get(self.project_id)
        result = delivered["export"]
        self.assertEqual(result["status"], "complete", result.get("error"))
        key = result["candidateHash"]
        delivery_id = result["deliveryId"]
        self.assertEqual(result["verification"], "verified")
        video, _ = self.studio.media(self.project_id, "video", delivery_id)
        captions, _ = self.studio.media(self.project_id, "captions", delivery_id)
        receipt, _ = self.studio.media(self.project_id, "receipt", delivery_id)
        file = json.loads(receipt.read_bytes())
        self.assertEqual(file["candidateHash"], key)
        self.assertEqual(file["deliveryId"], delivery_id)
        self.assertEqual(file["accepted"]["hash"], key)
        self.assertEqual(file["videoHash"], hashlib.sha256(video.read_bytes()).hexdigest())
        self.assertEqual(file["captionsHash"], hashlib.sha256(captions.read_bytes()).hexdigest())
        self.assertEqual(file["candidate"]["mix"]["rendered"]["sha256"], self.project["mix"]["rendered"]["sha256"])
        self.assertEqual(file["captionsTiming"], "source-cues")
        self.assertEqual(file["captionReview"], report)
        self.assertEqual(result["captionReview"], report)
        self.assertEqual(delivered["lastDelivery"]["captionReview"], report)
        self.assertEqual(delivered["input"]["lines"], self.project["input"]["lines"])
        self.assertEqual(delivered["plan"]["hash"], self.project["plan"]["hash"])
        self.assertEqual(file["media"]["audio"], "aac")
        self.assertEqual((file["media"]["width"], file["media"]["height"], file["media"]["fps"]), (3840, 2160, 60))
        self.assertEqual(file["media"]["frames"], 120)
        self.assertEqual(file["media"]["decodedFrames"], 120)
        self.assertTrue(file["media"]["decodedAudio"])
        self.assertAlmostEqual(file["media"]["duration"], 2, places=2)
        self.assertEqual(len(file["review"]["assets"]), 1)
        self.assertEqual(len(file["review"]["animation"]), 2)
        for scope, checks in file["review"].items():
            for check_id, check in checks.items():
                self.assertEqual(check["status"], "passed")
                self.assertTrue(check["reviewedAt"])
                proof = self.studio.review_proof(self.project_id, scope, check_id)
                self.assertEqual(check["proofHash"], hashlib.sha256(proof.read_bytes()).hexdigest())
        captions_text = captions.read_text(encoding="utf-8")
        self.assertNotEqual(captions_text, subtitles(self.project))
        sections = captions_text.strip().split("\n\n")
        spoken = [word for cue in sections for line in cue.splitlines()[2:] for word in line.split()]
        self.assertEqual(spoken, SCRIPT.split())
        self.assertEqual(len(sections), report["cueCount"])

        saved = self.studio._read(self.project_id)
        saved["renderEngine"] = "0.3.0"
        self.studio._save(saved)
        reopened = Studio(self.studio.root)
        upgraded = reopened.get(self.project_id)
        self.assertFalse(upgraded["accepted"])
        self.assertFalse(upgraded["mix"]["approved"])
        self.assertIsNone(upgraded["export"])
        self.assertEqual(upgraded["lastDelivery"]["deliveryId"], delivery_id)
        self.assertEqual(reopened.media(self.project_id, "video", delivery_id)[0], video)

    @unittest.skipUnless(render.render_available(), "FFmpeg and FFprobe are required")
    def test_delivery_rerender_and_repair_keep_acceptance_and_last_good_files(self):
        self.plan_with_art()
        self.approve_dry_mix()
        self.prepare_animation()
        self.pass_animation_review()

        def current():
            self.wait_for_jobs()
            self.project = self.studio.get(self.project_id)
            self.assertEqual(self.project["export"]["status"], "complete", self.project["export"])
            return self.project["export"]

        original_text = subtitles(self.project)
        with patch("bacchus.workflow.video_encoder_mode", return_value="cpu"):
            with patch.object(self.studio, "_caption_data", return_value=(original_text, None)):
                self.change("export")
                previous = current()
            original_receipt = self.studio.media(self.project_id, "receipt", previous["deliveryId"])[0].read_bytes()
            self.assertIsNone(previous.get("captionReview"))
            self.assertEqual(self.change("export")["export"]["deliveryId"], previous["deliveryId"])
        with patch("bacchus.workflow.video_encoder_mode", return_value="auto"):
            started = self.change("export", rerender=True)
            self.assertEqual(started["lastDelivery"]["deliveryId"], previous["deliveryId"])
            second = current()
            self.assertNotEqual(second["deliveryId"], previous["deliveryId"])
            self.assertEqual(second["encoderMode"], "auto")
            self.assertEqual(second["candidateHash"], previous["candidateHash"])
            self.assertEqual(second["captionReview"]["method"], "timed-words-v1")
            new_captions = self.studio.media(self.project_id, "captions", second["deliveryId"])[0]
            self.assertNotEqual(new_captions.read_bytes(), original_text.encode("utf-8"))

            with patch.object(render, "export_video", side_effect=render.RenderError("Test encoder failure.")):
                self.change("export", rerender=True)
                self.wait_for_jobs()
            self.project = self.studio.get(self.project_id)
            self.assertEqual(self.project["export"]["status"], "failed")
            self.assertEqual(self.project["lastDelivery"]["deliveryId"], second["deliveryId"])

            self.change("export")
            third = current()
            captions, _ = self.studio.media(self.project_id, "captions", third["deliveryId"])
            captions.write_bytes(b"invalid captions")
            self.project = self.studio.get(self.project_id)
            self.assertEqual(self.project["export"]["status"], "invalid")
            self.assertEqual(self.project["export"]["verification"], "corrupt")
            self.assertIn("captions", self.project["export"]["integrityError"])
            self.assertNotIn("url", self.project["export"])
            self.assertEqual(self.project["lastDelivery"]["deliveryId"], second["deliveryId"])
            with self.assertRaisesRegex(WorkflowError, "captions"):
                self.studio.media(self.project_id, "video", third["deliveryId"])
            self.change("export")
            repaired = current()
        self.assertNotEqual(repaired["deliveryId"], third["deliveryId"])
        self.assertEqual(repaired["candidateHash"], previous["candidateHash"])
        self.assertTrue(self.project["accepted"])
        self.studio.media(self.project_id, "video", previous["deliveryId"])
        self.assertEqual(self.studio.media(self.project_id, "captions", previous["deliveryId"])[0].read_bytes(),
                         original_text.encode("utf-8"))
        self.assertEqual(self.studio.media(self.project_id, "receipt", previous["deliveryId"])[0].read_bytes(), original_receipt)

    def test_completed_legacy_delivery_keeps_its_link_and_verifies_against_adopted_hashes(self):
        project = self.studio._read(self.project_id)
        exports = self.studio._folder(self.project_id) / "exports"
        exports.mkdir()
        key = "1" * 64
        previous_mp4 = b"Bytes from the earlier verified delivery."
        (exports / f"{key}.mp4").write_bytes(previous_mp4)
        (exports / f"{key}.srt").write_bytes(b"Caption bytes")
        receipt = {"videoHash": hashlib.sha256(previous_mp4).hexdigest(), "candidateHash": key, "accepted": {"hash": key}}
        (exports / f"{key}.json").write_text(json.dumps(receipt), encoding="utf-8")
        project["export"] = {"status": "complete", "candidateHash": key, "media": {"encoderMode": "auto"}}
        self.studio._save(project)

        reopened = Studio(self.studio.root, self.studio.library_root)
        adopted = reopened.get(self.project_id)["export"]
        self.assertEqual(adopted["deliveryId"], key)
        self.assertEqual(adopted["verification"], "verified")
        self.assertIn("on upgrade", adopted["verificationNote"])
        self.assertTrue(adopted["url"].endswith(key))
        video, _ = reopened.media(self.project_id, "video", key)
        self.assertEqual(video.read_bytes(), previous_mp4)
        (exports / f"{key}.srt").write_bytes(b"Edited caption bytes")
        self.assertEqual(reopened.get(self.project_id)["export"]["status"], "invalid")
        with self.assertRaisesRegex(WorkflowError, "captions"):
            reopened.media(self.project_id, "video", key)

    def test_legacy_catalog_restores_historical_links_and_last_valid_delivery(self):
        project = self.studio._read(self.project_id)
        exports = self.studio._folder(self.project_id) / "exports"
        exports.mkdir()
        oldest, previous, broken = "a" * 64, "2" * 64, "3" * 64
        for index, key in enumerate((oldest, previous, broken), start=1):
            mp4 = f"Legacy delivery {index}".encode()
            (exports / f"{key}.mp4").write_bytes(mp4 if key != broken else b"wrong bytes")
            (exports / f"{key}.srt").write_bytes(b"Caption bytes")
            receipt = {"candidateHash": key, "accepted": {"hash": key},
                       "videoHash": hashlib.sha256(mp4).hexdigest(), "createdAt": f"2026-11-{index:02d}T00:00:00+00:00"}
            (exports / f"{key}.json").write_text(json.dumps(receipt), encoding="utf-8")
        project["export"] = {"status": "failed", "candidateHash": broken, "error": "Render interrupted."}
        self.studio._save(project)

        reopened = Studio(self.studio.root, self.studio.library_root)
        view = reopened.get(self.project_id)
        self.assertEqual(view["export"]["status"], "failed")
        self.assertEqual(view["lastDelivery"]["deliveryId"], previous)
        self.assertTrue(view["lastDelivery"]["url"].endswith(previous))
        for key in (oldest, previous):
            reopened.media(self.project_id, "video", key)
        with self.assertRaisesRegex(WorkflowError, "Delivery not found"):
            reopened.media(self.project_id, "video", broken)

        reopened_again = Studio(self.studio.root, self.studio.library_root)
        self.assertEqual(reopened_again.get(self.project_id)["revision"], view["revision"])


if __name__ == "__main__":
    unittest.main()
