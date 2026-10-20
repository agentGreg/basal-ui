"""Persistent basal-ui settings."""
import json
import os
from pathlib import Path

from huggingface_hub.constants import HF_HUB_CACHE

SETTINGS_PATH = Path(os.environ.get("BASAL_UI_CONFIG", "~/.config/basal-ui/settings.json")).expanduser()


def _settings_path() -> Path:
    return Path(os.environ.get("BASAL_UI_CONFIG", SETTINGS_PATH)).expanduser()


def load() -> dict:
    try:
        value = json.loads(_settings_path().read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def save(settings: dict) -> None:
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2))


def models_dir() -> Path:
    configured = load().get("models_dir")
    return Path(configured).expanduser() if configured else Path(HF_HUB_CACHE)


def set_models_dir(path: str | None) -> Path:
    if path is None:
        settings = load()
        settings.pop("models_dir", None)
        save(settings)
        return Path(HF_HUB_CACHE)
    directory = Path(path).expanduser().resolve()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ValueError(f"{directory} is not writable") from exc
    if not os.access(directory, os.W_OK):
        raise ValueError(f"{directory} is not writable")
    settings = load()
    settings["models_dir"] = str(directory)
    save(settings)
    return directory
