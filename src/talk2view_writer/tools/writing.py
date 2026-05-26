"""Writing tools — insert / delete / edit content in the active Writer document.

Faithful port of ``Talk2View-Word/src/taskpane/tools/writing.ts``:

- :func:`insert_content` — multi-mode paragraph insertion with fused
  paragraph formatting
- :func:`insert_table`   — new table with optional initial cell data
- :func:`insert_image`   — base64-encoded inline image
- :func:`undo_redo`      — N-step undo / redo with a paragraph diff
- :func:`delete_content` — by paragraph index, range, or query
- :func:`edit_table`     — edit_cell, add/delete rows, add/delete columns

Return shapes are JSON strings matching the Word equivalents per ADR-0021.
Behavioural deltas vs Word noted at the top of each tool.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import json
import logging
import os
import tempfile
from typing import Any

from talk2view import tool  # type: ignore[import-not-found]

from talk2view_writer.extension import get_extension_or_raise
from talk2view_writer.tools._base import get_writer_document, ui_thread_tool
from talk2view_writer.tools._constants import (
    VALID_STYLES,
    lower_enum,
    points_to_hmm,
    preview,
)
from talk2view_writer.uno_helpers.styles import word_to_libreoffice_style

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Local helpers
# ---------------------------------------------------------------------------

_ALIGNMENT_MAP = {
    # com.sun.star.style.ParagraphAdjust enum integer values.
    "left": 0,
    "right": 1,
    "block": 2,  # equivalent to "justified" with no special last-line handling
    "center": 3,
    "justified": 2,
}


def _enumerate_paragraphs(doc: Any) -> list[Any]:
    """Return the top-level paragraphs in document order."""
    paragraphs: list[Any] = []
    enum = doc.getText().createEnumeration()
    while enum.hasMoreElements():
        element = enum.nextElement()
        if element.supportsService("com.sun.star.text.Paragraph"):
            paragraphs.append(element)
    return paragraphs


def _apply_paragraph_format(
    para: Any,
    *,
    alignment: str | None,
    space_before: float | None,
    space_after: float | None,
    line_spacing: float | None,
    left_indent: float | None,
    right_indent: float | None,
    first_line_indent: float | None,
) -> None:
    """Apply Word-shaped paragraph formatting to a UNO paragraph object."""
    if alignment is not None:
        para.ParaAdjust = _ALIGNMENT_MAP[alignment]
    if space_before is not None:
        para.ParaTopMargin = points_to_hmm(space_before)
    if space_after is not None:
        para.ParaBottomMargin = points_to_hmm(space_after)
    if line_spacing is not None:
        # com.sun.star.style.LineSpacing struct: Mode=3 (FIX, height in 1/100 mm),
        # Height=points_to_hmm(line_spacing).
        import uno  # type: ignore[import-not-found]

        ls = uno.createUnoStruct("com.sun.star.style.LineSpacing")
        ls.Mode = 3
        ls.Height = points_to_hmm(line_spacing)
        para.ParaLineSpacing = ls
    if left_indent is not None:
        para.ParaLeftMargin = points_to_hmm(left_indent)
    if right_indent is not None:
        para.ParaRightMargin = points_to_hmm(right_indent)
    if first_line_indent is not None:
        para.ParaFirstLineIndent = points_to_hmm(first_line_indent)


def _insert_paragraph_at_cursor(
    text_obj: Any,
    cursor: Any,
    paragraph_text: str,
    *,
    style: str | None,
    doc: Any | None = None,
) -> Any:
    """Insert one paragraph at ``cursor`` and return the new paragraph object.

    The cursor advances past the inserted paragraph so subsequent
    insertions land after it.

    Skips the leading PARAGRAPH_BREAK when the cursor's host paragraph
    is already empty — otherwise the break would split that empty
    paragraph into two, leaving a phantom blank above the just-written
    text. Common at location="start" / "end" on a fresh doc (single
    empty p0) and after ``target_query`` / ``replace_selection`` has
    cleared its matched range.

    Pass ``doc`` so the paragraph-style assignment can suspend
    ``RecordChanges`` for that single write — LibreOffice rejects
    ``ParaStyleName`` mutations on a paragraph still inside an active
    redline, which is exactly the state our track-changes envelope
    puts every fresh insert in.
    """
    if not _cursor_at_empty_paragraph(text_obj, cursor):
        text_obj.insertControlCharacter(cursor, _PARAGRAPH_BREAK, False)
    text_obj.insertString(cursor, paragraph_text, False)
    # Re-find the just-written paragraph via the cursor's current paragraph.
    # The XParagraphCursor interface gives us gotoStartOfParagraph / range.
    para_cursor = text_obj.createTextCursorByRange(cursor.getStart())
    para_cursor.gotoStartOfParagraph(False)
    para_cursor.gotoEndOfParagraph(True)
    if style:
        from talk2view_writer.tools._base import suspend_record_changes

        ctx = suspend_record_changes(doc) if doc is not None else contextlib.nullcontext()
        with ctx:
            para_cursor.ParaStyleName = word_to_libreoffice_style(style)
    return para_cursor


def _cursor_at_empty_paragraph(text_obj: Any, cursor: Any) -> bool:
    """``True`` when ``cursor`` sits inside a paragraph with no text.

    Uses the canonical UNO XParagraphCursor selection trick: copy the
    cursor, snap to the start of the host paragraph, then extend to
    the end. The selected string is the paragraph's full text — empty
    means the cursor is in an empty paragraph that can host the next
    write without a phantom break above it.
    """
    probe = text_obj.createTextCursorByRange(cursor.getStart())
    probe.gotoStartOfParagraph(False)
    probe.gotoEndOfParagraph(True)
    # bool() because probe.getString() is Any (UNO proxy); the equality
    # result is Any too, which mypy rejects from a -> bool function.
    return bool(probe.getString() == "")


# com.sun.star.text.ControlCharacter.PARAGRAPH_BREAK == 0.
_PARAGRAPH_BREAK = 0


def _is_uniform_table(table: Any) -> bool:
    """``True`` when no cell in the table spans multiple rows / columns.

    UNO doesn't expose Word's ``isUniform`` directly, but for a uniform
    table the number of distinct cell names equals ``rows * columns``.
    Merged cells reduce the count.
    """
    expected = table.getRows().getCount() * table.getColumns().getCount()
    return bool(len(table.getCellNames()) == expected)


# ---------------------------------------------------------------------------
# insert_content
# ---------------------------------------------------------------------------


@tool
@ui_thread_tool
def insert_content(
    text: str | None = None,
    # ``blocks`` accepts ``{text, style?}`` dicts at runtime, but engine
    # LLMs sometimes emit plain strings (gemini-3-pro, 2026-05-22).
    # Typed wide so the coercion below is honest about what the runtime
    # accepts; the normalisation produces a clean dict shape internally.
    blocks: list[Any] | None = None,
    style: str | None = None,
    location: str | None = None,
    target_query: str | None = None,
    match_index: int = 0,
    paragraph_index: int | None = None,
    alignment: str | None = None,
    space_before: float | None = None,
    space_after: float | None = None,
    line_spacing: float | None = None,
    left_indent: float | None = None,
    right_indent: float | None = None,
    first_line_indent: float | None = None,
) -> str:
    """Insert one or more styled paragraphs.

    ONE CALL, MANY OPERATIONS: use ``blocks`` for multiple paragraphs,
    and optionally set paragraph-level formatting (alignment, spacing,
    indent) on the inserted content so you never need a follow-up
    format_paragraph call. Target by ``location`` (document- or
    selection-relative) OR by ``target_query`` (find text and replace
    it in one call). NEVER fake a heading with format_text — set
    ``style`` here.

    Args:
        text: Single paragraph to insert. Mutually exclusive with blocks.
        blocks: Insert multiple paragraphs in one call — list of
            ``{"text": "...", "style": "..."}`` dicts. Always prefer
            this over multiple insert_content calls.
        style: Word built-in style for single-paragraph insertion. Valid:
            Normal, Heading1-Heading4, Title, Subtitle, Quote,
            IntenseQuote, ListParagraph, NoSpacing.
        location: Where to insert — one of ``start``, ``end``,
            ``before_paragraph``, ``after_paragraph``,
            ``after_selection``, ``replace_selection``. Defaults to
            ``end`` if neither location nor target_query is set.
        target_query: Find this text and replace it with the inserted
            content. Mutually exclusive with location. <=255 chars.
        match_index: Which target_query match to replace (0-based).
            Defaults to 0.
        paragraph_index: Required when location is ``before_paragraph``
            or ``after_paragraph``.
        alignment: One of ``left``, ``center``, ``right``, ``justified``.
        space_before: Space above each inserted paragraph in points.
        space_after: Space below each inserted paragraph in points.
        line_spacing: Line spacing within each paragraph in points.
        left_indent: Left indent in points (72pt = 1 inch).
        right_indent: Right indent in points.
        first_line_indent: First-line indent in points (negative = hanging).

    Returns:
        JSON string with success / preview / counts / applied formatting.

    Raises:
        WriterDocumentRequiredError: If no Writer document is active.
        ValueError: For schema violations (empty inputs, mutually
            exclusive args set together, etc.).
    """
    # Case-insensitive enum arg (schema enum dropped — see Writer #5).
    location = lower_enum(location)

    # ----- Validation ------------------------------------------------------
    if text is not None and blocks is not None:
        return json.dumps(
            {
                "error": "Provide either 'text' or 'blocks', not both.",
                "recovery": "Remove one of text or blocks.",
            }
        )
    if text is None and blocks is None:
        return json.dumps(
            {
                "error": "Provide either text or blocks.",
                "recovery": ("Use text for a single paragraph, or blocks for multiple."),
            }
        )
    if target_query is not None and location is not None:
        return json.dumps(
            {
                "error": "Provide target_query OR location, not both.",
                "recovery": (
                    "target_query replaces found text; location inserts at a position. Pick one."
                ),
            }
        )
    if target_query is not None:
        if not target_query.strip():
            return json.dumps(
                {
                    "error": "target_query is empty.",
                    "recovery": "Provide the text to find and replace.",
                }
            )
        if len(target_query) > 255:
            return json.dumps(
                {
                    "error": (
                        f"target_query is {len(target_query)} characters; Word.search caps at 255."
                    ),
                    "recovery": (
                        "Use a short unique phrase (5-15 words) from the START "
                        "of the text you want to replace."
                    ),
                }
            )
    if location in ("before_paragraph", "after_paragraph") and paragraph_index is None:
        return json.dumps(
            {
                "error": f"paragraph_index is required when location is '{location}'.",
                "recovery": (
                    "Call get_document to find paragraph indices, then provide paragraph_index."
                ),
            }
        )
    if text is not None:
        if not text.strip():
            return json.dumps(
                {
                    "error": "text parameter is empty.",
                    "recovery": "Provide the text content you want to insert.",
                }
            )
        if style and style not in VALID_STYLES:
            return json.dumps(
                {
                    "error": f'Unknown style "{style}".',
                    "recovery": f"Use one of: {', '.join(VALID_STYLES)}.",
                }
            )
    if blocks is not None:
        if not blocks:
            return json.dumps(
                {
                    "error": "blocks array is empty.",
                    "recovery": "Provide at least one block with a text property.",
                }
            )
        # Engine LLMs sometimes emit blocks as plain strings instead of
        # {text, style?} dicts (observed 2026-05-22 with gemini-3-pro).
        # Coerce in place so the rest of the function can assume dicts.
        # Fresh-dict construction makes the value-type widening explicit
        # rather than relying on dict invariance.
        normalised: list[dict[str, str | None]] = []
        for block in blocks:
            if isinstance(block, str):
                normalised.append({"text": block, "style": None})
            else:
                normalised.append(
                    {"text": block.get("text", ""), "style": block.get("style")}
                )
        blocks = normalised
        for i, block in enumerate(blocks):
            block_text = block.get("text") or ""
            if not block_text.strip():
                return json.dumps(
                    {
                        "error": f"blocks[{i}].text is empty.",
                        "recovery": "Every block must have non-empty text.",
                    }
                )
            block_style = block.get("style")
            if block_style and block_style not in VALID_STYLES:
                return json.dumps(
                    {
                        "error": f'blocks[{i}] has unknown style "{block_style}".',
                        "recovery": f"Use one of: {', '.join(VALID_STYLES)}.",
                    }
                )
    for arg_name, arg_val, allow_zero in (
        ("space_before", space_before, True),
        ("space_after", space_after, True),
        ("line_spacing", line_spacing, False),
    ):
        if arg_val is None:
            continue
        if allow_zero and arg_val < 0:
            return json.dumps(
                {
                    "error": f"{arg_name} must be >= 0.",
                    "recovery": "Use 0 or a positive value in points.",
                }
            )
        if not allow_zero and arg_val <= 0:
            return json.dumps(
                {
                    "error": f"{arg_name} must be > 0.",
                    "recovery": "Use a positive value in points.",
                }
            )

    # Default location.
    if target_query is None and location is None:
        location = "end"

    # ----- UNO insertion ---------------------------------------------------
    ext = get_extension_or_raise()
    doc = get_writer_document(ext.ctx)
    text_obj = doc.getText()

    # Normalise both modes to a single list of {text, style?} dicts so the
    # insertion loop below doesn't branch.
    #
    # When ``blocks`` is provided alongside the top-level ``style``
    # argument, the engine LLM means "apply this style to every block
    # that doesn't override". Common pattern: blocks=['The Frosty
    # March'], style='Title' — the model intends one Title-styled
    # paragraph. Falling back to the top-level style here preserves
    # that intent; per-block styles still win when present.
    items: list[dict[str, str | None]]
    if blocks is not None:
        items = [{"text": b["text"], "style": b.get("style") or style} for b in blocks]
    else:
        items = [{"text": text or "", "style": style}]

    # Resolve the anchor cursor up front.
    cursor, error_response = _resolve_insertion_cursor(
        doc=doc,
        text_obj=text_obj,
        location=location,
        target_query=target_query,
        match_index=match_index,
        paragraph_index=paragraph_index,
    )
    if error_response is not None:
        return error_response

    paragraphs_to_format: list[Any] = []
    previews: list[dict[str, str]] = []
    total_chars = 0
    for item in items:
        item_text = item["text"] or ""
        item_style = item.get("style")
        para = _insert_paragraph_at_cursor(
            text_obj, cursor, item_text, style=item_style, doc=doc
        )
        paragraphs_to_format.append(para)
        total_chars += len(item_text)
        previews.append(
            {
                "text": preview(item_text),
                "style": item_style or "plain text",
            }
        )

    for para in paragraphs_to_format:
        _apply_paragraph_format(
            para,
            alignment=alignment,
            space_before=space_before,
            space_after=space_after,
            line_spacing=line_spacing,
            left_indent=left_indent,
            right_indent=right_indent,
            first_line_indent=first_line_indent,
        )

    applied_format = {
        k: v
        for k, v in {
            "alignment": alignment,
            "space_before": space_before,
            "space_after": space_after,
            "line_spacing": line_spacing,
            "left_indent": left_indent,
            "right_indent": right_indent,
            "first_line_indent": first_line_indent,
        }.items()
        if v is not None
    }
    common: dict[str, Any] = {
        "location": location
        if location is not None
        else ("target_query" if target_query is not None else None),
    }
    if applied_format:
        common["paragraph_format"] = applied_format

    if blocks is not None:
        return json.dumps(
            {
                "success": True,
                "blocks_inserted": len(items),
                "previews": previews,
                "total_chars": total_chars,
                **common,
            }
        )
    return json.dumps(
        {
            "success": True,
            "inserted": preview(text or ""),
            "style": style or "plain text (no style)",
            "char_count": len(text or ""),
            **common,
        }
    )


def _resolve_insertion_cursor(
    *,
    doc: Any,
    text_obj: Any,
    location: str | None,
    target_query: str | None,
    match_index: int,
    paragraph_index: int | None,
) -> tuple[Any, str | None]:
    """Return ``(cursor, None)`` or ``(None, error_json_string)``.

    The returned cursor points at the insertion site. For
    ``target_query`` the matched range is removed first so the new
    paragraph takes its place.
    """
    if target_query is not None:
        searcher = doc.createSearchDescriptor()
        searcher.SearchString = target_query
        searcher.SearchCaseSensitive = False
        results = doc.findAll(searcher)
        total = results.getCount() if results is not None else 0
        if total == 0:
            return None, json.dumps(
                {
                    "error": f'target_query "{preview(target_query, 60)}" not found.',
                    "recovery": "Use get_document to verify the exact text.",
                }
            )
        if match_index < 0 or match_index >= total:
            return None, json.dumps(
                {
                    "error": f"match_index {match_index} out of range ({total} matches).",
                    "recovery": f"Use 0 to {total - 1}.",
                }
            )
        hit = results.getByIndex(match_index)
        hit.setString("")  # remove the matched text
        cursor = text_obj.createTextCursorByRange(hit.getStart())
        # We're now sitting at the cleared range. The forthcoming
        # _insert_paragraph_at_cursor emits a paragraph break first,
        # which gives us a clean new paragraph at this position.
        return cursor, None

    if location in ("before_paragraph", "after_paragraph"):
        paragraphs = _enumerate_paragraphs(doc)
        if paragraph_index is None or paragraph_index >= len(paragraphs):
            return None, json.dumps(
                {
                    "error": (
                        f"paragraph_index {paragraph_index} out of range "
                        f"(document has {len(paragraphs)} paragraphs)."
                    ),
                    "recovery": (
                        f"Use 0 to {len(paragraphs) - 1}. Call get_document for valid indices."
                    ),
                }
            )
        para = paragraphs[paragraph_index]
        if location == "before_paragraph":
            return text_obj.createTextCursorByRange(para.getStart()), None
        return text_obj.createTextCursorByRange(para.getEnd()), None

    if location in ("replace_selection", "after_selection"):
        controller = doc.getCurrentController()
        selection = controller.getSelection()
        if selection is None or not hasattr(selection, "getCount") or selection.getCount() == 0:
            return None, json.dumps(
                {
                    "error": "No selection available.",
                    "recovery": "Highlight text in the document, or use select_text first.",
                }
            )
        range_ = selection.getByIndex(0)
        if location == "replace_selection":
            range_.setString("")
            return text_obj.createTextCursorByRange(range_.getStart()), None
        return text_obj.createTextCursorByRange(range_.getEnd()), None

    # start / end (default fallback).
    if location == "start":
        return text_obj.createTextCursorByRange(text_obj.getStart()), None
    return text_obj.createTextCursorByRange(text_obj.getEnd()), None


# ---------------------------------------------------------------------------
# insert_table
# ---------------------------------------------------------------------------


@tool
@ui_thread_tool
def insert_table(
    rows: int,
    columns: int,
    location: str,
    data: list[list[str]] | None = None,
) -> str:
    """Insert a new table at the document start or end.

    Pass ``data`` (2D string array — first row is the header) in the
    same call so cells are populated up front; avoid insert_table +
    repeated edit_table calls. For modifying existing tables, use
    edit_table.

    Args:
        rows: Total number of rows including the header. >= 1.
        columns: Number of columns. >= 1.
        location: ``start`` (before all content) or ``end``
            (after all content). Paragraph-relative insertion is not
            supported.
        data: Optional 2D string array to populate the table. First
            inner array is the header row.

    Returns:
        JSON string with success / rows / columns / cells_populated.

    Raises:
        WriterDocumentRequiredError: If no Writer document is active.
    """
    # Case-insensitive enum arg (schema enum dropped — see Writer #5).
    location = lower_enum(location) or ""

    if not isinstance(rows, int) or rows < 1:
        return json.dumps(
            {
                "error": "rows must be a positive integer.",
                "recovery": "Provide rows >= 1.",
            }
        )
    if not isinstance(columns, int) or columns < 1:
        return json.dumps(
            {
                "error": "columns must be a positive integer.",
                "recovery": "Provide columns >= 1.",
            }
        )
    if location not in ("start", "end"):
        return json.dumps(
            {
                "error": f"Unknown location '{location}'.",
                "recovery": "Use 'start' or 'end'.",
            }
        )
    if data is not None:
        if not all(isinstance(row, list) for row in data):
            return json.dumps(
                {
                    "error": "data must be a 2D array of strings.",
                    "recovery": 'Format: [["cell1","cell2"],["cell3","cell4"]]',
                }
            )
        if len(data) > rows:
            return json.dumps(
                {
                    "error": f"data has {len(data)} rows but table only has {rows} rows.",
                    "recovery": f"Set rows to {len(data)}, or trim data to {rows} rows.",
                }
            )
        max_cols = max((len(row) for row in data), default=0)
        if max_cols > columns:
            return json.dumps(
                {
                    "error": (
                        f"data has rows with up to {max_cols} columns but table only "
                        f"has {columns} columns."
                    ),
                    "recovery": (f"Set columns to {max_cols}, or trim data rows to {columns}."),
                }
            )

    ext = get_extension_or_raise()
    doc = get_writer_document(ext.ctx)
    text_obj = doc.getText()
    table = doc.createInstance("com.sun.star.text.TextTable")
    table.initialize(rows, columns)

    if location == "start":
        cursor = text_obj.createTextCursorByRange(text_obj.getStart())
    else:
        cursor = text_obj.createTextCursorByRange(text_obj.getEnd())
    text_obj.insertTextContent(cursor, table, False)

    cells_populated = 0
    if data is not None:
        for r, row in enumerate(data[:rows]):
            for c, value in enumerate(row[:columns]):
                # UNO order: getCellByPosition(column, row).
                table.getCellByPosition(c, r).setString(value)
                cells_populated += 1

    return json.dumps(
        {
            "success": True,
            "rows": rows,
            "columns": columns,
            "cells_populated": cells_populated,
            "location": location,
        }
    )


# ---------------------------------------------------------------------------
# insert_image
# ---------------------------------------------------------------------------


@tool
@ui_thread_tool
def insert_image(
    base64_data: str,
    location: str,
    width: float | None = None,
    height: float | None = None,
) -> str:
    """Insert a base64-encoded image (PNG, JPEG, or GIF) into the document.

    The base64 string must be raw — no ``data:image/png;base64,`` prefix.
    Set both width and height in points (72pt = 1 inch) to control size,
    or omit both for original dimensions. Cannot modify or resize
    existing images — only insert new ones.

    Args:
        base64_data: Raw base64-encoded image data (no data URI prefix).
        location: ``start``, ``end``, or ``after_selection``.
        width: Optional image width in points (72pt = 1 inch). > 0.
        height: Optional image height in points. > 0.

    Returns:
        JSON string with success / location / width / height.

    Raises:
        WriterDocumentRequiredError: If no Writer document is active.
    """
    # Case-insensitive enum arg (schema enum dropped — see Writer #5).
    location = lower_enum(location) or ""

    if not base64_data or not base64_data.strip():
        return json.dumps(
            {
                "error": "base64 is empty.",
                "recovery": "Provide valid base64-encoded image data.",
            }
        )
    if base64_data.startswith("data:"):
        return json.dumps(
            {
                "error": "base64 contains a data URI prefix.",
                "recovery": (
                    'Remove the "data:image/...;base64," prefix and pass only the '
                    "raw base64 string."
                ),
            }
        )
    if len(base64_data) < 20:
        return json.dumps(
            {
                "error": "base64 data is too short to be a valid image.",
                "recovery": "Provide the full base64-encoded image data (PNG, JPEG, or GIF).",
            }
        )
    if width is not None and width <= 0:
        return json.dumps(
            {
                "error": "width must be positive.",
                "recovery": "Provide width in points (72pt = 1 inch).",
            }
        )
    if height is not None and height <= 0:
        return json.dumps(
            {
                "error": "height must be positive.",
                "recovery": "Provide height in points.",
            }
        )
    if location not in ("start", "end", "after_selection"):
        return json.dumps(
            {
                "error": f"Unknown location '{location}'.",
                "recovery": "Use 'start', 'end', or 'after_selection'.",
            }
        )

    try:
        raw = base64.b64decode(base64_data, validate=True)
    except binascii.Error:
        return json.dumps(
            {
                "error": "base64 data is not valid base64.",
                "recovery": (
                    "Verify the encoding — common foot-guns: stray whitespace, missing padding."
                ),
            }
        )

    ext = get_extension_or_raise()
    doc = get_writer_document(ext.ctx)
    text_obj = doc.getText()

    # Resolve insertion cursor.
    if location == "after_selection":
        controller = doc.getCurrentController()
        selection = controller.getSelection()
        if selection is None or not hasattr(selection, "getCount") or selection.getCount() == 0:
            return json.dumps(
                {
                    "error": "No selection available.",
                    "recovery": "Highlight text in the document, or use select_text first.",
                }
            )
        cursor = text_obj.createTextCursorByRange(selection.getByIndex(0).getEnd())
    elif location == "start":
        cursor = text_obj.createTextCursorByRange(text_obj.getStart())
    else:
        cursor = text_obj.createTextCursorByRange(text_obj.getEnd())

    # Write image bytes to a temp file (UNO loads images by URL).
    fd, tmp_path = tempfile.mkstemp(suffix=".img")
    try:
        os.write(fd, raw)
        os.close(fd)
        graphic_provider = ext.ctx.ServiceManager.createInstanceWithContext(
            "com.sun.star.graphic.GraphicProvider", ext.ctx
        )
        import uno  # type: ignore[import-not-found]

        prop = uno.createUnoStruct("com.sun.star.beans.PropertyValue")
        prop.Name = "URL"
        # convertToURL converts a system path to a file:// URL.
        from com.sun.star.uno import (
            RuntimeException,  # type: ignore[import-not-found]  # noqa: F401
        )

        prop.Value = _systempath_to_url(tmp_path)
        graphic = graphic_provider.queryGraphic((prop,))

        image = doc.createInstance("com.sun.star.text.TextGraphicObject")
        image.Graphic = graphic
        if width is not None:
            image.Width = points_to_hmm(width)
        if height is not None:
            image.Height = points_to_hmm(height)
        text_obj.insertTextContent(cursor, image, False)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    return json.dumps(
        {
            "success": True,
            "location": location,
            "width": width if width is not None else "original",
            "height": height if height is not None else "original",
        }
    )


def _systempath_to_url(path: str) -> str:
    """Convert a system file path to a ``file://`` URL UNO can load."""
    # uno.systemPathToFileUrl exists in PyUNO ≥ all recent LibreOffice
    # versions but is not always exposed by static analysers — use
    # an explicit prefix-based conversion as a fallback.
    import uno  # type: ignore[import-not-found]

    converter = getattr(uno, "systemPathToFileUrl", None)
    if callable(converter):
        return converter(path)  # type: ignore[no-any-return]
    return "file://" + path


