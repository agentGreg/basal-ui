"""The Inspect view's backend: basal-viz for a downloaded bf16 model at /viz, explaining a decision, stopping it."""
from tests.e2e.support import CHOICE, catalog


def test_basal_viz_explains_a_decision_of_a_bf16_model_and_stops(ui, bf16_model):
    model_id = bf16_model["id"]
    assert ui.get("/viz/api/info", expect=(503,))["error"].startswith("no model is being inspected")
    committed = ui.status()["memory"]["committed_bytes"]
    job = ui.run_job("/api/inspect", {"id": model_id}, "inspect")
    assert job["result"]["model"] == model_id and ui.get("/api/inspect")["state"] == "ready"
    assert ui.status()["memory"]["committed_bytes"] > committed  # counted in the budget while it runs
    page = ui.http.get("/viz/")
    assert page.status_code == 200 and "api/explain" in page.text
    info = ui.get("/viz/api/info")
    assert info["model"] == model_id and info["layers"] > 0
    explained = ui.call("POST", "/viz/api/explain", json=CHOICE)
    question = explained["questions"][0]
    assert question["qname"] == "dept" and len(question["keys"]) == 3
    assert question["served_max_diff"] is not None and question["served_max_diff"] < 0.05  # viz = basal-serve
    ui.delete("/api/inspect")
    assert ui.get("/api/inspect")["state"] == "idle"
    assert ui.http.get("/viz/api/info").status_code == 503
    assert ui.status()["memory"]["committed_bytes"] == committed


def test_ports_basal_viz_cannot_read_are_refused(ui):
    other = next((m for m in catalog(ui) if m["port"] != "bf16" or m["experimental"]), None)
    assert other is not None
    assert "bf16 PyTorch weights" in ui.detail("POST", "/api/inspect", {"id": other["id"]}, 400)
