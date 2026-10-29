# Contributing to basal-ui

## Dev setup

```bash
make install                 # uv sync: dev group (engine pinned by [tool.uv.sources]) + mcp extra
make engine-pin              # the pinned engine release (basal_ui/engine.py PINNED_RELEASE)
make engine REF=v2.4.2       # later: another release tag
make engine REF=feat/engine-adapter-upstream   # or any ref of the fork pawelkiszczak/basal
make lint                    # ruff check + ruff format --check
make format                  # ruff format
make test                    # the unit tests (tests/, not tests/e2e)
make e2e                     # BASAL_E2E=1: real models on real hardware; docs/E2E.md
make run                     # start basal-ui
make screenshots             # refresh docs/images (see "Screenshots")
```

`make install` syncs the environment from the lockfile; the dev group's `[tool.uv.sources]` pin decides which engine
release it installs. `make engine REF=…` switches to another release-tag archive or a fork ref for a trial — a later
`make install` restores the pinned tag. An engine outside `SUPPORTED` starts only with
`BASAL_UI_ENGINE_UNCHECKED=1` (see the README, "Experimental engine").

## Code style

- Python: 120 columns; Ruff (`F,I,B,UP,SIM`, `[tool.ruff.lint]` in `pyproject.toml`) and `ruff format`; run
  `make lint` / `make format`. `uv run --no-sync pre-commit install` (optional; CI runs the same checks). Follow the
  surrounding code: docstrings that say what a function returns and why, no speculative abstractions.
- Frontend: plain JavaScript and CSS in `basal_ui/static/` (no build step). Every UI string is English in the code and
  passed through `t()` / `tp()`; its Polish translation goes into `basal_ui/static/i18n.js`.
  `tests/test_ui_i18n.py` fails on a string without one.
- Server errors stay English; translatable explanations carry a message code alongside the English text.

## Tests

- **Unit tests** (`make test`, `tests/test_*.py`): every route and behaviour with stubbed `basal-serve` children; fast,
  no models, run on every CI job. Add one for each behaviour a user or API client can observe: answers, refusals,
  boundaries, state transitions.
- **End-to-end gate** (`make e2e`, `tests/e2e/`, [docs/E2E.md](docs/E2E.md)): a real basal-ui, real models, the pages in
  Chromium. Add to it when the behaviour depends on a real engine or a real browser; a test that needs hardware the
  machine lacks skips with the reason.
- No tests of wiring (that a function calls another, that a field is copied), of wording, or of mocks echoing
  themselves.

## Branches and CI

| Branch | Role | Changes arrive by |
|---|---|---|
| `dev` | integration: every feature and fix lands here first | a pull request from a topic branch |
| `stage` | release candidate: what the next release will ship, where the e2e gate runs | a pull request `dev` → `stage` |
| `main` | released code; `vX.Y.Z` tags are cut from it | a pull request `stage` → `main` |

A fix made on `stage` (found while testing a release candidate) goes back with a pull request `stage` → `dev`, so
`dev` never loses it.

CI (`.github/workflows/ci.yml`: Ruff check and format, the unit tests, Ubuntu and macOS × Python 3.10 and 3.13 ×
every tested engine release) runs on every push to `dev`, `stage` and `main` and on every pull request into them, in
either direction: `dev` → `stage`, `stage` → `dev`, `stage` → `main`. Its last job, `ci-ok`, passes only when every
matrix job passed; it is the one check branch protection requires (the matrix job names change with the engine
releases). CD is `publish.yml`: a `vX.Y.Z` tag on `main` publishes to PyPI and creates the GitHub release (see
Releasing).

Branch protection on GitHub enforces this:

| | `dev` | `stage` | `main` |
|---|---|---|---|
| changes only through a pull request with `ci-ok` green | yes | yes | yes |
| the pull request must be up to date with the branch | no | yes | yes |
| applies to admins too | no (the maintainer may push a fix directly) | yes | yes |
| force pushes and deletion | refused | refused | refused |