# ---------------------------------------------------------------------------
# undo_redo
# ---------------------------------------------------------------------------


@tool
@ui_thread_tool
def undo_redo(action: str, count: int = 1) -> str:
    """Undo or redo document operations.

    Pass ``count`` to undo/redo N steps in one call. Faster and more
    reliable than manually reversing. Do not use undo to reverse
    operations you can fix directly. Returns a diff of paragraph
    text/style changes.

    Args:
        action: ``undo`` or ``redo``. Redo is only valid immediately
            after an undo (no intervening edits).
        count: Number of steps to apply. >= 1, defaults to 1.

    Returns:
        JSON string with success / action / paragraph diff /
        paragraph_count.

    Raises:
        WriterDocumentRequiredError: If no Writer document is active.
    """
    # Case-insensitive enum arg (schema enum dropped — see Writer #5).
    action = lower_enum(action) or ""

    if action not in ("undo", "redo"):
        return json.dumps(
            {
                "error": f"Unknown action '{action}'.",
                "recovery": "Use 'undo' or 'redo'.",
            }
        )
    steps = max(1, int(count))

    ext = get_extension_or_raise()
    doc = get_writer_document(ext.ctx)
    undo_manager = doc.getUndoManager()

    before = [
        {
            "text": p.getString(),
            "style": getattr(p, "ParaStyleName", "") or "",
        }
        for p in _enumerate_paragraphs(doc)
    ]
    for _ in range(steps):
        if action == "undo":
            if not undo_manager.isUndoPossible():
                break
            undo_manager.undo()
        else:
            if not undo_manager.isRedoPossible():
                break
            undo_manager.redo()
    after = [
        {
            "text": p.getString(),
            "style": getattr(p, "ParaStyleName", "") or "",
        }
        for p in _enumerate_paragraphs(doc)
    ]

    changes: list[dict[str, Any]] = []
    max_len = max(len(before), len(after))
    for i in range(max_len):
        b = before[i] if i < len(before) else None
        a = after[i] if i < len(after) else None
        if b is None:
            changes.append({"index": i, "after": a})
        elif a is None:
            changes.append({"index": i, "before": b})
        elif b["text"] != a["text"] or b["style"] != a["style"]:
            changes.append({"index": i, "before": b, "after": a})

    return json.dumps(
        {
            "success": True,
            "action": action,
            "changes": changes
            if changes
            else "no visible text/style changes (may be a formatting-only change)",
            "paragraph_count": {"before": len(before), "after": len(after)},
        }
    )


