"""Hardware detection branches (basal_ui/hardware.py _static) with a fake torch: CUDA, ROCm (Strix Halo), Apple, CPU."""

import sys
from types import SimpleNamespace

import pytest

from basal_ui import hardware


@pytest.fixture
def machine(monkeypatch):
    """Configure the detected machine: torch (None = not installed), installed packages, sys.platform, CPU arch."""
    hardware._static.cache_clear()
    monkeypatch.setattr(hardware, "_ram", lambda: 64 * 1024**3)
    monkeypatch.setattr(hardware, "_tpu", lambda: False)
    monkeypatch.setitem(sys.modules, "basal.engine", SimpleNamespace(gemma_fast_kernels=lambda: None))
    monkeypatch.setitem(sys.modules, "mlx.core", None)  # no MLX device details: the Apple fallback name

    def configure(torch=None, packages=(), platform="linux", arch="x86_64"):
        monkeypatch.setitem(sys.modules, "torch", torch)
        installed = set(packages)
        monkeypatch.setattr(hardware.importlib.util, "find_spec", lambda name: object() if name in installed else None)
        monkeypatch.setattr(hardware, "sys", SimpleNamespace(platform=platform))
        monkeypatch.setattr(hardware.host_platform, "machine", lambda: arch)
        hardware._static.cache_clear()
        return hardware._static()

    yield configure
    hardware._static.cache_clear()


def fake_torch(name="NVIDIA H100", memory=80 * 1024**3, capability=(9, 0), hip=None, arch=None, cuda=True):
    props = SimpleNamespace(name=name, total_memory=memory, gcnArchName=arch)
    return SimpleNamespace(
        cuda=SimpleNamespace(
            is_available=lambda: cuda,
            get_device_properties=lambda index: props,
            get_device_capability=lambda index: capability,
        ),
        version=SimpleNamespace(hip=hip),
        backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: False)),
    )


def test_an_nvidia_gpu_is_cuda_with_its_installed_engines(machine):
    hw = machine(fake_torch(), packages={"vllm", "fla"})
    assert (hw.platform, hw.device_name, hw.memory_bytes, hw.cuda_capability) == (
        "cuda",
        "NVIDIA H100",
        80 * 1024**3,
        (9, 0),
    )
    assert hw.engines == {"cuda", "vllm", "fla"}


def test_rocm_on_strix_halo_is_cuda_with_rocm_and_strix(machine):
    torch = fake_torch("AMD Radeon 8060S", 96 * 1024**3, (11, 5), hip="6.4", arch="gfx1151:sramecc+:xnack-")
    hw = machine(torch)
    assert hw.platform == "cuda" and {"cuda", "rocm", "strix"} <= hw.engines
    other_amd = machine(fake_torch("AMD Instinct MI300X", 192 * 1024**3, (9, 4), hip="6.4", arch="gfx942"))
    assert "rocm" in other_amd.engines and "strix" not in other_amd.engines


def test_apple_silicon_without_torch(machine):
    hw = machine(None, packages={"mlx", "mlx_lm"}, platform="darwin", arch="arm64")
    assert (hw.platform, hw.device_name) == ("apple", "Apple Silicon")
    assert hw.memory_bytes == int(64 * 1024**3 * 0.7) and hw.engines == {"mlx"}


def test_anything_else_is_cpu(machine):
    hw = machine(fake_torch(cuda=False), packages={"llama_cpp"})
    assert (hw.platform, hw.memory_bytes, hw.engines) == ("cpu", int(64 * 1024**3 * 0.6), {"gguf"})
    assert machine(None, platform="darwin", arch="x86_64").platform == "cpu"  # an Intel Mac
