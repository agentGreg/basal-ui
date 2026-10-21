"""Custom calibration: basal-calibrate against a temporary uncalibrated child, the files and their report, selecting
one for the model (and back to the shipped one), cancelling, and the inputs refused."""
from tests.e2e.support import EXAMPLES

LABELLED = str(EXAMPLES / "questions.jsonl")  # simple basal-run items with gold: basal-calibrate takes them


def test_fit_inspect_select_and_apply_a_calibration(ui, text_model):
    model_id = text_model["id"]
    ui.load(model_id)
    before = ui.get("/api/calibrations", params={"id": model_id})
    assert before["shipped"] and before["shipped"]["file"].endswith(".json")
    committed = ui.status()["memory"]["committed_bytes"]
    job = ui.run_job("/api/calibrate", {"id": model_id, "input": LABELLED, "targets": ["0.05"]}, "calibrate")
    result = job["result"]
    assert result["fitted_on"]["decisions"] == 20 and result["fitted_on"]["model"] == text_model["repo"]
    assert "temperature" in result["report"] and "max_error" in result["report"]
    assert ui.status()["memory"]["committed_bytes"] == committed  # the temporary child is gone
    sources = {s["id"]: s["state"] for s in ui.get("/api/logs")["sources"]}
    assert sources["job:calibrate"] == "done"

    listing = ui.get("/api/calibrations", params={"id": model_id})
    assert listing["files"][0]["file"] == result["file"] and len(listing["files"]) == len(before["files"]) + 1
    report = ui.get("/api/calibrations/report", params={"id": model_id, "file": result["file"]})["report"]
    assert report.strip() == result["report"].strip()

    try:
        assert ui.post("/api/calibrations/select", {"id": model_id, "file": result["file"]})["selected"] == result["file"]
        assert ui.model(model_id)["restart_required"] is True
        ui.post("/api/restart", {"id": model_id})
        m = ui.wait_model(model_id)
        assert m["state"] == "ready" and m["restart_required"] is False
        assert m["command"][m["command"].index("--calibration") + 1] == result["file"]
        assert not m["entry"].get("calibration_warning"), m["entry"].get("calibration_warning")
        assert m["entry"]["temperature_per_prim"] != {}
    finally:
        ui.post("/api/calibrations/select", {"id": model_id, "file": None})
        ui.post("/api/restart", {"id": model_id})
        assert "--calibration" not in ui.wait_model(model_id)["command"]


def test_a_calibration_can_be_cancelled_and_bad_inputs_are_refused(ui, text_model, tmp_path):
    model_id = text_model["id"]
    ui.post("/api/calibrate", {"id": model_id, "input": LABELLED})
    ui.delete("/api/calibrate")
    assert ui.wait_job("calibrate")["phase"] in ("cancelled", "error")  # stopped while starting or mid-fit
    assert "cannot read" in ui.detail("POST", "/api/calibrate", {"id": model_id, "input": str(tmp_path / "no.jsonl")},
                                      400)
    assert "targets" in ui.detail("POST", "/api/calibrate", {"id": model_id, "input": LABELLED, "targets": [3]}, 400)
    assert ui.detail("POST", "/api/calibrations/select", {"id": model_id, "file": str(tmp_path / "x.json")}, 400)
