import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from basal import run
from basal.ui import settings
from basal.ui.app import Fleet, Runner, create_app
from basal.ui.hardware import Hardware
from basal.ui.history import History
from basal.ui.stats import Stats

HW = Hardware("cpu", "cpu", 1024**4, None, None, frozenset())


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    seen = []

    def handler(request):
        seen.append(request.headers.get("authorization"))
        return httpx.Response(200, json={"answers": {"q": {"type": "choice", "probabilities": {"0": 0.8, "1": 0.2},
                                                           "confidence": 0.8}}, "usage": {"latency_ms": 1.5}})

    real = httpx.AsyncClient
    monkeypatch.setattr(run.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    fleet = Fleet(models_dir_fn=lambda: tmp_path, platform_fn=lambda: "cpu", settings_mod=settings,
                  hardware_fn=lambda: HW, stats=Stats(tmp_path / "s.json"), history=History(tmp_path / "h.jsonl"))
    runner = Runner(internal_secret="secret")
    runner.state, runner.model, runner.base_url = "ready", "mini", "http://child"
    runner.models_entry = {"early_exit": []}
    fleet.runners["mini"] = runner
    app = create_app(catalog_fn=lambda **_: ([], "test"), hardware_fn=lambda: HW, fleet=fleet, stats=fleet.stats,
                     history=fleet.history)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.seen = seen
        yield c


def test_batch_answers_every_line_and_serves_the_rows_and_the_file(client, tmp_path):
    source = tmp_path / "items.jsonl"
    source.write_text("\n".join(json.dumps({"id": f"i{n}", "state": "s", "question": "q?", "options": ["a", "b"],
                                            "gold": g}) for n, g in enumerate([0, 1, 0])) + "\n")
    assert client.get("/api/batch/rows").status_code == 404
    started = client.post("/api/batch", json={"id": "mini", "input": str(source), "concurrency": 2})
    assert started.status_code == 202
    deadline = time.monotonic() + 5
    while client.get("/api/status").json()["jobs"]["batch"]["running"] and time.monotonic() < deadline:
        time.sleep(0.01)
    job = client.get("/api/status").json()["jobs"]["batch"]
    assert job["phase"] == "done" and (job["done"], job["total"]) == (3, 3)
    result = job["result"]
    assert (result["items"], result["errors"], result["accuracy"]) == (3, 0, round(2 / 3, 4))
    assert result["output"] == str(tmp_path / "items.answers.jsonl")
    assert client.seen == ["Bearer secret"] * 3  # straight to the child, with the internal key
    rows = client.get("/api/batch/rows", params={"limit": 2}).json()
    assert rows["total"] == 3 and [r["id"] for r in rows["rows"]] == ["i0", "i1"]
    assert [r["correct"] for r in client.get("/api/batch/rows", params={"offset": 2}).json()["rows"]] == [True]
    download = client.get("/api/batch/download")
    assert download.status_code == 200 and len(download.text.splitlines()) == 3
    assert client.get("/api/stats").json()["models"] == {}  # not counted in the statistics


@pytest.mark.parametrize("body, status, message", [
    ({"id": "mini", "input": "/nonexistent/x.jsonl"}, 400, "cannot read"),
    ({"id": "mini", "input": "BAD"}, 400, "line 1"),
    ({"id": "other", "input": "GOOD"}, 404, "not loaded"),
    ({"id": "mini", "input": "GOOD", "early_exit": "0.99"}, 400, "no early exits"),
    ({"id": "mini", "input": "GOOD", "output": "/nonexistent/dir/out.jsonl"}, 400, "cannot write"),
])
def test_batch_refuses_bad_input(client, tmp_path, body, status, message):
    good, bad = tmp_path / "good.jsonl", tmp_path / "bad.jsonl"
    good.write_text('{"state": "s", "question": "q?", "options": ["a", "b"]}\n')
    bad.write_text("not json\n")
    body = {**body, "input": {"GOOD": str(good), "BAD": str(bad)}.get(body["input"], body["input"])}
    response = client.post("/api/batch", json=body)
    assert response.status_code == status and message in response.json()["detail"]
