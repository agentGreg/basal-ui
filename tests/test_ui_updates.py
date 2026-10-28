import httpx
import pytest
from fastapi.testclient import TestClient

from basal_ui import catalog, settings, updates
from basal_ui.app import create_app
from basal_ui.hardware import Hardware

HW = Hardware("cpu", "cpu", 1024**4, None, None, frozenset())


def app(tmp_path):
    return create_app(catalog_fn=lambda **_: ([], "test"), hardware_fn=lambda: HW)


class Reply:
    def __init__(self, version):
        self.version = version

    @staticmethod
    def raise_for_status():
        pass

    def json(self):
        return {"tag_name": self.version}


def test_ui_and_engine_updates_newer_older_unsupported_errors_and_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    monkeypatch.setattr(updates, "_cached", {})
    monkeypatch.setattr("basal_ui.app.ui_version", lambda: "0.1.0")
    latest = {updates.UI_REPO: "0.2.0", updates.ENGINE_REPO: "2.4.9"}
    monkeypatch.setattr(httpx, "get", lambda url, **kwargs: Reply(next(v for r, v in latest.items() if r in url)))
    with TestClient(app(tmp_path), base_url="http://127.0.0.1") as client:
        d = client.get("/api/updates").json()
        assert (d["installed"], d["latest"], d["newer"]) == ("0.1.0", "0.2.0", True)
        assert d["url"] == "https://github.com/pawelkiszczak/basal-ui/releases/tag/v0.2.0"
        engine = d["engine"]
        assert (engine["version"], engine["latest"], engine["newer"], engine["latest_supported"]) == (
            "2.4.1", "2.4.9", True, True)
        assert engine["experimental"] is False and engine["supported"] == ">=2.4.1,<2.5"
        monkeypatch.setattr(updates, "_cached", {})
        latest.update({updates.UI_REPO: "0.0.9", updates.ENGINE_REPO: "2.5.0"})
        d = client.get("/api/updates").json()
        assert d["newer"] is False
        assert d["engine"]["newer"] is True and d["engine"]["latest_supported"] is False  # released, not supported yet
        monkeypatch.setattr(updates, "_cached", {})
        monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: (_ for _ in ()).throw(httpx.HTTPError("offline")))
        d = client.get("/api/updates").json()
        assert d["error"] == d["engine"]["error"] == "offline"
        client.put("/api/settings", json={"check_updates": False})
        monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("disabled update check made a request")))
        disabled = client.get("/api/updates").json()
        assert disabled["enabled"] is False and disabled["latest"] is None and disabled["error"] is None
        assert disabled["engine"]["latest"] is None and disabled["engine"]["version"] == "2.4.1"


def test_a_failed_check_is_retried_after_ten_minutes_not_six_hours(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(updates.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(updates, "_cached", {})
    replies = iter((httpx.HTTPError("offline"), "9.0.0"))

    def get(*args, **kwargs):
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        return Reply(reply)
    monkeypatch.setattr(httpx, "get", get)
    assert updates.latest(updates.UI_REPO) == (None, "offline")
    clock[0] += 9 * 60
    assert updates.latest(updates.UI_REPO) == (None, "offline")  # cached
    clock[0] += 2 * 60
    assert updates.latest(updates.UI_REPO) == ("9.0.0", None)
    clock[0] += 60 * 60
    assert updates.latest(updates.UI_REPO) == ("9.0.0", None)  # a success stays cached for hours


@pytest.mark.parametrize("source", ["cache", "hub"])
def test_only_the_hub_catalog_seeds_and_reports_new_models(tmp_path, monkeypatch, source):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    settings.set_check_updates(False)
    variants = [catalog.Variant("a", "r/a", "basal-2.0", "mini", "bf16", "bf16", None, 1, False)]
    catalogs = [(variants[:0], source), (variants, source)]
    app = create_app(catalog_fn=lambda **_: catalogs[0], hardware_fn=lambda: HW)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        first = client.get("/api/updates").json()
        assert first["new_models"] == [] and first["catalog"] == source
        catalogs.pop(0)
        later = client.get("/api/updates").json()
    if source == "hub":  # the first hub catalog is the starting point; a model added later is new
        assert settings.load()["seen_models"] == [] and later["new_models"] == ["a"]
    else:  # a cache-only catalog is partial: it neither seeds nor reports
        assert "seen_models" not in settings.load() and later["new_models"] == []
