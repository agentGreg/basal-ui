.DEFAULT_GOAL := help
PYTHON ?= .venv/bin/python
UV ?= uv
REF ?=
ARGS ?=

.PHONY: help install engine lint test e2e run
help:
	@printf '%s\n' \
	  'make install    uv sync: the venv with the dev group (engine pinned by [tool.uv.sources]) and the mcp extra' \
	  'make engine     Switch the installed engine: REF=<release tag> installs the tag archive, REF=<branch|sha> the fork ref' \
	  'make lint       Run ruff on all files' \
	  'make test       Run the unit tests (tests/, not tests/e2e)' \
	  'make e2e        Run the end-to-end gate with real models (BASAL_E2E=1; docs/E2E.md)' \
	  'make run        Start basal-ui' \
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

lint:
	"$$UV" run --no-sync ruff check .

test:
	"$$UV" run --no-sync pytest -q --ignore=tests/e2e $(ARGS)

e2e:
	BASAL_E2E=1 "$$UV" run --no-sync pytest tests/e2e $(ARGS)

run:
	"$$PYTHON" -m basal_ui $(ARGS)
