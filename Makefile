# Talk2View-Writer Makefile
# Develop, test, and package the LibreOffice Writer extension.

.PHONY: all install dev lint lint-fix format format-check test test-unit test-synthetic test-mock-chat test-integration test-gui-smoke test-live coverage typecheck security vendor-wheels build package install-oxt clean help

BUILD_DIR := build
DIST_DIR  := dist
EXT_NAME  := Talk2ViewWriter
OXT       := $(DIST_DIR)/$(EXT_NAME).oxt

# Pure-Python runtime dependencies bundled as source under
# pythonpath/. Each entry MUST be installable in the dev venv.
# pydantic_core is INTENTIONALLY ABSENT — it ships as per-platform
# wheels under vendor/extracted/ (see ADR-0023).
PY_RUNTIME_DEPS := talk2view httpx httpcore h11 certifi idna anyio pydantic typing_extensions annotated_types typing_inspection webview bottle proxy_tools

# Optional pure-Python deps — bundled if present, skipped if not.
# anyio runs fine without sniffio (it has a `try: import sniffio`
# fallback path); we still bundle it when available so anyio's
# async-backend detection works.
PY_OPTIONAL_DEPS := sniffio

all: lint test-unit

install:
	uv sync

dev:
	uv sync --dev

lint:
	uv run ruff check src/ tests/

lint-fix:
	uv run ruff check --fix src/ tests/

format:
	uv run ruff format src/ tests/

format-check:
	uv run ruff format --check src/ tests/

test:
	uv run pytest

# Local default: every test that does NOT need a real soffice or live
# engine. Includes unit (helpers), synthetic (real tool bodies vs an
# in-process Writer doc), and mock_chat (SDK round-trip vs canned SSE).
# Fast — runs the whole tool surface + chat flow in well under a second.
test-unit:
	uv run pytest -m "unit or synthetic or mock_chat"

# Just the in-process tool-body tests against the synthetic UNO model.
test-synthetic:
	uv run pytest -m synthetic

# Just the SDK round-trip tests against the mock engine (httpx mocks).
test-mock-chat:
	uv run pytest -m mock_chat

test-integration:
	uv run pytest -m integration

# Linux-only GUI smoke test via dogtail/AT-SPI. Requires
# `uv sync --group gui-smoke` first + an X display (xvfb-run or a
# real session).
test-gui-smoke:
	xvfb-run -a uv run pytest -m gui_smoke

# Live chat E2E against engine.talk2view.com. Requires
# T2V_TEST_USER_EMAIL + T2V_TEST_USER_PASSWORD env vars (or it
# skips with a clear message).
test-live:
	uv run pytest -m live

coverage:
	uv run pytest --cov=src/talk2view_writer --cov-report=html --cov-report=term

typecheck:
	uv run mypy src/

security:
	uv run bandit -c pyproject.toml -r src/

# Refresh the cross-platform pydantic_core wheel matrix. Run once per
# pydantic_core version bump; populates vendor/wheels/ +
# vendor/extracted/. Gitignored — re-run after `make clean` or on a
# fresh checkout. See ADR-0023 + scripts/vendor_wheels.py.
vendor-wheels:
	@echo "Downloading + extracting pydantic_core wheel matrix..."
	@uv run python scripts/vendor_wheels.py

build-web:
	@echo "Building Talk2View-Writer web bundle (React + Talk2View SDK)..."
	@if [ ! -d src/web/node_modules ]; then \
		echo "  installing npm dependencies (one-off)..."; \
		cd src/web && npm install --silent; \
	fi
	@cd src/web && npm run build --silent
	@echo "Web bundle built: src/web/dist/"
	@ls -la src/web/dist/ | tail -n +2

