"""The Engine basal-ui runs on (rkinas/basal): version gate, feature probes, experimental-engine identity."""

import importlib.metadata
import importlib.util
import json
import os

from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version

ENGINE_REPO = "rkinas/basal"
TESTED_RELEASES = ("2.4.1",)  # every release basal-ui is tested with; CI runs each; the last is the pin
SUPPORTED_RANGE = ">=2.4.1,<2.5"  # bump with TESTED_RELEASES
SUPPORTED = SpecifierSet(SUPPORTED_RANGE)
UNCHECKED_ENV = "BASAL_UI_ENGINE_UNCHECKED"
PINNED_RELEASE = TESTED_RELEASES[-1]
ENGINE_URL = f"https://github.com/{ENGINE_REPO}/archive/refs/tags/v{PINNED_RELEASE}.tar.gz"
INSTALL_HINT = f'uv pip install "basal @ {ENGINE_URL}"'


def _spec(name):
    """importlib's spec of a module, None when it is missing or its package fails to import (the unrelated PyPI
    "basal" imports packages of its own)."""
    try:
        return importlib.util.find_spec(name)
    except (ImportError, ValueError):
        return None


def installed():
    """(version, commit or None) of the Engine; SystemExit with the install command when it is missing or is the
    unrelated PyPI "basal" (no basal.server)."""
    if _spec("basal") is None or _spec("basal.server") is None:
        raise SystemExit(f"basal-ui needs the basal engine (rkinas/basal), not found. Install it: {INSTALL_HINT}")
    import basal

    commit = None
    try:
        direct = json.loads(importlib.metadata.distribution("basal").read_text("direct_url.json") or "{}")
        commit = (direct.get("vcs_info") or {}).get("commit_id")
    except importlib.metadata.PackageNotFoundError:  # a source checkout on sys.path
        pass
    return getattr(basal, "__version__", "0"), commit


def _in_range(version):
    try:
        return Version(version) in SUPPORTED
    except InvalidVersion:
        return False


def status():
    """The installed Engine as the UI shows it: version, commit (an install from a git branch), the supported range,
    and whether it is an experimental engine (outside the range, or from a branch rather than a release)."""
    version, commit = installed()
    return {
        "version": version,
        "commit": commit,
        "supported": SUPPORTED_RANGE,
        "experimental": not _in_range(version) or commit is not None,
    }


def check():
    """status(); SystemExit for an Engine outside SUPPORTED unless BASAL_UI_ENGINE_UNCHECKED=1."""
    out = status()
    if not _in_range(out["version"]) and os.environ.get(UNCHECKED_ENV) != "1":
        raise SystemExit(
            f"basal-ui supports engine {SUPPORTED_RANGE}, found {out['version']}. Install a supported "
            f"release ({INSTALL_HINT}) or set {UNCHECKED_ENV}=1 to run anyway."
        )
    return out


FEATURES = {
    "adapter": lambda: _spec("basal.adapter") is not None,  # --adapter (rkinas/basal#52, 2.5.1)
    # basal-viz reads Gemma 4 basal-omni (rkinas/basal#75); basal_ui.inspect imports basal.viz anyway
    "viz_gemma4": lambda: hasattr(importlib.import_module("basal.viz"), "is_gemma4"),
}


def feature(name):
    """Whether the installed Engine provides capability `name` (a key of FEATURES)."""
    return FEATURES[name]()


def supported(version):
    """Whether an Engine release (e.g. the latest on GitHub) is in this basal-ui's supported range."""
    return _in_range(version)
