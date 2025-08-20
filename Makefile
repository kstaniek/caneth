# Makefile — local dev helpers for caneth
#
# Usage: `make <target>`
#
# Variables you can override:
#   PY      = python executable (default: python3)
#   VENV    = virtualenv dir     (default: .venv)
#   HOST    = device IP          (default: 127.0.0.1)
#   PORT    = device TCP port    (default: 20001)
#
# Examples:
#   make venv dev
#   make lint type test
#   make docs docs-serve
#   make run HOST=172.31.11.67 PORT=20001

# --------------------------- config ---------------------------

PY      ?= python3
VENV    ?= .venv
BIN     := $(VENV)/bin
PYTHON  := $(BIN)/python
PIP     := $(BIN)/pip

HOST    ?= 127.0.0.1
PORT    ?= 20001
PACKAGE := caneth

.PHONY: help venv dev install precommit-install clean dist \
        lint lint-fix fmt format format-check type test test-verbose coverage \
        docs docs-serve docs-open run check ci

# ---------------------------- help ----------------------------

help: ## Show this help
	@awk 'BEGIN {FS":.*##"; printf "\n\033[1mTargets:\033[0m\n"} /^[a-zA-Z0-9_%-]+:.*?##/ { printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

# ------------------------- environment ------------------------

venv: ## Create virtual environment in $(VENV)
	$(PY) -m venv $(VENV)
	$(PIP) install -U pip

dev: venv ## Install package in editable mode + dev tools
	$(PIP) install -e .
	$(PIP) install pytest pytest-asyncio pytest-cov ruff mypy pdoc build pre-commit twine

install: venv ## Install package in editable mode (no dev deps)
	$(PIP) install -e .

twine-check: dist ## Validate built artifacts
	$(BIN)/twine check dist/*

precommit-install: ## Install pre-commit hooks
	$(BIN)/pre-commit install

# ----------------------- quality checks -----------------------

lint: ## Run Ruff lint
	$(BIN)/ruff check

lint-fix: ## Ruff auto-fix + format
	$(BIN)/ruff check --fix
	$(BIN)/ruff format

fmt format: ## Format with Ruff (no changes printed)
	$(BIN)/ruff format

format-check: ## Check formatting without changing files
	$(BIN)/ruff format --check

type: ## Type-check with mypy
	$(BIN)/mypy $(PACKAGE)

test: ## Run tests (quiet)
	$(BIN)/pytest -q

test-verbose: ## Run tests verbose with stdout
	$(BIN)/pytest -vv -s

coverage: dev ## Test with coverage (XML + terminal)
	$(BIN)/pytest --cov=$(PACKAGE) --cov-report=term-missing --cov-report=xml

check: ## Lint + format-check + type + tests
	$(MAKE) lint
	$(MAKE) format-check
	$(MAKE) type
	$(MAKE) test

ci: ## Mimic CI locally (install dev, lint, type, tests with coverage)
	$(MAKE) dev
	$(MAKE) lint
	$(MAKE) format-check
	$(MAKE) type
	$(MAKE) coverage

# --------------------------- docs -----------------------------

docs: ## Build API docs site with pdoc into ./site
	$(BIN)/pdoc -o site $(PACKAGE)

docs-serve: ## Serve docs at http://localhost:8000
	$(PYTHON) -m http.server -d site 8000

docs-open: ## Open docs in default browser (best-effort)
	$(PYTHON) -c "import webbrowser,sys; webbrowser.open('http://localhost:8000') or sys.exit(0)"

# ----------------------- packaging / run ----------------------

dist: ## Build sdist + wheel into ./dist
	$(PYTHON) -m build

run: ## Run REPL quickly: make run HOST=... PORT=...
	$(BIN)/caneth --host $(HOST) --port $(PORT) repl

# ---------------------------- clean ---------------------------

clean: ## Remove caches, builds, docs site, coverage
	rm -rf \
	  __pycache__ */__pycache__ \
	  .pytest_cache .mypy_cache .ruff_cache .pyright .pytype \
	  .coverage coverage.xml htmlcov \
	  build dist *.egg-info .eggs \
	  site docs/_build
