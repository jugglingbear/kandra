# ── Kandra Monorepo ───────────────────────────────────────
# Build, test, and publish kandra + kandra-runtime from one venv.
# Type "make help" (or just "make") to see available targets.
# ──────────────────────────────────────────────────────────

PYTHON       ?= python3
POETRY       ?= poetry
PKG_ROOT     := packages
RUNTIME_DIR  := $(PKG_ROOT)/kandra-runtime
KANDRA_DIR   := $(PKG_ROOT)/kandra
RUNTIME_TESTS:= $(RUNTIME_DIR)/tests
KANDRA_TESTS := $(KANDRA_DIR)/tests
E2E_TESTS    := tests
EXAMPLES_DIR := examples
DIST_DIR     := dist
DOCS_SRC     := docs
DOCS_OUT     := docs/_build/html
SCHEMA_FILE  := schemas/manifest.schema.json
EXAMPLE_MANIFEST ?= $(EXAMPLES_DIR)/pneumatic_bear_poker/manifest.yaml
RELEASE      := $(POETRY) run python3 scripts/release.py

# Default target: print help.
.DEFAULT_GOAL := help

.PHONY: help
help:  ## Show this help message
	@printf "\n\033[1mKandra monorepo — available targets:\033[0m\n"
	@awk 'BEGIN {FS = ":.*?## "} \
		/^##@ / { printf "\n\033[1;38;5;208m%s\033[0m\n", substr($$0, 5); next } \
		/^[a-zA-Z0-9_-]+:.*?## / { printf "  \033[97m%-20s\033[0m %s\n", $$1, $$2 }' \
		$(MAKEFILE_LIST)
	@printf "\n\033[1;38;5;208mCommon workflows\033[0m\n"
	@printf "  make qa                 -> lint + typecheck + tests\n"
	@printf "  make version-patch      -> bump both pkg versions, e.g. 0.1.0 -> 0.1.1\n"
	@printf "  make build-wheels       -> produce dist/*.whl for both packages\n"
	@printf "  make publish-test       -> publish both wheels to test.pypi.org\n"
	@printf "  make publish            -> publish both wheels to pypi.org (asks first)\n\n"

##@ Environment

.PHONY: check
check:  ## Verify required dev tools are installed
	@echo "\033[1mChecking development environment...\033[0m"
	@ok=true; \
	command -v $(PYTHON) >/dev/null 2>&1 \
		&& echo "  ✅ python3   $$($(PYTHON) --version 2>&1 | awk '{print $$2}')" \
		|| { echo "  ❌ python3   not found"; ok=false; }; \
	command -v $(POETRY) >/dev/null 2>&1 \
		&& echo "  ✅ poetry    $$($(POETRY) --version 2>&1 | sed 's/[^0-9.]//g')" \
		|| { echo "  ❌ poetry    not found — install from https://python-poetry.org"; ok=false; }; \
	$$ok || { echo "\n\033[31mEnvironment check failed.\033[0m"; exit 1; }; \
	echo "\n\033[32mAll checks passed.\033[0m"

.PHONY: install
install:  ## Install both packages editable into one shared .venv (+ git hooks)
	@echo "📦 Installing workspace (kandra + kandra-runtime, editable)"
	$(POETRY) install --with docs
	@$(POETRY) run pre-commit install >/dev/null 2>&1 && echo "🪝 pre-commit hook installed" || true

.PHONY: reinstall
reinstall:  ## Wipe the venv and reinstall from scratch
	@echo "🔄 Reinstalling workspace from scratch"
	rm -rf .venv
	$(MAKE) install

##@ Tests

.PHONY: test
test:  ## Run the full fast suite (runtime + generator + e2e)
	@echo "🧪 Running full suite"
	$(POETRY) run pytest -q

.PHONY: test-verbose
test-verbose:  ## Run the full fast suite with verbose output
	@echo "🧪 Running full suite (verbose)"
	$(POETRY) run pytest -v

.PHONY: test-runtime
test-runtime:  ## Run runtime-only tests (no kandra.* imports)
	@echo "🧪 Running runtime tests"
	$(POETRY) run pytest -q $(RUNTIME_TESTS)

