"""basal-ui runs on a supported engine release; anything else is refused with the install command, unless the user
opts in to an experimental engine."""
import importlib.util
import subprocess
import sys

import pytest

from basal_ui import engine


def fake_engine(monkeypatch, version, commit=None):
    monkeypatch.setattr(engine, "installed", lambda: (version, commit))


def test_a_supported_release_runs_as_is(monkeypatch):
    fake_engine(monkeypatch, "2.4.1")
    monkeypatch.delenv(engine.UNCHECKED_ENV, raising=False)
    assert engine.check() == {"version": "2.4.1", "commit": None, "supported": ">=2.4.1,<2.5", "experimental": False}


@pytest.mark.parametrize("version", ["2.5.0", "2.4.0", "not-a-version"])
def test_an_engine_outside_the_range_is_refused_unless_unchecked(monkeypatch, version):
    fake_engine(monkeypatch, version)
    monkeypatch.delenv(engine.UNCHECKED_ENV, raising=False)
    with pytest.raises(SystemExit, match=f"found {version}.*BASAL_UI_ENGINE_UNCHECKED=1"):
        engine.check()
    monkeypatch.setenv(engine.UNCHECKED_ENV, "1")
    assert engine.check()["experimental"] is True


def test_a_branch_install_in_the_range_is_experimental(monkeypatch):
    fake_engine(monkeypatch, "2.4.1", "0123456789abcdef")
    assert engine.check() == {"version": "2.4.1", "commit": "0123456789abcdef", "supported": ">=2.4.1,<2.5",
                              "experimental": True}


def test_a_missing_engine_or_the_unrelated_pypi_basal_names_the_install(monkeypatch):
    real = importlib.util.find_spec
    monkeypatch.setattr(engine.importlib.util, "find_spec", lambda name: None if name == "basal.server" else real(name))
    with pytest.raises(SystemExit, match=r"rkinas/basal.*archive/refs/tags/v2\.4\.1\.tar\.gz"):
        engine.check()


def test_the_command_refuses_before_importing_the_engine(tmp_path):
    """`basal-ui` (basal_ui.__main__) checks first: an unsupported engine never gets as far as basal_ui.app."""
    fake = tmp_path / "basal"
    (fake / "server.py").parent.mkdir()
    (fake / "__init__.py").write_text('__version__ = "9.0.0"\n')
    (fake / "server.py").write_text("raise RuntimeError('basal_ui.app imported the engine')\n")
    result = subprocess.run([sys.executable, "-m", "basal_ui"], capture_output=True, text=True,
                            env={"PYTHONPATH": f"{tmp_path}:{engine.__file__.rsplit('/basal_ui/', 1)[0]}",
                                 "PATH": "/usr/bin:/bin"})
    assert result.returncode == 1
    assert "basal-ui supports engine >=2.4.1,<2.5, found 9.0.0" in result.stderr
