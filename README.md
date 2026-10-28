# basal-ui

A local browser app for [basal](https://github.com/rkinas/basal) models, in the style of LM Studio. basal-ui detects
your hardware, lists every basal model on Hugging Face with what this machine can run, downloads the one you choose
and starts a private `basal-serve` for each loaded model. Around that it gives you:

- a **Playground** for decisions, with a builder for every question type, evidence and calibrated confidence;
- **calibration** on your own labelled requests, benchmark sets, load tests, determinism checks and engine-mode
  comparisons;
- **batch** inference over a JSONL file;
- **Inspect**, which embeds `basal-viz` to show how a decision is made, layer by layer;
- an **API server** with named API keys, CORS, request ids, typed errors, OpenAI Decisions translation and MCP — one
  port for every loaded model.

## Install

```bash
uv venv
uv pip install "basal @ https://github.com/rkinas/basal/archive/refs/tags/v2.4.1.tar.gz" basal-ui
basal-ui                    # then open http://127.0.0.1:7860
```

Or as a standalone tool:

```bash
uv tool install basal-ui --with "basal @ https://github.com/rkinas/basal/archive/refs/tags/v2.4.1.tar.gz"
```

The engine extras of your machine are chosen on the *engine* install, not on basal-ui, for example
`uv pip install "basal[mlx] @ https://github.com/rkinas/basal/archive/refs/tags/v2.4.1.tar.gz" basal-ui`
on Apple Silicon (`mlx`), or `basal[gguf]`, `basal[fp8]`, `basal[vllm]` elsewhere; `basal[mcp]` brings the MCP
server. basal-ui does not declare the engine as a dependency: PyPI rejects direct-URL requirements, and the PyPI
package named `basal` is an unrelated project. Instead basal-ui checks for the engine at startup and refuses to start
without it, printing the install command above.

## Supported engine range

basal-ui runs on upstream engine releases `>=2.4.1,<2.5` (see `basal_ui/engine.py`). An engine outside that range is
refused with the install command of a supported release.

## Experimental engine

To try an engine change that has not shipped in a supported release (for example a proposal branch from the fork
[pawelkiszczak/basal](https://github.com/pawelkiszczak/basal)):

```bash
uv pip install "basal @ git+https://github.com/pawelkiszczak/basal@<topic-branch>"
BASAL_UI_ENGINE_UNCHECKED=1 basal-ui
```

basal-ui starts and shows a persistent **experimental engine** badge in the top bar with the engine's version (and
commit). Features the engine does not provide yet stay hidden or greyed out with their reason.

## Documentation

- [docs/USAGE.md](docs/USAGE.md) — the browser app end to end
- [docs/API.md](docs/API.md) — basal-ui's API server: keys, CORS, errors, OpenAI Decisions, MCP, clients
- [docs/E2E.md](docs/E2E.md) — the end-to-end release gate
- [CONTRIBUTING.md](CONTRIBUTING.md) — dev setup, tests and the Engine-proposal pipeline
- Engine: [rkinas/basal](https://github.com/rkinas/basal)

## License

Apache-2.0. basal-ui by Paweł Kiszczak; it wraps the [basal engine](https://github.com/rkinas/basal) by Remigiusz
Kinas, also Apache-2.0. See [NOTICE](NOTICE) for bundled fonts and UI libraries.
