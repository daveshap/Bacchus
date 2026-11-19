import hashlib
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from bacchus import auto_review, local_timing, media_tools, planning, providers, render, settings, visual_review
from bacchus.workflow import Studio, WorkflowError
from test_workflow import SCRIPT, jpeg, two_scenes, wav_tone, word_timings


def decision(stage, status="pass", message="The supplied evidence meets the criterion."):
    return {"model": "independent-test", "provider": "test", "requestId": stage, "findings": [
        {"id": key, "status": status, "evidence": message} for key in auto_review.CHECKLISTS[stage]
    ]}


class CheckerAdapterTests(unittest.TestCase):
    def test_artwork_free_plan_persists_an_independently_checked_inventory(self):
        with TemporaryDirectory(prefix="bacchus-art-inventory-") as temp:
            studio = Studio(temp)
            pid = studio.create("No external artwork", demo=True)["id"]
            project = studio._read(pid)
            project["autoRun"] = {"checks": []}
            studio._save(project)
            with patch.object(auto_review, "check", return_value=decision("asset-inventory")) as checker:
                studio._auto_proofs(pid, "assets")
            checker.assert_called_once()
            [record] = studio._read(pid)["autoRun"]["checks"]
            self.assertEqual((record["stage"], record["status"]), ("asset-inventory", "passed"))
            path = Path(temp) / pid / "records" / "checks" / (record["evidenceHash"] + ".json")
            context = json.loads(path.read_bytes())["context"]
            self.assertEqual(context, checker.call_args.args[1])
            self.assertEqual([row["artwork"] for row in context["scenes"]], [[] for _ in project["plan"]["scenes"]])

    def test_delivery_refuses_exporter_with_different_renderer_or_film(self):
        with TemporaryDirectory(prefix="bacchus-encoded-binding-") as temp:
            studio = Studio(temp)
            record = {"renderer": "canvas", "buildHash": "a" * 64, "mix": {"rendered": {"sha256": "d" * 64}}}
            for mode in ("manual", "auto"):
                for encoder_result, expected in [
                    ({"renderer": "pillow"}, "encoded renderer"),
                    ({"renderer": "canvas", "visualVerification": {"buildHash": "b" * 64}}, "accepted film build"),
                    ({"renderer": "canvas"}, "accepted film build"),
                ]:
                    with self.subTest(mode=mode, result=encoder_result), patch.object(
                        render, "export_video", return_value=encoder_result,
                    ), self.assertRaisesRegex(WorkflowError, expected):
                        studio._build_delivery_bundle({}, Path(temp), "candidate", record, "cpu", "delivery", ("", {}), mode)
                    self.assertFalse((Path(temp) / "exports" / "delivery").exists())

    def test_independent_requests_for_each_supported_provider(self):
        findings = decision("plan")["findings"]
        output = json.dumps({"findings": findings})
        with patch.object(settings, "planner_choice", return_value="openai"), patch.object(
            providers, "openai_json", return_value=({"status": "completed", "output": [
                {"type": "message", "content": [{"type": "output_text", "text": output}]}]}, "openai-fixture"),
        ) as send:
            verdict = auto_review.check("plan", {"brief": "Show a green board."})
            payload = send.call_args.args[1]
            self.assertEqual(verdict["findings"], findings)
            self.assertFalse(payload["store"])
            self.assertEqual(len(payload["input"]), 1)
            self.assertNotIn("previous_response_id", payload)
        with patch.object(settings, "planner_choice", return_value="anthropic"), \
                patch.object(settings, "provider_value", return_value="fixture"), patch.object(
                    providers, "post_json", return_value=({"stop_reason": "end_turn", "content": [
                        {"type": "text", "text": output}]}, "anthropic-fixture"),
                ) as send:
            verdict = auto_review.check("plan", {"brief": "Show a green board."})
            self.assertEqual(verdict["requestId"], "anthropic-fixture")
            self.assertEqual(len(send.call_args.args[1]["messages"]), 1)
        with patch.object(settings, "planner_choice", return_value="openai"), patch.object(
            providers, "openai_json", return_value=({"status": "completed", "output": [
                {"type": "message", "content": [{"type": "output_text", "text": '{"findings":[]}'}]}]}, "invalid"),
        ):
            with self.assertRaises(visual_review.VisualReviewError):
                auto_review.check("plan", {"brief": "Show a green board."})


