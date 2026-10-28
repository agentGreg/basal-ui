# LoRA adapters at load

Serve a fine-tune without keeping a merged copy of the weights: `--adapter DIR` merges a LoRA into the weights at
load. With basal-finetune (engine 2.5) writing its LoRA to `--out/lora`, a fine-tune can be served the moment
training ends.

## Use case

A fine-tuned basal model on a machine that cannot afford a second full checkpoint: the base weights stay on disk
once and the adapter — basal-finetune's `--out/lora` output or any mlx-lm / PEFT LoRA directory — is merged at load
in seconds. The served model is the fine-tune, under its own name and calibration.

## Proposal

`--adapter DIR` (mlx-lm or PEFT LoRA) on `basal-serve`, `basal-calibrate` and `basal-bench`: merged into the weights
at load on every in-process engine (bit-identical to `mlx_lm fuse`), passed natively to vLLM / SGLang, and refused
with a message on GGUF / Ollama / llama.cpp and FP8 checkpoints, for which the new `basal-fuse-adapter` writes the
merged checkpoint. Served names carry the adapter; `GET /v1/models` reports its identity
`name@sha256(adapter_config.json + weights)[:12]`; calibration files record `fitted_on.adapter`, a mismatch warns
(or exits under `--strict-calibration`), and the base model's file loads for its temperatures only (answers marked
`unvalidated`). Full detail in the PR description.

## Acceptance check

The maintainer's criteria for 2.5.1, set in advance:

- without `--adapter`: answers byte-identical to 2.5;
- `--adapter DIR` vs the `basal-fuse-adapter` checkpoint, same mode: bit-identical probabilities;
- vs a PEFT `merge_and_unload()` reference: the same top answer on ≥ 99.5% of readouts, with no flip where the
  margin is ≥ 0.05;
- covering fast, eager, mlx / `-MLX-8bit` exports, `--quant fp8`, and vLLM with its native LoRA, on an rsLoRA
  adapter for Gemma 4 E4B and 31B, plus a basal-finetune output.

Added at the maintainer's request for the 2.5 rebase: the prepared-start key carries the adapter identity; the
adapter is merged before `--quant-load cpu`; `--adapter <out>/lora` answers the same as the merged `<out>`.

## Upstream

- PR: [rkinas/basal#52](https://github.com/rkinas/basal/pull/52) (draft; no issue — proposed in the PR)
- Scheduled for engine 2.5.1, with a rebase on 2.5 requested first.

## Measurements

Apple M4 Max, basal-2.0-mini bf16 and `-MLX-8bit`, a rank-16 LoRA (scale 2.0) on all 7 projections of layers 16–31:

| check | result |
|---|---|
| merged weights vs `mlx_lm fuse` | bit-identical, 515/515 tensors |
| `--adapter` vs the `basal-fuse-adapter` checkpoint, one request at a time | byte-identical in `mlx`, `mps`, `eager` |
| vs PEFT `merge_and_unload()`, 1,066 readouts (`mlx` and `mps`) | top 1059/1066 (99.34%); 7 flips, all at margin ≤ 0.005; none at ≥ 0.05 |
| noise floor: the same PEFT weights, `mlx` vs `mps` | also 1059/1066 — the flips are the floor, not the merge |
| `-MLX-8bit --adapter` vs bf16 `--adapter` | max abs Δp 0.0324, top 37/40 |
| `--mode vllm --adapter` (vLLM 0.32 via vllm-metal) | start-up probe passed; vs the fused checkpoint top 40/40 |
| without `--adapter`, branch vs 2.4.1 | answers byte-identical, 84/84 in `mlx` / `mps` / `eager` |

Not run here: CUDA, SGLang and Gemma 4 (covered by the acceptance check when 2.5.1 runs it).

## Outcome

Pending engine 2.5.1. basal-ui offers the adapter load option behind `engine.feature("adapter")`: enabled on the
proposal branch (experimental engine), greyed out with *needs engine ≥ 2.5.1* on supported releases. When 2.5.1
enters `SUPPORTED`, the probe and the gating go.