# ---------------------------------------------------------------------------
# delete_content
# ---------------------------------------------------------------------------


@tool
@ui_thread_tool
def delete_content(
    paragraph_index: int | None = None,
    start_index: int | None = None,
    end_index: int | None = None,
    query: str | None = None,
    match_case: bool = False,
) -> str:
    """Delete entire paragraphs by index, range, or text query.

    For deleting inline text WITHIN paragraphs (preserving paragraph
    structure), use ``search_document(query, replace_with="")`` instead
    — query mode is kept for back-compat only. For removing list
    FORMATTING without deleting text, use ``manage_list(action="remove")``.
    Paragraph deletion shifts subsequent indices — re-call get_document
    before further index-based edits.

    Args:
        paragraph_index: Delete a single paragraph by zero-based index.
        start_index: Start of paragraph range to delete (inclusive).
            Requires end_index.
        end_index: End of paragraph range (inclusive). Must be >= start_index.
        query: Delete all text matching this query, preserving paragraph
            structure. Mutually exclusive with the index modes.
        match_case: Case-sensitive query matching. Defaults to False.

    Returns:
        JSON string with success / mode / count / warning.

    Raises:
        WriterDocumentRequiredError: If no Writer document is active.
    """
    has_paragraph = paragraph_index is not None
    has_range = start_index is not None or end_index is not None
    has_query = query is not None
    mode_count = sum([has_paragraph, has_range, has_query])
    if mode_count == 0:
        return json.dumps(
            {
                "error": "No deletion mode specified.",
                "recovery": (
                    "Provide paragraph_index (single), start_index + end_index "
                    "(range), or query (text match)."
                ),
            }
        )
    if mode_count > 1:
        return json.dumps(
            {
                "error": "Multiple deletion modes specified.",
                "recovery": "Use only one: paragraph_index, start_index + end_index, or query.",
            }
        )
    if has_paragraph and paragraph_index is not None and paragraph_index < 0:
        return json.dumps(
            {
                "error": "paragraph_index must be >= 0.",
                "recovery": "Use 0 for the first paragraph.",
            }
        )
    if has_range:
        if start_index is None or end_index is None:
            return json.dumps(
                {
                    "error": "Both start_index and end_index are required for range deletion.",
                    "recovery": "Provide both start_index and end_index.",
                }
            )
        if start_index < 0 or end_index < 0:
            return json.dumps(
                {
                    "error": "Indices must be >= 0.",
                    "recovery": "Use 0 for the first paragraph.",
                }
            )
        if start_index > end_index:
            return json.dumps(
                {
                    "error": (
                        f"start_index ({start_index}) is greater than end_index ({end_index})."
                    ),
                    "recovery": (
                        f"Swap the values: start_index={end_index}, end_index={start_index}."
                    ),
                }
            )
    if has_query and (query is None or not query.strip()):
        return json.dumps(
            {
                "error": "query is empty.",
                "recovery": "Provide the text to search for and delete.",
            }
        )

    ext = get_extension_or_raise()
    doc = get_writer_document(ext.ctx)
    text_obj = doc.getText()

    if has_paragraph:
        paragraphs = _enumerate_paragraphs(doc)
        assert paragraph_index is not None
        if paragraph_index >= len(paragraphs):
            return json.dumps(
                {
                    "error": (
                        f"paragraph_index {paragraph_index} out of range "
                        f"(document has {len(paragraphs)} paragraphs)."
                    ),
                    "recovery": (
                        f"Use 0 to {len(paragraphs) - 1}. Call get_document for valid indices."
                    ),
                }
            )
        para = paragraphs[paragraph_index]
        deleted_text = para.getString()
        _delete_paragraph(text_obj, para)
        return json.dumps(
            {
                "success": True,
                "mode": "paragraph",
                "deleted_preview": preview(deleted_text),
                "count": 1,
                "warning": "Paragraph indices have shifted. Call get_document for updated indices.",
            }
        )

    if has_range:
        paragraphs = _enumerate_paragraphs(doc)
        assert start_index is not None and end_index is not None
        if end_index >= len(paragraphs):
            return json.dumps(
                {
                    "error": (
                        f"end_index {end_index} out of range "
                        f"(document has {len(paragraphs)} paragraphs)."
                    ),
                    "recovery": (
                        f"Use 0 to {len(paragraphs) - 1}. Call get_document for valid indices."
                    ),
                }
            )
        # Delete back-to-front to keep earlier indices stable.
        for i in range(end_index, start_index - 1, -1):
            _delete_paragraph(text_obj, paragraphs[i])
        return json.dumps(
            {
                "success": True,
                "mode": "range",
                "count": end_index - start_index + 1,
                "deleted_range": {"start": start_index, "end": end_index},
                "warning": "Paragraph indices have shifted. Call get_document for updated indices.",
            }
        )

    # Query mode.
    assert query is not None
    searcher = doc.createSearchDescriptor()
    searcher.SearchString = query
    searcher.SearchCaseSensitive = match_case
    results = doc.findAll(searcher)
    total = results.getCount() if results is not None else 0
    if total == 0:
        return json.dumps(
            {
                "success": True,
                "mode": "query",
                "count": 0,
                "hint": (
                    f'No matches found for "{preview(query, 60)}". '
                    f"Use get_document to check exact text."
                ),
            }
        )
    for i in range(total):
        results.getByIndex(i).setString("")
    return json.dumps(
        {
            "success": True,
            "mode": "query",
            "query": preview(query, 60),
            "count": total,
            "hint": (
                f"Deleted {total} occurrence{'s' if total != 1 else ''}. "
                f"Paragraph structure preserved."
            ),
        }
    )


