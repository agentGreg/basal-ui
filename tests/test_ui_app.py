import os
import sys
import threading
import time

from fastapi.testclient import TestClient

from basal.ui import catalog
from basal.ui.app import Runner, create_app
from basal.ui.catalog import Variant
from basal.ui.hardware import Hardware


class FakeRunner:
    def __init__(self, ready=False):
        self.ready = ready
        self.model = None
        self.media_requested = False
        self.media = []
    def status(self):
        return {"state": "ready" if self.ready else "idle", "model": self.model,
                "media_requested": self.media_requested, "media": self.media}
    def load(self, assessment, media=False):
        self.ready, self.model, self.media_requested = True, assessment.variant.id, media
        self.media = ["image", "audio"] if media else []
        return self.status()
    def unload(self): self.ready = False; self.model = None; return self.status()
    def while_idle(self, fn):
        if self.ready: raise RuntimeError("unload the model before changing the models directory")
        return fn()
    def while_not_busy(self, repo, fn):
        if self.ready: raise RuntimeError("unload the model first")
        return fn()
    def decide(self, body):
        if not self.ready: raise RuntimeError("No model is ready")

def test_model_api_sorting_load_validation_and_unready_decide():
    items = [Variant("mini", "Remek/basal-2.0-mini", "basal-2.0", "mini", "bf16", "bf16", None, 100, False),
             Variant("large", "Remek/basal-2.0-4.5B-FP8", "basal-2.0", "4.5B", "FP8", "FP8", None, 100, False)]
    hw = Hardware("cpu", "cpu", 1024**4, None, None, frozenset())
    runner = FakeRunner()
    app = create_app(catalog_fn=lambda **kwargs: (items, "hub"), hardware_fn=lambda: hw, runner=runner)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        models = client.get("/api/models").json()
        assert models["source"] == "hub"
        assert [m["id"] for m in models["models"]] == ["mini", "large"]
        assert "suggestions" in models and "recommended" not in models and "best_quality" not in models
        assert all("fit_now" in model for model in models["models"])
        assert client.get("/api/hardware").json()["available_bytes"] is None
        assert client.post("/api/load", json={"id": "large"}).status_code == 400
        assert client.post("/api/decide", json={"state": "x", "questions": {}}).status_code == 409


def test_load_with_media_requires_engine_and_echoes_status(monkeypatch):
    omni = Variant("omni", "Remek/basal-omni-1.0-mini", "basal-omni-1.0", "mini", "bf16", "bf16", None, 100,
                   True, ("image", "audio"))
    hw = Hardware("cpu", "cpu", 1024**4, None, None, frozenset())
    runner = FakeRunner()
    app = create_app(catalog_fn=lambda **kwargs: ([omni], "hub"), hardware_fn=lambda: hw, runner=runner)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post("/api/load", json={"id": "omni", "media": True})
        assert response.status_code == 400
        assert "install basal[media]" in response.json()["detail"]

    media_hw = Hardware("cpu", "cpu", 1024**4, None, None, frozenset({"media"}))
    monkeypatch.setattr(catalog, "downloaded_bytes", lambda variant, cache_dir: None)
    app = create_app(catalog_fn=lambda **kwargs: ([omni], "hub"), hardware_fn=lambda: media_hw, runner=runner)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        model = client.get("/api/models").json()["models"][0]
        assert model["media"] == ["image", "audio"]
        assert model["media_mode"] == "eager"
        assert model["media_reason"] is None
        response = client.post("/api/load", json={"id": "omni", "media": True})
        assert response.status_code == 202
        assert response.json()["media_requested"] is True
        assert runner.media_requested is True
        status = client.get("/api/status").json()
        assert status["media_requested"] is True
        assert status["media"] == ["image", "audio"]