No review approval is required: the project has one maintainer, who cannot approve their own pull requests.

## Pull requests

One topic per PR, opened against `dev`. The template (`.github/PULL_REQUEST_TEMPLATE.md`) asks for what and why,
whether the engine range is affected, the tests run (`make lint`, `make test`, and `make e2e` with the hardware for
changes that touch loading, serving or the pages), refreshed screenshots for visible changes and a
`docs/WHATS_NEW.md` bullet.

## Screenshots

The README's screenshots and tour GIF (`docs/images/`) come from `make screenshots` (`scripts/screenshots.py`): it
starts basal-ui on a fresh settings directory, loads `Remek/basal-2.0-mini`, captures five views in Chromium and
records the tour. Refresh them after a UI change to a view the README shows. It needs `Remek/basal-2.0-mini` on disk,
Chromium for Playwright (`uv run --extra e2e playwright install chromium`) and `ffmpeg` on `PATH`.

## Engine proposals

basal-ui depends only on upstream engine releases, so every engine change it needs goes upstream, one at a time; the
UI never blocks on a proposal. The pipeline:

1. **Research note and issue.** Copy `docs/research/TEMPLATE.md` to `docs/research/<topic>.md` (use case,
   proposal, measurements) and open an issue on [rkinas/basal](https://github.com/rkinas/basal) stating the problem
   and an **acceptance check** — the measurable criteria that will decide it, agreed before implementation.
2. **One topic branch, one PR.** Cut the branch on the fork [pawelkiszczak/basal](https://github.com/pawelkiszczak/basal)
   from `upstream/main`, never from the fork's `main`: the fork's `main` only mirrors upstream
   (`git pull --ff-only upstream main`) and is never committed to. Exactly one PR per proposal, referencing the
   issue; rebase when the maintainer names the release it is scheduled for.
3. **Ship the UI control behind a probe.** The basal-ui control for the capability lands behind
   `basal_ui.engine.feature("<name>")` and is tried through the experimental engine route
   (`BASAL_UI_ENGINE_UNCHECKED=1`), so users of supported releases are unaffected.
4. **Close the loop.** When the release enters `SUPPORTED` (see "Bumping the engine" below), drop the probe and
   update the research note's **Outcome**.

`docs/adr/0003-separate-repo-on-upstream-engine.md` records why the repo is set up this way.

## Releasing

1. If the engine moves, bump it ("Bumping the engine" below); add a `docs/WHATS_NEW.md` section for the version.
   Both land on `dev` like any change.
2. Open the pull request `dev` → `stage`. On `stage`, run the e2e gate on real hardware (`make e2e`, `docs/E2E.md`)
   and record the run (models, hardware, results) in that pull request; fixes go to `stage` and back to `dev`
   (`stage` → `dev`).
3. Bump `version` in `pyproject.toml` to X.Y.Z on `stage` (`publish.yml` refuses a tag that differs), then open the
   pull request `stage` → `main`.
4. After it merges, tag `vX.Y.Z` on `main` and push the tag; `publish.yml` uploads to PyPI (trusted publishing) and
   creates the GitHub release from the `# X.Y.Z` section of WHATS_NEW.

### Bumping the engine

`basal_ui/engine.py` holds the engine release basal-ui runs on: `TESTED_RELEASES` (every release CI tests; the last
is the pin, `PINNED_RELEASE`) and `SUPPORTED_RANGE`. CI's engine matrix is generated from `TESTED_RELEASES`. To move:

1. Edit `TESTED_RELEASES` and `SUPPORTED_RANGE` in `basal_ui/engine.py`.
2. Edit the `basal` url in `pyproject.toml` `[tool.uv.sources]` (uv cannot read Python) and run `uv lock`.
3. Edit the install lines and the engine badge in `README.md`, and the install line in `docs/E2E.md`.

`make test` (`tests/test_engine_gate.py::test_every_pin_names_the_tested_release`) fails until they all agree.