def _delete_paragraph(text_obj: Any, para: Any) -> None:
    """Remove a single paragraph and its trailing paragraph break."""
    cursor = text_obj.createTextCursorByRange(para.getStart())
    cursor.gotoEndOfParagraph(True)
    # Extend selection by one character to swallow the paragraph break,
    # unless we're at the very end of the document.
    try:
        cursor.goRight(1, True)
    except Exception:
        logger.debug("goRight at end of doc; deleting paragraph in place")
    cursor.setString("")


# ---------------------------------------------------------------------------
# edit_table
# ---------------------------------------------------------------------------


@tool
@ui_thread_tool
def edit_table(
    table_index: int,
    action: str,
    row: int | None = None,
    column: int | None = None,
    value: str | None = None,
    count: int = 1,
    values: list[list[str]] | None = None,
    insert_location: str | None = None,
) -> str:
    """Modify an existing table in the document.

    PREREQUISITE: Call get_document to get table indices, row counts,
    and column counts. Actions:

    - ``edit_cell``     — set a cell's text (requires row, column, value)
    - ``add_rows``      — add rows at start or end
    - ``delete_rows``   — remove rows starting at ``row``
    - ``add_columns``   — add columns (uniform tables only)
    - ``delete_columns`` — remove columns (uniform tables only)

    Args:
        table_index: Zero-based table index from get_document.
        action: One of the five actions above.
        row: Zero-based row index. Required for edit_cell / delete_rows.
        column: Zero-based column index. Required for edit_cell / delete_columns.
        value: New cell text for edit_cell.
        count: Number of rows/columns to add or delete. Defaults to 1.
        values: Optional 2D string array to populate added rows/columns.
            Currently ignored by the LibreOffice backend — see
            investigations.md #16.
        insert_location: ``start`` or ``end``. Required for
            add_rows / add_columns.

    Returns:
        JSON string with success / action / detail.

    Raises:
        WriterDocumentRequiredError: If no Writer document is active.
    """
    # Case-insensitive enum args (schema enums dropped — see Writer #5).
    action = lower_enum(action) or ""
    insert_location = lower_enum(insert_location)

    if not isinstance(table_index, int) or table_index < 0:
        return json.dumps(
            {
                "error": "table_index must be a non-negative number.",
                "recovery": "Call get_document to see available tables.",
            }
        )

    ext = get_extension_or_raise()
    doc = get_writer_document(ext.ctx)
    tables = doc.getTextTables()
    if table_index >= tables.getCount():
        return json.dumps(
            {
                "error": (
                    f"table_index {table_index} out of range "
                    f"(document has {tables.getCount()} tables)."
                ),
                "recovery": f"Use 0 to {tables.getCount() - 1}.",
            }
        )
    table = tables.getByIndex(table_index)
    cnt = max(1, int(count))

    if action == "edit_cell":
        if row is None or column is None:
            return json.dumps(
                {
                    "error": "edit_cell requires row and column.",
                    "recovery": "Provide both row and column (zero-based).",
                }
            )
        if value is None:
            return json.dumps(
                {
                    "error": "edit_cell requires value.",
                    "recovery": "Provide the new cell text as value.",
                }
            )
        if row >= table.getRows().getCount() or column >= table.getColumns().getCount():
            return json.dumps(
                {
                    "error": (
                        f"Cell ({row}, {column}) out of range for "
                        f"{table.getRows().getCount()}x{table.getColumns().getCount()} table."
                    ),
                    "recovery": "Call get_document for current table dimensions.",
                }
            )
        table.getCellByPosition(column, row).setString(value)
        return json.dumps(
            {
                "success": True,
                "table_index": table_index,
                "action": action,
                "detail": f'Cell ({row}, {column}) set to "{preview(value, 60)}"',
            }
        )

    if action == "add_rows":
        if insert_location not in ("start", "end"):
            return json.dumps(
                {
                    "error": "add_rows requires insert_location.",
                    "recovery": "Provide insert_location: 'start' or 'end'.",
                }
            )
        insert_at = 0 if insert_location == "start" else table.getRows().getCount()
        table.getRows().insertByIndex(insert_at, cnt)
        return json.dumps(
            {
                "success": True,
                "table_index": table_index,
                "action": action,
                "detail": (f"Added {cnt} row{'s' if cnt != 1 else ''} at {insert_location}"),
            }
        )

    if action == "delete_rows":
        if row is None:
            return json.dumps(
                {
                    "error": "delete_rows requires row.",
                    "recovery": "Provide the zero-based row index to start deleting from.",
                }
            )
        if row + cnt > table.getRows().getCount():
            return json.dumps(
                {
                    "error": (
                        f"Cannot delete {cnt} rows starting at {row} from a "
                        f"{table.getRows().getCount()}-row table."
                    ),
                    "recovery": "Reduce count or pick a smaller row index.",
                }
            )
        table.getRows().removeByIndex(row, cnt)
        return json.dumps(
            {
                "success": True,
                "table_index": table_index,
                "action": action,
                "detail": (f"Deleted {cnt} row{'s' if cnt != 1 else ''} starting at row {row}"),
                "warning": (
                    "Row indices have shifted. Call get_document for updated table dimensions."
                ),
            }
        )

    if action == "add_columns":
        if not _is_uniform_table(table):
            return json.dumps(
                {
                    "error": "add_columns requires a uniform table (no merged cells).",
                    "recovery": "Check table structure with get_document.",
                }
            )
        if insert_location not in ("start", "end"):
            return json.dumps(
                {
                    "error": "add_columns requires insert_location.",
                    "recovery": "Provide insert_location: 'start' or 'end'.",
                }
            )
        insert_at = 0 if insert_location == "start" else table.getColumns().getCount()
        table.getColumns().insertByIndex(insert_at, cnt)
        return json.dumps(
            {
                "success": True,
                "table_index": table_index,
                "action": action,
                "detail": (f"Added {cnt} column{'s' if cnt != 1 else ''} at {insert_location}"),
            }
        )

    if action == "delete_columns":
        if not _is_uniform_table(table):
            return json.dumps(
                {
                    "error": "delete_columns requires a uniform table.",
                    "recovery": "Check table structure with get_document.",
                }
            )
        if column is None:
            return json.dumps(
                {
                    "error": "delete_columns requires column.",
                    "recovery": "Provide the zero-based column index.",
                }
            )
        if column + cnt > table.getColumns().getCount():
            return json.dumps(
                {
                    "error": (
                        f"Cannot delete {cnt} columns starting at {column} from a "
                        f"{table.getColumns().getCount()}-column table."
                    ),
                    "recovery": "Reduce count or pick a smaller column index.",
                }
            )
        table.getColumns().removeByIndex(column, cnt)
        return json.dumps(
            {
                "success": True,
                "table_index": table_index,
                "action": action,
                "detail": (
                    f"Deleted {cnt} column{'s' if cnt != 1 else ''} starting at column {column}"
                ),
                "warning": (
                    "Column indices have shifted. Call get_document for updated table dimensions."
                ),
            }
        )

    return json.dumps(
        {
            "error": f'Unknown action "{action}".',
            "recovery": (
                "Use one of: edit_cell, add_rows, delete_rows, add_columns, delete_columns."
            ),
        }
    )


# ---------------------------------------------------------------------------
# Public list — see ADR-0019 (tool registry aggregation).
# ---------------------------------------------------------------------------


TOOLS = [
    insert_content,
    insert_table,
    insert_image,
    undo_redo,
    delete_content,
    edit_table,
]
