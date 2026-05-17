"""Load the bundled Writer system prompt.

The cloud engine has its own authoritative copy of the system prompt
tied to the partner key, but we also bundle ``SYSTEM_PROMPT.md`` into
the ``.oxt`` so:

  1. The extension can self-document — anyone unzipping the package
     can see exactly what skill catalog + delta notes the Writer build
     was developed against.
  2. The SDK's per-session ``system_prompt`` argument can override
     the engine default when needed (e.g. for QA against the local
     copy without re-uploading to the engine).

Resolution order:

  1. ``$TALK2VIEW_WRITER_SYSTEM_PROMPT`` — explicit override (useful
     for development / CI).
  2. ``<extension-root>/resources/SYSTEM_PROMPT.md`` — production
     install path (set by the Makefile's ``build`` target).
  3. Repo-root ``SYSTEM_PROMPT.md`` — development checkout walk-up
     from ``src/talk2view_writer/``.

If none of the three resolve, returns ``None``. Callers MUST treat
``None`` as "fall back to the engine's server-side prompt" rather
than raising — the engine has its own copy and the chat session
remains functional without a client-supplied override.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)


_PACKAGE_DIR = Path(__file__).resolve().parent


def _candidate_paths() -> list[Path]:
    candidates: list[Path] = []
    env = os.environ.get("TALK2VIEW_WRITER_SYSTEM_PROMPT")
    if env:
        candidates.append(Path(env))
    # Production install path: the extension stages this file at
    # ``<install-root>/pythonpath/talk2view_writer/system_prompt.py``
    # and the resources at ``<install-root>/resources/``. From
    # ``_PACKAGE_DIR = .../pythonpath/talk2view_writer``, walking up
    # two parents lands on ``<install-root>``.
    install_root = _PACKAGE_DIR.parent.parent
    candidates.append(install_root / "resources" / "SYSTEM_PROMPT.md")
    # Development checkout path: the file lives at
    # ``<repo>/src/talk2view_writer/system_prompt.py``; walking up two
    # parents lands on the repo root (``Talk2View-Writer/``).
    repo_root = _PACKAGE_DIR.parent.parent
    candidates.append(repo_root / "SYSTEM_PROMPT.md")
    return candidates


@lru_cache(maxsize=1)
def load_system_prompt() -> str | None:
    """Return the bundled system prompt text, or ``None`` if not found.

    Result is cached for the lifetime of the process; the file does not
    change at runtime.
    """
    for path in _candidate_paths():
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            logger.info("Loaded system prompt from %s (%d chars)", path, len(text))
            return text
    logger.warning(
        "SYSTEM_PROMPT.md not found in any candidate location. "
        "The engine's server-side prompt will be used instead."
    )
    return None


def reset_cache() -> None:
    """Clear the cached prompt — primarily for tests."""
    load_system_prompt.cache_clear()