# Build the extension into BUILD_DIR/EXT_NAME/
build: lint test-unit build-web
	@echo "Building Talk2View-Writer extension..."
	@if [ ! -d vendor/extracted ]; then \
		echo "ERROR: vendor/extracted/ missing. Run 'make vendor-wheels' first."; \
		exit 1; \
	fi
	@mkdir -p $(BUILD_DIR)/$(EXT_NAME)/META-INF
	@mkdir -p $(BUILD_DIR)/$(EXT_NAME)/description
	@cp extension/description.xml         $(BUILD_DIR)/$(EXT_NAME)/
	@cp extension/talk2view_writer.py     $(BUILD_DIR)/$(EXT_NAME)/
	@cp extension/Addons.xcu              $(BUILD_DIR)/$(EXT_NAME)/
	@cp extension/ProtocolHandler.xcu     $(BUILD_DIR)/$(EXT_NAME)/
	@cp extension/META-INF/manifest.xml   $(BUILD_DIR)/$(EXT_NAME)/META-INF/
	@cp extension/description/description_en.txt $(BUILD_DIR)/$(EXT_NAME)/description/
	@mkdir -p $(BUILD_DIR)/$(EXT_NAME)/registration
	@cp LICENSE $(BUILD_DIR)/$(EXT_NAME)/registration/LICENSE
	@mkdir -p $(BUILD_DIR)/$(EXT_NAME)/pythonpath
	@cp -r src/talk2view_writer $(BUILD_DIR)/$(EXT_NAME)/pythonpath/
	@mkdir -p $(BUILD_DIR)/$(EXT_NAME)/resources
	@cp SYSTEM_PROMPT.md $(BUILD_DIR)/$(EXT_NAME)/resources/
	@cp -r skills $(BUILD_DIR)/$(EXT_NAME)/resources/
	@cp -r extension/icons $(BUILD_DIR)/$(EXT_NAME)/icons
	@cp -r extension/panels $(BUILD_DIR)/$(EXT_NAME)/panels
	@# Web bundle: copy the webpack output from src/web/dist/ rather
	@# than the source HTML smoke-test in extension/web/. build-web
	@# is a prerequisite of `build` so the dist/ dir is up to date.
	@mkdir -p $(BUILD_DIR)/$(EXT_NAME)/web
	@cp -r src/web/dist/* $(BUILD_DIR)/$(EXT_NAME)/web/
	@echo "Bundling pure-Python runtime dependencies..."
	@# Resolve each dep via the venv's Python so editable installs
	@# (.pth indirection — e.g. our local talk2view SDK) and single-
	@# file modules (typing_extensions.py) both copy correctly.
	@for pkg in $(PY_RUNTIME_DEPS); do \
		src=$$(uv run python -c "import importlib.util, sys; s = importlib.util.find_spec('$$pkg'); print(s.submodule_search_locations[0] if (s and s.submodule_search_locations) else (s.origin if s else ''), end='')" 2>/dev/null); \
		if [ -z "$$src" ]; then \
			echo "  ERROR: required dep '$$pkg' not installed in venv — run 'make dev'"; \
			exit 1; \
		elif [ -d "$$src" ]; then \
			cp -r "$$src" $(BUILD_DIR)/$(EXT_NAME)/pythonpath/; \
		else \
			cp "$$src" $(BUILD_DIR)/$(EXT_NAME)/pythonpath/; \
		fi; \
	done
	@for pkg in $(PY_OPTIONAL_DEPS); do \
		src=$$(uv run python -c "import importlib.util, sys; s = importlib.util.find_spec('$$pkg'); print(s.submodule_search_locations[0] if (s and s.submodule_search_locations) else (s.origin if s else ''), end='')" 2>/dev/null); \
		if [ -z "$$src" ]; then \
			echo "  (optional dep '$$pkg' not in venv — skipped)"; \
		elif [ -d "$$src" ]; then \
			cp -r "$$src" $(BUILD_DIR)/$(EXT_NAME)/pythonpath/; \
		else \
			cp "$$src" $(BUILD_DIR)/$(EXT_NAME)/pythonpath/; \
		fi; \
	done
	@echo "Bundling pydantic_core wheels for the cross-platform matrix..."
	@mkdir -p $(BUILD_DIR)/$(EXT_NAME)/pythonpath/_vendored_wheels
	@cp -r vendor/extracted/* $(BUILD_DIR)/$(EXT_NAME)/pythonpath/_vendored_wheels/
	@find $(BUILD_DIR)/$(EXT_NAME)/pythonpath -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@echo "Build complete: $(BUILD_DIR)/$(EXT_NAME)/"
	@du -sh $(BUILD_DIR)/$(EXT_NAME)/pythonpath/_vendored_wheels

package: build
	@echo "Packaging $(OXT)..."
	@mkdir -p $(DIST_DIR)
	@rm -f $(OXT)
	@cd $(BUILD_DIR)/$(EXT_NAME) && zip -qr ../../$(OXT) . -x "*.pyc" -x "__pycache__/*"
	@echo "Package complete: $(OXT) ($$(du -h $(OXT) | cut -f1))"

install-oxt: package
	@# unopkg mutates the user-profile extension deployment registry.
	@# Doing that while soffice is alive — even with --force — risks
	@# leaving the registry in a half-committed state that wipes the
	@# entire user extension set (Talk2View, Zotero, every other
	@# user-installed .oxt) silently. The cache files get orphaned
	@# from the pmap and "unopkg list" reports <none> on next launch.
	@# Refuse to run unless soffice is fully closed.
	@if pgrep -x soffice.bin >/dev/null 2>&1 || pgrep -x soffice >/dev/null 2>&1; then \
		echo "ERROR: soffice is running. Close all LibreOffice windows and re-run."; \
		echo "       Installing while soffice is alive can corrupt the user extension"; \
		echo "       registry and wipe other extensions (e.g. Zotero)."; \
		echo ""; \
		echo "       Running soffice processes:"; \
		pgrep -af "soffice" | sed 's/^/         /'; \
		exit 1; \
	fi
	@echo "Installing extension into user's LibreOffice profile..."
	@unopkg add --force --suppress-license $(OXT)
	@echo "Installed. Start LibreOffice Writer to see the Talk2View sidebar."

clean:
	rm -rf $(BUILD_DIR)/ $(DIST_DIR)/ .pytest_cache/ .ruff_cache/ .mypy_cache/ htmlcov/ .coverage
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

help:
	@echo "Talk2View-Writer development targets:"
	@echo "  install        Install runtime deps via uv"
	@echo "  dev            Install dev deps"
	@echo "  lint / format  Ruff checks"
	@echo "  test           Run pytest (all markers)"
	@echo "  test-unit      Fast tests, no LibreOffice needed"
	@echo "  test-integration  UNO-socket tests (needs running soffice on :2002)"
	@echo "  test-gui-smoke    Linux-only AT-SPI/dogtail tests"
	@echo "  test-live      Live chat against engine.talk2view.com (needs T2V_TEST_USER_* env)"
	@echo "  vendor-wheels  Refresh cross-platform pydantic_core matrix"
	@echo "  build          Stage extension into $(BUILD_DIR)/"
	@echo "  package        Create $(OXT)"
	@echo "  install-oxt    Install .oxt into LibreOffice user profile"
	@echo "  clean          Remove build artifacts (vendor/ persists)"
