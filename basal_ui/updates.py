"""Check the latest basal-ui and engine releases on GitHub with a small process-local cache."""
import threading
import time

import httpx

UI_REPO = "pawelkiszczak/basal-ui"
ENGINE_REPO = "rkinas/basal"
_TTL = 6 * 60 * 60
_ERROR_TTL = 10 * 60  # a failed check is retried sooner than a successful one
_lock = threading.Lock()
_cached: dict[str, tuple[float, str | None, str | None]] = {}


def latest(repo):
    """(version without the leading v, None) of the repository's latest GitHub release, or (None, error)."""
    with _lock:
        now, version, error = _cached.get(repo, (0.0, None, None))
        if now and time.monotonic() - now < (_TTL if error is None else _ERROR_TTL):
            return version, error
        try:
            response = httpx.get(f"https://api.github.com/repos/{repo}/releases/latest", timeout=5)
            response.raise_for_status()
            version = response.json().get("tag_name", "").removeprefix("v") or None
            error = None if version else "release response has no tag_name"
        except (httpx.HTTPError, ValueError, AttributeError) as exc:
            version, error = None, str(exc)
        _cached[repo] = (time.monotonic(), version, error)
        return version, error
