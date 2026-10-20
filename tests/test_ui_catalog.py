import math
from pathlib import Path

from basal.ui.catalog import Variant, _label, _media_mode, _variants, assess, delete, download_kwargs, parse_repo, serve_args, suggest
from basal.ui.hardware import Hardware


def hw(platform="cuda", name="H100", cap=(9, 0), engines=frozenset({"cuda"}), memory=80 * 1024**3,
       available=None):
    return Hardware(platform, name, memory, memory, cap, engines, available)


def variant(repo="Remek/basal-2.0-4.5B", port=None, size=None, gguf=None):
    family, tier, parsed_port = parse_repo(repo)
    port = port or parsed_port
    size = size if size is not None else int({"mini": 2, "4.5B": 8, "max": 12, "ultra": 16}.get(tier, 8) * 1024**3)
    return Variant(f"{repo}:{gguf}" if gguf else repo, repo, family, tier, port, _label(port, gguf), gguf, size,
                   family.startswith("basal-omni-"))


def test_hardware_detects_media_engine(monkeypatch):
    from basal.ui import hardware

    find_spec = hardware.importlib.util.find_spec
    packages = {"PIL", "soundfile", "scipy", "torchvision"}
    monkeypatch.setattr(hardware.importlib.util, "find_spec",
                        lambda name: object() if name in packages else find_spec(name))
    hardware._static.cache_clear()
    assert "media" in hardware.detect().engines
    monkeypatch.setattr(hardware.importlib.util, "find_spec",
                        lambda name: None if name == "torchvision" else find_spec(name))
    hardware._static.cache_clear()
    assert "media" not in hardware.detect().engines
    hardware._static.cache_clear()


def test_media_kinds_and_mode_eligibility():
    for tier, kinds in (("mini", ("image", "audio")), ("max", ("image", "audio")), ("ultra", ("image",))):
        (v,) = _variants(f"Remek/basal-omni-1.0-{tier}", {})
        assert v.media == kinds
        (mlx,) = _variants(f"Remek/basal-omni-1.0-{tier}-MLX-8bit", {})
        assert mlx.media == ()
    for repo in ("Remek/basal-omni-1.1-max", "Remek/basal-omni-1.0-mini-MLX"):
        (v,) = _variants(repo, {})
        assert v.media == ()

    v = _variants("Remek/basal-omni-1.0-mini", {})[0]
    for platform in ("cuda", "apple", "cpu"):
        assert _media_mode(v, hw(platform, engines=frozenset({"media"}))) == ("eager", None)
        mode, reason = _media_mode(v, hw(platform, engines=frozenset()))
        assert mode is None and "install basal[media]" in reason
    assert _media_mode(_variants("Remek/basal-2.0-mini", {})[0], hw())[1] == \
        "this checkpoint has no image or audio encoder"


def test_media_serve_args_devices():
    v = _variants("Remek/basal-omni-1.0-mini", {})[0]
    for platform, device in (("cuda", []), ("apple", ["--device", "mps"]), ("cpu", ["--device", "cpu"])):
        args = serve_args(v, Path("/model"), "mlx", media_platform=platform)
        assert args == ["--model", "/model", "--mode", "eager", "--media", "on", *device]


def test_parse_repo_contract_names():
    assert parse_repo("Remek/basal-omni-1.0-max-GGUF") == ("basal-omni-1.0", "max", "GGUF")
    assert parse_repo("Remek/basal-2.0-4.5B-MLX-8bit") == ("basal-2.0", "4.5B", "MLX-8bit")
    assert parse_repo("Remek/basalt-1.0-4.5B") is None
    assert parse_repo("Remek/basal-1.0-4.5B-GGUF-extra") is None


def test_assessment_runtime_engines_and_fit_boundaries():
    fp8 = variant("Remek/basal-2.0-4.5B-FP8", "FP8")
    assert assess(fp8, hw(cap=(8, 9))).runnable
    assert not assess(fp8, hw(cap=(8, 6))).runnable
    mlx = variant("Remek/basal-2.0-4.5B-MLX-8bit", "MLX-8bit")
    assert not assess(mlx, hw(platform="cuda", engines=frozenset({"cuda"}))).runnable
    assert assess(mlx, hw(platform="apple", cap=None, engines=frozenset({"mlx"}))).runnable
    gguf = variant("Remek/basal-2.0-4.5B-GGUF", "GGUF", gguf="model-Q8_0.gguf")
    a = assess(gguf, hw(engines=frozenset()))
    assert not a.runnable and "basal[gguf]" in a.reason
    assert not assess(variant("Remek/basal-omni-1.0-mini", "bf16"), hw(platform="cpu", cap=None, engines=frozenset())).runnable
    size = 10 * 1024**3
    v = variant(size=size)
    budget = size * 1.2 + 1024**3
    assert assess(v, hw(memory=math.ceil(budget / .85))).fit == "fits"
    assert assess(v, hw(memory=int(budget))).fit == "tight"
    assert assess(v, hw(memory=int(budget), available=math.ceil(budget / .85))).fit_now == "fits"
    assert assess(v, hw(memory=int(budget), available=int(budget))).fit_now == "tight"
    assert assess(v, hw(memory=int(budget), available=0)).fit_now == "too_large"
    unknown = Variant("unknown", "Remek/basal-2.0-4.5B", "basal-2.0", "4.5B", "bf16", "bf16", None, None, False)
    assert assess(unknown, hw()).fit == "unknown"
    assert assess(v, hw()).fit_now == "unknown"


