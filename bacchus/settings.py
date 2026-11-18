"""Local preferences and process-only provider credentials."""

from contextlib import suppress
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from threading import RLock

from . import api_log


DEFAULT_CLAUDE_MODEL = "claude-sonnet-5-5"
DEFAULT_OPENAI_MODEL = "gpt-6-luna"
DEFAULT_TRANSCRIBE_MODEL = "gpt-transcribe"
DEFAULT_IMAGE_MODEL = "gpt-image-2.5-flare-2026-09-08"

_MODELS = {
    "claudeModel": ("BACCHUS_CLAUDE_MODEL", DEFAULT_CLAUDE_MODEL),
    "openAIModel": ("BACCHUS_OPENAI_MODEL", DEFAULT_OPENAI_MODEL),
    "transcribeModel": ("BACCHUS_TRANSCRIBE_MODEL", DEFAULT_TRANSCRIBE_MODEL),
    "imageModel": ("BACCHUS_IMAGE_MODEL", DEFAULT_IMAGE_MODEL),
}
_PROVIDER_KEY = "BACCHUS_PLANNER_PROVIDER"
_API_LOG_DIR = "BACCHUS_API_LOG_DIR"
_PROVIDERS = ("anthropic", "openai")
_FEATURES = {
    "useOpenAITranscription": "BACCHUS_USE_OPENAI_TRANSCRIPTION",
    "useOpenAIImages": "BACCHUS_USE_OPENAI_IMAGES",
}
_BOOLEANS = {
    "completed": "Choose whether setup is complete.",
    "useOpenAITranscription": "Choose whether to enable OpenAI transcription.",
    "useOpenAIImages": "Choose whether to enable OpenAI artwork generation.",
}
_KEYS = {
    "OPENAI_API_KEY": ("openAIKey", "clearOpenAIKey", "OpenAI"),
    "ANTHROPIC_API_KEY": ("anthropicKey", "clearAnthropicKey", "Anthropic"),
}
_PREFERENCES = {"plannerProvider", "apiLogDirectory", *_BOOLEANS, *_MODELS}
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


def provider_secrets():
    with _LOCK:
        return tuple(value for name in _KEYS if (value := provider_value(name, "").strip()))


def _log_directory(value, secrets):
    if not isinstance(value, str):
        raise SettingsError("Enter an absolute folder path for API logs.")
    value = value.strip()
    if not value:
        return ""
    if (
        len(value) > 2048 or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in value)
        or any(secret and secret in value for secret in secrets) or not Path(value).is_absolute()
    ):
        raise SettingsError("Enter an absolute folder path for API logs, or leave it empty to use the default.")
    return str(Path(value))


def api_log_directory():
    try:
        selected = _log_directory(provider_value(_API_LOG_DIR, ""), provider_secrets())
    except SettingsError:
        selected = ""
    return Path(selected) if selected else api_log.default_directory()


def _normalize_planner(value):
    return "openai" if value.strip().lower() == "openai" else "anthropic"


def planner_choice():
    """Resolve the configured planner; provider readiness never changes this choice."""
    return _normalize_planner(provider_value(_PROVIDER_KEY, ""))


def openai_transcription_enabled():
    return provider_value(_FEATURES["useOpenAITranscription"], "").strip() == "1"


def openai_images_enabled():
    return provider_value(_FEATURES["useOpenAIImages"], "").strip() == "1"


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
        for field in _BOOLEANS:
            if type(stored.get(field)) is bool:
                preferences[field] = stored[field]
        selected = stored.get("plannerProvider")
        if isinstance(selected, str) and selected.strip().lower() in (*_PROVIDERS, "", "auto"):
            preferences["plannerProvider"] = _normalize_planner(selected)
        secrets = {provider_value(name) for name in _KEYS}
        if "apiLogDirectory" in stored:
            with suppress(SettingsError):
                preferences["apiLogDirectory"] = _log_directory(stored["apiLogDirectory"], secrets)
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
        if "apiLogDirectory" in preferences:
            _OVERRIDES[_API_LOG_DIR] = preferences["apiLogDirectory"]
        for field, name in _FEATURES.items():
            if field in preferences:
                _OVERRIDES[name] = "1" if preferences[field] else "0"
        for field, (name, _) in _MODELS.items():
            if field in preferences:
                _OVERRIDES[name] = preferences[field]

    def get(self):
        with _LOCK:
            view = {
                "completed": self.preferences.get("completed", False),
                "plannerProvider": planner_choice(),
                "useOpenAITranscription": openai_transcription_enabled(),
                "useOpenAIImages": openai_images_enabled(),
                "hasOpenAIKey": bool(provider_value("OPENAI_API_KEY", "").strip()),
                "hasAnthropicKey": bool(provider_value("ANTHROPIC_API_KEY", "").strip()),
            }
            secrets = {provider_value(name) for name in _KEYS}
            with suppress(SettingsError):
                view["apiLogDirectory"] = _log_directory(provider_value(_API_LOG_DIR, ""), secrets)
            view.setdefault("apiLogDirectory", "")
            view["apiLogPath"] = str(api_log_directory())
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
            for field, message in _BOOLEANS.items():
                if field in data:
                    if type(data[field]) is not bool:
                        raise SettingsError(message)
                    preferences[field] = data[field]
            if "plannerProvider" in data:
                if data["plannerProvider"] not in _PROVIDERS:
                    raise SettingsError("Choose Anthropic or OpenAI for the planner.")
                preferences["plannerProvider"] = data["plannerProvider"]
            secrets = {provider_value(name) for name in _KEYS} | set(credentials.values())
            if "apiLogDirectory" in data or "apiLogDirectory" in preferences:
                value = data.get("apiLogDirectory", preferences.get("apiLogDirectory"))
                preferences["apiLogDirectory"] = _log_directory(value, secrets)
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
