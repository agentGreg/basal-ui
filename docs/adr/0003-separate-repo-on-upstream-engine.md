# Separate repository on the upstream engine

basal-ui lives in its own repository and installs only upstream engine releases (rkinas/basal tags; `>=2.4.1,<2.5` at
0.1.0), never a fork build. Its API server owns authentication, request ids, error envelopes and the OpenAI Decisions
translation, so the `basal-serve` children it starts are stock upstream servers on loopback; the engine's tools
(loadtest, bench, run) are executed as subprocesses. Capabilities outside the supported range — the LoRA adapter load
option until engine 2.5.1 — are offered only behind feature probes (`basal_ui.engine.feature`).

Depending on the fork instead was rejected: basal-ui would sit on an engine that diverges from upstream forever.
Upstreaming the UI whole was rejected too: a 78-file PR (rkinas/basal#45) could not be reviewed. The consequence:
engine changes basal-ui needs are proposed upstream one at a time — an issue with an acceptance check, one PR each,
tracked in a research note — and the UI never blocks on them.
