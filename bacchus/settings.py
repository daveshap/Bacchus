"""Local preferences and process-only provider credentials."""

from contextlib import suppress
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from threading import RLock


DEFAULT_OPENAI_MODEL = "gpt-6-luna"
DEFAULT_TRANSCRIBE_MODEL = "gpt-transcribe"
DEFAULT_IMAGE_MODEL = "gpt-image-2.5-flare-2026-09-08"

_MODELS = {
    "claudeModel": ("BACCHUS_CLAUDE_MODEL", ""),
    "openAIModel": ("BACCHUS_OPENAI_MODEL", DEFAULT_OPENAI_MODEL),
    "transcribeModel": ("BACCHUS_TRANSCRIBE_MODEL", DEFAULT_TRANSCRIBE_MODEL),
    "imageModel": ("BACCHUS_IMAGE_MODEL", DEFAULT_IMAGE_MODEL),
}
_PROVIDER_KEY = "BACCHUS_PLANNER_PROVIDER"
_PROVIDERS = ("auto", "openai", "anthropic")
_KEYS = {
    "OPENAI_API_KEY": ("openAIKey", "clearOpenAIKey", "OpenAI"),
    "ANTHROPIC_API_KEY": ("anthropicKey", "clearAnthropicKey", "Anthropic"),
}
_PREFERENCES = {"completed", "plannerProvider", *_MODELS}
_INPUT_FIELDS = _PREFERENCES | {field for key, clear, _ in _KEYS.values() for field in (key, clear)}
_MODEL_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_LOCK = RLock()
_OVERRIDES = {}


class SettingsError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def provider_value(name, default=None):
    """Read a local override or the environment inherited at launch."""
    with _LOCK:
        if name in _OVERRIDES:
            value = _OVERRIDES[name]
            return default if value is None else value
        return os.environ.get(name, default)


def _model(value, default, secrets):
    if not isinstance(value, str):
        raise SettingsError("Model IDs must be text.")
    value = value.strip() or default
    if value and (not _MODEL_ID.fullmatch(value) or value.lower().startswith("sk-") or value in secrets):
        raise SettingsError("Enter a model ID in each model field. Use the API key fields for credentials.")
    return value


def _credential_changes(data):
    updates = {}
    for name, (field, clear_field, label) in _KEYS.items():
        clear = data.get(clear_field, False)
        key = data.get(field, "")
        if type(clear) is not bool or not isinstance(key, str):
            raise SettingsError(f"Check the {label} credential fields.")
        key = key.strip()
        if clear and key:
            raise SettingsError(f"Choose either replace or clear for the {label} API key.")
        if key and (len(key) > 4096 or any(not 33 <= ord(char) <= 126 for char in key)):
            raise SettingsError(f"Enter a valid {label} API key.")
        if clear or key:
            updates[name] = key or None
    return updates


class Settings:
    def __init__(self, root):
        self.path = Path(root) / "settings.json"
        with _LOCK:
            _OVERRIDES.clear()
            self.preferences = self._load()
            self._apply_preferences(self.preferences)

    def _load(self):
        try:
            stored = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError):
            return {}
        if not isinstance(stored, dict):
            return {}
        preferences = {}
        if type(stored.get("completed")) is bool:
            preferences["completed"] = stored["completed"]
        if stored.get("plannerProvider") in _PROVIDERS:
            preferences["plannerProvider"] = stored["plannerProvider"]
        secrets = {provider_value(name) for name in _KEYS}
        for field, (_, default) in _MODELS.items():
            if field in stored:
                try:
                    preferences[field] = _model(stored[field], default, secrets)
                except SettingsError:
                    continue
        return preferences

    @staticmethod
    def _apply_preferences(preferences):
        if "plannerProvider" in preferences:
            _OVERRIDES[_PROVIDER_KEY] = preferences["plannerProvider"]
        for field, (name, _) in _MODELS.items():
            if field in preferences:
                _OVERRIDES[name] = preferences[field]

    def get(self):
        with _LOCK:
            provider = provider_value(_PROVIDER_KEY, "auto").strip().lower()
            view = {
                "completed": self.preferences.get("completed", False),
                "plannerProvider": provider if provider in _PROVIDERS else "auto",
                "hasOpenAIKey": bool(provider_value("OPENAI_API_KEY", "").strip()),
                "hasAnthropicKey": bool(provider_value("ANTHROPIC_API_KEY", "").strip()),
            }
            secrets = {provider_value(name) for name in _KEYS}
            for field, (name, default) in _MODELS.items():
                try:
                    view[field] = _model(provider_value(name, default), default, secrets)
                except SettingsError:
                    view[field] = default
            return view

    def save(self, data):
        if not isinstance(data, dict) or set(data) - _INPUT_FIELDS:
            raise SettingsError("The settings request contains an unsupported field.")
        with _LOCK:
            credentials = _credential_changes(data)
            preferences = self.preferences.copy()
            if "completed" in data:
                if type(data["completed"]) is not bool:
                    raise SettingsError("Choose whether setup is complete.")
                preferences["completed"] = data["completed"]
            if "plannerProvider" in data:
                if data["plannerProvider"] not in _PROVIDERS:
                    raise SettingsError("Choose Auto, OpenAI, or Anthropic for the planner.")
                preferences["plannerProvider"] = data["plannerProvider"]
            secrets = {provider_value(name) for name in _KEYS} | set(credentials.values())
            for field, (_, default) in _MODELS.items():
                if field in data:
                    preferences[field] = _model(data[field], default, secrets)
                elif field in preferences and preferences[field] in secrets:
                    raise SettingsError("Change the model field that already contains the submitted credential.")
            if preferences != self.preferences:
                self._write(preferences)
                self.preferences = preferences
            self._apply_preferences(preferences)
            _OVERRIDES.update(credentials)
            return self.get()

    def _write(self, preferences):
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent, delete=False) as handle:
                temporary = Path(handle.name)
                json.dump(preferences, handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(self.path)
        except OSError as error:
            raise SettingsError("Bacchus could not save local preferences.", 500) from error
        finally:
            if temporary is not None:
                with suppress(OSError):
                    temporary.unlink(missing_ok=True)
