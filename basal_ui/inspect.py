"""The Inspect view: basal-viz's Explainer for one downloaded model, loaded into basal-ui's own process (the page reads
every layer of the PyTorch model) and served by basal-viz's own app mounted at /viz (basal/viz.py make_app)."""
import asyncio
import gc
from types import SimpleNamespace

from basal.viz import Explainer

from .jobs import Cancelled

NOT_INSPECTABLE = ("basal-viz reads the bf16 PyTorch weights of basal-1.0, basal-1.5 and basal-2.0; download the bf16 "
                   "variant")


def inspectable(variant) -> str | None:
    """None when basal-viz can read the variant, else why not (MLX / GGUF / FP8 / NVFP4 ports, basal-omni)."""
    if variant.port != "bf16" or variant.experimental:
        return NOT_INSPECTABLE
    return None


def device(hw) -> str:
    return "mps" if hw.platform == "apple" else "cuda" if hw.platform == "cuda" else "cpu"


class Inspector:
    """At most one Explainer; its basal-serve batching loop (Server.worker) runs on basal-ui's event loop, which the
    page's /viz/api/explain awaits on."""

    def __init__(self):
        self.ex, self.model, self.state_name, self.error = None, None, "idle", None
        self.loop, self.worker, self.gen = None, None, 0

    def bind(self, loop):
        """The event loop of basal-ui's HTTP server (captured at start-up)."""
        self.loop = loop

    def explainer(self):
        if self.ex is None or self.state_name != "ready":
            raise RuntimeError("no model is being inspected: start one in basal-ui's Inspect view")
        return self.ex

    def start(self, job, variant, local_dir, hw, calibration=None):
        """The inspect job: load the Explainer (minutes for a large model), then run its worker on the loop. A stop()
        meanwhile discards the loaded model (the job ends cancelled)."""
        self.stop()
        gen = self.gen
        self.model, self.state_name, self.error = variant.id, "loading", None
        job.progress("loading model")
        try:
            ex = Explainer(SimpleNamespace(model=str(local_dir), dtype="bfloat16", device=device(hw), orders=None,
                                           calibration=calibration))
        except BaseException as exc:
            if gen == self.gen:
                self.state_name, self.error = "error", str(exc)
            raise
        if gen != self.gen:  # stopped while loading
            del ex
            gc.collect()
            raise Cancelled()
        ex.name = ex.info["model"] = variant.id  # a cache snapshot directory would show as its commit hash
        self.ex = ex
        self.worker = asyncio.run_coroutine_threadsafe(ex.srv.worker(), self.loop)
        self.state_name = "ready"
        return {"model": variant.id, "info": ex.info}

    def stop(self):
        self.gen += 1
        worker, self.worker = self.worker, None
        if worker is not None:
            worker.cancel()
        self.ex, self.model, self.state_name, self.error = None, None, "idle", None
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            elif torch.backends.mps.is_available():
                torch.mps.empty_cache()
        except ImportError:
            pass

    def state(self) -> dict:
        return {"model": self.model, "state": self.state_name, "error": self.error}
