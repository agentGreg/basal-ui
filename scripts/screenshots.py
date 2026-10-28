"""Refresh the README's screenshots and tour GIF (docs/images/) from a real basal-ui with a real model.

Starts basal-ui on a fresh settings directory, loads Remek/basal-2.0-mini (it must be on disk: HF_HUB_OFFLINE=1),
captures five views in Chromium (1280x800 at 2x, the default light theme), then records a tour (Models, load the model
in Model manager, ask a question in the Playground) and converts it to a GIF with ffmpeg. `make screenshots`; needs
the e2e extra, `playwright install chromium` and ffmpeg on PATH. Exits non-zero on any failure.
"""

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "images"
PORT = 7877
URL = f"http://127.0.0.1:{PORT}"
MODEL = "Remek/basal-2.0-mini"
LOAD_TIMEOUT_S = 600
GIF_LIMIT = 3 * 2**20
# the Playground request of the e2e gate (tests/e2e/support.py CHOICE)
CHOICE = {
    "state": "Klient pisze, że karta została zablokowana po trzech błędnych PIN-ach i prosi o odblokowanie.",
    "questions": {
        "dept": {
            "type": "choice",
            "instructions": "Do którego działu skierować zgłoszenie?",
            "criteria": {"cards": "karty płatnicze", "loans": "kredyty", "online": "bankowość internetowa"},
        }
    },
}
SHOTS = (
    ("overview", "overview"),
    ("models", "models"),
    ("playground", "playground"),
    ("manager", "model-manager"),
    ("api", "api-server"),
)
READY = "id => (statusCache?.models || []).some(m => m.id === id && m.state === 'ready')"


def api(method, path, **kwargs):
    response = httpx.request(method, URL + path, timeout=30, **kwargs)
    response.raise_for_status()
    return response.json()


def wait_ready(model_id):
    deadline = time.monotonic() + LOAD_TIMEOUT_S
    while time.monotonic() < deadline:
        m = next((m for m in api("GET", "/api/status")["models"] if m["id"] == model_id), None)
        if m and m["state"] == "ready":
            return
        if m and m["state"] == "error":
            raise SystemExit(f"{model_id} failed to load: {m.get('error')}")
        time.sleep(1)
    raise SystemExit(f"{model_id} not ready after {LOAD_TIMEOUT_S} s")


def start(tmp):
    env = {**os.environ, "BASAL_UI_CONFIG": str(Path(tmp) / "settings.json"), "HF_HUB_OFFLINE": "1"}
    proc = subprocess.Popen([sys.executable, "-m", "basal_ui", "--port", str(PORT)], env=env, start_new_session=True)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise SystemExit(f"basal-ui exited ({proc.returncode})")
        try:
            api("GET", "/api/status")
            return proc
        except httpx.HTTPError:
            time.sleep(0.5)
    raise SystemExit("basal-ui did not answer /api/status within 60 s")


def stop(proc):
    os.killpg(proc.pid, signal.SIGTERM)  # basal-ui and the basal-serve children it started
    try:
        proc.wait(30)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()


def open_page(context):
    page = context.new_page()
    page.set_default_timeout(LOAD_TIMEOUT_S * 1000)
    page.goto(URL + "/#overview")
    page.wait_for_function("() => typeof statusCache !== 'undefined' && statusCache && modelData.length > 0")
    return page


def show(page, name):
    page.evaluate("name => { showView(name, true); window.scrollTo(0, 0); }", name)  # showView keeps the scroll
    page.wait_for_timeout(800)  # views render their lists after their own requests


def ask(page):
    page.select_option("#playgroundModel", MODEL)
    page.evaluate("r => setRequest(r)", CHOICE)
    page.click("#sendButton")
    page.wait_for_selector("#results .chips.safety")


def screenshots(browser):
    context = browser.new_context(viewport={"width": 1280, "height": 800}, device_scale_factor=2)
    page = open_page(context)
    page.wait_for_function(READY, arg=MODEL)
    for view, name in SHOTS:
        show(page, view)
        if view == "playground":
            ask(page)
            page.wait_for_timeout(500)
        page.screenshot(path=str(OUT / f"{name}.png"))
        print(f"docs/images/{name}.png")
    context.close()


def tour(browser, tmp):
    videos = Path(tmp) / "video"
    context = browser.new_context(
        viewport={"width": 960, "height": 600},
        record_video_dir=str(videos),
        record_video_size={"width": 960, "height": 600},
    )
    page = open_page(context)
    page.wait_for_timeout(1500)
    show(page, "models")
    page.wait_for_timeout(1500)
    show(page, "manager")  # the Load buttons of the models on disk
    load = page.locator(f'section.view[data-page="manager"] button.load-model.primary[data-id="{MODEL}"]').first
    load.scroll_into_view_if_needed()
    page.wait_for_timeout(1000)
    load.click()
    page.wait_for_function(READY, arg=MODEL)
    page.wait_for_timeout(1000)
    show(page, "playground")
    ask(page)
    page.locator("#results").scroll_into_view_if_needed()
    page.wait_for_timeout(2000)
    video = page.video.path()
    context.close()  # writes the video
    return Path(video)


def gif(webm):
    out = OUT / "tour.gif"
    for fps, width in ((10, 960), (8, 800)):
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(webm),
                "-vf",
                f"fps={fps},scale={width}:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse",
                str(out),
            ],
            check=True,
        )
        if out.stat().st_size < GIF_LIMIT:
            print(f"docs/images/tour.gif ({out.stat().st_size / 2**20:.1f} MiB, {fps} fps, {width} px)")
            return
    raise SystemExit(f"tour.gif is {out.stat().st_size / 2**20:.1f} MiB at 8 fps / 800 px; shorten the tour")


def main():
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg is not on PATH")
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        proc = start(tmp)
        try:
            api("POST", "/api/load", json={"id": MODEL})
            wait_ready(MODEL)
            api("PUT", "/api/serve", json={"enabled": True, "port": 8878, "require_key": True})
            api("POST", "/api/serve/restart")
            with sync_playwright() as p:
                browser = p.chromium.launch()
                try:
                    screenshots(browser)
                    api("POST", "/api/unload", json={"id": MODEL})
                    deadline = time.monotonic() + 60
                    while any(m["id"] == MODEL for m in api("GET", "/api/status")["models"]):
                        if time.monotonic() > deadline:
                            raise SystemExit(f"{MODEL} did not unload within 60 s")
                        time.sleep(0.5)
                    webm = tour(browser, tmp)
                finally:
                    browser.close()
            gif(webm)
        finally:
            stop(proc)


if __name__ == "__main__":
    main()
