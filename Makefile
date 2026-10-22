# Makefile for myetl.
#
# Every Python tool runs through `uv run --locked`: the environment is synced
# from uv.lock first, and the run fails if uv.lock is out of date with
# pyproject.toml (run `make lock` after changing dependencies).
#
# Docker, SAST and deploy recipes are added in later steps.

SHELL       := /usr/bin/env bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help

UV_RUN := uv run --locked

# pycodestyle (the PEP 8 reference checker) does not read pyproject.toml,
# so its options live here. Limits match [tool.ruff]: 79 for code, 72 for
# docstrings and comments. The ignore list is pycodestyle's default with
# two changes:
#   - W504 (line break after a binary operator) is checked: PEP 8 recommends
#     breaking before the operator, which is what `ruff format` produces.
#   - E203 (whitespace before ':') is ignored: PEP 8 asks for equal spacing
#     around ':' in slices with complex expressions, which E203 rejects.
PYCODESTYLE_ARGS := --max-line-length=79 --max-doc-length=72 \
	--ignore=E121,E123,E126,E203,E226,E24,E704,W503
PY_SOURCES := src tests

.PHONY: help sync lock format format-check lint typecheck test coverage check clean

help: ## List available recipes
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "} {printf "  %-14s %s\n", $$1, $$2}'

sync: ## Create/update .venv exactly from uv.lock (incl. dev group)
	uv sync --locked

lock: ## Re-resolve uv.lock after editing dependencies in pyproject.toml
	uv lock

format: ## Format code in place (ruff format + import sorting)
	$(UV_RUN) ruff check --select I --fix .
	$(UV_RUN) ruff format .

format-check: ## Fail if any file would be reformatted (CI)
	$(UV_RUN) ruff format --check .

lint: ## Run the linters: ruff, then pycodestyle (PEP 8)
	$(UV_RUN) ruff check .
	$(UV_RUN) pycodestyle $(PYCODESTYLE_ARGS) $(PY_SOURCES)

typecheck: ## Run mypy in strict mode
	$(UV_RUN) mypy

test: ## Run the test suite
	$(UV_RUN) pytest

coverage: ## Run the test suite with branch coverage
	$(UV_RUN) pytest --cov --cov-report=term-missing

check: format-check lint typecheck test ## Run all quality gates (same set as CI)

clean: ## Remove caches and build output (keeps .venv)
	rm -rf .mypy_cache .pytest_cache .ruff_cache .coverage htmlcov build dist
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
