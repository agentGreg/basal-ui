"""Local hardware detection for the UI."""
from dataclasses import asdict, dataclass, replace
from functools import lru_cache
import importlib.util
import os
import platform as host_platform
import re
import subprocess
import sys


@dataclass(frozen=True)
class Hardware:
    platform: str
    device_name: str
    memory_bytes: int
    system_ram_bytes: int | None
    cuda_capability: tuple[int, int] | None
    engines: frozenset[str]
    available_bytes: int | None = None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["cuda_capability"] = list(self.cuda_capability) if self.cuda_capability else None
        data["engines"] = sorted(self.engines)
        return data


def _available_bytes() -> int | None:
    try:
        if sys.platform == "darwin":
            stats = subprocess.check_output(["vm_stat"], text=True)
            page_size = int(re.search(r"page size of (\d+) bytes", stats).group(1))
            pages = {
                name: int(re.search(rf"^{name}:\s+(\d+)\.", stats, re.MULTILINE).group(1))
                for name in ("Pages free", "Pages inactive", "Pages speculative")
            }
            return sum(pages.values()) * page_size
        if sys.platform.startswith("linux"):
            with open("/proc/meminfo") as meminfo:
                available = re.search(r"^MemAvailable:\s+(\d+) kB$", meminfo.read(), re.MULTILINE)
            return int(available.group(1)) * 1024
    except Exception:
        return None
    return None


def _ram() -> int | None:
    try:
        if sys.platform == "darwin":
            return int(subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True).strip())
        return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, AttributeError):
        return None


def _tpu() -> bool:
    try:
        from basal.tpu import tpu_available
        return bool(tpu_available())
    except Exception:  # noqa: BLE001 — a broken JAX counts as no TPU, as for basal-serve's default mode
        return False


@lru_cache(maxsize=1)
def _static() -> Hardware:
    ram = _ram()
    engines: set[str] = set()
    if all(importlib.util.find_spec(name) is not None for name in ("PIL", "soundfile", "scipy", "torchvision")):
        engines.add("media")
    if _tpu():
        engines.add("tpu")
    try:
        import torch
        if torch.cuda.is_available():
            props = torch.cuda.get_device_properties(0)
            capability = torch.cuda.get_device_capability(0)
            engines.add("cuda")
            if getattr(torch.version, "hip", None):  # ROCm PyTorch; Strix Halo = gfx1151 (Radeon 8060S)
                engines.add("rocm")
                arch = getattr(props, "gcnArchName", None)
                if ("gfx1151" in arch) if arch else ("8060S" in props.name):
                    engines.add("strix")
            if importlib.util.find_spec("vllm"):
                engines.add("vllm")
            if importlib.util.find_spec("sglang"):
                engines.add("sglang")
            if importlib.util.find_spec("llama_cpp"):
                engines.add("gguf")
            return Hardware("cuda", props.name, props.total_memory, ram, capability, frozenset(engines))
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            engines.add("mps")
    except (ImportError, RuntimeError):
        pass
    if sys.platform == "darwin" and host_platform.machine() == "arm64":
        try:
            import mlx.core as mx
            info = mx.device_info()
            name = info.get("device_name", "Apple Silicon")
            budget = int(info.get("max_recommended_working_set_size", 0))
        except Exception:
            name, budget = "Apple Silicon", 0
        if importlib.util.find_spec("mlx") and importlib.util.find_spec("mlx_lm"):
            engines.add("mlx")
        if importlib.util.find_spec("llama_cpp"):
            engines.add("gguf")
        return Hardware("apple", name, budget or int((ram or 0) * .7), ram, None, frozenset(engines))
    if importlib.util.find_spec("llama_cpp"):
        engines.add("gguf")
    return Hardware("cpu", host_platform.processor() or host_platform.machine(), int((ram or 0) * .6), ram, None,
                    frozenset(engines))


def detect() -> Hardware:
    """The machine (detected once per process) with the memory available right now."""
    static = _static()
    if static.platform != "cuda":
        return replace(static, available_bytes=_available_bytes())
    try:
        import torch
        return replace(static, available_bytes=int(torch.cuda.mem_get_info(0)[0]))
    except Exception:
        return static
