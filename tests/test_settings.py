import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from bacchus import planning, providers, settings


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
        setup.save({"anthropicKey": "claude-session", "claudeModel": "claude-test", "plannerProvider": "anthropic"})
        self.assertEqual(planning.planner_provider(), "anthropic")
        self.assertNotIn("ANTHROPIC_API_KEY", os.environ)
        setup.save({"plannerProvider": "auto", "imageModel": "test-image"})
        self.assertEqual(planning.planner_provider(), "openai")
        self.assertEqual(providers.image_request("ink", "an-asset", "A bird.")["model"], "test-image")

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
