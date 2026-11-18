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

from bacchus import audio, planning, render
from bacchus.workflow import Studio, WorkflowError


SCRIPT = "A cup sits on the table.\nThe cup catches the light."


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
        self.change("input", script=SCRIPT, intention="Observe a detail of an everyday object.")

    def approve_dry_mix(self):
        self.change("mix", musicMode="none", musicGainDb=-24, effects=[])
        self.change("mix/approve")

    def plan_with_art(self):
        self.lock_input()
        self.change("plan", scenes=two_scenes())
        self.change("plan/approve")
        self.upload_art("tan")
        self.approve_art()

    def upload_art(self, color):
        self.project = self.studio.upload_art(
            self.project_id, self.project["revision"], "cup", "cup.jpg", jpeg(color),
        )

    def approve_art(self):
        self.project = self.studio.approve_art(
            self.project_id, "cup", {"revision": self.project["revision"]},
        )

    def upload_mix(self, kind, name, raw):
        self.project = self.studio.upload_mix(
            self.project_id, self.project["revision"], kind, name, raw,
        )

    def test_transcribing_leaves_editable_draft(self):
        empty = self.studio.create("Missing recording")
        with self.assertRaisesRegex(WorkflowError, "(?i)recording|audio|narration"):
            self.studio.change(
                empty["id"], "input",
                {"revision": empty["revision"], "script": SCRIPT, "intention": "A test."},
            )
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
        self.assertIsNone(self.project["input"])
        self.assertIsNone(self.project["plan"])
        corrected = SCRIPT.replace("sits", "stands")
        self.change("input", script=corrected, intention="Observe an everyday object.")
        self.assertEqual(self.project["input"]["script"], corrected)
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
        self.change("input", script="Hello there.", intention="Keep the supplied timing.")
        source = self.project["input"]
        self.assertEqual(source["timing"], "source-cues")
        self.assertEqual((source["lines"][0]["start"], source["lines"][0]["end"]), (0.41, 1.78))
        self.assertEqual(source["timingHash"], hashlib.sha256(timing_bytes).hexdigest())
        self.change("plan/starter")
        self.change("plan/approve")
        self.approve_dry_mix()
        self.change("qc")
        self.change("candidate/accept")
        self.change("input/unlock")
        self.change("input", script="An edited cue.", intention="Use estimated timing.")
        self.assertEqual(self.project["input"]["timing"], "estimated")
        self.assertFalse(self.project["accepted"])

    def test_gate_order_and_plan_invalidation(self):
        with self.assertRaisesRegex(WorkflowError, "Lock the narration"):
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
            self.change("qc")
        self.change("plan/approve")
        with self.assertRaisesRegex(WorkflowError, "Upload and approve"):
            self.change("qc")
        self.assertEqual([item["id"] for item in self.project["missing"]], ["cup"])
        self.assertIn("A brown ceramic cup", self.project["artPrompt"])
        self.upload_art("tan")
        self.approve_art()
        self.approve_dry_mix()
        with self.assertRaisesRegex(WorkflowError, "Run and pass QC"):
            self.change("candidate/accept")
        self.change("qc")
        with self.assertRaisesRegex(WorkflowError, "Accept the current candidate"):
            self.change("export")
        self.change("candidate/accept")
        self.assertTrue(self.project["accepted"])
        edited = two_scenes()
        edited[0]["title"] = "A changed title"
        self.change("plan", scenes=edited)
        self.assertEqual(self.project["input"]["hash"], locked)
        self.assertFalse(self.project["plan"]["approved"])
        self.assertFalse(self.project["accepted"])
        self.assertIsNone(self.project["qc"])
        self.change("input/unlock")
        self.assertIsNone(self.project["input"])
        self.assertIsNone(self.project["plan"])

    def test_art_normalization_fingerprint_and_revocation(self):
        self.plan_with_art()
        self.approve_dry_mix()
        original = self.project["assets"][0]
        path, content_type = self.studio.media(self.project_id, "art", original["hash"])
        self.assertEqual(content_type, "image/png")
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), original["hash"])
        with Image.open(path) as art:
            self.assertEqual((art.format, art.mode, art.size), ("PNG", "RGBA", (160, 112)))
        with Image.open(BytesIO(self.studio.preview(self.project_id, 1.5))) as frame:
            self.assertEqual(frame.size, (render.WIDTH, render.HEIGHT))
        self.change("qc")
        self.change("candidate/accept")
        self.upload_art("brown")
        self.assertTrue(self.project["plan"]["approved"])
        self.assertFalse(self.project["assets"][0]["approved"])
        self.assertNotEqual(self.project["assets"][0]["hash"], original["hash"])
        self.assertFalse(self.project["accepted"])
        self.assertIsNone(self.project["qc"])
        with self.assertRaisesRegex(WorkflowError, "Upload and approve"):
            self.change("qc")

    def test_planner_cannot_move_the_gates(self):
        self.plan_with_art()
        self.approve_dry_mix()
        self.change("qc")
        self.change("candidate/accept")
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

    def test_library_pin_keeps_the_selected_version_across_projects(self):
        self.plan_with_art()
        original = self.project["assets"][0]
        self.assertEqual(original["libraryVersion"], 1)
        self.upload_art("brown")
        self.approve_art()
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

    def test_planner_reuses_approved_art_from_another_project(self):
        self.plan_with_art()
        original = self.project["assets"][0]
        second = self.studio.create("A cup in view")
        second = self.studio.upload_audio(second["id"], second["revision"], "narration.wav", wav_recording())
        second = self.studio.change(second["id"], "input", {
            "revision": second["revision"], "script": SCRIPT, "intention": "A ceramic cup.",
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
        with self.assertRaisesRegex(WorkflowError, "(?i)mix|music|composition"):
            self.change("qc")
        self.upload_mix("music", "score.wav", wav_tone(220, 0.5))
        self.upload_mix("effects", "chime.wav", wav_tone(660, 0.3))
        effect_id = self.project["mix"]["effects"][0]["id"]
        mix = {"musicMode": "track", "musicGainDb": -24,
               "effects": [{"id": effect_id, "start": 0.25, "gainDb": -9}]}
        self.change("mix", **mix)
        with self.assertRaisesRegex(WorkflowError, "(?i)mix|music|composition"):
            self.change("qc")
        self.change("mix/approve")
        first_preview = self.studio.mix_preview(self.project_id)
        first_mix_bytes = first_preview.read_bytes()
        with wave.open(str(first_preview), "rb") as recording:
            self.assertAlmostEqual(recording.getnframes() / recording.getframerate(), 2, places=2)
        self.change("qc")
        first_candidate = self.project["qc"]["candidateHash"]
        self.change("candidate/accept")
        mix["effects"][0]["start"] = 0.75
        self.change("mix", **mix)
        self.assertFalse(self.project["accepted"])
        self.change("mix/approve")
        second_preview = self.studio.mix_preview(self.project_id)
        self.assertNotEqual(first_mix_bytes, second_preview.read_bytes())
        self.change("qc")
        self.assertNotEqual(self.project["qc"]["candidateHash"], first_candidate)

    @unittest.skipUnless(render.render_available(), "FFmpeg and FFprobe are required")
    def test_verified_export_with_narration_captions_and_receipt(self):
        self.plan_with_art()
        self.approve_dry_mix()
        self.change("qc")
        self.change("candidate/accept")
        self.change("export")
        self.wait_for_jobs()
        result = self.studio.get(self.project_id)["export"]
        self.assertEqual(result["status"], "complete", result.get("error"))
        key = result["candidateHash"]
        video, _ = self.studio.media(self.project_id, "video", key)
        captions, _ = self.studio.media(self.project_id, "captions", key)
        receipt, _ = self.studio.media(self.project_id, "receipt", key)
        file = json.loads(receipt.read_bytes())
        self.assertEqual(file["candidateHash"], key)
        self.assertEqual(file["accepted"]["hash"], key)
        self.assertEqual(file["videoHash"], hashlib.sha256(video.read_bytes()).hexdigest())
        self.assertEqual(file["media"]["audio"], "aac")
        self.assertEqual(file["media"]["frames"], 48)
        self.assertAlmostEqual(file["media"]["duration"], 2, places=2)
        captions_text = captions.read_text(encoding="utf-8")
        self.assertIn("00:00:00,000 --> 00:00:01,091", captions_text)
        self.assertIn("The cup catches the light.", captions_text)


if __name__ == "__main__":
    unittest.main()
