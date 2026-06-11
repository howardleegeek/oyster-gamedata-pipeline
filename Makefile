.PHONY: install test coverage lint fmt format smoke schema clean help precommit-install precommit-run sample sample-lint

help:
	@echo "Targets: install test coverage lint fmt smoke schema precommit-install precommit-run clean sample sample-lint"
	@echo "  install            venv + editable install with dev deps"
	@echo "  test               pytest -v"
	@echo "  coverage           pytest with html + term coverage (htmlcov/)"
	@echo "  lint               ruff + black --check"
	@echo "  fmt                alias for format"
	@echo "  format             ruff --fix + black"
	@echo "  precommit-install  install git hooks from .pre-commit-config.yaml"
	@echo "  precommit-run      run all pre-commit hooks on every file"
	@echo "  sample             regenerate samples/buyer-spec-v1-rc1.tar.gz (38/38 lint)"
	@echo "  sample-lint        regenerate and lint the sample tarball"
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

sample:  ## Regenerate samples/buyer-spec-v1-rc1.tar.gz (38/38 lint compliant)
	$(PY) bin/sample_tarball_builder.py --output samples/buyer-spec-v1-rc1.tar.gz

sample-lint: sample  ## Regenerate and lint the sample tarball, expect 38/38
	@rm -rf /tmp/lint-target-a
	@mkdir -p /tmp/lint-target-a
	@tar -xzf samples/buyer-spec-v1-rc1.tar.gz -C /tmp/lint-target-a
	@$(PY) bin/lint_v3_prd_grounded.py /tmp/lint-target-a -o /tmp/lint_sample.json
	@$(PY) -c "import json; d=json.load(open('/tmp/lint_sample.json')); s=d['summary']; print('SAMPLE:', s); assert s['failed']==0, f'lint failed: {s}'"
