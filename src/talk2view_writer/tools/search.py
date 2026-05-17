"""Search tools — search and replace in the active document body.

One tool: :func:`search_document`. Faithful port of
``Talk2View-Word/src/taskpane/tools/search.ts`` with optional
find-and-replace plus optional inline formatting on the replaced text.

UNO ``SearchDescriptor`` covers the search options Word exposes:

  match_case        -> SearchCaseSensitive
  match_whole_word  -> SearchWords
  match_wildcards   -> SearchRegularExpression (UNO doesn't speak Word
                       wildcards, but regex is closest — flagged as a
                       behavioural delta in investigations.md #19)
  match_prefix/suffix/ignore_punct/ignore_space — no UNO equivalents;
                       see investigation.

Return shapes mirror Word per ADR-0021.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from talk2view import tool  # type: ignore[import-not-found]

from talk2view_writer.extension import get_extension_or_raise
from talk2view_writer.tools._base import get_writer_document, ui_thread_tool
from talk2view_writer.tools._constants import HIGHLIGHT_COLORS, preview
from talk2view_writer.tools.formatting import _apply_inline_formatting

logger = logging.getLogger(__name__)

_REPLACE_FORMAT_KEYS = (
    "bold",
    "italic",
    "underline",
    "underline_style",
    "strikethrough",
    "color",
    "size",
    "font",
    "highlight",
)

_HEX_RE = re.compile(r"^[0-9A-Fa-f]{6}$")


def _validate_replace_format(f: dict[str, Any]) -> dict[str, str] | None:
    """Sub-validator for the ``replace_format`` payload."""
    color = f.get("color")
    if isinstance(color, str):
        if color.startswith("#"):
            return {
                "error": "replace_format.color should not include the # prefix.",
                "recovery": f'Use "{color[1:]}" instead of "{color}".',
            }
        if not _HEX_RE.match(color):
            return {
                "error": f'Invalid replace_format.color "{color}".',
                "recovery": 'Use a 6-character hex without #. Example: "FF0000".',
            }
    size = f.get("size")
    if isinstance(size, (int, float)) and not isinstance(size, bool) and size <= 0:
        return {"error": "replace_format.size must be > 0.", "recovery": "Use 11, 12, etc."}
    highlight = f.get("highlight")
    if isinstance(highlight, str) and highlight not in HIGHLIGHT_COLORS:
        return {
            "error": f'Invalid replace_format.highlight "{highlight}".',
            "recovery": f"Use one of: {', '.join(HIGHLIGHT_COLORS)}.",
        }
    return None


@tool
@ui_thread_tool
def search_document(
    query: str,
    replace_with: str | None = None,
    replace_format: dict[str, Any] | None = None,
    match_case: bool = False,
    match_whole_word: bool = False,
    match_wildcards: bool = False,
    match_prefix: bool = False,
    match_suffix: bool = False,
    ignore_punct: bool = False,
    ignore_space: bool = False,
) -> str:
    """Search the document body, optionally replace and format.

    Searches body text only (not headers / footers / comments / tables).
    For delete-all-matches, pass ``replace_with=""``. For
    replace-and-format, pass ``replace_format`` alongside
    ``replace_with``.

    Args:
        query: Text to search for. <=255 chars.
        replace_with: If provided, replaces ALL matches with this text.
            Use ``""`` to delete all matches.
        replace_format: Inline formatting applied to the replaced text.
            Same fields as format_text. Only applied with replace_with.
        match_case: Case-sensitive search. Defaults to False.
        match_whole_word: Whole-word match. Defaults to False.
        match_wildcards: Treat query as a regex (Writer's closest
            equivalent to Word wildcards — see Investigation #19).
        match_prefix: Match only at the start of words. Not directly
            supported by UNO — falls back to a regex prefix anchor.
        match_suffix: Match only at the end of words. Same fallback.
        ignore_punct: Ignore punctuation. Not supported by UNO; the
            flag is accepted but has no effect (Investigation #19).
        ignore_space: Ignore whitespace. Same as above.

    Returns:
        JSON string with ``query``, ``count`` or ``replacements``,
        ``matches`` or ``hint``, optional ``replace_format_applied``.

    Raises:
        WriterDocumentRequiredError: If no Writer document is active.
    """
    if not query or not query.strip():
        return json.dumps(
            {"error": "query is empty.", "recovery": "Provide the text to search for."}
        )
    if len(query) > 255:
        return json.dumps(
            {
                "error": f"query is {len(query)} characters; Word.search caps at 255.",
                "recovery": (
                    "Use a short unique phrase (3-15 words) from within the target text. "
                    "If you need to act on a long passage, use insert_content with "
                    "location='before_paragraph' / 'after_paragraph' using paragraph_index "
                    "from get_document instead."
                ),
            }
        )
    if replace_format is not None:
        if not isinstance(replace_with, str):
            return json.dumps(
                {
                    "error": "replace_format requires replace_with.",
                    "recovery": "Provide replace_with (possibly '') alongside replace_format.",
                }
            )
        if not isinstance(replace_format, dict):
            return json.dumps(
                {
                    "error": "replace_format must be an object.",
                    "recovery": 'Example: {"bold":true,"color":"FF0000"}.',
                }
            )
        provided = [k for k in _REPLACE_FORMAT_KEYS if k in replace_format]
        if not provided:
            return json.dumps(
                {
                    "error": "replace_format has no formatting properties.",
                    "recovery": f"Include at least one of: {', '.join(_REPLACE_FORMAT_KEYS)}.",
                }
            )
        err = _validate_replace_format(replace_format)
        if err:
            return json.dumps(err)

    ext = get_extension_or_raise()
    doc = get_writer_document(ext.ctx)
    text_obj = doc.getText()

    searcher = doc.createSearchDescriptor()
    if match_prefix or match_suffix:
        # Word's match_prefix / match_suffix have no UNO equivalent.
        # Approximate via regex anchors at word boundaries.
        regex = re.escape(query)
        if match_prefix:
            regex = r"\b" + regex
        if match_suffix:
            regex = regex + r"\b"
        searcher.SearchString = regex
        searcher.SearchRegularExpression = True
    else:
        searcher.SearchString = query
        searcher.SearchRegularExpression = match_wildcards
    searcher.SearchCaseSensitive = match_case
    searcher.SearchWords = match_whole_word
    # ignore_punct / ignore_space are accepted but no-op on Writer.
    _ = (ignore_punct, ignore_space)

    results = doc.findAll(searcher)
    total = results.getCount() if results is not None else 0
    matches = [results.getByIndex(i).getString() for i in range(total)]

    if isinstance(replace_with, str):
        replaced_cursors: list[Any] = []
        for i in range(total):
            rng = results.getByIndex(i)
            rng.setString(replace_with)
            replaced_cursors.append(text_obj.createTextCursorByRange(rng))

        if replace_format and replaced_cursors:
            for cur in replaced_cursors:
                _apply_inline_formatting(cur, replace_format)

        response: dict[str, Any] = {
            "query": query,
            "replace_with": replace_with,
            "replacements": total,
            "hint": (
                "No matches found — nothing was replaced. "
                "Use get_document to verify the exact text."
            )
            if total == 0
            else (
                f"Replaced {total} occurrence{'s' if total != 1 else ''}. "
                f"Paragraph styles were preserved."
            ),
        }
        if replace_format:
            applied = {k: replace_format[k] for k in _REPLACE_FORMAT_KEYS if k in replace_format}
            response["replace_format_applied"] = applied
        if len(query) <= 2 and total > 0:
            response["warning"] = (
                f'Short query "{query}" matched {total} times. All were replaced. '
                f"Use match_whole_word=true for safety with short queries."
            )
        return json.dumps(response)

    return json.dumps(
        {
            "query": query,
            "count": total,
            "matches": matches,
            "hint": (
                "No matches found. Check spelling or try a shorter/broader query. "
                "Use get_document to see the full document text."
            )
            if total == 0
            else None,
        }
    )


TOOLS = [search_document]


# Silence "unused import": preview is exported for symmetry with other tool
# modules even if we don't use it directly here.
_ = preview
