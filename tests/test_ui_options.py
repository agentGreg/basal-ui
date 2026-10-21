import time

import pytest
from fastapi.testclient import TestClient

from basal.ui import catalog, options, settings
from basal.ui.app import Fleet, create_app
from basal.ui.catalog import Variant
from basal.ui.hardware import Hardware
from basal.ui.history import History
from basal.ui.stats import Stats

CPU = Hardware("cpu", "cpu", 1024**4, None, None, frozenset())
CUDA = Hardware("cuda", "NVIDIA H100", 80 * 1024**3, None, (9, 0), frozenset({"cuda", "vllm", "sglang"}))
BF16 = Variant("mini", "Remek/basal-2.0-mini", "basal-2.0", "mini", "bf16", "bf16", None, 100, False)
GGUF = Variant("mini-gguf", "Remek/basal-2.0-mini-GGUF", "basal-2.0", "mini", "GGUF", "GGUF Q8_0", "m.gguf", 100, False)


@pytest.fixture(autouse=True)
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))


def wait_for(condition, timeout=3):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert condition()


def available(variant, hw):
    return {m["mode"] for m in options.available_modes(variant, hw, catalog.assess(variant, hw)) if m["available"]}


def test_defaults_emit_nothing_and_every_non_default_its_flag():
    assert options.args({}) == [] and options.args(dict(options.DEFAULTS)) == []
    flags = options.args({"mode": "fast", "quant": "fp8", "attn": "fa3", "deterministic": True, "orders": "2le4",
                          "auto_band": [0.1, 0.9], "soam": False, "max_batch": 16, "wait_ms": 2, "prefix_cache": True,
                          "state_cache_gb": 1.5, "scheduler": "on", "long_lane": True, "long_tokens": 4096,
                          "context_overflow": "refuse", "truncate_head": 0.5, "max_len": 8192,
                          "vllm_preset": "deterministic", "ollama_model": "basal:mini", "device": "cpu",
                          "think": True, "think_tokens": 512, "early_exit": "0.99"})
    assert flags == ["--quant", "fp8", "--attn", "fa3", "--deterministic", "--orders", "2le4",
                     "--auto-band", "0.1", "0.9", "--soam", "off", "--max-batch", "16", "--wait-ms", "2",
                     "--state-cache-gb", "1.5", "--prefix-cache", "on", "--scheduler", "on", "--long-lane", "on",
                     "--long-tokens", "4096", "--context-overflow", "refuse", "--truncate-head", "0.5",
                     "--max-len", "8192", "--vllm-preset", "deterministic", "--ollama-model", "basal:mini",
                     "--device", "cpu", "--think", "auto", "--think-tokens", "512", "--early-exit", "0.99"]


@pytest.mark.parametrize("bad, message", [
    ({"colour": 1}, "unknown load option: colour"), ({"orders": "3"}, "orders"), ({"max_batch": 0}, "max_batch"),
    ({"gpu_memory": 0}, "gpu_memory"), ({"truncate_head": 1.5}, "truncate_head"), ({"early_exit": "0.9"}, "early_exit"),
    ({"deterministic": "yes"}, "deterministic"), ({"max_len": 100}, "max_len"), ({"mode": "warp"}, "mode"),
    ({"mode": "mlx", "quant": "fp8"}, "--quant fp8 is not supported with --mode mlx"),
])
def test_invalid_options_name_the_option(bad, message):
    with pytest.raises(ValueError, match=message):
        options.validate(bad)


def test_available_modes_follow_the_hardware_and_the_variant():
    assert available(BF16, CPU) == {"eager", "ollama", "llamacpp"}
    gguf_cpu = Hardware("cpu", "cpu", 1024**4, None, None, frozenset({"gguf"}))
    assert available(GGUF, gguf_cpu) == {"gguf", "ollama", "llamacpp"}
    cuda = available(BF16, CUDA)
    assert {"fast", "fast-nocompile", "fp8", "eager", "vllm", "sglang"} <= cuda
    assert not cuda & {"tpu", "strix", "mlx", "mps", "nvfp4", "fast-exit"}
    reasons = {m["mode"]: m["reason"] for m in options.available_modes(BF16, CUDA, catalog.assess(BF16, CUDA))}
    assert reasons["fast-exit"] == "exit heads ship with basal-1.0" and "TPU" in reasons["tpu"]
    assert [m["mode"] for m in options.available_modes(BF16, CUDA, catalog.assess(BF16, CUDA)) if m["default"]] == ["fast"]


