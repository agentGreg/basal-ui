import asyncio
import json
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from basal.ui import bench, settings
from basal.ui.app import Fleet, Runner, create_app
from basal.ui.hardware import Hardware
from basal.ui.history import History
from basal.ui.stats import Stats

HW = Hardware("cpu", "cpu", 1024**4, None, None, frozenset())


def write_set(path, golds):
    path.write_text("\n".join(json.dumps({"state": f"s{i}", "question": "q?", "options": ["a", "b"], "gold": g})
                              for i, g in enumerate(golds)) + "\n")
    return path


class AnswersOptionOne:
    """A fleet whose model always prefers option_1."""

    def __init__(self):
        self.sources = []

    async def adecide(self, model_id, body, path="/v1/systemone", source="api", client=None):
        self.sources.append(source)
        await asyncio.sleep(0.001)
        return httpx.Response(200, json={"answers": {"q0": {"probabilities": {"option_1": 0.7, "option_2": 0.3}}},
                                         "usage": {"input_tokens": 10}}, request=httpx.Request("POST", "http://x"))


def test_run_benchmark_measures_latency_throughput_and_accuracy(tmp_path):
    fleet = AnswersOptionOne()
    paths = [str(write_set(tmp_path / "two.jsonl", [0, 1]))]
    phases = []
    result = asyncio.run(bench.run_benchmark(fleet, "m", paths, "two", n_seq=4, n_conc=10, concurrency=3,
                                             progress=lambda phase, done, total: phases.append(phase)))
    assert result["accuracy"] == {"overall": 0.5, "by_file": {"two": 0.5}}
    assert (result["model"], result["set"], result["items"], result["errors"]) == ("m", "two", 2, 0)
    assert 0 < result["p50_ms"] <= result["p95_ms"] and result["mean_ms"] > 0
    assert result["decisions_per_s"] > 0 and result["input_tokens_per_s"] == pytest.approx(result["decisions_per_s"] * 10)
    assert set(fleet.sources) == {"benchmark"} and len(fleet.sources) == bench.WARMUP + 4 + 10
    assert phases[0] == "warm-up" and "sequential" in phases and phases[-1] == "concurrent"


def test_bundled_set_and_custom_set_validation(tmp_path):
    label, paths = bench.resolve_set("bundled")
    assert label == "bundled" and len(bench.load_items(paths)) == 44
    for spec, reason in ((str(tmp_path / "missing.jsonl"), "does not exist"),
                         (str(write_set(tmp_path / "small.jsonl", [0, 1, 0])), "3 items"),
                         (str(tmp_path), "at least 10")):
        with pytest.raises(ValueError, match=reason):
            bench.resolve_set(spec)
    (tmp_path / "bad.jsonl").write_text('{"state": "x"\n')
    with pytest.raises(ValueError, match="bad.jsonl:1: not JSON"):
        bench.resolve_set(str(tmp_path / "bad.jsonl"))
    (tmp_path / "set").mkdir()
    write_set(tmp_path / "set" / "one.jsonl", [0] * 6)
    write_set(tmp_path / "set" / "two.jsonl", [1] * 6)
    assert bench.resolve_set(str(tmp_path / "set"))[1] == [str(tmp_path / "set" / f) for f in ("one.jsonl",
                                                                                               "two.jsonl")]


def test_benchmark_routes_run_one_at_a_time_and_store_the_result(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    release = threading.Event()

    async def fake_run(fleet, model_id, paths, label, progress=None, cancel=None):
        progress("sequential", 1, 30)
        while not release.is_set():
            if cancel.is_set():
                raise bench.Cancelled()
            await asyncio.sleep(0.01)
        return {"model": model_id, "set": label, "p50_ms": 1.0}

    monkeypatch.setattr(bench, "run_benchmark", fake_run)
    stats = Stats(tmp_path / "stats.json")
    fleet = Fleet(models_dir_fn=lambda: tmp_path, platform_fn=lambda: "cpu", settings_mod=settings, hardware_fn=lambda: HW, stats=stats,
        history=History(tmp_path / "h.jsonl"))
    runner = Runner(internal_secret="x")
    runner.state, runner.model = "ready", "mini"
    fleet.runners["mini"] = runner
    app = create_app(catalog_fn=lambda **_: ([], "test"), hardware_fn=lambda: HW, fleet=fleet, stats=stats,
                     history=fleet.history)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.post("/api/benchmark", json={"id": "nope"}).status_code == 404
        too_small = client.post("/api/benchmark", json={"id": "mini", "set": str(write_set(
            tmp_path / "s.jsonl", [0, 1]))})
        assert too_small.status_code == 400 and "at least 10" in too_small.json()["detail"]
        started = client.post("/api/benchmark", json={"id": "mini"})
        assert started.status_code == 202 and started.json()["running"] is True
        busy = client.post("/api/benchmark", json={"id": "mini"})
        assert busy.status_code == 409 and busy.json()["detail"] == "a benchmark is already running"
        deadline = time.monotonic() + 3
        while client.get("/api/status").json()["benchmark"]["phase"] != "sequential" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert client.get("/api/status").json()["benchmark"]["phase"] == "sequential"
        release.set()
        deadline = time.monotonic() + 3
        while client.get("/api/status").json()["benchmark"]["running"] and time.monotonic() < deadline:
            time.sleep(0.01)
        assert client.get("/api/status").json()["benchmark"]["phase"] == "done"
        assert client.get("/api/benchmarks").json() == {"mini": [{"model": "mini", "set": "bundled", "p50_ms": 1.0}]}

        release.clear()
        client.post("/api/benchmark", json={"id": "mini"})
        assert client.delete("/api/benchmark").status_code == 200
        deadline = time.monotonic() + 3
        while client.get("/api/status").json()["benchmark"]["running"] and time.monotonic() < deadline:
            time.sleep(0.01)
        assert client.get("/api/status").json()["benchmark"]["phase"] == "cancelled"
