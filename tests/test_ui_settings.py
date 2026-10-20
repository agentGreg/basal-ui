from pathlib import Path

import pytest
from huggingface_hub.constants import HF_HUB_CACHE

from basal.ui import settings


def test_set_and_reset_models_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    target = tmp_path / "models"

    assert settings.set_models_dir(str(target)) == target
    assert target.is_dir()
    assert settings.models_dir() == target
    assert settings.set_models_dir(None) == Path(HF_HUB_CACHE)
    assert settings.models_dir() == Path(HF_HUB_CACHE)


def test_set_models_dir_rejects_unwritable_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    target = tmp_path / "readonly"
    target.mkdir()
    target.chmod(0o500)
    try:
        with pytest.raises(ValueError, match="is not writable"):
            settings.set_models_dir(str(target))
    finally:
        target.chmod(0o700)


def test_invalid_json_shape_uses_default_and_relative_path_is_absolute(tmp_path, monkeypatch):
    config = tmp_path / "settings.json"
    monkeypatch.setenv("BASAL_UI_CONFIG", str(config))
    monkeypatch.chdir(tmp_path)
    config.write_text("[]")
    assert settings.models_dir() == Path(HF_HUB_CACHE)
    stored = settings.set_models_dir("relative-models-dir")
    assert stored == (tmp_path / "relative-models-dir").resolve()
    assert settings.load()["models_dir"] == str(stored)
