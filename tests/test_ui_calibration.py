import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from basal import calibrate
from basal.ui import catalog, settings
from basal.ui.app import Fleet, Runner, create_app
from basal.ui.catalog import Variant
from basal.ui.hardware import Hardware
from basal.ui.history import History
from basal.ui.stats import Stats

HW = Hardware("cpu", "cpu", 1024**4, None, None, frozenset())
MINI = Variant("mini", "Remek/basal-2.0-mini", "basal-2.0", "mini", "bf16", "bf16", None, 100, False)
FITTED = {"prompt": 2, "temperature_per_prim": {"choice": 1.2}, "engine": "eager",
          "fitted_on": {"decisions": 20, "per_type": {"choice": 20}},
          "gates": {"automated_at": {"0.05": 0.4}},
          "thresholds": {"0.05": {"per_type": {"choice": {"confidence": 0.93}}}}}


@pytest.fixture(autouse=True)
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    model = tmp_path / "model"
    model.mkdir()
    (model / "CALIBRATION.json").write_text(json.dumps({"temperature_per_prim": {"choice": 1.1}}))
    monkeypatch.setattr(catalog, "is_downloaded", lambda variant, cache_dir: model)

    def start(self, assessment, gen, media=False, cache_dir=None, serve=None):
        with self.lock:
            if gen == self.gen:
                self.state, self.base_url, self.ready_at = "ready", "http://127.0.0.1:1", time.time()
                self.serve_snapshot = {"log_decisions": "", "max_len": 4096, "calibration": self._calibration(MINI)}
                self.command = self._build_command(MINI, model, assessment, False, 1, settings.serve_settings())
    monkeypatch.setattr(Runner, "_start", start)
    return model


def wait_job(client, kind="calibrate"):
    deadline = time.monotonic() + 5
    while client.get("/api/status").json()["jobs"][kind]["running"] and time.monotonic() < deadline:
        time.sleep(0.01)
    return client.get("/api/status").json()["jobs"][kind]


def test_calibrate_fits_against_a_temporary_child_lists_and_selects_the_file(tmp_path, monkeypatch, config):
    release, calls = threading.Event(), []

    def fake_main(argv):
        calls.append(argv)
        release.wait(5)
        print("report line")
        out = argv[argv.index("--out") + 1]
        with open(out, "w") as f:
            json.dump(FITTED, f)

    monkeypatch.setattr(calibrate, "main", fake_main)
    fleet = Fleet(models_dir_fn=lambda: tmp_path, platform_fn=lambda: "cpu", settings_mod=settings,
                  hardware_fn=lambda: HW, stats=Stats(tmp_path / "s.json"), history=History(tmp_path / "h.jsonl"))
    app = create_app(catalog_fn=lambda **_: ([MINI], "test"), hardware_fn=lambda: HW, fleet=fleet, stats=fleet.stats,
                     history=fleet.history)
    labelled = tmp_path / "labelled.jsonl"
    labelled.write_text('{"state": "s", "question": "q?", "options": ["a", "b"], "gold": 0}\n')
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.post("/api/load", json={"id": "mini"}).status_code == 202
        missing = client.post("/api/calibrate", json={"id": "mini", "input": str(tmp_path / "none.jsonl")})
        assert missing.status_code == 400 and "cannot read" in missing.json()["detail"]
        assert client.post("/api/calibrate", json={"id": "mini", "input": str(labelled)}).status_code == 202
        deadline = time.monotonic() + 5
        while not calls and time.monotonic() < deadline:
            time.sleep(0.01)
        assert "calibrate" in fleet.temporary  # the uncalibrated child, without --api-keys, counted in the budget
        child = fleet.temporary["calibrate"].command
        assert "--no-calibration" in child and "--api-keys" not in child
        busy = client.post("/api/calibrate", json={"id": "mini", "input": str(labelled)})
        assert busy.status_code == 409 and busy.json()["detail"] == "a calibrate job is already running"
        release.set()
        job = wait_job(client)
        assert job["phase"] == "done", job["error"]
        assert calls[0][calls[0].index("--url") + 1] == "http://127.0.0.1:1"
        assert calls[0][calls[0].index("--model") + 1] == str(config)
        result = job["result"]
        assert result["report"] == "report line" and result["thresholds"] == {"0.05": {"choice": 0.93}}
        assert fleet.temporary == {}

        listing = client.get("/api/calibrations", params={"id": "mini"}).json()
        assert listing["selected"] is None and [f["file"] for f in listing["files"]] == [result["file"]]
        assert listing["shipped"]["file"] == str(config / "CALIBRATION.json")
        report = client.get("/api/calibrations/report", params={"id": "mini", "file": result["file"]})
        assert report.json() == {"report": "report line\n"}
        stranger = client.post("/api/calibrations/select", json={"id": "mini", "file": str(tmp_path / "x.json")})
        assert stranger.status_code == 400

        runner = fleet.runners["mini"]
        assert runner.status()["restart_required"] is False
        assert client.post("/api/calibrations/select", json={"id": "mini", "file": result["file"]}).json()[
            "selected"] == result["file"]
        assert runner.status()["restart_required"] is True  # the running child has the shipped calibration
        command = runner._build_command(MINI, config, catalog.assess(MINI, HW), False, 1, settings.serve_settings())
        assert command[command.index("--calibration") + 1] == result["file"]
        client.post("/api/calibrations/select", json={"id": "mini", "file": None})
        assert runner.status()["restart_required"] is False


def test_calibrate_refuses_an_unknown_model_and_a_model_over_the_budget(tmp_path):
    fleet = Fleet(models_dir_fn=lambda: tmp_path, platform_fn=lambda: "cpu", settings_mod=settings,
                  hardware_fn=lambda: HW, stats=Stats(tmp_path / "s.json"), history=History(tmp_path / "h.jsonl"))
    app = create_app(catalog_fn=lambda **_: ([MINI], "test"), hardware_fn=lambda: HW, fleet=fleet,
                     stats=fleet.stats, history=fleet.history)
    labelled = tmp_path / "labelled.jsonl"
    labelled.write_text("{}\n")
    settings.update_memory({"reserve_bytes": 1024**4})  # nothing fits
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.post("/api/calibrate", json={"id": "nope", "input": str(labelled)}).status_code == 400
        over = client.post("/api/calibrate", json={"id": "mini", "input": str(labelled)})
        assert over.status_code == 409 and over.json()["detail"].startswith("over memory budget")
        assert client.post("/api/calibrate", json={"id": "mini", "input": str(labelled), "targets": [2]}).status_code == 400
