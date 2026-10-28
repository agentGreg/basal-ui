"""Local backend inference and navigation on an isolated UI process."""

import time

import pytest


def test_local_backend_inference_and_browser(ui):
    inventory = ui.get("/api/backends")
    candidates = [
        (b, m) for b in inventory["backends"] if b["status"] == "installed" for m in b["models"] if m["downloaded"]
    ]
    if not candidates:
        pytest.skip("No installed backend with a downloaded compatible model")
    backend, model = min(
        candidates, key=lambda pair: (pair[1]["estimated_memory_bytes"] or float("inf"), pair[0]["id"] != "mlx")
    )
    before = ui.get("/api/history"), ui.get("/api/stats"), ui.settings.read_bytes() if ui.settings.exists() else None
    child_pids_before = set(ui.child_pids())
    token = ui.post("/api/backends/tests", {"backend": backend["id"], "model": model["id"], "mode": model["modes"][0]})[
        "id"
    ]
    deadline = time.monotonic() + 1950
    while time.monotonic() < deadline:
        record = next(r for r in ui.get("/api/backends/tests")["tests"] if r["id"] == token)
        job = ui.status()["jobs"].get("backend-test")
        if job and not job["running"]:
            break
        time.sleep(0.5)
    assert record["status"] == "passed", (record, ui.get("/api/logs?source=job:backend-test"))
    assert not (set(ui.child_pids()) - child_pids_before)
    assert before == (
        ui.get("/api/history"),
        ui.get("/api/stats"),
        ui.settings.read_bytes() if ui.settings.exists() else None,
    )

    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(ui.url + "/#backends")
        page.wait_for_selector(".backend-card")
        page.locator("#backendTests").get_by_text("Inference test passed", exact=True).wait_for()
        page.locator("#showUnavailableBackends").check()
        assert page.locator("#backendInventory .backend-card").count() == 7
        page.locator(f'[data-backend-models="{backend["id"]}"]').click()
        playwright.expect(page.locator("#backendModelFilterNotice")).to_be_visible()
        page.locator("#clearBackendModelFilter").click()
        page.keyboard.press("Alt+0")
        playwright.expect(page.locator("#pageTitle")).to_have_text("Backends")
        page.locator(f'[data-backend-test="{backend["id"]}"]').click()
        playwright.expect(page.locator("#backendTestDialog")).to_be_visible()
        page.keyboard.press("Escape")
        page.keyboard.press("Alt+4")
        playwright.expect(page.locator("#pageTitle")).to_have_text("Playground")
        assert not errors, errors
        browser.close()

    # Explicit displacement/restoration with actual children, preserving saved settings.
    ui.load(model["id"])
    active = ui.model(model["id"])
    saved = ui.settings.read_bytes()
    token = ui.post(
        "/api/backends/tests",
        {
            "backend": backend["id"],
            "model": model["id"],
            "mode": model["modes"][0],
            "unload": [{"id": model["id"], "generation": active["generation"]}],
        },
    )["id"]
    ui.wait_job("backend-test")
    record = next(r for r in ui.get("/api/backends/tests")["tests"] if r["id"] == token)
    assert record["status"] == "passed", record
    assert ui.model(model["id"]) is None
    ui.post(f"/api/backends/tests/{token}/restore")
    ui.wait_job("backend-restore")
    restored = ui.model(model["id"])
    assert restored["state"] == "ready" and restored["mode"] == active["mode"]
    assert ui.settings.read_bytes() == saved
    ui.post("/api/unload", {"id": model["id"]})
