"""Formatting tools — font / paragraph / list formatting on the active doc.

Faithful port of ``Talk2View-Word/src/taskpane/tools/formatting.ts``:

- :func:`format_text`      — inline font formatting (single or 20-region batch)
- :func:`format_paragraph` — paragraph-level (style, alignment, spacing, indent)
- :func:`manage_list`      — bullet / number list create or remove

Return shapes mirror Word per ADR-0021. Behavioural deltas noted in
``docs/investigations.md`` (style names, mixed-formatting paragraphs).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from talk2view import tool  # type: ignore[import-not-found]

from talk2view_writer.extension import get_extension_or_raise
from talk2view_writer.tools._base import (
    get_writer_document,
    suspend_record_changes,
    ui_thread_tool,
)
from talk2view_writer.tools._constants import (
    HIGHLIGHT_COLOR_RGB,
    HIGHLIGHT_COLORS,
    UNDERLINE_STYLE_UNO,
    VALID_STYLES,
    hex_to_rgb_int,
    lower_enum,
    points_to_hmm,
    preview,
)
from talk2view_writer.tools.writing import (
    _apply_paragraph_format,
    _enumerate_paragraphs,
)
from talk2view_writer.uno_helpers.styles import word_to_libreoffice_style

logger = logging.getLogger(__name__)

# Keys that mark per-item formatting payloads in format_text.
_FORMATTING_KEYS = (
    "bold",
    "italic",
    "underline",
    "underline_style",
    "strikethrough",
    "superscript",
    "subscript",
    "color",
    "size",
    "font",
    "highlight",
)

_PARAGRAPH_FORMAT_KEYS = (
    "style",
    "alignment",
    "space_before",
    "space_after",
    "line_spacing",
    "left_indent",
    "right_indent",
    "first_line_indent",
    "keep_together",
    "keep_with_next",
    "page_break_before",
)

_HEX_RE = re.compile(r"^[0-9A-Fa-f]{6}$")


def _validate_format_fields(item: dict[str, Any]) -> dict[str, str] | None:
    """Word-faithful validation of an inline format payload."""
    if item.get("superscript") is True and item.get("subscript") is True:
        return {
            "error": "Cannot set both superscript and subscript to true.",
            "recovery": "Set only one of superscript or subscript to true.",
        }
    color = item.get("color")
    if isinstance(color, str):
        if color.startswith("#"):
            return {
                "error": "color should not include the # prefix.",
                "recovery": f'Use "{color[1:]}" instead of "{color}".',
            }
        if not _HEX_RE.match(color):
            return {
                "error": f'Invalid color format "{color}".',
                "recovery": 'Use a 6-character hex color without #. Example: "FF0000" for red.',
            }
    size = item.get("size")
    if isinstance(size, (int, float)) and not isinstance(size, bool) and size <= 0:
        return {
            "error": "Font size must be positive.",
            "recovery": "Use a value like 11, 12, or 14.",
        }
    highlight = item.get("highlight")
    if isinstance(highlight, str) and highlight not in HIGHLIGHT_COLORS:
        return {
            "error": f'Invalid highlight color "{highlight}".',
            "recovery": f"Use one of: {', '.join(HIGHLIGHT_COLORS)}.",
        }
    return None


def _apply_inline_formatting(cursor: Any, item: dict[str, Any]) -> None:
    """Apply font properties from a Word-shaped payload to a UNO cursor."""
    if isinstance(item.get("bold"), bool):
        # UNO CharWeight: NORMAL=100.0, BOLD=150.0.
        cursor.CharWeight = 150.0 if item["bold"] else 100.0
    if isinstance(item.get("italic"), bool):
        # UNO CharPosture: NONE=0 (ITALIC=2 in com.sun.star.awt.FontSlant).
        cursor.CharPosture = 2 if item["italic"] else 0
    if isinstance(item.get("strikethrough"), bool):
        # com.sun.star.awt.FontStrikeout: NONE=0, SINGLE=1.
        cursor.CharStrikeout = 1 if item["strikethrough"] else 0
    if isinstance(item.get("superscript"), bool):
        # CharEscapement: positive int for super, negative for sub.
        # CharEscapementHeight: percent of normal height (Word default ~58).
        if item["superscript"]:
            cursor.CharEscapement = 33
            cursor.CharEscapementHeight = 58
        else:
            cursor.CharEscapement = 0
            cursor.CharEscapementHeight = 100
    if isinstance(item.get("subscript"), bool):
        if item["subscript"]:
            cursor.CharEscapement = -33
            cursor.CharEscapementHeight = 58
        else:
            cursor.CharEscapement = 0
            cursor.CharEscapementHeight = 100
    underline_style = item.get("underline_style")
    if isinstance(underline_style, str) and underline_style in UNDERLINE_STYLE_UNO:
        cursor.CharUnderline = UNDERLINE_STYLE_UNO[underline_style]
    elif isinstance(item.get("underline"), bool):
        cursor.CharUnderline = 1 if item["underline"] else 0
    color = item.get("color")
    if isinstance(color, str):
        cursor.CharColor = hex_to_rgb_int(color)
    size = item.get("size")
    if isinstance(size, (int, float)) and not isinstance(size, bool):
        cursor.CharHeight = float(size)
    font_name = item.get("font")
    if isinstance(font_name, str):
        cursor.CharFontName = font_name
    highlight = item.get("highlight")
    if isinstance(highlight, str):
        cursor.CharHighlight = HIGHLIGHT_COLOR_RGB[highlight]


# ---------------------------------------------------------------------------
# format_text
# ---------------------------------------------------------------------------


@tool
@ui_thread_tool
def format_text(
    queries: list[dict[str, Any]] | None = None,
    query: str | None = None,
    match_index: int = 0,
    paragraph_index: int | None = None,
    bold: bool | None = None,
    italic: bool | None = None,
    underline: bool | None = None,
    underline_style: str | None = None,
    strikethrough: bool | None = None,
    superscript: bool | None = None,
    subscript: bool | None = None,
    color: str | None = None,
    size: float | None = None,
    font: str | None = None,
    highlight: str | None = None,
) -> str:
    """Apply inline font formatting to text.

    ONE CALL, MANY REGIONS: prefer the ``queries`` array to format up
    to 20 different text regions with different properties in a single
    call. Single-target mode: use ``query`` (find text),
    ``paragraph_index`` (whole paragraph), or neither (current selection).
    For heading level / alignment / spacing, use format_paragraph.
    Never fake headings with bold + size.

    Args:
        queries: Batch mode. List of ``{query|paragraph_index,
            match_index?, ...format fields}`` — up to 20 items.
        query: Single-target text to find and format (case-insensitive).
            Mutually exclusive with paragraph_index and queries.
        match_index: Which match to format when query finds multiple.
            Defaults to 0.
        paragraph_index: Format every character in this paragraph.
        bold: True for bold, False to remove. Omit to leave unchanged.
        italic: True / False / omit.
        underline: True / False / omit. For non-single styles use
            ``underline_style``.
        underline_style: One of ``none``, ``single``, ``double``,
            ``dotted``, ``dashed``, ``wave``.
        strikethrough: True / False / omit.
        superscript: True / False / omit. Mutually exclusive with subscript.
        subscript: True / False / omit. Mutually exclusive with superscript.
        color: 6-char hex without ``#``. E.g. ``FF0000``.
        size: Font size in points. > 0.
        font: Font family name as displayed.
        highlight: Named highlight colour. Use ``NoColor`` to remove.

    Returns:
        JSON string. Single mode returns one result object; batch
        returns ``{success, formatted, total, results}``.

    Raises:
        WriterDocumentRequiredError: If no Writer document is active.
    """
    is_batch = queries is not None
    # Build a single-item dict from kwargs for single-mode reuse.
    single_item: dict[str, Any] = {
        k: v
        for k, v in {
            "query": query,
            "paragraph_index": paragraph_index,
            "match_index": match_index,
            "bold": bold,
            "italic": italic,
            "underline": underline,
            "underline_style": underline_style,
            "strikethrough": strikethrough,
            "superscript": superscript,
            "subscript": subscript,
            "color": color,
            "size": size,
            "font": font,
            "highlight": highlight,
        }.items()
        if v is not None
    }

    has_single = query is not None or paragraph_index is not None

    if is_batch and has_single:
        return json.dumps(
            {
                "error": (
                    "Provide either queries (batch) or query/paragraph_index (single), not both."
                ),
                "recovery": "Move single-item inputs into the queries array, or remove the array.",
            }
        )
    if is_batch:
        if not queries:
            return json.dumps(
                {"error": "queries array is empty.", "recovery": "Provide at least one item."}
            )
        if len(queries) > 20:
            return json.dumps(
                {
                    "error": f"queries has {len(queries)} items; maximum is 20.",
                    "recovery": "Split into multiple calls of up to 20 items each.",
                }
            )
        for i, q in enumerate(queries):
            provided = [k for k in _FORMATTING_KEYS if k in q]
            if not provided:
                return json.dumps(
                    {
                        "error": f"queries[{i}] has no formatting properties.",
                        "recovery": (
                            "Each item must include at least one of "
                            "bold/italic/underline/color/size/font/highlight/..."
                        ),
                    }
                )
            if "query" not in q and "paragraph_index" not in q:
                return json.dumps(
                    {
                        "error": f"queries[{i}] must provide query or paragraph_index.",
                        "recovery": "Add a query string or paragraph_index to target the text.",
                    }
                )
            if "query" in q and "paragraph_index" in q:
                return json.dumps(
                    {
                        "error": f"queries[{i}] has both query and paragraph_index.",
                        "recovery": "Use one target per item.",
                    }
                )
            item_err = _validate_format_fields(q)
            if item_err:
                return json.dumps(
                    {
                        "error": f"queries[{i}]: {item_err['error']}",
                        "recovery": item_err["recovery"],
                    }
                )
    else:
        provided = [k for k in _FORMATTING_KEYS if k in single_item]
        if not provided:
            return json.dumps(
                {
                    "error": "No formatting properties provided.",
                    "recovery": (
                        "Include at least one of: bold, italic, underline, color, size, font, "
                        "highlight, strikethrough, superscript, subscript, underline_style."
                    ),
                }
            )
        err = _validate_format_fields(single_item)
        if err:
            return json.dumps(err)
        if query is not None and paragraph_index is not None:
            return json.dumps(
                {
                    "error": "Provide either query or paragraph_index, not both.",
                    "recovery": (
                        "Use query to find text by content, or paragraph_index "
                        "to target a whole paragraph."
                    ),
                }
            )

    ext = get_extension_or_raise()
    doc = get_writer_document(ext.ctx)
    text_obj = doc.getText()
    items: list[dict[str, Any]] = list(queries) if queries is not None else [single_item]
    results: list[dict[str, Any]] = []

    for i, item in enumerate(items):
        resolved = _resolve_format_target(doc, text_obj, item)
        if "error" in resolved:
            results.append({"index": i, **resolved})
            continue
        cursor = resolved["cursor"]
        _apply_inline_formatting(cursor, item)
        applied = {k: item[k] for k in _FORMATTING_KEYS if k in item}
        base: dict[str, Any] = {
            "success": True,
            "formatted_text": preview(resolved["text"], 60),
            "formatting_applied": applied,
            "char_count": len(resolved["text"]),
        }
        if "total_matches" in resolved:
            base["total_matches"] = resolved["total_matches"]
            base["match_index"] = resolved["match_index"]
        results.append(base)

    if is_batch:
        successes = sum(1 for r in results if r.get("success"))
        return json.dumps(
            {
                "success": successes == len(results),
                "formatted": successes,
                "total": len(results),
                "results": results,
            }
        )
    return json.dumps(results[0])


def _resolve_format_target(doc: Any, text_obj: Any, item: dict[str, Any]) -> dict[str, Any]:
    """Resolve a format item to ``{cursor, text, ...}`` or ``{error, recovery}``."""
    q = item.get("query")
    pidx = item.get("paragraph_index")
    if isinstance(q, str):
        searcher = doc.createSearchDescriptor()
        searcher.SearchString = q
        searcher.SearchCaseSensitive = False
        found = doc.findAll(searcher)
        total = found.getCount() if found is not None else 0
        if total == 0:
            return {
                "error": f'Text "{preview(q, 60)}" not found.',
                "recovery": "Use get_document to check exact text.",
            }
        mi = item.get("match_index", 0) or 0
        if mi < 0 or mi >= total:
            return {
                "error": f"match_index {mi} out of range ({total} matches).",
                "recovery": f"Use 0 to {total - 1}.",
            }
        rng = found.getByIndex(mi)
        cursor = text_obj.createTextCursorByRange(rng.getStart())
        cursor.gotoEndOfWord(False)
        # Better: create a cursor spanning rng.start to rng.end.
        cursor = text_obj.createTextCursorByRange(rng)
        return {
            "cursor": cursor,
            "text": rng.getString(),
            "total_matches": total,
            "match_index": mi,
        }
    if isinstance(pidx, int):
        paragraphs = _enumerate_paragraphs(doc)
        if pidx < 0 or pidx >= len(paragraphs):
            return {
                "error": f"paragraph_index {pidx} out of range ({len(paragraphs)} paragraphs).",
                "recovery": f"Use 0 to {len(paragraphs) - 1}. Call get_document for valid indices.",
            }
        para = paragraphs[pidx]
        cursor = text_obj.createTextCursorByRange(para.getStart())
        cursor.gotoEndOfParagraph(True)
        return {"cursor": cursor, "text": para.getString()}
    # Fall back to the current selection.
    controller = doc.getCurrentController()
    selection = controller.getSelection()
    if selection is None or not hasattr(selection, "getCount") or selection.getCount() == 0:
        return {
            "error": "No selection available.",
            "recovery": "Highlight text in the document, or use select_text first.",
        }
    rng = selection.getByIndex(0)
    cursor = text_obj.createTextCursorByRange(rng)
    return {"cursor": cursor, "text": rng.getString()}


# ---------------------------------------------------------------------------
# format_paragraph
# ---------------------------------------------------------------------------


@tool
@ui_thread_tool
def format_paragraph(
    paragraph_index: int | None = None,
    paragraph_indices: list[int] | None = None,
    style: str | None = None,
    alignment: str | None = None,
    space_before: float | None = None,
    space_after: float | None = None,
    line_spacing: float | None = None,
    left_indent: float | None = None,
    right_indent: float | None = None,
    first_line_indent: float | None = None,
    keep_together: bool | None = None,
    keep_with_next: bool | None = None,
    page_break_before: bool | None = None,
) -> str:
    """Paragraph-level formatting: style, alignment, spacing, indents, breaks.

    Use ``paragraph_indices`` to format multiple paragraphs in one call.
    For inline font formatting (bold, italic, color) use format_text.
    Only provided properties change.

    Args:
        paragraph_index: Single zero-based paragraph index. Mutex with
            paragraph_indices.
        paragraph_indices: List of zero-based indices to format.
        style: Built-in Word style name (Heading1, Title, Quote, etc.).
        alignment: ``left`` / ``center`` / ``right`` / ``justified``.
        space_before: Points >= 0.
        space_after: Points >= 0.
        line_spacing: Points > 0.
        left_indent: Points.
        right_indent: Points.
        first_line_indent: Points (negative = hanging).
        keep_together: Keep all lines of the paragraph on one page.
        keep_with_next: Keep on same page as the next paragraph.
        page_break_before: Force a page break before this paragraph.

    Returns:
        JSON string with success / paragraphs_formatted / results.

    Raises:
        WriterDocumentRequiredError: If no Writer document is active.
    """
    has_single = paragraph_index is not None
    has_batch = paragraph_indices is not None
    if has_single and has_batch:
        return json.dumps(
            {
                "error": (
                    "Provide either paragraph_index (single) or "
                    "paragraph_indices (batch), not both."
                ),
                "recovery": (
                    "Use paragraph_index for one paragraph, or paragraph_indices for multiple."
                ),
            }
        )
    if not has_single and not has_batch:
        return json.dumps(
            {
                "error": "No paragraph targeted.",
                "recovery": (
                    "Provide paragraph_index (single) or paragraph_indices "
                    "(array) from get_document."
                ),
            }
        )
    args_dict = {
        "style": style,
        "alignment": alignment,
        "space_before": space_before,
        "space_after": space_after,
        "line_spacing": line_spacing,
        "left_indent": left_indent,
        "right_indent": right_indent,
        "first_line_indent": first_line_indent,
        "keep_together": keep_together,
        "keep_with_next": keep_with_next,
        "page_break_before": page_break_before,
    }
    if not any(v is not None for v in args_dict.values()):
        return json.dumps(
            {
                "error": "No formatting properties provided.",
                "recovery": f"Include at least one of: {', '.join(_PARAGRAPH_FORMAT_KEYS)}.",
            }
        )
    if style is not None and style not in VALID_STYLES:
        return json.dumps(
            {
                "error": f'Unknown style "{style}".',
                "recovery": f"Use one of: {', '.join(VALID_STYLES)}.",
            }
        )
    if space_before is not None and space_before < 0:
        return json.dumps(
            {
                "error": "space_before must be >= 0.",
                "recovery": "Provide a non-negative value in points.",
            }
        )
    if space_after is not None and space_after < 0:
        return json.dumps(
            {
                "error": "space_after must be >= 0.",
                "recovery": "Provide a non-negative value in points.",
            }
        )
    if line_spacing is not None and line_spacing <= 0:
        return json.dumps(
            {
                "error": "line_spacing must be > 0.",
                "recovery": "Provide a positive value in points.",
            }
        )
    if alignment is not None:
        from talk2view_writer.tools.writing import _ALIGNMENT_MAP

        if alignment not in _ALIGNMENT_MAP:
            return json.dumps(
                {
                    "error": f'Unknown alignment "{alignment}".',
                    "recovery": (
                        "Use one of: "
                        + ", ".join(sorted(_ALIGNMENT_MAP))
                    ),
                }
            )
    if has_single:
        assert paragraph_index is not None
        if paragraph_index < 0:
            return json.dumps(
                {
                    "error": "paragraph_index must be a non-negative integer.",
                    "recovery": "Call get_document to see valid paragraph indices (0-based).",
                }
            )

    ext = get_extension_or_raise()
    doc = get_writer_document(ext.ctx)
    paragraphs = _enumerate_paragraphs(doc)
    indices: list[int] = (
        list(paragraph_indices) if paragraph_indices is not None else [paragraph_index]  # type: ignore[list-item]
    )

    results: list[dict[str, Any]] = []
    for idx in indices:
        assert idx is not None
        if idx < 0 or idx >= len(paragraphs):
            results.append(
                {"index": idx, "error": f"Index {idx} out of range ({len(paragraphs)} paragraphs)"}
            )
            continue
        p = paragraphs[idx]
        if style is not None:
            from com.sun.star.uno import (  # type: ignore[import-not-found]
                RuntimeException,
            )

            lo_style = word_to_libreoffice_style(style)
            if not _paragraph_style_exists(doc, lo_style):
                # The build ships no such paragraph style (LO 26.2 has none
                # of the list styles — investigation #50). Degrade to a
                # structured per-paragraph error instead of letting the raw
                # UNO RuntimeException crash the tool call.
                results.append(
                    {
                        "index": idx,
                        "error": (
                            f'Paragraph style "{style}" (LibreOffice '
                            f'"{lo_style}") is not registered in this build.'
                        ),
                        "recovery": (
                            "This build ships no matching paragraph style. "
                            "For bulleted/numbered lists use manage_list (it "
                            "applies real list numbering without needing a "
                            "list style). For headings or quotes pick a style "
                            "that exists, or omit style and set alignment / "
                            "spacing / indents directly."
                        ),
                    }
                )
                continue
            with suspend_record_changes(doc):
                try:
                    p.ParaStyleName = lo_style
                except RuntimeException:
                    # Even when the style exists, an active track-changes
                    # redline can reject the write (cf.
                    # writing.py::_insert_paragraph_with_style). Degrade
                    # rather than propagate the raw UNO error; logger keeps
                    # the trace.
                    logger.exception(
                        "Could not apply paragraph style %r (track-changes "
                        "redline constraint); left existing style",
                        style,
                    )
                    results.append(
                        {
                            "index": idx,
                            "error": (
                                f'Could not apply style "{style}" to '
                                f"paragraph {idx}."
                            ),
                            "recovery": (
                                "The paragraph may be inside a tracked change; "
                                "accept/reject changes or retry without style."
                            ),
                        }
                    )
                    continue
        _apply_paragraph_format(
            p,
            alignment=alignment,
            space_before=space_before,
            space_after=space_after,
            line_spacing=line_spacing,
            left_indent=left_indent,
            right_indent=right_indent,
            first_line_indent=first_line_indent,
        )
        # Best-effort flow properties.
        for attr_name, value in (
            ("ParaKeepTogether", keep_together),
            ("ParaSplit", None if keep_together is None else not keep_together),
            ("ParaKeepWithNext", keep_with_next),
            ("BreakType", None if page_break_before is None else (4 if page_break_before else 0)),
        ):
            if value is None:
                continue
            try:
                setattr(p, attr_name, value)
            except Exception:
                logger.debug("Property %s not settable on this LibreOffice build", attr_name)
        results.append(
            {
                "index": idx,
                "success": True,
                "text": preview(p.getString()),
                "resulting_style": getattr(p, "ParaStyleName", "") or "",
            }
        )

    applied = {k: v for k, v in args_dict.items() if v is not None}
    if has_batch:
        return json.dumps(
            {
                "success": True,
                "paragraphs_formatted": sum(1 for r in results if r.get("success")),
                "results": results,
                "formatting_applied": applied,
            }
        )
    single = results[0]
    if "error" in single:
        # A per-paragraph result that carries its own recovery (e.g. a
        # missing-style degrade) is surfaced verbatim; the out-of-range
        # result has no recovery key, so keep the original index message.
        if "recovery" in single:
            return json.dumps(
                {"error": single["error"], "recovery": single["recovery"]}
            )
        return json.dumps(
            {
                "error": (
                    f"Paragraph index {indices[0]} out of range ({len(paragraphs)} paragraphs)."
                ),
                "recovery": (
                    f"Use an index from 0 to {len(paragraphs) - 1}. "
                    f"Call get_document to see valid indices."
                ),
            }
        )
    return json.dumps(
        {
            "success": True,
            "paragraph_index": indices[0],
            "paragraph_text": single["text"],
            "resulting_style": single["resulting_style"],
            "formatting_applied": applied,
        }
    )


# ---------------------------------------------------------------------------
# manage_list
# ---------------------------------------------------------------------------


# Aliases tried in priority order against this LO build's
# ParagraphStyles family. The first one that actually exists is used
# for ``p.ParaStyleName = …``. Different builds / localisations ship
# different names: LO 24.x apt favours "List Bullet" / "List Number";
# the Word-compat "List Paragraph" exists in some builds. See
# Investigation #37 for the bug that motivated the resolver.
_BULLET_STYLE_ALIASES = (
    "List Bullet",
    "Bulleted List",
    "List Paragraph",
    "ListBullet",  # no-space variant on some 24.x builds
)
_NUMBER_STYLE_ALIASES = (
    "List Number",
    "Numbered List",
    "List Paragraph",
    "ListNumber",
)


class _ListStyleUnavailableError(Exception):
    """Raised when none of the known list-style aliases exists in the doc.

    Surfaced to the caller as a structured JSON error with a recovery
    hint, not as a raw exception — manage_list catches and converts.
    """


def _resolve_list_style(doc: Any, list_type: str) -> str:
    """Pick the first valid LO paragraph style name for a bullet / numbered list.

    Args:
        doc: Active Writer document.
        list_type: Either ``bullet`` or ``number``.

    Returns:
        A style name that exists in ``doc.StyleFamilies['ParagraphStyles']``.

    Raises:
        _ListStyleUnavailableError: if none of the known aliases exist.
    """
    aliases = (
        _BULLET_STYLE_ALIASES
        if list_type == "bullet"
        else _NUMBER_STYLE_ALIASES
    )
    families = doc.getStyleFamilies()
    paragraph_styles = families.getByName("ParagraphStyles")
    for name in aliases:
        if paragraph_styles.hasByName(name):
            return name
    raise _ListStyleUnavailableError(
        f"None of the known {list_type} list paragraph styles "
        f"({', '.join(aliases)}) are registered in this LibreOffice build."
    )


def _try_resolve_list_style(doc: Any, list_type: str) -> str | None:
    """Return a list paragraph-style name if this build has one, else None.

    LO 26.2 registers none of the list paragraph styles, so the list marker
    must come from NumberingRules (:func:`_build_numbering_rules`). A style
    is applied on top only as a Styles-sidebar nicety on builds that have
    one — never depended on.
    """
    try:
        return _resolve_list_style(doc, list_type)
    except _ListStyleUnavailableError:
        return None


def _paragraph_style_exists(doc: Any, lo_style: str) -> bool:
    """``True`` if ``lo_style`` is a registered paragraph style on this build.

    Assigning ``ParaStyleName`` a style the build does not ship makes
    LibreOffice throw ``com.sun.star.uno.RuntimeException``. LO 26.2 ships
    none of the list paragraph styles (investigation #50), so a tool that
    blindly sets, e.g., ``"List Bullet"`` crashes with a raw UNO error
    instead of degrading. Check existence first via the same
    ``StyleFamilies`` gate :func:`_resolve_list_style` uses.
    """
    families = doc.getStyleFamilies()
    return bool(families.getByName("ParagraphStyles").hasByName(lo_style))


# NumberingType values from com.sun.star.style.NumberingType, hardcoded to
# their stable UNO constants so the helper needs no getConstantByName round
# trip and stays unit-testable. ARABIC = 4 (1. 2. 3.); CHAR_SPECIAL = 6 (a
# bullet glyph).
_NUMBERING_TYPE_ARABIC = 4
_NUMBERING_TYPE_CHAR_SPECIAL = 6
_BULLET_CHAR = "•"  # •


def _make_property_value(name: str, value: Any) -> Any:
    """Build a ``com.sun.star.beans.PropertyValue`` struct."""
    import uno

    pv = uno.createUnoStruct("com.sun.star.beans.PropertyValue")
    pv.Name = name
    pv.Value = value
    return pv


def _build_numbering_rules(doc: Any, list_type: str, level: int) -> Any:
    """Build a NumberingRules object for a bullet / numbered list.

    Works on every LibreOffice build because it does not rely on a list
    paragraph style existing — it configures the document's own
    NumberingRules, which the caller assigns to each paragraph's
    ``NumberingRules`` property. Levels 0..``level`` are configured so the
    requested nesting level renders. The same object is shared across the
    target paragraphs so they form one continuous list (1. 2. 3. for
    numbers).

    Each level is replaced with a **minimal, explicit** PropertyValue set —
    only the few properties that define the marker. It deliberately does
    NOT read the level's current properties via ``getByIndex`` and re-submit
    them; partial sets merge cleanly with each level's existing defaults on
    every build.

    The property sequence is passed to ``replaceByIndex`` wrapped in an
    explicit ``uno.Any("[]com.sun.star.beans.PropertyValue", ...)``. That
    wrapper is **not** optional: ``replaceByIndex``'s second parameter is
    typed ``any``, and PyUNO marshals a bare Python tuple as
    ``Sequence<Any>``. LibreOffice's ``SvxUnoNumberingRules::replaceByIndex``
    then fails to extract it as ``Sequence<PropertyValue>`` and throws a
    message-less ``com.sun.star.lang.IllegalArgumentException`` — observed
    live on LO 26.2.3.2 (investigation #50). Wrapping in a typed ``uno.Any``
    makes PyUNO hand over a real ``Sequence<PropertyValue>`` so the ``>>=``
    succeeds. The in-process synthetic ``NumberingRules`` now enforces this
    same contract so the gap can't recur.
    """
    import uno

    rules = doc.createInstance("com.sun.star.text.NumberingRules")
    for lvl in range(level + 1):
        if list_type == "bullet":
            props = (
                _make_property_value(
                    "NumberingType", _NUMBERING_TYPE_CHAR_SPECIAL
                ),
                _make_property_value("BulletChar", _BULLET_CHAR),
                _make_property_value("BulletFontName", "OpenSymbol"),
            )
        else:
            props = (
                _make_property_value("NumberingType", _NUMBERING_TYPE_ARABIC),
                _make_property_value("Prefix", ""),
                _make_property_value("Suffix", "."),
            )
        rules.replaceByIndex(
            lvl, uno.Any("[]com.sun.star.beans.PropertyValue", props)
        )
    return rules


@tool
@ui_thread_tool
def manage_list(
    action: str,
    paragraph_indices: list[int],
    list_type: str | None = None,
    level: int = 0,
    left_indent: float | None = None,
    right_indent: float | None = None,
) -> str:
    """Create or remove bulleted / numbered lists on specific paragraphs.

    LibreOffice's list model is numbering-rule-based, not style-based: we
    build a ``NumberingRules`` object (:func:`_build_numbering_rules`) and
    assign it to each targeted paragraph's ``NumberingRules`` property,
    setting ``NumberingIsNumber`` / ``NumberingLevel`` alongside. This works
    on every build, including LO 26.2 which registers no list paragraph
    style. A list paragraph style is applied on top only when this build
    happens to ship one (a Styles-sidebar nicety); it is never required.
    ``remove`` clears ``NumberingRules`` and resets the paragraph style.

    Args:
        action: ``add`` or ``remove``.
        paragraph_indices: Zero-based paragraph indices. For ``add``,
            should be contiguous for a well-formed list.
        list_type: ``bullet`` or ``number``. Required for ``add``.
        level: Nesting level 0-8. Defaults to 0.
        left_indent: Optional left indent in points applied to every
            affected paragraph (fused — saves a follow-up call).
        right_indent: Optional right indent in points.

    Returns:
        JSON string.

    Raises:
        WriterDocumentRequiredError: If no Writer document is active.
    """
    # Case-insensitive enum args (schema enums dropped — see Writer #5).
    action = lower_enum(action) or ""
    list_type = lower_enum(list_type)

    if not paragraph_indices:
        return json.dumps(
            {
                "error": "paragraph_indices is empty.",
                "recovery": "Provide at least one paragraph index from get_document.",
            }
        )
    if action == "add" and not isinstance(list_type, str):
        return json.dumps(
            {
                "error": "list_type ('bullet' or 'number') is required when action is 'add'.",
                "recovery": "Add list_type: 'bullet' for unordered or 'number' for ordered.",
            }
        )
    if level < 0 or level > 8:
        return json.dumps(
            {
                "error": f"level {level} out of range.",
                "recovery": "Use a value from 0 (top level) to 8 (deepest nesting).",
            }
        )
    if action not in ("add", "remove"):
        return json.dumps(
            {"error": f"Unknown action '{action}'.", "recovery": "Use 'add' or 'remove'."}
        )

    ext = get_extension_or_raise()
    doc = get_writer_document(ext.ctx)
    paragraphs = _enumerate_paragraphs(doc)

    for idx in paragraph_indices:
        if idx < 0 or idx >= len(paragraphs):
            return json.dumps(
                {
                    "error": (
                        f"paragraph_indices contains {idx}, "
                        f"which is out of range ({len(paragraphs)} paragraphs)."
                    ),
                    "recovery": (
                        f"Use indices from 0 to {len(paragraphs) - 1}. Call get_document to check."
                    ),
                }
            )

    # Apply lists via the paragraph NumberingRules property (works on every
    # build) rather than depending on a "List Bullet" paragraph style that
    # LO 26.2 does not register — that gap made the model fall back to
    # typing literal "•" characters (Investigation #37 / #50). A list
    # paragraph style is applied on top only when this build happens to
    # register one (Styles-sidebar nicety); it is never required.
    rules = None
    style_name: str | None = None
    if action == "add":
        rules = _build_numbering_rules(doc, list_type or "bullet", level)
        style_name = _try_resolve_list_style(doc, list_type or "")

    for idx in paragraph_indices:
        p = paragraphs[idx]
        with suspend_record_changes(doc):
            if action == "add":
                if style_name:
                    p.ParaStyleName = style_name
                p.NumberingRules = rules
                p.NumberingIsNumber = True
                try:
                    p.NumberingLevel = level
                except Exception:
                    logger.debug("NumberingLevel not settable on this build")
            else:
                try:
                    p.NumberingRules = None
                except Exception:
                    logger.debug("NumberingRules not clearable on this build")
                p.NumberingIsNumber = False
                try:
                    p.ParaStyleName = "Default Paragraph Style"
                except Exception:
                    logger.debug("Default Paragraph Style not settable")
        if left_indent is not None:
            p.ParaLeftMargin = points_to_hmm(left_indent)
        if right_indent is not None:
            p.ParaRightMargin = points_to_hmm(right_indent)

    return json.dumps(
        {
            "success": True,
            "action": action,
            "list_type": list_type if action == "add" else None,
            "paragraphs_affected": len(paragraph_indices),
            "level": level if action == "add" else None,
            "left_indent": left_indent,
            "right_indent": right_indent,
        }
    )


TOOLS = [format_text, format_paragraph, manage_list]
