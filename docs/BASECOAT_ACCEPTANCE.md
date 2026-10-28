# Basecoat UI acceptance — 2026-10-27

Implemented on the current fork with three isolated Luna work packages. Shared preparation extracted the classic script without introducing a frontend build. Production bundles Basecoat 1.1.0 and its MIT license, the application styles, JavaScript, and existing fonts. Sidebar navigation, all nine views, native dialogs, and fork serving contracts are retained.

## Changes and regression coverage

| Package | Change | Evidence |
| --- | --- | --- |
| Visual | Basecoat presentation, responsive sidebar and Playground, associated dynamic labels | Browser sweep across nine views at 1440, 1280, 1024, 768, and 390 pixels in light/dark; question control containment and labels; visual screenshot inspection |
| Playground/replay | Unique default names, reject duplicate/blank names, preserve prototype-named question keys | Browser checks, including blocked sends and preset saves |
| Playground/replay | Replace attachments, restore supported media/detail, keep unsupported requests in JSON mode | Browser round trips and visible fallback notices; text-only replacement removes prior media |
| Playground/replay | Normalize Decisions history via existing translator; block redacted-media replay; never automatically send | History endpoint tests and browser replay/no-send checks; original request remains available |
| Diagnostics | Generation-aware log reset | LogBuffer/API tests and browser replacement of old lines |
| Diagnostics | Oldest-first history polling and complete burst draining | 150-record burst with identical timestamps; browser polling followed by older-page retrieval; retained-record recovery tests |
| Diagnostics | Allow X-Request-Id preflight | Authentication/CORS test |
| Diagnostics | Effective shipped calibration and separate selection display | Preset/layout/adapter resolver and route tests; active settings override saved settings; model-card coverage |

Agent packages were committed separately on `basecoat-visual`, `basecoat-replay`, and `basecoat-diagnostics`. Integration also corrected example metadata handling, independent history cursors, visible JSON fallback notices, tablet footer sizing, and toolbar wrapping.

## Final checks

- `pytest tests/test_ui_*.py tests/test_auth.py tests/test_api_compat.py -q`: **226 passed**.
- With `BASAL_E2E=1`, `HF_HUB_OFFLINE=1`, `BASAL_E2E_MODEL=Remek/basal-2.0-mini-MLX-8bit`, `BASAL_E2E_BF16_MODEL=Remek/basal-2.0-mini`, and `BASAL_E2E_LOAD_TIMEOUT=180`:
  `pytest tests/e2e/test_pages.py tests/e2e/test_activity.py tests/e2e/test_visual.py tests/e2e/test_engine231.py::test_browser_errors_health_warnings_and_options -q -rs`: **34 passed**.
- `node --check basal_ui/static/app.js` and `git diff --check`: passed.
- Wheel build: passed. All **nine** production static files match source bytes; HTML asset references resolve; prototypes are excluded.

The browser suite exercised actual downloaded models on this Apple M4 Max using isolated temporary settings/history. History bursts, log restarts, HTTP error responses, and health-warning edge cases use deterministic fixtures; those checks do not establish physical failure behavior. No model downloads were requested or performed.

Final screenshots were inspected after disabling capture-time CSS animations: `/tmp/basal-final-overview.png` and `/tmp/basal-final-mobile.png`. The locally built wheel is `/tmp/basal-final-wheel/basal-2.4.1-py3-none-any.whl`.

## Boundaries

CI, deployment, NVIDIA hardware, and the full hardware release suite were not run. This is local implementation and validation, not a release or user visual sign-off. No remote push or publication was performed.

The original interrupted edits are recoverable in `/tmp/basal-basecoat-checkpoint`; the existing untracked `basal_ui/prototype-components/` remains intact. The three agent worktrees and branches remain available for review.