def test_directory_change_rejected_while_runner_ready(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    item = Variant("mini", "Remek/basal-2.0-mini", "basal-2.0", "mini", "bf16", "bf16", None, 100, False)
    runner = FakeRunner(ready=True)
    runner.model = item.id
    app = create_app(catalog_fn=lambda **kwargs: ([item], "hub"), runner=runner)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.put("/api/settings", json={"models_dir": str(tmp_path / "models")})
    assert response.status_code == 409
    assert response.json()["detail"] == "unload the model before changing the models directory"


def test_delete_rejects_loaded_and_unknown_models(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    item = Variant("mini", "Remek/basal-2.0-mini", "basal-2.0", "mini", "bf16", "bf16", None, 100, False)
    runner = FakeRunner(ready=True)
    runner.model = item.id
    app = create_app(catalog_fn=lambda **kwargs: ([item], "hub"), runner=runner)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        loaded = client.post("/api/models/delete", json={"id": item.id})
        unknown = client.post("/api/models/delete", json={"id": "missing"})
    assert loaded.status_code == 409
    assert loaded.json()["detail"] == "unload the model first"
    assert unknown.status_code == 400


def test_hardware_endpoint_reads_available_memory_each_request():
    item1 = Hardware("cpu", "cpu", 1000, 2000, None, frozenset(), 100)
    item2 = Hardware("cpu", "cpu", 1000, 2000, None, frozenset(), 200)
    values = iter((item1, item2))
    app = create_app(hardware_fn=lambda: next(values), runner=FakeRunner())
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get("/api/hardware").json()["available_bytes"] == 100
        assert client.get("/api/hardware").json()["available_bytes"] == 200


def test_security_and_invalid_models_directory_type(tmp_path):
    item = Variant("mini", "Remek/basal-2.0-mini", "basal-2.0", "mini", "bf16", "bf16", None, 100, False)
    app = create_app(catalog_fn=lambda **kwargs: ([item], "hub"), runner=FakeRunner())
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get("/api/status", headers={"host": "evil.example"}).status_code == 400
        rejected = client.post("/api/unload", headers={"origin": "http://evil.example"})
        assert rejected.status_code == 403
        assert rejected.json() == {"detail": "cross-origin request refused"}
        assert client.post("/api/unload", headers={"origin": "http://127.0.0.1:7860"}).status_code == 200
        invalid = client.put("/api/settings", json={"models_dir": 123})
        assert invalid.status_code == 400
        assert invalid.json()["detail"] == "models_dir must be a string or null"


def test_delete_is_refused_while_repository_download_is_alive(tmp_path):
    item = Variant("mini", "Remek/basal-2.0-mini", "basal-2.0", "mini", "bf16", "bf16", None, 100, False)
    runner = Runner(models_dir_fn=lambda: tmp_path)
    release = threading.Event()
    worker = threading.Thread(target=release.wait, daemon=True)
    worker.start()
    runner.downloads[item.repo] = worker
    app = create_app(catalog_fn=lambda **kwargs: ([item], "hub"), runner=runner)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post("/api/models/delete", json={"id": item.id})
    release.set()
    worker.join()
    assert response.status_code == 409
    assert response.json()["detail"] == "unload the model first"


def test_startup_timeout_kills_child_process_group(tmp_path, monkeypatch):
    if os.name != "posix":
        import pytest
        pytest.skip("process-group lifecycle is POSIX-specific")
    item = Variant("mini", "Remek/basal-2.0-mini", "basal-2.0", "mini", "bf16", "bf16", None, 100, False)
    assessment = catalog.assess(item, Hardware("cpu", "cpu", 1000, 2000, None, frozenset()))
    pid_file = tmp_path / "grandchild.pid"
    code = ("import subprocess,sys,time; "
            f"p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
            f"open({str(pid_file)!r},'w').write(str(p.pid)); time.sleep(60)")
    runner = Runner(models_dir_fn=lambda: tmp_path)
    monkeypatch.setattr(catalog, "is_downloaded", lambda variant, cache_dir: tmp_path)
    monkeypatch.setattr(runner, "_build_command", lambda *args: [sys.executable, "-c", code])
    monkeypatch.setattr("basal.ui.app.START_TIMEOUT_S", 0.5)
    runner.load(assessment)
    deadline = time.monotonic() + 5
    while runner.status()["state"] != "error" and time.monotonic() < deadline:
        time.sleep(0.05)
    assert runner.status()["state"] == "error"
    assert pid_file.exists()
    grandchild = int(pid_file.read_text())
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            os.kill(grandchild, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        import pytest
        pytest.fail("grandchild process survived startup timeout")
