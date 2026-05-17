"""Writing tools — insert content into the active Writer document.

Phase C ships only ``insert_content`` as a proof of the tool execution
loop. Phase D adds ``insert_table``, ``insert_break``, ``manage_list``.
"""

from __future__ import annotations

import logging

from talk2view import tool  # type: ignore[import-not-found]

from talk2view_writer.extension import get_extension_or_raise
from talk2view_writer.tools._base import (
    get_writer_document,
    ui_thread_tool,
)

logger = logging.getLogger(__name__)


@tool
@ui_thread_tool
def insert_content(content: str) -> str:
    """Insert plain-text content at the end of the active Writer document.

    A simple append-to-end operation. Does not interpret Markdown,
    apply styles, or position relative to existing content — Phase D
    adds more capable variants (insert at heading, insert at cursor,
    insert formatted block, etc.).

    Args:
        content: The text to insert. May contain newlines (they
            translate to paragraph breaks in the document).

    Returns:
        Confirmation message naming the number of characters inserted.

    Raises:
        WriterDocumentRequired: If no Writer document is active.
        ValueError: If ``content`` is empty.
    """
    if not content:
        raise ValueError("Cannot insert empty content")
    ext = get_extension_or_raise()
    doc = get_writer_document(ext.ctx)
    text = doc.getText()
    cursor = text.createTextCursorByRange(text.getEnd())
    text.insertString(cursor, content, False)
    logger.info("insert_content inserted %d characters", len(content))
    return f"Inserted {len(content)} characters at end of document"


TOOLS = [insert_content]
