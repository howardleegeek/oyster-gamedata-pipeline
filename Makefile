.PHONY: install test coverage lint fmt format smoke schema clean help precommit-install precommit-run

help:
	@echo "Targets: install test coverage lint fmt smoke schema precommit-install precommit-run clean"
	@echo "  install            venv + editable install with dev deps"
	@echo "  test               pytest -v"
	@echo "  coverage           pytest with html + term coverage (htmlcov/)"
	@echo "  lint               ruff + black --check"
	@echo "  fmt                alias for format"
	@echo "  format             ruff --fix + black"
	@echo "  precommit-install  install git hooks from .pre-commit-config.yaml"
	@echo "  precommit-run      run all pre-commit hooks on every file"
	@echo "  smoke              run agent 5 steps against mock env"
	@echo "  schema             dump JSON schemas"
	@echo "  clean              nuke .venv + caches"

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

coverage: install  ## Run tests with html + term coverage (htmlcov/)
	$(BIN)/pytest \
		--cov=src/oyster_agent_runner \
		--cov-report=html \
		--cov-report=term-missing
	@echo ""
	@echo "HTML report: htmlcov/index.html"

precommit-install: install  ## Install git hooks from .pre-commit-config.yaml
	$(BIN)/pip install pre-commit
	$(BIN)/pre-commit install

precommit-run: install  ## Run all pre-commit hooks on every file
	$(BIN)/pre-commit run --all-files

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
