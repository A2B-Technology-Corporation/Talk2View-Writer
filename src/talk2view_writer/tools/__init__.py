"""Writer tool implementations registered with the Talk2View SDK.

Each submodule exposes ``@tool``-decorated Python functions that the SDK
calls (via the ``interrupt → resume`` loop) when the cloud agent decides
to use a tool. Function signatures + Google-style docstrings drive the
schema the LLM sees, so type hints and arg descriptions matter.

The 20 tools mirror ``Talk2View-Word/src/taskpane/tools/*.ts`` — see the
mapping table in the project plan and ``CLAUDE.md`` for the
TypeScript-to-UNO equivalence. (The Phase A plan's "26 tools" figure
was a miscount; see ``Phase D Group 1`` commit message for the correction.)

Phase D Group 1 shipped the Reading group (``get_document``,
``get_selection``, ``select_text``). Group 2 ships the Writing group:
``insert_content`` (now enriched to full Word schema),
``insert_table``, ``insert_image``, ``undo_redo``, ``delete_content``,
``edit_table``. Groups 3-6 add the remaining 11 across formatting /
search / structure / commenting.
"""

from __future__ import annotations

from collections.abc import Callable

from talk2view_writer.tools.formatting import TOOLS as _FORMATTING_TOOLS
from talk2view_writer.tools.reading import TOOLS as _READING_TOOLS
from talk2view_writer.tools.search import TOOLS as _SEARCH_TOOLS
from talk2view_writer.tools.structure import TOOLS as _STRUCTURE_TOOLS
from talk2view_writer.tools.writing import TOOLS as _WRITING_TOOLS


def all_tools() -> list[Callable]:
    """Return every tool function across every sub-module.

    The list is freshly assembled on each call so tests can monkey-patch
    individual modules without poisoning the others.
    """
    return [
        *_READING_TOOLS,
        *_WRITING_TOOLS,
        *_FORMATTING_TOOLS,
        *_SEARCH_TOOLS,
        *_STRUCTURE_TOOLS,
    ]


__all__ = ["all_tools"]
