# 0.1.0

- basal-ui is its own repository and package: `pip install basal-ui`, import `basal_ui`, the `basal-ui` command.
- It runs on upstream engine releases `>=2.4.1,<2.5` and refuses others, printing the install command, unless
  `BASAL_UI_ENGINE_UNCHECKED=1` is set (the top bar then shows an *experimental engine* badge with the version and
  commit). [README](../README.md#supported-engine-range)
- API keys, CORS, request ids, typed error envelopes and the OpenAI Decisions translation are owned by basal-ui's API
  server, so the engine children it starts are stock `basal-serve` on loopback, without keys.
  [API](API.md)
- Load tests, determinism checks, engine-mode comparisons and Batch run the engine's tools (`basal-loadtest`,
  `basal-bench`, `basal-run`) as subprocesses.
- The LoRA adapter load option appears only with an engine that provides it (≥ 2.5.1), behind
  `engine.feature("adapter")`; on 2.4.x it is greyed out with the reason.
  [Research note](research/lora-adapters-at-load.md)
- The update check covers basal-ui and the engine: the Overview reports a newer basal-ui release, a newer engine
  release inside the supported range, and an engine release this basal-ui does not support yet.
- On engine 2.4.1 basal-ui fills in what the engine does not report itself: the certified thresholds of a loaded
  model come from the calibration file it serves; a file fitted in basal-ui names the model's repo id and commit
  (not the cache path) so `basal-serve` applies it; Inspect explains a `"facts": "auto"` request with the facts the
  served model reads; the Playground's context meter appears once the engine reports its context policy. MCP on the
  API server answers with the first loaded model until the engine's MCP takes a `model` argument.