def test_thinking_needs_basal_2_0_4_5b_with_vllm_and_early_exit_needs_fast_exit():
    big = Variant("big", "Remek/basal-2.0-4.5B", "basal-2.0", "4.5B", "bf16", "bf16", None, 100, False)
    assert options.args({"think": True, "think_tokens": 512, "early_exit": "0.99", "mode": "fast-exit"}) == [
        "--think", "auto", "--think-tokens", "512", "--early-exit", "0.99"]
    merged = options.validate({"think": True})
    assert options.think_unavailable(BF16, CUDA) == options.THINK_REASON  # the mini has no thinking model
    assert options.think_unavailable(big, CUDA) is None
    assert "Python of an environment with vLLM" in options.think_unavailable(big, CPU)
    assert options.think_unavailable(big, CPU, {"think_url": "http://gpu:8100"}) is None
    with pytest.raises(ValueError, match="basal-2.0 4.5B"):
        options.check_combination(BF16, CUDA, merged, "fast")
    with pytest.raises(ValueError, match="early_exit needs --mode fast-exit"):
        options.check_combination(big, CUDA, options.validate({"early_exit": "0.99"}), "fast")
    options.check_combination(big, CUDA, options.validate({"early_exit": "0.99", "mode": "fast-exit"}), "fast")


def test_load_options_reach_the_command_are_remembered_and_reused_by_restart(tmp_path, monkeypatch):
    fleet = Fleet(models_dir_fn=lambda: tmp_path, platform_fn=lambda: "cpu", settings_mod=settings,
                  hardware_fn=lambda: CPU, stats=Stats(tmp_path / "s.json"), history=History(tmp_path / "h.jsonl"))
    commands = []

    def start(self, assessment, gen, media=False, cache_dir=None, serve=None):
        commands.append(self._build_command(BF16, tmp_path, assessment, media, 1, settings.serve_settings()))
        with self.lock:
            self.state = "ready"
        settings.remember_loaded(assessment.variant.id, media, self.options)

    monkeypatch.setattr("basal.ui.app.Runner._start", start)
    app = create_app(catalog_fn=lambda **_: ([BF16], "test"), hardware_fn=lambda: CPU, fleet=fleet, stats=fleet.stats,
                     history=fleet.history)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        bad = client.post("/api/load", json={"id": "mini", "options": {"orders": "7"}})
        assert bad.status_code == 400 and "orders" in bad.json()["detail"]
        unavailable = client.post("/api/load", json={"id": "mini", "options": {"mode": "mlx"}})
        assert unavailable.status_code == 400 and unavailable.json()["detail"].startswith("--mode mlx:")
        chosen = {"deterministic": True, "orders": "2", "context_overflow": "refuse"}
        assert client.post("/api/load", json={"id": "mini", "options": chosen}).status_code == 202
        wait_for(lambda: len(commands) == 1)
        assert commands[-1][-5:] == ["--deterministic", "--orders", "2", "--context-overflow", "refuse"]
        assert client.get("/api/status").json()["models"][0]["options"] == chosen
        assert settings.last_models() == [{"id": "mini", "media": False, "options": chosen}]
        assert client.post("/api/restart", json={"id": "mini"}).status_code == 202
        wait_for(lambda: len(commands) == 2)
        assert commands[-1][-5:] == ["--deterministic", "--orders", "2", "--context-overflow", "refuse"]
        client.post("/api/restart", json={"id": "mini", "options": {}})
        wait_for(lambda: len(commands) == 3)
        assert "--deterministic" not in commands[-1]
        saved = client.put("/api/models/options", json={"id": "mini", "options": {"max_batch": 8}}).json()
        assert saved["options"]["max_batch"] == 8 and saved["saved"] is True
        assert settings.load_options("mini") == {"max_batch": 8}
        listed = client.get("/api/models/options", params={"id": "mini"}).json()
        assert listed["defaults"]["max_batch"] == 64 and {m["mode"] for m in listed["modes"]} >= {"eager", "tpu"}
