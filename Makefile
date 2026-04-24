.PHONY: install test lint fmt format smoke schema clean help

help:
	@echo "Targets: install test lint fmt smoke schema clean"
	@echo "  install  venv + editable install with dev deps"
	@echo "  test     pytest -v"
	@echo "  lint     ruff + black --check"
	@echo "  fmt      alias for format"
	@echo "  format   ruff --fix + black"
	@echo "  smoke    run agent 5 steps against mock env"
	@echo "  schema   dump JSON schemas"
	@echo "  clean    nuke .venv + caches"

fmt: format

PY ?= python3
VENV ?= .venv
BIN = $(VENV)/bin

$(VENV)/bin/activate:
	$(PY) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip setuptools wheel
	$(BIN)/pip install -e '.[dev]'

install: $(VENV)/bin/activate  ## Create venv and install package with dev deps

test: install  ## Run the full test suite
	$(BIN)/pytest -v

lint: install  ## Run ruff + black --check
	$(BIN)/ruff check src tests
	$(BIN)/black --check src tests

format: install  ## Auto-format with black + ruff --fix
	$(BIN)/ruff check --fix src tests
	$(BIN)/black src tests

smoke: install  ## Run the runner end-to-end against the mock env
	$(BIN)/oyster-agent run \
		--env mock --task "five noops" --provider mock \
		--max-steps 5 --output-dir /tmp/oyster-agent-smoke

schema: install  ## Dump schemas to stdout
	$(BIN)/oyster-agent schema

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache build dist *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
