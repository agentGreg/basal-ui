# Contributing to basal-ui

## Dev setup

```bash
make install                 # uv sync: dev group (engine pinned to v2.4.1 by [tool.uv.sources]) + mcp extra
make engine REF=v2.4.2       # later: another release tag
make engine REF=feat/engine-adapter-upstream   # or any ref of the fork pawelkiszczak/basal
make lint                    # ruff check
make test                    # the unit tests (tests/, not tests/e2e)
make e2e                     # BASAL_E2E=1: real models on real hardware; docs/E2E.md
make run                     # start basal-ui
```

`make install` syncs the environment from the lockfile; the dev group's `[tool.uv.sources]` pin decides which engine
release it installs. `make engine REF=…` switches to another release-tag archive or a fork ref for a trial — a later
`make install` restores the pinned tag. An engine outside `SUPPORTED` starts only with
`BASAL_UI_ENGINE_UNCHECKED=1` (see the README, "Experimental engine").

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
4. **Close the loop.** When the release enters `SUPPORTED` (bump `basal_ui/engine.py` and the CI engine matrix),
   drop the probe and update the research note's **Outcome**.

`docs/adr/0003-separate-repo-on-upstream-engine.md` records why the repo is set up this way.

## Releasing

1. Bump `SUPPORTED` in `basal_ui/engine.py` and the `engine` matrix in `.github/workflows/ci.yml` (one entry per
   supported release), and add a `docs/WHATS_NEW.md` section for the version.
2. Run the e2e gate on real hardware (`make e2e`, `docs/E2E.md`) and record the run (models, hardware, results) in
   the release PR.
3. Tag `vX.Y.Z`; `publish.yml` builds and uploads to PyPI (trusted publishing) and the GitHub release notes come
   from WHATS_NEW.
