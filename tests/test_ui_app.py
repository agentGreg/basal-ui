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
def test_api_serve_settings_and_key_routes(tmp_path, monkeypatch):
    from basal.ui import settings

    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    app = create_app(catalog_fn=lambda **_: ([], "test"), hardware_fn=lambda: Hardware(
        "cpu", "cpu", 1024**4, None, None, frozenset()), runner=FakeRunner())
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get("/api/serve").json()["settings"]["enabled"] is False
        assert client.put("/api/serve", json={"enabled": True, "port": 8123}).json()["settings"]["port"] == 8123
        key = client.post("/api/serve/keys", json={"name": "laptop"})
        assert key.status_code == 201 and key.json()["key"].startswith("bsl_")
        assert client.delete("/api/serve/keys/laptop").json() == {"deleted": "laptop"}
        assert client.put("/api/serve", json={"port": 80}).status_code == 400
def test_runner_build_command_has_api_server_flags_and_model_name(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from basal.ui import settings

    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    model = Variant("model-id", "Remek/basal-2.0-mini", "basal-2.0", "mini", "bf16", "bf16", None, 100, False)
    runner = Runner(settings_mod=settings, platform_fn=lambda: "cpu", internal_secret="internal")
    assessment = SimpleNamespace(variant=model, mode="vllm")
    private_port = 43217
    command = runner._build_command(model, tmp_path, assessment, False, private_port, settings.serve_settings())
    assert "--name" in command and command[command.index("--name") + 1] == model.id
    assert command[command.index("--host") + 1] == "127.0.0.1"
    assert command[command.index("--port") + 1] == str(private_port)
    assert not {"--api-keys", "--cors-origins", "--mcp", "--log-decisions"} & set(command)
    assert command[command.index("--max-len") + 1] == "4096"

    settings.update_serve({"enabled": True, "require_key": True, "cors_origins": ["https://example.com"],
                           "log_decisions": str(tmp_path / "decisions.jsonl"), "mcp": True, "max_len": 8192})
    monkeypatch.setattr("basal.mcp_server.available", lambda: None)
    command = runner._build_command(model, tmp_path, assessment, False, private_port, settings.serve_settings())
    for flag in ("--api-keys", "--cors-origins", "--log-decisions", "--mcp"):
        assert flag in command
    assert command[command.index("--max-len") + 1] == "8192"
    assessment.mode = "sglang"
    command = runner._build_command(model, tmp_path, assessment, False, private_port, settings.serve_settings())
    assert command[command.index("--max-len") + 1] == "8192"
    settings.update_serve({"host": "0.0.0.0"})
    command = runner._build_command(model, tmp_path, assessment, False, private_port, settings.serve_settings())
    assert command[command.index("--host") + 1] == "0.0.0.0"
    assessment.mode = "eager"
    command = runner._build_command(model, tmp_path, assessment, False, private_port, settings.serve_settings())
    assert "--max-len" not in command


def test_port_probe_rejects_real_listener_despite_reuseaddr(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    item = Variant("mini", "Remek/basal-2.0-mini", "basal-2.0", "mini", "bf16", "bf16", None, 100, False)
    listener = __import__("socket").socket()
    listener.bind(("127.0.0.1", 0)); listener.listen()
    port = listener.getsockname()[1]
    runner = Runner(models_dir_fn=lambda: tmp_path)
    runner.settings_mod.update_serve({"enabled": True, "host": "127.0.0.1", "port": port})
    assessment = catalog.assess(item, Hardware("cpu", "cpu", 1000, 2000, None, frozenset()))
    monkeypatch.setattr(catalog, "is_downloaded", lambda variant, cache_dir: tmp_path)
    try:
        runner.load(assessment)
        deadline = time.monotonic() + 3
        while runner.status()["state"] != "error" and time.monotonic() < deadline:
            time.sleep(0.02)
        assert runner.status()["state"] == "error"
        assert runner.status()["error"] == f"port {port} on 127.0.0.1 is in use"
    finally:
        listener.close()


def test_real_subprocess_requires_internal_key_and_proxies_decision(tmp_path, monkeypatch):
    import json

    item = Variant("mini", "Remek/basal-2.0-mini", "basal-2.0", "mini", "bf16", "bf16", None, 100, False)
    runner = Runner(models_dir_fn=lambda: tmp_path)
    monkeypatch.setattr(catalog, "is_downloaded", lambda variant, cache_dir: tmp_path)
    secret = runner.internal_secret
    code = f"""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
class Handler(BaseHTTPRequestHandler):
    def reply(self, status, body=b''):
        self.send_response(status); self.send_header('Content-Length', str(len(body))); self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        if self.path != '/health' and self.headers.get('Authorization') != 'Bearer {secret}':
            return self.reply(401)
        self.reply(200, b'{{"status":"ok"}}')
    def do_POST(self):
        body = self.rfile.read(int(self.headers['Content-Length']))  # read first: replying early resets the socket
        if self.headers.get('Authorization') != 'Bearer {secret}':
            return self.reply(401)
        self.reply(200, json.dumps({{'received': json.loads(body)}}).encode())
    def log_message(self, *args): pass
HTTPServer(('127.0.0.1', int(__import__('sys').argv[1])), Handler).serve_forever()
"""
    monkeypatch.setattr(runner, "_build_command", lambda v, path, assessment, media, port, serve:
                        [sys.executable, "-c", code, str(port)])
    hw = Hardware("cpu", "cpu", 1024**4, None, None, frozenset())
    app = create_app(catalog_fn=lambda **kwargs: ([item], "test"), hardware_fn=lambda: hw, runner=runner)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.post("/api/load", json={"id": item.id}).status_code == 202
        deadline = time.monotonic() + 5
        while runner.status()["state"] != "ready" and time.monotonic() < deadline:
            time.sleep(0.02)
        import httpx
        assert runner.status()["state"] == "ready"
        assert httpx.get(runner.base_url + "/health").status_code == 200
        assert httpx.get(runner.base_url + "/v1/models").status_code == 401
        assert httpx.get(runner.base_url + "/v1/models", headers=runner._headers()).status_code == 200
        body = {"state": "hello"}
        assert httpx.post(runner.base_url + "/v1/systemone", json=body).status_code == 401
        assert httpx.post(runner.base_url + "/v1/systemone", json=body, headers=runner._headers()).json() == {"received": body}
        assert client.post("/api/decide", json=body).json() == {"received": body}
        assert runner.status()["model"] == item.id
        client.post("/api/unload")
        assert runner.status()["state"] == "idle"


def test_serving_status_reports_name_and_caches_lan_ip(monkeypatch):
    calls = []
    monkeypatch.setattr("basal.ui.app._lan_ip", lambda: calls.append("lookup") or "192.0.2.10")
    runner = Runner()
    runner.state, runner.model = "ready", "Remek/basal-2.0-mini"
    runner.serve_snapshot = {"enabled": True, "host": "0.0.0.0", "port": 8123, "require_key": False, "mcp": False}
    status = runner.status()
    assert status["serving"]["url"] == "http://192.0.2.10:8123"
    assert status["serving"]["model_name"] == runner.model
    runner.status()
    assert calls == ["lookup"]


def test_private_server_is_not_reported_as_serving(tmp_path, monkeypatch):
    from basal.ui import settings

    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    runner = Runner(settings_mod=settings)
    runner.state, runner.model = "ready", "Remek/basal-2.0-mini"
    runner.serve_snapshot = settings.serve_settings()  # serving disabled: the model answers only basal-ui
    status = runner.status()
    assert status["serving"] is None and status["restart_required"] is False
    settings.update_serve({"enabled": True})
    assert runner.status()["restart_required"] is True


def test_api_serve_routes_validate_settings_and_hide_secrets(tmp_path, monkeypatch):
    from basal.ui import settings

    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    app = create_app(catalog_fn=lambda **_: ([], "test"), runner=FakeRunner())
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.get("/api/serve")
        assert response.status_code == 200
        assert response.json()["settings"]["port"] == 8000
        assert {"auto_load", "keys", "mcp_available", "mcp_hint", "lan_ip"} <= response.json().keys()
        for patch in ({"host": "10.0.0.1"}, {"port": 80}, {"port": 70000}, {"max_len": 100},
                      {"cors_origins": ["bad-origin"]}, {"log_decisions": str(tmp_path / "missing" / "x.log")}):
            assert client.put("/api/serve", json=patch).status_code == 400
        monkeypatch.setattr("basal.mcp_server.available", lambda: "install basal[mcp]")
        assert client.put("/api/serve", json={"mcp": True}).status_code == 400
        assert client.put("/api/serve", json={"enabled": True, "port": 8123}).json()["settings"]["port"] == 8123
        invalid_auto_load = client.put("/api/serve", json={"port": 9000, "auto_load": "yes"})
        assert invalid_auto_load.status_code == 400
        assert invalid_auto_load.json()["detail"] == "auto_load must be a boolean"
        assert client.get("/api/serve").json()["settings"]["port"] == 8123
        created = client.post("/api/serve/keys", json={"name": "desk"})
        assert created.status_code == 201
        secret = created.json()["key"]
        assert secret.startswith("bsl_")
        assert client.post("/api/serve/keys", json={"name": "desk"}).status_code == 400
        assert client.post("/api/serve/keys", json={"name": "!"}).status_code == 400
        listing = client.get("/api/serve").json()
        assert listing["keys"][0]["name"] == "desk"
        assert "sha256" not in str(listing) and secret not in str(listing)
        assert client.delete("/api/serve/keys/missing").status_code == 404
        key_file = settings.keys_file("a" * 64)
        api_keys = __import__("json").loads(key_file.read_text())["keys"]
        assert {entry["name"] for entry in api_keys} == {"desk", "basal-ui"}
        assert key_file.stat().st_mode & 0o777 == 0o600
        saved = settings.load()
        assert "sha256" in saved["api_keys"][0] and secret not in (tmp_path / "settings.json").read_text()
        saved_settings = client.put("/api/serve", json={"cors_origins": ["https://example.com"]}).json()
        assert saved_settings["settings"]["cors_origins"] == ["https://example.com"]
        assert client.delete("/api/serve/keys/desk").json() == {"deleted": "desk"}
        revoked = __import__("json").loads(key_file.read_text())["keys"]
        assert [entry["name"] for entry in revoked] == ["basal-ui"]
    assert not key_file.exists()  # basal-ui removes its own key file when it exits


def test_restart_requires_ready_model_and_reloads_media(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    item = Variant("mini", "Remek/basal-2.0-mini", "basal-2.0", "mini", "bf16", "bf16", None, 100, False)

    class ReloadRunner(FakeRunner):
        state = "idle"

        def status(self):
            return {"state": self.state, "model": self.model, "media_requested": self.media_requested,
                    "media": self.media}

        def load(self, assessment, media=False):
            self.state = "ready"
            return super().load(assessment, media)

    runner = ReloadRunner()
    app = create_app(catalog_fn=lambda **_: ([item], "test"), runner=runner)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.post("/api/restart").status_code == 409
        runner.state = "downloading"
        runner.model = item.id
        assert client.post("/api/restart").status_code == 409
        runner.state = "ready"
        runner.media_requested = True
        response = client.post("/api/restart")
        assert response.status_code == 202
        assert runner.media_requested is True


def test_auto_load_on_startup_and_unknown_model_is_skipped(tmp_path, monkeypatch, capsys):
    from basal.ui import settings

    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    settings.set_auto_load(True)
    settings.remember_model("mini", False)
    item = Variant("mini", "Remek/basal-2.0-mini", "basal-2.0", "mini", "bf16", "bf16", None, 100, False)

    class RecordingRunner(FakeRunner):
        def __init__(self):
            super().__init__()
            self.loads = []

        def load(self, assessment, media=False):
            self.loads.append((assessment.variant.id, media))
            return super().load(assessment, media)

    runner = RecordingRunner()
    app = create_app(catalog_fn=lambda **_: ([item], "test"), runner=runner)
    with TestClient(app, base_url="http://127.0.0.1"):
        deadline = time.monotonic() + 2
        while not runner.loads and time.monotonic() < deadline:
            time.sleep(0.01)
    assert runner.loads == [("mini", False)]

    settings.remember_model("missing", False)
    runner = RecordingRunner()
    app = create_app(catalog_fn=lambda **_: ([item], "test"), runner=runner)
    with TestClient(app, base_url="http://127.0.0.1"):
        deadline = time.monotonic() + 2
        while not capsys.readouterr().err and time.monotonic() < deadline:
            time.sleep(0.01)
    assert runner.loads == []



def test_auto_load_waiting_on_catalog_is_cancelled_during_shutdown(tmp_path, monkeypatch):
    from basal.ui import settings

    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    settings.set_auto_load(True)
    settings.remember_model("mini", False)
    item = Variant("mini", "Remek/basal-2.0-mini", "basal-2.0", "mini", "bf16", "bf16", None, 100, False)
    real_event = threading.Event
    catalog_entered, release_catalog, shutdown_started = real_event(), real_event(), real_event()
    auto_load_event = real_event()
    set_auto_load_event = auto_load_event.set
    auto_load_event.set = lambda: (shutdown_started.set(), set_auto_load_event())
    event_count = 0

    def event_factory():
        nonlocal event_count
        event_count += 1
        return auto_load_event if event_count == 1 else real_event()

    monkeypatch.setattr("basal.ui.app.threading.Event", event_factory)

    def blocked_catalog(**_):
        catalog_entered.set()
        release_catalog.wait()
        return [item], "test"

    class RecordingRunner(FakeRunner):
        def __init__(self):
            super().__init__()
            self.loads = []

        def load(self, assessment, media=False):
            self.loads.append((assessment.variant.id, media))
            return super().load(assessment, media)

    runner = RecordingRunner()
    app = create_app(catalog_fn=blocked_catalog, runner=runner)
    client = TestClient(app)
    client.__enter__()
    assert catalog_entered.wait(timeout=2)
    def shutdown():
        client.__exit__(None, None, None)

    shutdown_thread = threading.Thread(target=shutdown)
    shutdown_thread.start()
    assert shutdown_started.wait(timeout=2)
    release_catalog.set()
    shutdown_thread.join(timeout=5)
    assert not shutdown_thread.is_alive()
    assert runner.loads == []


def test_create_app_passes_settings_module_to_default_runner(monkeypatch):
    from types import SimpleNamespace
    import basal.ui.app as app_module

    fake_settings = SimpleNamespace(models_dir=lambda: None)
    captured = {}

    def runner_factory(**kwargs):
        captured.update(kwargs)
        return FakeRunner()

    monkeypatch.setattr(app_module, "Runner", runner_factory)
    create_app(settings_mod=fake_settings)
    assert captured["settings_mod"] is fake_settings


def test_stale_overlapping_start_does_not_replace_last_ready_model(tmp_path, monkeypatch):
    import io
    from types import SimpleNamespace
    from basal.ui import settings

    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    runner = Runner(settings_mod=settings)
    old = SimpleNamespace(variant=Variant("old", "repo/old", "basal-2.0", "mini", "bf16", "bf16",
                                          None, 100, False), mode="eager")
    newer = SimpleNamespace(variant=Variant("newer", "repo/newer", "basal-2.0", "mini", "bf16", "bf16",
                                            None, 100, False), mode="eager")
    runner.gen = 1
    runner.state, runner.model = "starting", "old"
    monkeypatch.setattr(catalog, "is_downloaded", lambda *_: tmp_path)
    monkeypatch.setattr(runner, "_build_command", lambda *args: ["fake"])

    class Process:
        stdout = io.StringIO("")
        returncode = 0

        def poll(self):
            return None

    monkeypatch.setattr("basal.ui.app.subprocess.Popen", lambda *args, **kwargs: Process())
    old_health_entered, release_old_health = threading.Event(), threading.Event()

    def healthy(*args, **kwargs):
        if threading.current_thread().name == "old-start":
            old_health_entered.set()
            release_old_health.wait()
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr("basal.ui.app.httpx.get", healthy)
    old_thread = threading.Thread(target=runner._start, args=(old, 1, False, tmp_path, None), name="old-start")
    old_thread.start()
    assert old_health_entered.wait(timeout=2)
    runner.gen = 2
    runner.state, runner.model = "starting", "newer"
    runner._start(newer, 2, False, tmp_path, None)
    assert settings.last_model()["id"] == "newer"
    release_old_health.set()
    old_thread.join(timeout=2)
    assert not old_thread.is_alive()
    assert settings.last_model()["id"] == "newer"

def test_restart_required_tracks_settings_until_reload(tmp_path, monkeypatch):
    from basal.ui import settings

    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    settings.update_serve({"enabled": True})
    item = Variant("mini", "Remek/basal-2.0-mini", "basal-2.0", "mini", "bf16", "bf16", None, 100, False)
    runner = Runner(settings_mod=settings)
    runner.state, runner.model = "ready", item.id
    runner.serve_snapshot = settings.serve_settings()

    def reload(assessment, media=False):
        runner.serve_snapshot = settings.serve_settings()
        runner.media_requested = media
        return runner.status()

    runner.load = reload
    app = create_app(catalog_fn=lambda **_: ([item], "test"), runner=runner, settings_mod=settings)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert runner.status()["serving"]["restart_required"] is False
        response = client.put("/api/serve", json={"port": 8123})
        assert response.status_code == 200
        assert runner.status()["serving"]["restart_required"] is True
        assert client.post("/api/restart").status_code == 202
        assert runner.status()["serving"]["restart_required"] is False
