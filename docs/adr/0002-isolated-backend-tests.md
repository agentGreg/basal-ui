# Isolated backend tests and explicit restoration

The backend manager reports local dependency/device availability separately from model compatibility and successful inference. Reuse catalog assessment and execution-mode rules, existing Jobs, temporary Runners and Fleet memory accounting. Dependency imports run in bounded subprocesses so a broken native package cannot crash the UI process.

Inference requires an explicit request, downloaded files and an offline private child. It bypasses decision history/statistics and never installs packages. Selected loaded models can be displaced only after generation confirmation, a shared activity admission gate, whole-job leases, and fresh zero-activity child health checks. Reserve test memory before stopping any selected model.

Restoration is explicit and session-local. Capture active settings rather than saved settings, pin the loaded cache revision, and leave failed restores retryable without replacing another instance. Keep Inspector and saved autoload configuration intact. Reuse the static Basecoat UI and existing local API access controls; external inference services are outside this manager's scope.