@unittest.skipUnless(render.render_available(), "FFmpeg is required")
class AutoPipelineTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix="bacchus-auto-gates-")
        self.addCleanup(temp.cleanup)
        self.studio = Studio(temp.name)
        self.project = self.studio.create("Narrated artwork fixture", auto_mode=True)
        self.pid = self.project["id"]
        self.folder = Path(temp.name) / self.pid
        self.project = self.studio.upload_audio(self.pid, self.project["revision"], "voice.wav", wav_tone(220, 2))
        self.project = self.studio.import_timings(self.pid, self.project["revision"],
                                                 word_timings(self.project["audio"]["originalHash"]))

    def start(self):
        revision = self.studio.get(self.pid)["revision"]
        self.studio.start_auto_pipeline(self.pid, {"revision": revision})
        for job in tuple(self.studio.jobs):
            job.join(90)
            self.assertFalse(job.is_alive())
        return self.studio.get(self.pid)

    def saved_plan(self):
        self.project = self.studio.change(self.pid, "input", {"revision": self.project["revision"],
                                          "script": SCRIPT, "intention": "Communicate the change of light.", "confirmTiming": True})
        self.project = self.studio.change(self.pid, "plan", {"revision": self.project["revision"], "scenes": two_scenes()})
        self.project = self.studio.upload_art(self.pid, self.project["revision"], "cup", "cup.jpg", jpeg("#996633"))

    def test_hard_provider_error_halts_immediately(self):
        with patch.object(auto_review, "check", side_effect=providers.ProviderError("API unavailable")) as checker, \
                patch.object(planning, "request_plan") as planner:
            project = self.start()
        checker.assert_called_once()
        planner.assert_not_called()
        self.assertEqual(project["autoRun"]["status"], "failed")
        self.assertIn("API unavailable", project["autoRun"]["error"])
        self.assertIsNone(project["busy"])
        self.assertIsNone(project["export"])

    def test_invalid_generated_image_closes_the_art_run_before_auto_stops(self):
        with patch.object(auto_review, "check", side_effect=lambda stage, *_: decision(stage)), \
                patch.object(planning, "planner_available", return_value=True), \
                patch.object(planning, "request_plan", return_value=(two_scenes(), {"model": "fixture"})), \
                patch.object(providers, "image_available", return_value=True), \
                patch.object(providers, "request_image", return_value=(b"unreadable image", {"requestId": "img-1"})) as image:
            project = self.start()
        image.assert_called_once()
        self.assertEqual(project["autoRun"]["status"], "failed")
        self.assertIsNone(project["busy"])
        self.assertIsNone(project["export"])
        [run] = json.loads((self.folder / "project.json").read_bytes())["artRuns"]
        self.assertEqual((run["mode"], run["status"], run["requestId"]), ("auto", "failed", "img-1"))
        self.assertTrue(run["finishedAt"])
        self.assertEqual(run["error"], project["autoRun"]["error"])
        self.assertIn("could not be decoded", run["error"])
        self.assertFalse(project["assets"])

        with patch.object(providers, "image_available", return_value=True), \
                patch.object(providers, "request_image", return_value=(b"unreadable image", {})), \
                self.assertRaisesRegex(WorkflowError, "could not be decoded") as failure:
            self.studio.generate_art(self.pid, "cup", {"revision": project["revision"]})
        self.assertEqual(failure.exception.status, 422)
        saved = json.loads((self.folder / "project.json").read_bytes())
        self.assertIsNone(saved["busy"])
        self.assertEqual(saved["artRuns"][-1]["status"], "failed")
        self.assertNotIn("mode", saved["artRuns"][-1])

    def test_one_failed_repair_survives_restart(self):
        self.saved_plan()
        revised = two_scenes()
        revised[0]["title"] = "Revised light"
        def check(stage, context, proof=None):
            if stage == "plan":
                return decision(stage, "fail", "Clarify the source of the light.")
            return decision(stage)
        with patch.object(auto_review, "check", side_effect=check), \
                patch.object(planning, "planner_available", return_value=True), \
                patch.object(planning, "request_plan", return_value=(revised, {"model": "fixture"})) as planner:
            project = self.start()
        planner.assert_called_once()
        self.assertIn("Clarify the source of the light.", json.dumps(planner.call_args.args[0]))
        failures = [row for row in project["autoRun"]["checks"] if row["stage"] == "plan"]
        self.assertEqual([row["status"] for row in failures], ["failed", "failed"])
        self.assertTrue(failures[0]["repairAttempted"])
        self.assertTrue(project["autoRun"]["blocked"])
        self.studio = Studio(self.folder.parent)
        with self.assertRaisesRegex(WorkflowError, "Correct the rejected"):
            self.start()

    def test_each_gate_receives_its_evidence_and_final_receipt(self):
        self.saved_plan()
        seen = []
        def check(stage, context, proof=None):
            seen.append(stage)
            if stage in ("assets", "animation", "delivery"):
                self.assertTrue(proof.startswith(b"\x89PNG"))
            if stage == "delivery":
                self.assertEqual(context["encodedMedia"]["decodedFrames"], 120)
                self.assertGreater(len(context["sampledScenes"]), 1)
                self.assertTrue(context["voiceoverRequired"])
            return decision(stage)
        with patch.object(auto_review, "check", side_effect=check):
            project = self.start()
        self.assertEqual(project["autoRun"]["status"], "complete", project["autoRun"]["error"])
        self.assertEqual(seen, ["intake", "plan", "assets", "composition", "animation", "animation", "qc", "delivery"])
        receipt = self.folder / "exports" / project["export"]["deliveryId"] / "receipt.json"
        checks = json.loads(receipt.read_bytes())["independentChecks"]
        self.assertEqual([row["stage"] for row in checks], seen)
        self.assertEqual(checks[-1]["status"], "passed")
        self.assertTrue(all(len(row["evidenceHash"]) == 64 for row in checks))
        for row in checks:
            saved_context = self.folder / "records" / "checks" / f"{row['evidenceHash']}.json"
            self.assertEqual(hashlib.sha256(saved_context.read_bytes()).hexdigest(), row["evidenceHash"])
        self.assertTrue((self.folder / "records" / "checks" / f"{checks[-1]['proofHash']}.png").is_file())

    def test_auto_cannot_overwrite_a_negative_advisory_on_the_same_proof(self):
        self.saved_plan()
        self.project = self.studio.change(self.pid, "plan/approve", {"revision": self.project["revision"]})
        self.project = self.studio.approve_art(self.pid, "cup", {"revision": self.project["revision"]})
        self.project = self.studio.change(self.pid, "assets/checks", {"revision": self.project["revision"]})
        check_id = self.project["assetChecks"][0]["id"]
        advisory = decision("assets", "fail", "The cup handle is cut off.")
        with patch("bacchus.workflow.visual_review.available", return_value=True), \
                patch("bacchus.workflow.visual_review.review", return_value=advisory):
            self.studio.advise_review(self.pid, "assets", check_id, {"revision": self.project["revision"]})
        # A generation call that returns the same pixels does not resolve the finding.
        with patch.object(auto_review, "check", side_effect=lambda stage, *args: decision(stage)), \
                patch.object(providers, "image_available", return_value=True), \
                patch.object(providers, "request_image", return_value=(jpeg("#996633"), {})) as image:
            project = self.start()
        self.assertEqual(project["autoRun"]["status"], "failed")
        self.assertTrue(project["autoRun"]["blocked"])
        self.assertIn("The cup handle is cut off", project["autoRun"]["error"])
        self.assertEqual(image.call_count, 1)
        self.assertIn("The cup handle is cut off", image.call_args.args[0]["prompt"])
        self.assertFalse(project["accepted"])
        self.assertEqual(project["assetChecks"][0]["status"], "failed")

    def test_rejected_encoded_evidence_prevents_publication(self):
        self.saved_plan()
        def check(stage, context, proof=None):
            if stage == "delivery":
                result = decision(stage)
                result["findings"][1].update(status="fail", evidence="Audio measurement conflicts with the master.")
                return result
            return decision(stage)
        with patch.object(auto_review, "check", side_effect=check):
            project = self.start()
        self.assertEqual(project["autoRun"]["status"], "failed")
        self.assertEqual(project["export"]["status"], "failed")
        self.assertIsNone(project["lastDelivery"])
        self.assertFalse(list((self.folder / "exports").glob("*/video.mp4")))

    def test_new_plan_and_repaired_art_get_fresh_independent_reviews(self):
        mp3 = subprocess.run(
            [media_tools.command("ffmpeg"), "-v", "error", "-i", "pipe:0", "-c:a", "libmp3lame", "-f", "mp3", "-"],
            input=wav_tone(330, 2), capture_output=True, check=True, timeout=20, **media_tools.process_options(),
        ).stdout
        self.project = self.studio.upload_audio(self.pid, self.project["revision"], "narration.mp3", mp3)
        words = json.loads(word_timings(self.project["audio"]["originalHash"]))["words"]
        self.project = self.studio.set_auto_settings(self.pid, {"revision": self.project["revision"],
            "autoLimits": {}, "autoBrief": "Show how light changes over time."})
        attempts = []
        def check(stage, context, proof=None):
            if stage == "assets":
                attempts.append(hashlib.sha256(proof).hexdigest())
                if len(attempts) == 1:
                    return decision(stage, "fail", "The illustrated subject needs a brighter background.")
            return decision(stage)
        with patch.object(auto_review, "check", side_effect=check), \
                patch.object(self.studio.timing_models, "resolve", return_value="fixture-model"), \
                patch.object(local_timing, "derive", return_value={"words": words}) as recognize, \
                patch.object(planning, "planner_available", return_value=True), \
                patch.object(planning, "request_plan", return_value=(two_scenes(), {"model": "fixture"})) as planner, \
                patch.object(providers, "image_available", return_value=True), \
                patch.object(providers, "request_image", side_effect=[(jpeg("black"), {}), (jpeg("tan"), {})]) as image:
            project = self.start()
        self.assertEqual(project["autoRun"]["status"], "complete", project["autoRun"]["error"])
        planner.assert_called_once()
        recognize.assert_called_once()
        self.assertEqual(project["audio"]["sourceExtension"], ".mp3")
        self.assertEqual(project["mix"]["musicMode"], "track")
        self.assertTrue(project["mix"]["music"]["arrangement"])
        self.assertTrue(project["mix"]["effects"])
        self.assertIn("Show how light changes over time.", json.dumps(planner.call_args.args[0]))
        self.assertEqual(len(set(attempts)), 2)
        self.assertEqual(image.call_count, 2)
        self.assertIn("brighter background", image.call_args.args[0]["prompt"])
        self.assertEqual(project["autoRun"]["checks"][-1]["stage"], "delivery")