def test_suggestions_by_hardware_and_available_memory():
    def choices(hw_, repos):  # built the way the catalog builds them, so basal-omni bf16 carries its media kinds
        return suggest([assess(_variants(repo, {"model.safetensors": variant(repo).weights_bytes})[0], hw_)
                        for repo in repos], hw_)

    apple = hw("apple", "M4 Max", None, frozenset({"mlx"}), 24 * 1024**3, 6 * 1024**3)
    repos = ["Remek/basal-2.0-4.5B-MLX-8bit", "Remek/basal-2.0-max-MLX-8bit",
             "Remek/basal-2.0-mini-MLX-8bit"]
    cards = choices(apple, repos)
    assert cards[0]["slot"] == "recommended"
    assert cards[0]["id"] == "Remek/basal-2.0-mini-MLX-8bit"

    dc = hw("cuda", "H100", (9, 0), frozenset({"cuda"}), 80 * 1024**3, 70 * 1024**3)
    cards = choices(dc, ["Remek/basal-2.0-mini", "Remek/basal-2.0-4.5B", "Remek/basal-2.0-max"])
    assert [(card["slot"], card["id"]) for card in cards[:3]] == [
        ("recommended", "Remek/basal-2.0-4.5B"),
        ("best_quality", "Remek/basal-2.0-max"),
        ("fastest", "Remek/basal-2.0-mini"),
    ]
    assert cards[0]["why"] == "4.5B in bf16: needs about 11.4 GB of 75.2 GB available"

    omni = "Remek/basal-omni-1.0-mini"
    without_media = choices(dc, [omni])
    assert all(card["slot"] != "multimodal" for card in without_media)
    media_hw = hw("cuda", "H100", (9, 0), frozenset({"cuda", "media"}), 80 * 1024**3, 70 * 1024**3)
    media_cards = choices(media_hw, [omni])
    assert media_cards == [{"slot": "multimodal", "id": omni,
                            "why": "images and audio, served with --mode eager"}]


def test_available_memory_parses_macos_vm_stat(monkeypatch):
    from basal.ui import hardware

    output = ("Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
              "Pages free: 10.\nPages inactive: 20.\nPages speculative: 3.\n")
    monkeypatch.setattr(hardware.sys, "platform", "darwin")
    monkeypatch.setattr(hardware.subprocess, "check_output", lambda *args, **kwargs: output)
    assert hardware._available_bytes() == 33 * 16384


def test_gguf_launch_and_download_patterns():
    v = variant("Remek/basal-2.0-4.5B-GGUF", "GGUF", gguf="basal-2.0-4.5B-Q8_0.gguf")
    assert serve_args(v, Path("/models/repo"), "gguf")[-2:] == ["--gguf", "/models/repo/basal-2.0-4.5B-Q8_0.gguf"]
    assert download_kwargs(v)["allow_patterns"][0] == v.gguf_file


def test_thinking_model_is_neither_counted_nor_downloaded():
    (v,) = _variants("Remek/basal-2.0-4.5B", {"model.safetensors": 9, "think/model.safetensors": 9, "basal.json": 1})
    assert v.weights_bytes == 9
    assert download_kwargs(v) == {"ignore_patterns": ["think/*"]}


def test_serving_mode_per_family_and_port():
    apple = hw("apple", "M4 Max", None, frozenset({"mlx"}), 64 * 1024**3)
    assert assess(variant("Remek/basal-2.0-4.5B-MLX-8bit", "MLX-8bit"), apple).mode == "mlx"  # the port as stored
    h100 = hw()
    assert assess(variant("Remek/basal-omni-1.0-max", "bf16"), h100).mode == "eager"
    older_fp8 = assess(variant("Remek/basal-1.5-4.5B-FP8", "FP8"), h100)
    assert not older_fp8.runnable and "basal[vllm]" in older_fp8.reason
    assert assess(variant("Remek/basal-1.5-4.5B-FP8", "FP8"), hw(engines=frozenset({"cuda", "vllm"}))).mode == "vllm"
    assert "--device" not in serve_args(variant("Remek/basal-omni-1.0-max", "bf16"), Path("/m"), "eager")


