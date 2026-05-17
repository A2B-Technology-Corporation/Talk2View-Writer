# Talk2View-Writer Makefile
# Develop, test, and package the LibreOffice Writer extension.

.PHONY: all install dev lint lint-fix format format-check test test-unit test-integration coverage typecheck security build package install-oxt clean help

BUILD_DIR := build
DIST_DIR  := dist
EXT_NAME  := Talk2ViewWriter
OXT       := $(DIST_DIR)/$(EXT_NAME).oxt

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

test-unit:
	uv run pytest -m unit

test-integration:
	uv run pytest -m integration

coverage:
	uv run pytest --cov=src/talk2view_writer --cov-report=html --cov-report=term

typecheck:
	uv run mypy src/

security:
	uv run bandit -c pyproject.toml -r src/

# Build the extension into BUILD_DIR/EXT_NAME/
build: lint test-unit
	@echo "Building Talk2View-Writer extension..."
	@mkdir -p $(BUILD_DIR)/$(EXT_NAME)/META-INF
	@mkdir -p $(BUILD_DIR)/$(EXT_NAME)/description
	@cp extension/description.xml         $(BUILD_DIR)/$(EXT_NAME)/
	@cp extension/talk2view_writer.py     $(BUILD_DIR)/$(EXT_NAME)/
	@cp extension/Addons.xcu              $(BUILD_DIR)/$(EXT_NAME)/
	@cp extension/Sidebar.xcu             $(BUILD_DIR)/$(EXT_NAME)/
	@cp extension/META-INF/manifest.xml   $(BUILD_DIR)/$(EXT_NAME)/META-INF/
	@cp extension/description/description_en.txt $(BUILD_DIR)/$(EXT_NAME)/description/
	@mkdir -p $(BUILD_DIR)/$(EXT_NAME)/pythonpath
	@cp -r src/talk2view_writer $(BUILD_DIR)/$(EXT_NAME)/pythonpath/
	@mkdir -p $(BUILD_DIR)/$(EXT_NAME)/resources
	@cp SYSTEM_PROMPT.md $(BUILD_DIR)/$(EXT_NAME)/resources/
	@cp -r skills $(BUILD_DIR)/$(EXT_NAME)/resources/
	@echo "Bundling talk2view SDK and httpx..."
	@for pkg in talk2view httpx httpcore h11 certifi sniffio idna anyio pydantic pydantic_core typing_extensions annotated_types; do \
		src=$$(find .venv -path "*/site-packages/$$pkg" -type d -prune | head -1); \
		[ -n "$$src" ] && cp -r "$$src" $(BUILD_DIR)/$(EXT_NAME)/pythonpath/ || echo "  (skipped $$pkg — not found)"; \
	done
	@find $(BUILD_DIR)/$(EXT_NAME)/pythonpath -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@echo "Build complete: $(BUILD_DIR)/$(EXT_NAME)/"

package: build
	@echo "Packaging $(OXT)..."
	@mkdir -p $(DIST_DIR)
	@cd $(BUILD_DIR)/$(EXT_NAME) && zip -r ../../$(OXT) . -x "*.pyc" -x "__pycache__/*"
	@echo "Package complete: $(OXT)"

install-oxt: package
	@echo "Installing extension into user's LibreOffice profile..."
	@unopkg add --force $(OXT)
	@echo "Installed. Restart LibreOffice Writer to see the Talk2View sidebar."

clean:
	rm -rf $(BUILD_DIR)/ $(DIST_DIR)/ .pytest_cache/ .ruff_cache/ .mypy_cache/ htmlcov/ .coverage
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

help:
	@echo "Talk2View-Writer development targets:"
	@echo "  install        Install runtime deps via uv"
	@echo "  dev            Install dev deps"
	@echo "  lint / format  Ruff checks"
	@echo "  test           Run pytest"
	@echo "  build          Stage extension into $(BUILD_DIR)/"
	@echo "  package        Create $(OXT)"
	@echo "  install-oxt    Install .oxt into LibreOffice user profile"
	@echo "  clean          Remove build artifacts"