.PHONY: test-generator
test-generator:  ## Run generator + CLI tests
	@echo "🧪 Running generator tests"
	$(POETRY) run pytest -q $(KANDRA_TESTS)

.PHONY: test-e2e
test-e2e:  ## Run cross-package end-to-end tests
	@echo "🧪 Running e2e tests"
	$(POETRY) run pytest -q $(E2E_TESTS)

.PHONY: test-integration
test-integration:  ## Run docker-backed integration tests
	@echo "🐳 Running integration tests (requires docker)"
	$(POETRY) run pytest -m integration -v

.PHONY: check-boundary
check-boundary:  ## Verify kandra-runtime never imports kandra.*
	@echo "🔒 Checking runtime/generator import boundary"
	@if grep -rn --include='*.py' -E 'from kandra(\.[a-zA-Z_]|\s+import)|^import kandra(\.|$$|\s)' $(RUNTIME_DIR)/src/ ; then \
		echo "\033[31m❌ runtime leaked an import from the generator package\033[0m"; \
		exit 1; \
	else \
		echo "\033[32m✅ runtime boundary clean\033[0m"; \
	fi

##@ Quality

.PHONY: lint
lint:  ## Run ruff lint across both packages + workspace tests
	@echo "🧹 Running ruff"
	$(POETRY) run ruff check $(PKG_ROOT) $(E2E_TESTS) scripts

.PHONY: typecheck
typecheck:  ## Run mypy in strict mode across both packages
	@echo "🔍 Running mypy"
	$(POETRY) run mypy $(RUNTIME_DIR)/src $(KANDRA_DIR)/src

.PHONY: qa
qa: check-boundary lint typecheck check-schema test  ## Full quality gate
	@echo "\n\033[32m✅ All quality checks passed.\033[0m"

##@ Pre-commit

.PHONY: precommit-install
precommit-install:  ## Install the git pre-commit hook into .git/hooks
	@echo "🪝 Installing pre-commit hook"
	$(POETRY) run pre-commit install

.PHONY: precommit
precommit:  ## Run every pre-commit hook against every file
	@echo "🪝 Running all pre-commit hooks"
	$(POETRY) run pre-commit run --all-files

##@ Example SDK (kandra CLI smoke targets)

.PHONY: schema
schema:  ## Regenerate schemas/manifest.schema.json from the Pydantic models
	@mkdir -p $(dir $(SCHEMA_FILE))
	@$(POETRY) run kandra schema > $(SCHEMA_FILE)
	@echo "📝 Wrote $(SCHEMA_FILE)"

.PHONY: check-schema
check-schema:  ## Fail if schemas/manifest.schema.json is stale
	@echo "🔬 Checking $(SCHEMA_FILE) is up to date"
	@tmp=$$(mktemp); \
	  $(POETRY) run kandra schema > $$tmp; \
	  if ! diff -q $$tmp $(SCHEMA_FILE) >/dev/null 2>&1; then \
	    echo "\033[31m❌ $(SCHEMA_FILE) is stale. Run 'make schema' and commit the result.\033[0m"; \
	    diff -u $(SCHEMA_FILE) $$tmp || true; \
	    rm -f $$tmp; \
	    exit 1; \
	  fi; \
	  rm -f $$tmp; \
	  echo "\033[32m✅ schema is up to date\033[0m"

.PHONY: validate
validate:  ## Validate the example manifest (override with EXAMPLE_MANIFEST=path)
	@echo "📋 Validating $(EXAMPLE_MANIFEST)"
	@$(POETRY) run kandra validate $(EXAMPLE_MANIFEST)

.PHONY: build-examples
build-examples:  ## Generate the example SDK(s) into ./dist (override EXAMPLE_MANIFEST=path)
	@echo "🏗  Building SDK from $(EXAMPLE_MANIFEST)"
	@$(POETRY) run kandra build $(EXAMPLE_MANIFEST) --output-dir $(DIST_DIR) --clean

##@ Documentation

.PHONY: build-docs
build-docs:  ## Build the static Sphinx documentation site
	@echo "👷🏻 Building the docs website"
	$(POETRY) run sphinx-build -b html --keep-going $(DOCS_SRC) $(DOCS_OUT)