def test_cpu_with_llama_cpp_prefers_gguf_q8():
    cpu = hw("cpu", "CPU", None, frozenset({"gguf"}), 64 * 1024**3, 64 * 1024**3)
    repo = "Remek/basal-2.0-4.5B-GGUF"
    items = [assess(Variant(f"{repo}:{f}", repo, "basal-2.0", "4.5B", "GGUF", label, f, 5 * 1024**3, False), cpu)
             for f, label in (("basal-2.0-4.5B-Q5_K_M.gguf", "GGUF Q5_K_M"), ("basal-2.0-4.5B-Q8_0.gguf", "GGUF Q8_0"))]
    items.append(assess(variant(), cpu))
    assert suggest(items, cpu)[0]["id"] == f"{repo}:basal-2.0-4.5B-Q8_0.gguf"


def test_delete_gguf_variants_removes_only_selected_file_then_repo(tmp_path):
    repo_id = "Remek/basal-2.0-mini-GGUF"
    repo = tmp_path / "models--Remek--basal-2.0-mini-GGUF"
    blobs, snapshot = repo / "blobs", repo / "snapshots" / ("a" * 40)
    blobs.mkdir(parents=True)
    snapshot.mkdir(parents=True)
    (repo / "refs").mkdir()
    (repo / "refs" / "main").write_text("a" * 40)
    names = ("basal-2.0-mini-Q4_K_M.gguf", "basal-2.0-mini-Q8_0.gguf")
    for index, name in enumerate(names, 1):
        blob = blobs / (str(index) * 64)
        blob.write_bytes(bytes([index]) * index)
        (snapshot / name).symlink_to(blob)
    first, second = _variants(repo_id, {name: index for index, name in enumerate(names, 1)})

    assert delete(first, tmp_path) == 1
    assert not (snapshot / names[0]).exists()
    assert (snapshot / names[1]).exists()
    assert repo.is_dir()
    assert delete(second, tmp_path) == 2
    assert not repo.exists()


def test_old_cuda_bf16_requires_ampere_but_omni_remains_eager():
    sm75 = hw(cap=(7, 5))
    text = assess(variant("Remek/basal-2.0-mini"), sm75)
    assert not text.runnable
    assert text.reason == ("The basal engine needs an NVIDIA GPU with compute capability 8.0+ (Ampere or newer); "
                           "on this GPU use a GGUF file with basal[gguf].")
    omni = assess(variant("Remek/basal-omni-1.0-mini"), sm75)
    assert omni.runnable and omni.mode == "eager"


def test_suggestion_prefers_fit_before_preferred_format():
    apple = hw("apple", "M4 Max", None, frozenset({"mlx"}), 24 * 1024**3, 4_200_000_000)
    preferred = variant("Remek/basal-2.0-mini-MLX-8bit", "MLX-8bit", 3_000_000_000)
    compact = variant("Remek/basal-2.0-mini-MLX-fp4", "MLX-fp4", 2_000_000_000)
    cards = suggest([assess(preferred, apple), assess(compact, apple)], apple)
    assert cards[0]["id"] == compact.id


def _gguf_cache(tmp_path):
    repo = tmp_path / "models--Remek--basal-2.0-mini-GGUF"
    blobs, snapshot = repo / "blobs", repo / "snapshots" / ("b" * 40)
    blobs.mkdir(parents=True)
    snapshot.mkdir(parents=True)
    (repo / "refs").mkdir()
    (repo / "refs" / "main").write_text("b" * 40)
    return blobs, snapshot


def test_delete_gguf_in_a_cache_of_copied_files_counts_the_freed_file(tmp_path):
    blobs, snapshot = _gguf_cache(tmp_path)
    selected, other = "basal-2.0-mini-Q4_K_M.gguf", "basal-2.0-mini-Q8_0.gguf"
    (snapshot / selected).write_bytes(b"12345")  # no symlinks (Windows default): the snapshot holds the file
    (blobs / ("c" * 64)).write_bytes(b"67")
    (snapshot / other).symlink_to(blobs / ("c" * 64))
    first, _ = _variants("Remek/basal-2.0-mini-GGUF", {selected: 5, other: 2})
    assert delete(first, tmp_path) == 5
    assert not (snapshot / selected).exists()
    assert (snapshot / other).read_bytes() == b"67"


def test_delete_gguf_keeps_a_blob_another_file_name_still_uses(tmp_path):
    blobs, snapshot = _gguf_cache(tmp_path)
    selected, other = "basal-2.0-mini-Q4_K_M.gguf", "basal-2.0-mini-Q8_0.gguf"
    blob = blobs / ("c" * 64)
    blob.write_bytes(b"12345")
    (snapshot / selected).symlink_to(blob)
    (snapshot / other).symlink_to(blob)
    first, _ = _variants("Remek/basal-2.0-mini-GGUF", {selected: 5, other: 5})
    assert delete(first, tmp_path) == 0
    assert not (snapshot / selected).exists()
    assert (snapshot / other).read_bytes() == b"12345"
