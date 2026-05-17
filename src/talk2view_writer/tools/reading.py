"""Reading tools — extract content from the active Writer document.

Three tools mirror ``Talk2View-Word/src/taskpane/tools/reading.ts``:

- :func:`get_document` — paragraphs, tables, properties, section count
- :func:`get_selection` — currently highlighted text
- :func:`select_text`  — programmatically select text by query or paragraph index

Return shapes are JSON strings matching the Word equivalents so the
cloud agent's parsing remains identical across hosts. See
``docs/adrs/0021-json-string-tool-returns.md`` for the rationale.

Behavioural deltas vs Word noted at the top of each tool.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from talk2view import tool  # type: ignore[import-not-found]

from talk2view_writer.extension import get_extension_or_raise
from talk2view_writer.tools._base import (
    get_writer_document,
    ui_thread_tool,
)
from talk2view_writer.uno_helpers.styles import libreoffice_to_word_style

logger = logging.getLogger(__name__)

_MAX_COUNT = 100


# ---------------------------------------------------------------------------
# get_document
# ---------------------------------------------------------------------------


@tool
@ui_thread_tool
def get_document(
    start_index: int = 0,
    count: int = _MAX_COUNT,
    include_font_details: bool = False,
) -> str:
    """Read the document body: paragraphs, tables, properties, sections.

    Does NOT include headers, footers, or comments — use ``get_comments``
    for those. Call this FIRST before any write, format, delete, or
    comment operation. Do NOT re-read after every change — only when
    indices or text have shifted. By default font details are omitted
    to save tokens; pass ``include_font_details=true`` only when
    per-paragraph font info is needed.

    Args:
        start_index: Zero-based paragraph index to start from. Use with
            ``count`` to paginate when ``total_paragraphs`` exceeds 100.
            Defaults to 0.
        count: Number of paragraphs to return (max 100). Defaults to 100.
        include_font_details: Include per-paragraph font name / size /
            color / bold / italic / underline / highlight. Defaults to
            false. Set true only for font diagnostics — doubles response
            size.

    Returns:
        JSON string with ``text``, ``paragraphs``, ``total_paragraphs``,
        ``tables``, ``properties``, ``sections``. Includes ``hint`` when
        the document is empty.

    Raises:
        WriterDocumentRequired: If no Writer document is active.
        ValueError: If ``start_index`` < 0 or ``count`` is outside
            ``[1, 100]``.
    """
    if start_index < 0:
        raise ValueError(
            "start_index must be >= 0. Use 0 to start from the beginning."
        )
    if count < 1:
        raise ValueError("count must be >= 1. Use a value from 1 to 100.")
    if count > _MAX_COUNT:
        raise ValueError(
            f"count {count} exceeds maximum of {_MAX_COUNT}. "
            f"Paginate with start_index for large documents."
        )

    ext = get_extension_or_raise()
    doc = get_writer_document(ext.ctx)
    text = doc.getText()

    # ---- Enumerate paragraphs (top-level only; tables are returned
    # separately, like the Word tool does).
    paragraphs: List[Any] = []
    enum = text.createEnumeration()
    while enum.hasMoreElements():
        element = enum.nextElement()
        if element.supportsService("com.sun.star.text.Paragraph"):
            paragraphs.append(element)

    total = len(paragraphs)
    slice_ = paragraphs[start_index : start_index + count]

    para_data: List[Dict[str, Any]] = []
    for i, para in enumerate(slice_):
        entry: Dict[str, Any] = {
            "index": start_index + i,
            "text": para.getString(),
            "style": libreoffice_to_word_style(
                getattr(para, "ParaStyleName", "") or ""
            ),
        }
        if include_font_details:
            entry["font"] = _read_font_properties(para)
        para_data.append(entry)

    # ---- Tables
    tables = doc.getTextTables()
    table_data: List[Dict[str, Any]] = []
    for ti in range(tables.getCount()):
        t = tables.getByIndex(ti)
        first_row = _read_first_table_row(t)
        table_data.append({
            "index": ti,
            "rows": t.getRows().getCount(),
            "columns": t.getColumns().getCount(),
            "first_row": first_row,
        })

    # ---- Properties
    props = _read_document_properties(doc)

    # ---- Sections (delta: LibreOffice "text sections" are not Word
    # "sections". We report the text-section count and flag this in
    # the response. See investigations.md #14.)
    text_sections = doc.getTextSections()
    section_count = text_sections.getCount() if text_sections is not None else 0

    response: Dict[str, Any] = {
        "text": text.getString(),
        "paragraphs": para_data,
        "total_paragraphs": total,
        "tables": table_data,
        "properties": props,
        "sections": section_count,
    }

    if total == 0 or (total == 1 and not paragraphs[0].getString().strip()):
        response["hint"] = (
            "Document is empty. Use insert_content to add content."
        )

    return json.dumps(response)


def _read_font_properties(para: Any) -> Dict[str, Any]:
    """Read font properties from a paragraph via its text cursor.

    Returns Word-shaped keys (name, size, color, bold, italic,
    underline, highlight) so the agent sees consistent field names
    across Writer and Word. UNO returns char weight as a float
    (NORMAL=100.0, BOLD=150.0); we map to bool. Highlight color uses
    -1 in UNO when unset; we report None.
    """
    text = para.getText()
    cursor = text.createTextCursorByRange(para.getStart())
    cursor.gotoEndOfParagraph(True)  # select the whole paragraph
    underline = getattr(cursor, "CharUnderline", 0)
    highlight = getattr(cursor, "CharHighlight", -1)
    return {
        "name": getattr(cursor, "CharFontName", "") or "",
        "size": getattr(cursor, "CharHeight", 0.0),
        # CharColor is an int (0xRRGGBB). -1 means automatic / inherited.
        "color": getattr(cursor, "CharColor", -1),
        "bold": getattr(cursor, "CharWeight", 100.0) >= 150.0,
        "italic": bool(getattr(cursor, "CharPosture", 0)),
        # CharUnderline 0 = NONE; anything else is an underlined style.
        "underline": underline != 0,
        "highlight": None if highlight == -1 else highlight,
    }


def _read_first_table_row(table: Any) -> List[str]:
    """Return the first row's cell texts (Word-style preview).

    UNO cell names are like ``A1``, ``B1``, ``A10`` — leading letters
    are the column, trailing digits are the 1-based row. We must
    parse the row number rather than checking ``endswith("1")`` so
    cells in row 10/11/… don't incorrectly match row 1.
    """
    cells = table.getCellNames()
    if not cells:
        return []
    first_row_cells: List[str] = []
    for c in cells:
        digits = ""
        for ch in reversed(c):
            if ch.isdigit():
                digits = ch + digits
            else:
                break
        if digits == "1":
            first_row_cells.append(c)
    return [table.getCellByName(c).getString() for c in first_row_cells]


def _read_document_properties(doc: Any) -> Dict[str, Any]:
    """Map LibreOffice ``XDocumentProperties`` to Word-shaped keys."""
    props = doc.getDocumentProperties()
    return {
        "title": getattr(props, "Title", "") or "",
        "author": getattr(props, "Author", "") or "",
        "subject": getattr(props, "Subject", "") or "",
        # UNO Keywords is a tuple of strings; Word reports a single string.
        "keywords": ", ".join(getattr(props, "Keywords", ()) or ()),
        "lastAuthor": getattr(props, "ModifiedBy", "") or "",
        # Word has "revisionNumber"; LibreOffice has "EditingCycles".
        "revisionNumber": getattr(props, "EditingCycles", 0),
    }


# ---------------------------------------------------------------------------
# get_selection
# ---------------------------------------------------------------------------


@tool
@ui_thread_tool
def get_selection() -> str:
    """Return the currently selected (highlighted) text in the document.

    Returns an empty string if nothing is selected. Call BEFORE
    insert_content with ``location="replace_selection"`` or
    ``"after_selection"`` to verify the selection exists. Not required
    before format_text — that tool has built-in text targeting via
    query / paragraph_index. If the result is empty and a selection is
    needed, use select_text to programmatically select text.

    Returns:
        JSON string with ``text``. Includes ``hint`` when no text is
        selected.

    Raises:
        WriterDocumentRequired: If no Writer document is active.
    """
    ext = get_extension_or_raise()
    doc = get_writer_document(ext.ctx)
    controller = doc.getCurrentController()
    selection = controller.getSelection()

    text = ""
    # Writer's selection is an XIndexAccess of XTextRange.
    if selection is not None and hasattr(selection, "getCount"):
        parts: List[str] = []
        for i in range(selection.getCount()):
            r = selection.getByIndex(i)
            if hasattr(r, "getString"):
                parts.append(r.getString())
        text = "".join(parts)

    response: Dict[str, Any] = {"text": text}
    if not text:
        response["hint"] = (
            "No text is selected. Use select_text to select text "
            "programmatically, or ask the user to highlight text."
        )
    return json.dumps(response)


# ---------------------------------------------------------------------------
# select_text
# ---------------------------------------------------------------------------


@tool
@ui_thread_tool
def select_text(
    query: Optional[str] = None,
    match_index: int = 0,
    paragraph_index: Optional[int] = None,
) -> str:
    """Select text in the document (visible highlight).

    NICHE. Only call when you specifically need to show the user a
    visible highlighted range. For any actual operation, direct-targeting
    tools are better: insert_content accepts ``target_query``, format_text
    accepts ``query``/``queries``, search_document for find/replace. If
    you find yourself calling select_text before another tool — stop,
    use the direct-targeting parameter instead.

    Args:
        query: Exact text to search for and select (case-insensitive).
            Use 3+ unique words to avoid multiple matches. Mutually
            exclusive with ``paragraph_index``.
        match_index: Which match to select when ``query`` finds multiple
            results (0-based). Defaults to 0 (first match).
        paragraph_index: Select an entire paragraph by zero-based index.
            Mutually exclusive with ``query``.

    Returns:
        JSON string. On success: ``{success, selected, match_index,
        total_matches, hint?}``. On error: ``{error, recovery}``.

    Raises:
        WriterDocumentRequired: If no Writer document is active.
        ValueError: If neither ``query`` nor ``paragraph_index`` is
            provided, or both are provided.
    """
    if query is None and paragraph_index is None:
        raise ValueError(
            "Provide either query or paragraph_index. "
            "Use query for specific text, or paragraph_index for a whole "
            "paragraph. Call get_document to see available text/indices."
        )
    if query is not None and paragraph_index is not None:
        raise ValueError(
            "Provide either query or paragraph_index, not both. "
            "Drop whichever is less precise for your target."
        )

    ext = get_extension_or_raise()
    doc = get_writer_document(ext.ctx)
    controller = doc.getCurrentController()

    if query is not None:
        searcher = doc.createSearchDescriptor()
        searcher.SearchString = query
        searcher.SearchCaseSensitive = False
        results = doc.findAll(searcher)
        total = results.getCount() if results is not None else 0
        if total == 0:
            return json.dumps({
                "error": f'Text "{query}" not found in the document.',
                "recovery": (
                    "Use get_document to check the exact text. Even small "
                    "differences in spacing or punctuation cause mismatches."
                ),
            })
        if match_index < 0 or match_index >= total:
            return json.dumps({
                "error": (
                    f"match_index {match_index} out of range "
                    f"({total} matches found)."
                ),
                "recovery": f"Use a value from 0 to {total - 1}.",
            })
        controller.select(results.getByIndex(match_index))
        response: Dict[str, Any] = {
            "success": True,
            "selected": query,
            "match_index": match_index,
            "total_matches": total,
        }
        if total > 1:
            response["hint"] = (
                f"{total} matches found. Selected match {match_index}. "
                f"Use a longer query or match_index to target a specific one."
            )
        return json.dumps(response)

    # paragraph_index path
    assert paragraph_index is not None  # noqa: S101 — type narrowing
    paragraphs: List[Any] = []
    enum = doc.getText().createEnumeration()
    while enum.hasMoreElements():
        el = enum.nextElement()
        if el.supportsService("com.sun.star.text.Paragraph"):
            paragraphs.append(el)
    if paragraph_index < 0 or paragraph_index >= len(paragraphs):
        return json.dumps({
            "error": (
                f"Paragraph index {paragraph_index} out of range "
                f"(document has {len(paragraphs)} paragraphs)."
            ),
            "recovery": (
                f"Use an index from 0 to {len(paragraphs) - 1}. "
                f"Call get_document to see valid indices."
            ),
        })
    para = paragraphs[paragraph_index]
    controller.select(para)
    return json.dumps({
        "success": True,
        "selected_paragraph": paragraph_index,
        "text": para.getString(),
    })


TOOLS = [get_document, get_selection, select_text]
