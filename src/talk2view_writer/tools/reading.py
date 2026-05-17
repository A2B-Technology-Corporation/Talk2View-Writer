"""Reading tools — extract content from the active Writer document.

Phase C ships only ``get_document`` as a proof of the tool execution
loop. Phase D adds ``get_comments`` and ``list_tables`` per the tool
mapping in ``docs/adrs/0013-skill-and-prompt-copy-from-word.md``.
"""

from __future__ import annotations

import logging
from typing import List

from talk2view import tool  # type: ignore[import-not-found]

from talk2view_writer.extension import get_extension_or_raise
from talk2view_writer.tools._base import (
    get_writer_document,
    ui_thread_tool,
)

logger = logging.getLogger(__name__)


@tool
@ui_thread_tool
def get_document() -> str:
    """Return the entire active Writer document as plain text.

    Iterates the document's paragraphs in order, joining them with
    newline characters. Tables, headers, footers, footnotes, and
    annotations are *not* included — Phase D adds dedicated tools for
    those (``list_tables``, ``get_comments``, etc.).

    Returns:
        The document text. Empty string if the document has no
        paragraphs.

    Raises:
        WriterDocumentRequired: If no Writer document is active.
    """
    ext = get_extension_or_raise()
    doc = get_writer_document(ext.ctx)
    text = doc.getText()
    parts: List[str] = []
    enum = text.createEnumeration()
    while enum.hasMoreElements():
        element = enum.nextElement()
        if element.supportsService("com.sun.star.text.Paragraph"):
            parts.append(element.getString())
        elif element.supportsService("com.sun.star.text.TextTable"):
            # Phase D will produce a structured tool for tables; skip
            # them here so the plain-text view stays clean.
            parts.append("[table]")
    result = "\n".join(parts)
    logger.info("get_document returned %d characters", len(result))
    return result


TOOLS = [get_document]
