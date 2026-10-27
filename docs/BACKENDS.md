# Local backend manager

Open **Backends** in the sidebar (Alt+0). Existing Alt+1–9 shortcuts remain unchanged. This view describes the machine running basal-ui and its Python environment, not the browser machine or an external Ollama/llama.cpp server.

The inventory groups execution modes under PyTorch, MLX, in-process llama.cpp, vLLM, SGLang, Strix Halo and TPU/JAX. Compatible engines appear first; unavailable engines are hidden until **Show unavailable** is selected. Package versions and bounded dependency/device probes distinguish Installed, Not installed, Needs attention and Unavailable. Installed means dependency checks passed, not that inference was verified. Refresh reruns hardware and package probes in child processes, each with a 20-second deadline.

**View matching models** filters Models by the engine's model/mode compatibility, including engines whose dependencies are missing. The visible filter can be cleared. Quantization and format remain model attributes, not backend groups.

## Setup

Setup guidance offers copyable commands and official documentation. Nothing is installed by the UI. Commands for MLX, llama.cpp and TPU target the displayed Python interpreter; use the platform installer for PyTorch/ROCm. vLLM and SGLang need separate environments: install the same Basal fork and its UI dependencies in that environment, then launch basal-ui there. Restart after installing dependencies; Refresh alone does not replace already imported packages in the main app.

Basal integration limits may be narrower than upstream engine support. For example, this integration uses MLX on Apple Silicon and vLLM/SGLang on NVIDIA Linux. Commands follow this repository's installation constraints; consult [INSTALL.md](INSTALL.md) and the relevant upstream documentation before changing an environment:

- [PyTorch installer](https://pytorch.org/get-started/locally/)
- [MLX installation](https://ml-explore.github.io/mlx/build/html/install.html)
- [llama-cpp-python](https://llama-cpp-python.readthedocs.io/en/latest/)
- [vLLM GPU installation](https://docs.vllm.ai/en/stable/getting_started/installation/gpu/)
- [SGLang installation](https://docs.sglang.io/docs/get-started/install)
- [ROCm installation](https://rocm.docs.amd.com/projects/install-on-linux/en/latest/)
- [JAX TPU installation](https://docs.jax.dev/en/latest/installation.html#tpu)

## Explicit inference test

Choose an installed backend, a downloaded compatible model, and an execution mode. The smallest estimated model is preselected. The test reserves mode-aware memory and starts a private, offline child with one fixed text decision. It never downloads weights, changes saved load settings, or records decisions in History/statistics. Startup allows 30 minutes for compilation; inference allows two minutes. Cancel stops the private child and releases its reservation.

Optionally select idle loaded models to unload. Confirmation includes their current generation. The server blocks new decisions and model jobs for those models, rejects existing API/MCP/Playground requests and Batch/benchmark leases, and requires a fresh authenticated child health response with zero running and queued work. Unknown activity refuses the entire selection. Memory remains reserved while selected children stop. Inspector is never selected or unloaded, and its reservation still counts.

Stopped models remain stopped. **Restore stopped models** reloads their captured active revision, mode, options, media setting and serving configuration, without changing saved/autoload settings or replacing another loaded instance. Failed restores remain retryable. Restoration records last only for the current app process; restarting loses them. Logs show the latest job of each kind.

A passed result validates one model/mode/revision response. It is not an accuracy score, benchmark, or verification of every mode listed under that backend.

## Local UI API

These routes use the same local UI access boundary as the existing model controls:

| Route | Result |
| --- | --- |
| `GET /api/backends?refresh=false` | Environment, hardware, engine inventory, compatible models and session test records; probe failure returns 503. |
| `POST /api/backends/tests` | Start a test; 202 with `id`. Body: `backend`, `model`, `mode`, optional `unload: [{id, generation}]`. Invalid selections return 400, busy/conflicting state 409. |
| `GET /api/backends/tests` | `{tests: [...]}` including status, result/error, stopped models and restoration state. |
| `DELETE /api/backends/tests/{id}` | Cancel the active test/restoration; unknown session returns 404. |
| `POST /api/backends/tests/{id}/restore` | Start explicit restoration; 202, unknown session 404, active conflicting job 409. |

Model status includes an opaque `generation` token for unload confirmation. Test states are `starting`, `passed`, `error`, `cancelled`; restoration is tracked separately. Background progress and logs use existing `backend-test` and `backend-restore` job kinds.

## Verification

`tests/test_ui_backends.py` covers inventory, missing dependencies, health/activity refusal, job leases, isolated execution, cancellation, restoration and local-only failure. `BASAL_E2E=1 pytest tests/e2e/test_backends.py` runs an actual downloaded model in an isolated app and checks browser navigation/filtering/dialogs. Hardware unavailable on the test host requires separate real-device validation.

Local implementation validation (2026-10-27): 220 UI unit tests passed; 31 browser/end-to-end tests passed, including real local inference, explicit unload/restoration on Apple M4 Max, and navigation through all ten views. Desktop and 390px mobile layouts were inspected; no horizontal page overflow was found. After the final unload-conflict handling change, the 57 backend/app tests passed again. Other hardware families have fixture coverage, not real-device validation. CI/deployment were not run.
