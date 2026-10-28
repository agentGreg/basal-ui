.DEFAULT_GOAL := help
PYTHON ?= .venv/bin/python
UV ?= uv
REF ?=
ARGS ?=
export PYTHON UV REF  # recipes read them as shell variables ("$$UV"), which quotes paths with spaces

.PHONY: help install engine engine-pin lint format test e2e run screenshots
help:
	@printf '%s\n' \
	  'make install    uv sync: the venv with the dev group (engine pinned by [tool.uv.sources]) and the mcp extra' \
	  'make engine     Switch the installed engine: REF=<release tag> installs the tag archive, REF=<branch|sha> the fork ref' \
	  'make engine-pin Print the engine release basal-ui pins (basal_ui/engine.py PINNED_RELEASE)' \
	  'make lint       Run ruff check and ruff format --check on all files' \
	  'make format     Reformat all files with ruff format' \
	  'make test       Run the unit tests (tests/, not tests/e2e)' \
	  'make e2e        Run the end-to-end gate with real models (BASAL_E2E=1; docs/E2E.md)' \
	  'make run        Start basal-ui' \
	  'make screenshots Refresh docs/images (README screenshots, tour GIF): basal-2.0-mini on disk, Chromium, ffmpeg' \
	  'Variables: PYTHON UV REF ARGS' \
	  'ARGS appends pytest or basal-ui options (shell quoting applies).' \
	  'A later make install restores the engine pinned by the dev group over a make engine switch.'

install:
	"$$UV" sync --group dev --extra mcp

engine:
	@test -n "$$REF" || { echo 'engine requires REF=<release tag | fork branch | sha>' >&2; exit 1; }
	@if printf '%s' "$$REF" | grep -qE '^v[0-9]'; then \
	  "$$UV" pip install --reinstall-package basal "basal @ https://github.com/rkinas/basal/archive/refs/tags/$$REF.tar.gz"; \
	else \
	  "$$UV" pip install --reinstall-package basal "basal @ git+https://github.com/pawelkiszczak/basal@$$REF"; \
	fi

engine-pin:
	@"$$PYTHON" -c "from basal_ui.engine import PINNED_RELEASE; print(PINNED_RELEASE)"

lint:
	"$$UV" run --no-sync ruff check . && "$$UV" run --no-sync ruff format --check .

format:
	"$$UV" run --no-sync ruff format .

test:
	"$$UV" run --no-sync pytest -q --ignore=tests/e2e $(ARGS)

e2e:
	BASAL_E2E=1 "$$UV" run --no-sync pytest tests/e2e $(ARGS)

run:
	"$$PYTHON" -m basal_ui $(ARGS)

screenshots:
	"$$UV" run --extra e2e python scripts/screenshots.py