.PHONY: build-docs-strict
build-docs-strict:  ## Build the docs with -W (warnings become errors)
	@echo "👷🏻 Building the docs website (strict)"
	$(POETRY) run sphinx-build -b html -W --keep-going $(DOCS_SRC) $(DOCS_OUT)

.PHONY: serve-docs
serve-docs:  ## Live-reload the docs at http://localhost:8000
	@echo "🌐 Running local docs server on http://localhost:8000"
	$(POETRY) run sphinx-autobuild --port 8000 --watch $(PKG_ROOT) $(DOCS_SRC) $(DOCS_OUT)

.PHONY: docs-dev
docs-dev: serve-docs  ## Alias for serve-docs

.PHONY: clean-docs
clean-docs:  ## Remove generated docs artifacts
	@echo "🧼 Cleaning docs build output"
	rm -rf docs/_build

##@ Release / publish

.PHONY: version
version:  ## Show the current package versions
	@echo "kandra-runtime: $$(awk -F'\"' '/^version =/ {print $$2; exit}' $(RUNTIME_DIR)/pyproject.toml)"
	@echo "kandra:         $$(awk -F'\"' '/^version =/ {print $$2; exit}' $(KANDRA_DIR)/pyproject.toml)"

.PHONY: version-patch
version-patch:  ## Bump both pkg versions: 0.1.0 -> 0.1.1
	@$(RELEASE) bump patch
	@$(MAKE) version

.PHONY: version-minor
version-minor:  ## Bump both pkg versions: 0.1.0 -> 0.2.0
	@$(RELEASE) bump minor
	@$(MAKE) version

.PHONY: version-major
version-major:  ## Bump both pkg versions: 0.1.0 -> 1.0.0
	@$(RELEASE) bump major
	@$(MAKE) version

.PHONY: build-wheels
build-wheels:  ## Build sdist+wheel for both packages into dist/
	@echo "📦 Building wheels for both packages"
	@rm -rf $(DIST_DIR)/*.whl $(DIST_DIR)/*.tar.gz 2>/dev/null || true
	@mkdir -p $(DIST_DIR)
	@$(RELEASE) prepare-publish
	@set -e; \
	  ( cd $(RUNTIME_DIR) && POETRY_VIRTUALENVS_CREATE=false $(POETRY) build ) && cp $(RUNTIME_DIR)/dist/* $(DIST_DIR)/; \
	  ( cd $(KANDRA_DIR)  && POETRY_VIRTUALENVS_CREATE=false $(POETRY) build ) && cp $(KANDRA_DIR)/dist/*  $(DIST_DIR)/; \
	  status=$$?; \
	  $(RELEASE) restore-dev; \
	  exit $$status
	@echo "\n\033[32m✅ Wheels in $(DIST_DIR)/\033[0m"
	@ls -1 $(DIST_DIR)/*.whl $(DIST_DIR)/*.tar.gz 2>/dev/null

.PHONY: publish-test
publish-test: build-wheels  ## Publish both wheels to test.pypi.org
	@echo "🚀 Publishing to test.pypi.org"
	@cd $(RUNTIME_DIR) && $(POETRY) publish --repository test-pypi
	@cd $(KANDRA_DIR)   && $(POETRY) publish --repository test-pypi

.PHONY: publish
publish: build-wheels  ## Publish both wheels to PyPI (asks for confirmation)
	@echo "\n\033[33m⚠  About to publish to \033[1mpypi.org\033[0m (production)."
	@$(MAKE) version
	@printf "  Continue? [y/N] " && read ans && [ "$$ans" = "y" ] || { echo "aborted."; exit 1; }
	@cd $(RUNTIME_DIR) && $(POETRY) publish
	@cd $(KANDRA_DIR)   && $(POETRY) publish

##@ Cleanup

.PHONY: clean
clean: clean-docs  ## Remove build artifacts and caches
	@echo "🧼 Cleaning up"
	rm -rf $(DIST_DIR) build *.egg-info .pytest_cache .ruff_cache htmlcov .coverage
	rm -rf $(RUNTIME_DIR)/dist $(KANDRA_DIR)/dist
	find . -type d \( -name .mypy_cache -o -name .pytest_cache -o -name .ruff_cache -o -name __pycache__ \) -prune -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