- **Playground visual elements and media samples:** mark, label, move and resize numbered boxes on an uploaded image;
  save them as a `choice` question and see per-element probabilities over the marked image. Saved requests retain the
  original screenshot and marks. Evidence spans are disabled while media is attached. `basal_ui/examples/media.jsonl`
  adds three generated images (a checkout screenshot with numbered elements, a Polish invoice, a revenue chart) and
  two voice clips (Polish complaint, English appointment) for basal-omni, loaded into the media field with an audio
  player for clips and a notice on a model without media; Inspect skips them. [USAGE.md](USAGE.md#browser-app-pick-a-model-for-your-hardware-and-try-it)
- **Inspect for Gemma 4 basal-omni:** the bf16 basal-omni checkpoints (not the recurrent basal-omni-pico) can be
  inspected once the engine's basal-viz reads them (`engine.feature("viz_gemma4")`, proposed upstream in
  [rkinas/basal#75](https://github.com/rkinas/basal/pull/75)); on 2.4.x they are listed disabled with the reason.
  `/api/models` reports why each variant cannot be inspected (`inspect_reason`). Requests with media are refused.
  [USAGE.md](USAGE.md#inspect)
- **One engine pin:** `basal_ui/engine.py` holds the engine releases basal-ui is tested with (`TESTED_RELEASES`, the
  last one pinned); CI's engine matrix is generated from it and a test checks that `pyproject.toml`, the README and
  the E2E guide install the same release. `make engine-pin` prints it.
  [CONTRIBUTING.md](../CONTRIBUTING.md#bumping-the-engine)
- **API server hardening:** a browser request whose `Origin` is not in the **CORS origins** setting is refused
  (403 `ORIGIN_REFUSED`) before it reaches a model, so a web page cannot run decisions on a keyless server; bodies
  must be `application/json` (415) and at most 256 MiB (413). The page and the embedded inspector refuse framing by
  other sites and are revalidated on every load. [API](API.md#authentication-and-cors)
- **Reliability:** a restart that fails no longer strands its memory reservation, and a concurrent load waits for it;
  shutdown cancels jobs, ends their private children and waits up to 30 s for them; a process-group member that
  ignores SIGTERM is killed; a full disk no longer fails an answered decision (it is logged instead); resetting the
  statistics also discards the counts another basal-ui on the same settings file had not written yet; a settings file
  that is not valid JSON is never overwritten (changes are refused with the path to fix).
- **Models:** a sharded download counts as downloaded only with every shard its index names; an offline catalog
  entry reads one snapshot (the one `main` points at); ROCm GPUs are not offered FP8, NVFP4, vLLM or SGLang; the
  backend inference test checks the files at the revision the inventory showed; a malformed benchmark row fails with
  its line number instead of being skipped.
- **Accessibility:** History rows open from the keyboard (the time is a button with `aria-expanded`) and keep focus
  across live refreshes; element boxes can be added, moved (arrows) and resized (Shift+arrows) without a pointer;
  benchmark and band inputs have labels. The live History view keeps the newest 500 rows.
- **Development:** Ruff `F,I,B,UP,SIM` with `ruff format` (`make format`, optional pre-commit hooks), a coverage
  report in `make test`, `SECURITY.md`, Dependabot, and CI/release workflows with pinned actions, minimal permissions
  and a release job that checks the tag against `pyproject.toml` and publishes the GitHub release notes.

# Before 0.1.0 (in the fork)

- **Basal UI message codes:** model availability, recommendation, backend setup and job-phase explanations retain their English API fields and include additive translation codes for Polish rendering. Server errors remain English.

- For engine 2.3.1 the UI adds CPU packed execution, Blackwell omni routing, opt-in MLX packed rows, graph-batch
  guidance, sampled child load counters, HTTP error diagnostics, and calibration completion warnings.
  [Engine integration](USAGE.md#engine-integration-in-231), [the release gate](E2E.md)

- **`basal-ui` navigation and Playground:** Models now separates Suggested and All; Model manager groups Loaded statistics, benchmarks and calibration before On disk and Models directory. Language changes keep the tab's Playground draft while requiring media to be attached again. Load-option Setup explains that media mode is selected in Model manager. [USAGE.md](USAGE.md#browser-app-pick-a-model-for-your-hardware-and-try-it), [E2E.md](E2E.md#what-is-tested)

- For engine 2.4 the catalog adds **basal-omni-pico** (Qwen3.5, text only) with a "lightest" suggestion; its modes are
  the engine's: `fast` / `fast-nocompile` on CUDA with flash-linear-attention (the default on Hopper), `eager`
  elsewhere, and `fast-exit`, `fp8`, `nvfp4`, `mps` and MLX shown as unavailable with the reason, as are the
  graph-engine options 2.4 refuses for it. `fp8` is unavailable below compute capability 8.9 before loading. The
  memory estimate of the CUDA graph engine adds 4 GiB for graphs and compile workspace (2.3.3-2.3.5 measurements).
  The model card shows the repository's quantisation (`config.json` `quantization_config` / `quantization`,
  `basal.json` `quantization` and `engine`, also before download), the Apple recommendation points to
  `--mlx-packed`, text files are read and written as UTF-8, and the memory of Windows hosts is read with
  `GlobalMemoryStatusEx`.

- For engine 2.4.1 the load options offer the **prompt layout** (`--layout`; default: the model's basal.json), refuse
  a non-canonical layout with the state or prefix cache before loading (basal-serve's own message), and show it as a
  `layout` chip on the loaded row and in the model card. [USAGE.md](USAGE.md#load-options)

- **`basal-ui`**, a local browser app: detects the hardware and the memory available now,
  suggests models that fit (recommended, best quality, fastest, multimodal), lists every basal model with its fit and
  serving mode, downloads the model into a directory of your choice, lists and
  deletes what is on disk, runs `basal-serve` for it and offers a playground for decisions, with images and audio
  for the basal-omni-1.0 checkpoints that have encoders (`--mode eager`, or `--mode vllm` on an NVIDIA GPU with
  the engine's vLLM extra).
  [USAGE.md](USAGE.md#browser-app-pick-a-model-for-your-hardware-and-try-it)
- The app can expose the loaded models as an API server, with optional named API keys, CORS, decision logging,
  MCP and auto-load. [API server walkthrough](USAGE.md#serving-models-from-basal-ui)
- **`basal-ui` community models**: the catalog adds the `pawelkiszczak` (basal-1.0 GGUF, MLX 8-bit, oMLX oQ6e) and
  `agentGreg` (basal-1.5-mini MLX 6-bit, bf16) conversions, marked community; weights-only GGUF files are served with
  their base repository's tokenizer and calibration. [USAGE.md](USAGE.md#browser-app-pick-a-model-for-your-hardware-and-try-it)
- **`basal-ui` model cards, presets, shortcuts, dark theme and update notices**: a card per model (provenance,
  calibration, loaded entry, README), named load-option and request presets with export / import, Ctrl/⌘+Enter, Alt+1…9
  and `?`, light / dark / system theme, a notice for a newer basal release and for new catalog models; load options
  take a `revision`; calibration takes basal-calibrate's advanced controls (`--strict`, `--reference`, minimum counts,
  `--report-only`, `--save-rows` and a `--from-rows` refit); results show `branches`, `second_order_branches` and
  `lane`, loaded rows the prompt format, option orders and vLLM preset.
- **`basal-ui` with several models**: load several models at once, all served on one API port where every request
  names its `"model"` (`GET /v1/models` lists them); a memory budget with a keep-free reserve refuses loads that do not
  fit and shows each model's resident memory; per-model statistics (requests, average best-answer probability,
  latency, tokens, per question type; also over HTTP at `/v1/stats`); benchmarks of a loaded model on the bundled
  examples or your own JSONL set ([BENCHMARK_SETS.md](BENCHMARK_SETS.md)); a **Download** button that only downloads;
  a **History** of every decision (playground, API, MCP) with replay in the playground and a **Logs** view of every
  `basal-serve`. In the fork, `basal-mcp --model` names the model on such a server.
  [USAGE.md](USAGE.md#browser-app-pick-a-model-for-your-hardware-and-try-it)
- **`basal-ui` decision safety**: every playground answer shows whether it was deferred to a person (and the action or
  tool refused), whether its confidence clears the model's certified 1% / 5% thresholds, whether its calibration is
  validated, unvalidated or fitted on text (no automation claim then), and when the state was truncated to the
  context; `GET /v1/models` carries `certified` and `context`. History marks deferred and truncated decisions.
- **`basal-ui` builders for every question type**: `multi`, `act` (simple costs or a cost matrix), `tool` (with the set
  options of several tools), `classify`, `verify`, `compress` and `extract` next to choice / noul / score, with
  `max_error`, evidence, `facts` and `lang` controls, a dedicated result view per type and the state shown with
  evidence spans, kept / dropped units and extracted fields highlighted; a request is sent as given until a field is
  edited; `basal/examples/advanced.jsonl` has one full request per type.
- **`basal-ui` custom calibration**: fit a calibration file on your labelled requests for a downloaded model (a
  temporary uncalibrated `basal-serve` plus `basal-calibrate --url`; with task questions the engine in basal-ui's
  process), or import one fitted elsewhere (with basal-serve's warning when it belongs to another model); read its
  report, compare it with the shipped file and select it per model. Benchmarks and calibrations run as background
  jobs with their own log.
  [USAGE.md](USAGE.md#custom-calibration)
- **`basal-ui` load options**: choose the engine mode per load (with the reason a mode is unavailable on this
  machine; Ollama, llama-server, SGLang, TPU and Strix Halo where detected) and every runtime setting of
  `basal-serve` — quantisation, attention, dtype, determinism, option orders, batching, caching, scheduling, context
  policy, the CUDA graph engine's kernel switches, the Gemma 4 eager forward, `--strict-calibration` and the think
  server; remembered per model and reused by restart and auto-load.
  [USAGE.md](USAGE.md#load-options)
- **`basal-ui` batch inference**: run a JSONL file through a loaded model from the **Batch** view, follow the progress,
  page through the answers and download them. [USAGE.md](USAGE.md#batch)
- **`basal-ui` load tests, determinism checks and engine-mode comparisons**: configurable `basal-loadtest` runs and
  `--determinism` checks against a loaded model, and `basal-bench` mode comparisons of a downloaded model, next to
  the benchmark preset. [USAGE.md](USAGE.md#statistics-and-benchmarks)
- **`basal-ui` Inspect view**: `basal-viz` embedded for a downloaded bf16 model, with **Inspect this decision** from
  the Playground, which lists what differs from the served decision (weights, engine, option orders, calibration,
  thinking, early exit) and refuses what basal-viz cannot explain; `basal-viz` applies `facts: auto` and its page
  uses relative API paths, so it also works under a prefix. [USAGE.md](USAGE.md#inspect)
- **`basal-ui` thinking and early exit**: load basal-2.0 4.5B with thinking or a basal-1.0 `fast-exit` model with a
  default early-exit policy from the load options; the Playground then sends `think`, `think_text` and `early_exit`
  and shows the reasoning of each answer that thought. [USAGE.md](USAGE.md#load-options)
- **`basal-ui` end-to-end tests** (`tests/e2e`, `BASAL_E2E=1`, extra `basal-ui[e2e]`): a real basal-ui with the models on
  disk, through its API, its API server and its pages in Chromium, for every feature above; skipped by default.
  [E2E.md](E2E.md)
