"""Writer tool implementations registered with the Talk2View SDK.

Each submodule exposes ``@tool``-decorated Python functions that the SDK
calls (via the ``interrupt → resume`` loop) when the cloud agent decides
to use a tool. Function signatures + Google-style docstrings drive the
schema the LLM sees, so type hints and arg descriptions matter.

The 26 tools mirror ``Talk2View-Word/src/taskpane/tools/*.ts`` — see the
mapping table in the project plan and ``CLAUDE.md`` for the
TypeScript-to-UNO equivalence.

Phase C ships two proof tools (``get_document``, ``insert_content``).
Phase D adds the remaining 24 across reading / writing / formatting /
search / structure / commenting modules.
"""

from __future__ import annotations

from typing import Callable, List

from talk2view_writer.tools.reading import TOOLS as _READING_TOOLS
from talk2view_writer.tools.writing import TOOLS as _WRITING_TOOLS


def all_tools() -> List[Callable]:
    """Return every tool function across every sub-module.

    The list is freshly assembled on each call so tests can monkey-patch
    individual modules without poisoning the others.
    """
    return [
        *_READING_TOOLS,
        *_WRITING_TOOLS,
    ]


__all__ = ["all_tools"]
