import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from bacchus import audio, planning, providers, settings


class SettingsTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory(prefix="bacchus-settings-test-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.enterContext(patch.dict(settings._OVERRIDES, clear=True))
        self.enterContext(patch.dict(os.environ, {"OPENAI_API_KEY": "inherited-key"}, clear=True))

    def test_session_keys_stay_out_of_disk_environment_and_api_views(self):
        setup = settings.Settings(self.root)
        self.assertTrue(setup.get()["hasOpenAIKey"])
        view = setup.save({"openAIKey": "session-key", "completed": True, "openAIModel": "test-model"})
        self.assertTrue(view["hasOpenAIKey"])
        self.assertEqual(settings.provider_value("OPENAI_API_KEY"), "session-key")
        self.assertEqual(os.environ["OPENAI_API_KEY"], "inherited-key")
        self.assertNotIn("session-key", json.dumps(view))
        self.assertEqual(
            json.loads((self.root / "settings.json").read_text()),
            {"completed": True, "openAIModel": "test-model"},
        )
        setup.save({"openAIKey": "  "})
        self.assertEqual(settings.provider_value("OPENAI_API_KEY"), "session-key")
        self.assertFalse(setup.save({"clearOpenAIKey": True})["hasOpenAIKey"])
        self.assertFalse(providers.image_available())
        restarted = settings.Settings(self.root)
        self.assertEqual(settings.provider_value("OPENAI_API_KEY"), "inherited-key")
        self.assertEqual(restarted.get()["openAIModel"], "test-model")
        self.assertTrue(restarted.get()["completed"])

    def test_provider_preferences_apply_to_existing_adapters(self):
        setup = settings.Settings(self.root)
        self.assertEqual(setup.get()["plannerProvider"], "anthropic")
        self.assertEqual(setup.get()["claudeModel"], "claude-sonnet-5-5")
        setup.save({"anthropicKey": "claude-session", "claudeModel": "claude-test", "plannerProvider": "anthropic"})
        self.assertEqual(planning.planner_provider(), "anthropic")
        self.assertNotIn("ANTHROPIC_API_KEY", os.environ)
        setup.save({"plannerProvider": "openai", "imageModel": "test-image"})
        self.assertEqual(planning.planner_provider(), "openai")
        self.assertEqual(providers.image_request("ink", "an-asset", "A bird.")["model"], "test-image")

    def test_legacy_auto_planning_normalizes_to_anthropic(self):
        with patch.dict(os.environ, {"BACCHUS_PLANNER_PROVIDER": "auto"}):
            self.assertEqual(settings.Settings(self.root).get()["plannerProvider"], "anthropic")
        path = self.root / "settings.json"
        path.write_text('{"plannerProvider":"auto"}', encoding="utf-8")
        with patch.dict(os.environ, {"BACCHUS_PLANNER_PROVIDER": "openai"}):
            setup = settings.Settings(self.root)
            self.assertEqual(setup.get()["plannerProvider"], "anthropic")
            with self.assertRaises(settings.SettingsError):
                setup.save({"plannerProvider": "auto"})
        setup.save({"plannerProvider": "openai"})
        self.assertEqual(settings.Settings(self.root).get()["plannerProvider"], "openai")

    def test_optional_cloud_stages_require_independent_opt_in(self):
        setup = settings.Settings(self.root)
        with patch.object(audio, "audio_available", return_value=True):
            self.assertFalse(audio.transcriber_available())
            self.assertFalse(providers.image_available())
            view = setup.save({"useOpenAIImages": True, "useOpenAITranscription": False})
            self.assertTrue(view["useOpenAIImages"])
            self.assertFalse(view["useOpenAITranscription"])
            self.assertTrue(providers.image_available())
            with patch.dict(os.environ, {"BACCHUS_USE_OPENAI_TRANSCRIPTION": "1"}):
                restored = settings.Settings(self.root)
                self.assertFalse(audio.transcriber_available())
                restored.save({"useOpenAITranscription": True, "useOpenAIImages": False})
                self.assertTrue(audio.transcriber_available())
                self.assertFalse(providers.image_available())
        with patch.object(providers, "openai_json") as transport:
            with self.assertRaisesRegex(providers.ProviderError, "Enable OpenAI artwork"):
                providers.request_image({})
            transport.assert_not_called()

    def test_invalid_settings_do_not_persist_or_change_credentials(self):
        setup = settings.Settings(self.root)
        with self.assertRaises(settings.SettingsError):
            setup.save({"openAIKey": "incoming-key", "openAIModel": "incoming-key", "completed": True})
        with self.assertRaises(settings.SettingsError):
            setup.save({"openAIKey": "replacement-key", "clearOpenAIKey": True})
        self.assertEqual(settings.provider_value("OPENAI_API_KEY"), "inherited-key")
        self.assertFalse((self.root / "settings.json").exists())
        self.assertFalse(setup.get()["completed"])


if __name__ == "__main__":
    unittest.main()
