"""The Inspect view: basal-viz's Explainer for one downloaded model, loaded into basal-ui's own process (the page reads
every layer of the PyTorch model) and served at /viz by inspect_app: basal-viz's routes with basal-ui's copy of its
page (static/viz.html: relative API paths for the /viz mount, requests posted from the Playground)."""

import asyncio
import gc
import json
import threading
from pathlib import Path
from types import SimpleNamespace

from basal.facts import inject as inject_facts
from basal.server import http_status
from basal.viz import Explainer, compare_served

from .engine import feature
from .jobs import Cancelled

VIZ_PAGE = Path(__file__).parent / "static" / "viz.html"


def explained(body):
    """The request basal-viz explains: with "facts": "auto" its state carries the computed facts, as basal-serve reads
    it (basal-viz 2.4.1 explains the state without them)."""
    if not isinstance(body, dict) or body.get("facts") != "auto":
        return body
    state = body.get("state")
    return {
        **body,
        "state": inject_facts(state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)),
        "facts": "off",
    }


def inspect_app(explainer_fn, examples_fn, lifespan=None):
    """basal-viz's page and API (/, /api/info, /api/examples, /api/explain, as basal-viz's main builds them).
    explainer_fn() is the Explainer in use; RuntimeError when there is none yet: 503 on info and explain."""
    from starlette.applications import Starlette
    from starlette.responses import FileResponse, JSONResponse
    from starlette.routing import Route

    def current():
        try:
            return explainer_fn(), None
        except RuntimeError as e:
            return None, JSONResponse({"error": str(e)}, status_code=503)

    async def info(_):
        ex, unavailable = current()
        return unavailable or JSONResponse(ex.info)

    async def explain(request):
        ex, unavailable = current()
        if unavailable:
            return unavailable
        try:
            body = await request.json()
            res = await asyncio.get_running_loop().run_in_executor(None, ex.explain, explained(body))
            return JSONResponse(compare_served(res, await ex.srv.decide(body)))
        except Exception as e:  # noqa: BLE001
            return JSONResponse({"error": str(e)}, status_code=http_status(e))

    return Starlette(
        routes=[
            Route("/", lambda _: FileResponse(VIZ_PAGE)),
            Route("/api/info", info),
            Route("/api/examples", lambda _: JSONResponse(examples_fn())),
            Route("/api/explain", explain, methods=["POST"]),
        ],
        lifespan=lifespan,
    )


NOT_INSPECTABLE = (
    "basal-viz reads the bf16 PyTorch weights of basal-1.0, basal-1.5, basal-2.0 and basal-omni; "
    "download the bf16 variant"
)
OMNI_NEEDS_ENGINE = (
    "Gemma 4 basal-omni needs an engine whose basal-viz reads it (proposed upstream: "
    "rkinas/basal#75); this engine's basal-viz reads basal-1.0, basal-1.5 and basal-2.0"
)


def inspectable(variant) -> str | None:
    """None when basal-viz can read the bf16 PyTorch variant, else why not (other ports, the recurrent basal-omni-pico,
    Gemma 4 basal-omni on an engine whose basal-viz does not read it)."""
    if variant.port != "bf16" or variant.recurrent:
        return NOT_INSPECTABLE
    if variant.experimental and not variant.family.startswith("basal-omni"):
        return NOT_INSPECTABLE
    if variant.experimental and not feature("viz_gemma4"):
        return OMNI_NEEDS_ENGINE
    return None


def device(hw) -> str:
    return "mps" if hw.platform == "apple" else "cuda" if hw.platform == "cuda" else "cpu"


class Inspector:
    """At most one Explainer; its basal-serve batching loop (Server.worker) runs on basal-ui's event loop, which the
    page's /viz/api/explain awaits on."""

    def __init__(self):
        self.ex, self.model, self.state_name, self.error, self.calibration = None, None, "idle", None, None
        self.loop, self.worker, self.gen = None, None, 0
        self.lock = threading.RLock()
        self.worker_done = threading.Event()
        self.worker_done.set()

    def bind(self, loop):
        """The event loop of basal-ui's HTTP server (captured at start-up)."""
        self.loop = loop

    def explainer(self):
        with self.lock:
            if self.ex is None or self.state_name != "ready":
                raise RuntimeError("no model is being inspected: start one in basal-ui's Inspect view")
            return self.ex

    def start(self, job, variant, local_dir, hw, calibration=None, orders=None, on_exit=None):
        """Load the Explainer and run its batching loop on the UI event loop."""
        self.stop()
        with self.lock:
            if job.cancel.is_set():
                raise Cancelled()
            gen = self.gen
            self.model, self.state_name, self.error = variant.id, "loading", None
            self.calibration = calibration
        job.progress("loading model")
        if job.cancel.is_set():
            raise Cancelled()
        try:
            ex = Explainer(
                SimpleNamespace(
                    model=str(local_dir), dtype="bfloat16", device=device(hw), orders=orders, calibration=calibration
                )
            )
        except BaseException as exc:
            with self.lock:
                if gen == self.gen:
                    self.state_name, self.error = "error", str(exc)
            raise
        with self.lock:
            if gen != self.gen or job.cancel.is_set():
                stale = True
            else:
                stale = False
                ex.name = ex.info["model"] = variant.id  # a cache snapshot directory would show its commit hash
                self.ex = ex

                async def run_worker():
                    await ex.srv.worker()

                def finished(task):
                    if on_exit is not None:
                        on_exit()
                    self.worker_done.set()

                task = {}
                scheduled = threading.Event()

                def create_worker():
                    worker = self.loop.create_task(run_worker())
                    worker.add_done_callback(finished)
                    task["worker"] = worker
                    scheduled.set()

                self.worker_done.clear()
                self.loop.call_soon_threadsafe(create_worker)
                scheduled.wait()
                self.worker = task["worker"]
                self.state_name = "ready"
        if stale:
            del ex
            gc.collect()
            self._free_engine_memory()
            raise Cancelled()
        return {"model": variant.id, "info": ex.info}

    @staticmethod
    def _free_engine_memory():
        try:
            from .hardware import free_engine_memory

            free_engine_memory()
        except ImportError:
            pass

    def stop(self):
        with self.lock:
            self.gen += 1
            was_loading = self.state_name == "loading"
            worker, self.worker = self.worker, None
            self.ex, self.model, self.state_name, self.error, self.calibration = None, None, "idle", None, None
            if worker is not None:
                self.loop.call_soon_threadsafe(worker.cancel)
        exited = worker is None or self.worker_done.wait(timeout=5)
        if exited and not was_loading:
            gc.collect()
            self._free_engine_memory()

    def state(self) -> dict:
        """What the Playground's handoff compares with the served model: the engine, option orders and calibration."""
        with self.lock:
            out = {"model": self.model, "state": self.state_name, "error": self.error}
            if self.ex is not None and self.state_name == "ready":
                info = self.ex.info
                out.update(
                    mode=info["mode"],
                    device=info["device"],
                    orders=info["orders"],
                    calibration=self.calibration,
                    temperatures=info["temperatures"],
                    temperature_per_bucket=info["temperature_per_bucket"],
                )
            return out
