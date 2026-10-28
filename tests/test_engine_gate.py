"""basal-ui runs on a supported engine release; anything else is refused with the install command, unless the user
opts in to an experimental engine."""

import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

import pytest

from basal_ui import engine

ROOT = Path(__file__).parents[1]


def fake_engine(monkeypatch, version, commit=None):
    monkeypatch.setattr(engine, "installed", lambda: (version, commit))


def test_a_supported_release_runs_as_is(monkeypatch):
    fake_engine(monkeypatch, engine.PINNED_RELEASE)
    monkeypatch.delenv(engine.UNCHECKED_ENV, raising=False)
    assert engine.check() == {
        "version": engine.PINNED_RELEASE,
        "commit": None,
        "supported": engine.SUPPORTED_RANGE,
        "experimental": False,
    }


@pytest.mark.parametrize("version", ["2.5.0", "2.4.0", "not-a-version"])
def test_an_engine_outside_the_range_is_refused_unless_unchecked(monkeypatch, version):
    fake_engine(monkeypatch, version)
    monkeypatch.delenv(engine.UNCHECKED_ENV, raising=False)
    with pytest.raises(SystemExit, match=f"found {version}.*BASAL_UI_ENGINE_UNCHECKED=1"):
        engine.check()
    monkeypatch.setenv(engine.UNCHECKED_ENV, "1")
    assert engine.check()["experimental"] is True


def test_a_branch_install_in_the_range_is_experimental(monkeypatch):
    fake_engine(monkeypatch, engine.PINNED_RELEASE, "0123456789abcdef")
    assert engine.check() == {
        "version": engine.PINNED_RELEASE,
        "commit": "0123456789abcdef",
        "supported": engine.SUPPORTED_RANGE,
        "experimental": True,
    }


def test_a_missing_engine_or_the_unrelated_pypi_basal_names_the_install(monkeypatch):
    real = importlib.util.find_spec
    monkeypatch.setattr(engine.importlib.util, "find_spec", lambda name: None if name == "basal.server" else real(name))
    with pytest.raises(SystemExit, match=re.escape(engine.ENGINE_URL)):
        engine.check()


def test_the_command_refuses_before_importing_the_engine(tmp_path):
    """`basal-ui` (basal_ui.__main__) checks first: an unsupported engine never gets as far as basal_ui.app."""
    fake = tmp_path / "basal"
    (fake / "server.py").parent.mkdir()
    (fake / "__init__.py").write_text('__version__ = "9.0.0"\n')
    (fake / "server.py").write_text("raise RuntimeError('basal_ui.app imported the engine')\n")
    result = subprocess.run(
        [sys.executable, "-m", "basal_ui"],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": f"{tmp_path}:{engine.__file__.rsplit('/basal_ui/', 1)[0]}", "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 1
    assert f"basal-ui supports engine {engine.SUPPORTED_RANGE}, found 9.0.0" in result.stderr


def test_every_pin_names_the_tested_release():
    """basal_ui/engine.py holds the engine release; uv's source, the install lines and CI must name the same one
    (bumping it: CONTRIBUTING.md, "Bumping the engine")."""
    assert engine.PINNED_RELEASE in engine.SUPPORTED
    source = re.search(r'^basal = \{ url = "([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M).group(1)
    assert source == engine.ENGINE_URL, "pyproject.toml [tool.uv.sources] basal names another release"
    for doc in ("README.md", "docs/E2E.md", "CONTRIBUTING.md"):
        text = unquote((ROOT / doc).read_text())
        tags = set(re.findall(r"archive/refs/tags/v(\S+?)\.tar\.gz", text))
        assert tags <= {engine.PINNED_RELEASE}, f"{doc} installs engine {tags}"
        assert tags or doc == "CONTRIBUTING.md", f"{doc} lost its install line"
        ranges = set(re.findall(r">=\d[\d.]*,<\d[\d.]*", text))
        assert ranges <= {engine.SUPPORTED_RANGE}, f"{doc} names range {ranges}"
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    assert not re.search(r"v\d+\.\d+\.\d+", ci), "ci.yml names an engine tag; its matrix comes from TESTED_RELEASES"
