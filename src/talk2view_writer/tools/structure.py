"""Structure tools — breaks, page numbers, headers/footers, page setup.

Four tools mirror ``Talk2View-Word/src/taskpane/tools/structure.ts``:

- :func:`insert_break`         — page or section break
- :func:`set_header_footer`    — set header / footer content
- :func:`insert_page_numbers`  — page-number fields with optional
                                  prefix / suffix
- :func:`set_page_setup`       — orientation, margins, paper size

Significant behavioural deltas vs Word (see ``docs/investigations.md``
#14 + #20):

- LibreOffice has no Word-style "sections". Headers / footers and page
  setup live on **page styles** instead. We map ``section_index`` to
  the Nth distinct page style in the document. ``section_continuous``
  breaks degrade to a plain paragraph break.
- ``set_page_setup`` always operates on the page style of the current
  cursor / requested "section" because UNO doesn't let us modify the
  page setup of an arbitrary slice of pages without first creating a
  new page style.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from talk2view import tool  # type: ignore[import-not-found]

from talk2view_writer.extension import get_extension_or_raise
from talk2view_writer.tools._base import get_writer_document, ui_thread_tool
from talk2view_writer.tools._constants import (
    lower_enum,
    normalize_header_footer_type,
    points_to_hmm,
    preview,
)

logger = logging.getLogger(__name__)

_MARGIN_KEYS = ("top_margin", "bottom_margin", "left_margin", "right_margin")
_PAGE_SETUP_KEYS = ("orientation", "paper_size", *_MARGIN_KEYS)

# ISO 216 + US paper sizes in 1/100 mm (portrait).
_PAPER_SIZES_HMM: dict[str, tuple[int, int]] = {
    "letter": (21590, 27940),  # 8.5 x 11 in
    "legal": (21590, 35560),  # 8.5 x 14 in
    "a3": (29700, 42000),
    "a4": (21000, 29700),
    "a5": (14800, 21000),
}

# com.sun.star.style.NumberingType.PAGE_DESCRIPTOR (7): the page-number field
# follows the page style's own number format ("As Page Style" in the
# Insert > Field > Page Number dialog — which is that dialog's DEFAULT).
# A field made via createInstance leaves NumberingType at its property default
# 0 (== CHARS_UPPER_LETTER), so page numbers render as letters ("a of b")
# instead of arabic. Pinning PAGE_DESCRIPTOR replicates the manual default:
# arabic on an ordinary page, roman on a deliberately roman-numbered page
# style — the field never disagrees with the page it sits on. The numeral
# style is then controlled where it belongs, on the page style. See
# docs/investigations.md #57.
_NUMBERING_TYPE_PAGE_DESCRIPTOR = 7


def _list_page_styles_in_use(doc: Any) -> list[str]:
    """Return distinct PageDescriptorName values used by document paragraphs.

    LibreOffice's "section" concept is page-style-based; iterating
    paragraphs and collecting their ``PageDescName`` gives an
    order-preserving list of the page styles that drive the document's
    structure.

    A paragraph's ``PageDescName`` is set ONLY when it explicitly forces a
    page-style boundary; paragraphs governed by the document's implicit
    default page style report an empty ``PageDescName``. So a document that
    begins on the default and later switches to a named style (e.g. a forced
    "Landscape" page) must still list the default as the FIRST section —
    otherwise ``section_index=0`` resolves to the later named style and every
    structure-tool edit (margins, header/footer, page numbers) lands on the
    wrong pages, and the default section is unreachable.
    """
    seen: list[str] = []
    uses_default = False
    enum = doc.getText().createEnumeration()
    while enum.hasMoreElements():
        el = enum.nextElement()
        if el.supportsService("com.sun.star.text.Paragraph"):
            name = getattr(el, "PageDescName", "") or ""
            if not name:
                uses_default = True
            elif name not in seen:
                seen.append(name)
    default_name = "Default Page Style"
    if uses_default and default_name not in seen:
        seen.insert(0, default_name)
    if not seen:
        seen = [default_name]
    return seen


def _get_page_style(doc: Any, section_index: int) -> Any | None:
    """Resolve a section_index to a UNO ``XPageStyle`` instance.

    Returns ``None`` if no such page style exists, in which case the
    caller surfaces a Word-shaped "section out of range" error.
    """
    names = _list_page_styles_in_use(doc)
    if section_index < 0 or section_index >= len(names):
        return None
    families = doc.getStyleFamilies()
    page_styles = families.getByName("PageStyles")
    name = names[section_index]
    if not page_styles.hasByName(name):
        # Fall back to first page style in the family.
        names = page_styles.getElementNames()
        if not names:
            return None
        name = names[0]
    return page_styles.getByName(name)


# ---------------------------------------------------------------------------
# insert_break
# ---------------------------------------------------------------------------


@tool
@ui_thread_tool
def insert_break(type: str, location: str) -> str:
    """Insert a page or section break.

    - ``page``: simple page break (new page, same page style).
    - ``section_next_page``: forces the next paragraph onto a new page
      style boundary (closest LibreOffice equivalent to Word's section
      break).
    - ``section_continuous``: degrades to a plain paragraph break —
      LibreOffice has no in-line section concept (Investigation #14).

    Args:
        type: ``page`` / ``section_next_page`` / ``section_continuous``.
        location: ``end`` (after all body content) or
            ``after_selection`` (after current cursor).

    Returns:
        JSON string.

    Raises:
        WriterDocumentRequiredError: If no Writer document is active.
    """
    # Case-insensitive enum args (schema enums dropped — see Writer #5).
    type = lower_enum(type) or ""
    location = lower_enum(location) or ""

    allowed = ("page", "section_next_page", "section_continuous")
    if type not in allowed:
        return json.dumps(
            {
                "error": f'Unknown break type "{type}".',
                "recovery": "Use 'page', 'section_next_page', or 'section_continuous'.",
            }
        )
    if location not in ("end", "after_selection"):
        return json.dumps(
            {
                "error": f"Unknown location '{location}'.",
                "recovery": "Use 'end' or 'after_selection'.",
            }
        )

    ext = get_extension_or_raise()
    doc = get_writer_document(ext.ctx)
    text_obj = doc.getText()

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
    else:
        cursor = text_obj.createTextCursorByRange(text_obj.getEnd())

    if type == "page":
        # com.sun.star.style.BreakType.PAGE_BEFORE = 4.
        text_obj.insertControlCharacter(cursor, 0, False)  # paragraph break
        try:
            cursor.BreakType = 4
        except Exception:
            logger.debug("BreakType property not settable on this build")
    elif type == "section_next_page":
        # Insert a paragraph break and switch to a new page style start.
        text_obj.insertControlCharacter(cursor, 0, False)
        try:
            cursor.BreakType = 4
            # Force a page-style boundary by giving the paragraph a
            # PageDescName equal to the document's default page style.
            cursor.PageDescName = "Default Page Style"
        except Exception:
            logger.debug("Section break degraded to plain page break")
    else:  # section_continuous
        text_obj.insertControlCharacter(cursor, 0, False)

    is_section_break = type.startswith("section")
    return json.dumps(
        {
            "success": True,
            "break_type": type,
            "location": location,
            "hint": (
                "New section created. Use get_document to check the updated section count, "
                "then set_header_footer for the new section if needed."
            )
            if is_section_break
            else None,
        }
    )


# ---------------------------------------------------------------------------
# set_header_footer
# ---------------------------------------------------------------------------


@tool
@ui_thread_tool
def set_header_footer(
    type: str,
    text: str,
    section_index: int | None = None,
    section_indices: list[int] | None = None,
    header_footer_type: str = "primary",
) -> str:
    """Set the content of a header or footer for one or more sections.

    REPLACES existing content. For page numbers, prefer
    insert_page_numbers (it can include surrounding text via
    prefix_text / suffix_text).

    Args:
        type: ``header`` (top of page) or ``footer`` (bottom).
        text: New content. Cannot be empty — use ``" "`` to clear.
        section_index: Single section (0-based). Defaults to 0.
            Mutually exclusive with section_indices.
        section_indices: Apply to multiple sections in one call.
        header_footer_type: ``primary`` (default), ``firstPage``, or
            ``evenPages``. The latter two require LibreOffice to be
            in "different first / odd-even" mode.

    Returns:
        JSON string.

    Raises:
        WriterDocumentRequiredError: If no Writer document is active.
    """
    # Case-insensitive enum args (schema enums dropped — see Writer #5).
    # header_footer_type is camelCase so it needs the canonical map, not
    # a blind lowercase.
    type = lower_enum(type) or ""
    header_footer_type = normalize_header_footer_type(header_footer_type) or "primary"

    if type not in ("header", "footer"):
        return json.dumps(
            {"error": f"Unknown type '{type}'.", "recovery": "Use 'header' or 'footer'."}
        )
    if not isinstance(text, str) or len(text) == 0:
        return json.dumps(
            {
                "error": "text is required and cannot be empty.",
                "recovery": "Provide the header/footer text. To clear it, use a single space.",
            }
        )
    if section_index is not None and section_indices is not None:
        return json.dumps(
            {
                "error": "Provide either section_index or section_indices, not both.",
                "recovery": "Use section_indices for multiple sections, section_index for one.",
            }
        )
    if section_index is not None and section_index < 0:
        return json.dumps(
            {
                "error": "section_index must be >= 0.",
                "recovery": (
                    "Use get_document to check the section count. Section 0 is the first section."
                ),
            }
        )
    if section_indices is not None:
        if not section_indices:
            return json.dumps(
                {
                    "error": "section_indices is empty.",
                    "recovery": "Provide at least one section index.",
                }
            )
        for s in section_indices:
            if not isinstance(s, int) or s < 0:
                return json.dumps(
                    {
                        "error": f"section_indices contains invalid value {s}.",
                        "recovery": "All values must be non-negative integers.",
                    }
                )

    ext = get_extension_or_raise()
    doc = get_writer_document(ext.ctx)
    indices = section_indices if section_indices is not None else [section_index or 0]

    per_section: list[dict[str, Any]] = []
    for idx in indices:
        page_style = _get_page_style(doc, idx)
        if page_style is None:
            per_section.append({"section_index": idx, "error": f"Section {idx} out of range."})
            continue
        # Toggle the *IsOn property so the header/footer is visible.
        try:
            if type == "header":
                page_style.HeaderIsOn = True
                # FirstPage / left header variants require the
                # corresponding *IsShared property to be False.
                if header_footer_type == "firstPage":
                    page_style.FirstIsShared = False
                    page_style.HeaderTextFirst.setString(text)
                elif header_footer_type == "evenPages":
                    page_style.HeaderIsShared = False
                    page_style.HeaderTextLeft.setString(text)
                else:
                    page_style.HeaderText.setString(text)
            else:
                page_style.FooterIsOn = True
                if header_footer_type == "firstPage":
                    page_style.FirstIsShared = False
                    page_style.FooterTextFirst.setString(text)
                elif header_footer_type == "evenPages":
                    page_style.FooterIsShared = False
                    page_style.FooterTextLeft.setString(text)
                else:
                    page_style.FooterText.setString(text)
            per_section.append({"section_index": idx, "success": True})
        except Exception as exc:
            per_section.append({"section_index": idx, "error": str(exc)})

    text_preview = preview(text, 60)
    if section_indices is not None:
        return json.dumps(
            {
                "success": all(r.get("success") for r in per_section),
                "type": type,
                "text_preview": text_preview,
                "sections": per_section,
            }
        )
    only = per_section[0]
    if "error" in only:
        return json.dumps(
            {
                "error": only["error"],
                "recovery": "Use a smaller section index. Call get_document for the section count.",
            }
        )
    return json.dumps(
        {
            "success": True,
            "type": type,
            "section_index": only["section_index"],
            "text_preview": text_preview,
            "hint": "Headers/footers are not visible in get_document. Ask the user to verify.",
        }
    )


# ---------------------------------------------------------------------------
# insert_page_numbers
# ---------------------------------------------------------------------------


@tool
@ui_thread_tool
def insert_page_numbers(
    location: str = "footer",
    alignment: str = "center",
    format: str = "{PAGE}",
    prefix_text: str = "",
    suffix_text: str = "",
    section_index: int | None = None,
    section_indices: list[int] | None = None,
) -> str:
    """Insert automatic page-number fields into a header or footer.

    Each call CLEARS the target header/footer first. To combine
    page numbers with brand or copyright text in the same location,
    use ``prefix_text`` / ``suffix_text`` here rather than a separate
    set_header_footer call.

    Args:
        location: ``header`` or ``footer``. Defaults to ``footer``.
        alignment: ``left`` / ``center`` / ``right``. Defaults to ``center``.
        format: One of the supported page-number templates
            (``{PAGE}``, ``Page {PAGE}``, ``Page {PAGE} of {NUMPAGES}``,
            ``- {PAGE} -``, ``{PAGE} / {NUMPAGES}``).
        prefix_text: Inserted BEFORE the page-number field.
        suffix_text: Inserted AFTER the page-number field.
        section_index: Single section index (0-based). Defaults to 0.
        section_indices: Apply to multiple sections in one call.

    Returns:
        JSON string.

    Raises:
        WriterDocumentRequiredError: If no Writer document is active.
    """
    # Case-insensitive enum args (schema enums dropped — see Writer #5).
    location = lower_enum(location) or "footer"
    alignment = lower_enum(alignment) or "center"

    if section_index is not None and section_indices is not None:
        return json.dumps(
            {
                "error": "Provide either section_index or section_indices, not both.",
                "recovery": "Use section_indices for multiple sections.",
            }
        )
    if section_index is not None and section_index < 0:
        return json.dumps(
            {
                "error": "section_index must be >= 0.",
                "recovery": "Use get_document to check the section count.",
            }
        )
    if section_indices is not None:
        if not section_indices:
            return json.dumps(
                {"error": "section_indices is empty.", "recovery": "Provide at least one index."}
            )
        for s in section_indices:
            if not isinstance(s, int) or s < 0:
                return json.dumps(
                    {
                        "error": f"section_indices contains invalid value {s}.",
                        "recovery": "All values must be non-negative integers.",
                    }
                )
    if location not in ("header", "footer"):
        return json.dumps(
            {"error": f"Unknown location '{location}'.", "recovery": "Use 'header' or 'footer'."}
        )
    if alignment not in ("left", "center", "right"):
        return json.dumps(
            {
                "error": f"Unknown alignment '{alignment}'.",
                "recovery": "Use 'left', 'center', or 'right'.",
            }
        )

    ext = get_extension_or_raise()
    doc = get_writer_document(ext.ctx)
    indices = section_indices if section_indices is not None else [section_index or 0]
    full_template = f"{prefix_text}{format}{suffix_text}"

    align_map = {"left": 0, "center": 3, "right": 1}
    per_section: list[dict[str, Any]] = []

    for idx in indices:
        page_style = _get_page_style(doc, idx)
        if page_style is None:
            per_section.append({"section_index": idx, "error": f"Section {idx} out of range."})
            continue
        try:
            if location == "header":
                page_style.HeaderIsOn = True
                target_text = page_style.HeaderText
            else:
                page_style.FooterIsOn = True
                target_text = page_style.FooterText
            # Clear existing content.
            target_text.setString("")
            cursor = target_text.createTextCursorByRange(target_text.getStart())
            # Insert each literal-or-field segment in order.
            for part in _split_page_template(full_template):
                if part == "{PAGE}":
                    field = doc.createInstance("com.sun.star.text.TextField.PageNumber")
                    field.SubType = 1  # PageNumberType.CURRENT
                    # Follow the page style's number format (see constant) so
                    # the field renders arabic by default instead of letters.
                    field.NumberingType = _NUMBERING_TYPE_PAGE_DESCRIPTOR
                    target_text.insertTextContent(cursor, field, False)
                elif part == "{NUMPAGES}":
                    field = doc.createInstance("com.sun.star.text.TextField.PageCount")
                    field.NumberingType = _NUMBERING_TYPE_PAGE_DESCRIPTOR
                    target_text.insertTextContent(cursor, field, False)
                elif part:
                    target_text.insertString(cursor, part, False)
            # Align the first paragraph of the header/footer.
            enum = target_text.createEnumeration()
            if enum.hasMoreElements():
                first_para = enum.nextElement()
                if first_para.supportsService("com.sun.star.text.Paragraph"):
                    first_para.ParaAdjust = align_map[alignment]
            per_section.append({"section_index": idx, "success": True})
        except Exception as exc:
            per_section.append({"section_index": idx, "error": str(exc)})

    common = {
        "location": location,
        "alignment": alignment,
        "format": full_template,
        "warning": (
            f"Previous content in {location}"
            f"{' (each targeted section)' if len(indices) > 1 else ''} was cleared."
        ),
    }
    if section_indices is not None:
        return json.dumps(
            {
                "success": all(r.get("success") for r in per_section),
                **common,
                "sections": per_section,
            }
        )
    only = per_section[0]
    if "error" in only:
        return json.dumps(
            {
                "error": only["error"],
                "recovery": "Use a smaller section index. Call get_document for the section count.",
            }
        )
    return json.dumps({"success": True, "section_index": only["section_index"], **common})


def _split_page_template(template: str) -> list[str]:
    """Split a page-number template into literal + placeholder segments.

    >>> _split_page_template("Page {PAGE} of {NUMPAGES}")
    ['Page ', '{PAGE}', ' of ', '{NUMPAGES}']
    """
    parts: list[str] = []
    i = 0
    while i < len(template):
        if template.startswith("{PAGE}", i):
            parts.append("{PAGE}")
            i += len("{PAGE}")
        elif template.startswith("{NUMPAGES}", i):
            parts.append("{NUMPAGES}")
            i += len("{NUMPAGES}")
        else:
            j = i
            while j < len(template) and not (
                template.startswith("{PAGE}", j) or template.startswith("{NUMPAGES}", j)
            ):
                j += 1
            parts.append(template[i:j])
            i = j
    return [p for p in parts if p]


# ---------------------------------------------------------------------------
# set_page_setup
# ---------------------------------------------------------------------------


@tool
@ui_thread_tool
def set_page_setup(
    section_index: int = 0,
    orientation: str | None = None,
    top_margin: float | None = None,
    bottom_margin: float | None = None,
    left_margin: float | None = None,
    right_margin: float | None = None,
    paper_size: str | None = None,
) -> str:
    """Set page layout (orientation / margins / paper size) for a section.

    Only included properties change. ``section_index`` selects the page
    style to modify; in single-section documents pass 0 (the default).

    Args:
        section_index: Zero-based section index. Defaults to 0.
        orientation: ``portrait`` or ``landscape``.
        top_margin: Top margin in points (72pt = 1 inch). >= 0.
        bottom_margin: Bottom margin in points. >= 0.
        left_margin: Left margin in points. >= 0.
        right_margin: Right margin in points. >= 0.
        paper_size: ``letter`` / ``a4`` / ``legal`` / ``a3`` / ``a5``.

    Returns:
        JSON string with ``applied`` properties.

    Raises:
        WriterDocumentRequiredError: If no Writer document is active.
    """
    # Case-insensitive enum args (schema enum dropped — see Writer #5).
    # Normalise before building the args dict below so it captures the
    # canonical value. The model commonly emits the US spelling "Letter".
    orientation = lower_enum(orientation)
    paper_size = lower_enum(paper_size)

    args = {
        "orientation": orientation,
        "top_margin": top_margin,
        "bottom_margin": bottom_margin,
        "left_margin": left_margin,
        "right_margin": right_margin,
        "paper_size": paper_size,
    }
    if not any(v is not None for v in args.values()):
        return json.dumps(
            {
                "error": "No page setup properties provided.",
                "recovery": f"Include at least one of: {', '.join(_PAGE_SETUP_KEYS)}.",
            }
        )
    if orientation is not None and orientation not in ("portrait", "landscape"):
        return json.dumps(
            {
                "error": f'Unknown orientation "{orientation}".',
                "recovery": "Use 'portrait' or 'landscape'.",
            }
        )
    for key in _MARGIN_KEYS:
        val = args[key]
        if isinstance(val, (int, float)) and val < 0:
            return json.dumps(
                {
                    "error": f"{key} must be >= 0.",
                    "recovery": (
                        f"Provide a non-negative value in points (72pt = 1 inch). "
                        f"Example: {key}: 72."
                    ),
                }
            )
    if paper_size is not None and paper_size not in _PAPER_SIZES_HMM:
        return json.dumps(
            {
                "error": f'Unknown paper_size "{paper_size}".',
                "recovery": f"Use one of: {', '.join(_PAPER_SIZES_HMM)}.",
            }
        )

    ext = get_extension_or_raise()
    doc = get_writer_document(ext.ctx)
    page_style = _get_page_style(doc, section_index)
    if page_style is None:
        all_styles = _list_page_styles_in_use(doc)
        return json.dumps(
            {
                "error": (
                    f"Section index {section_index} out of range "
                    f"(document has {len(all_styles)} sections)."
                ),
                "recovery": (
                    f"Use 0 to {len(all_styles) - 1}. Call get_document to check section count."
                ),
            }
        )

    applied: dict[str, Any] = {}
    if orientation == "landscape":
        page_style.IsLandscape = True
        applied["orientation"] = "landscape"
    elif orientation == "portrait":
        page_style.IsLandscape = False
        applied["orientation"] = "portrait"

    if top_margin is not None:
        page_style.TopMargin = points_to_hmm(top_margin)
        applied["top_margin"] = top_margin
    if bottom_margin is not None:
        page_style.BottomMargin = points_to_hmm(bottom_margin)
        applied["bottom_margin"] = bottom_margin
    if left_margin is not None:
        page_style.LeftMargin = points_to_hmm(left_margin)
        applied["left_margin"] = left_margin
    if right_margin is not None:
        page_style.RightMargin = points_to_hmm(right_margin)
        applied["right_margin"] = right_margin

    if paper_size is not None:
        w, h = _PAPER_SIZES_HMM[paper_size]
        if applied.get("orientation") == "landscape" or page_style.IsLandscape:
            page_style.Size = _make_size_struct(h, w)
        else:
            page_style.Size = _make_size_struct(w, h)
        applied["paper_size"] = paper_size

    return json.dumps(
        {
            "success": True,
            "section_index": section_index,
            "applied": applied,
            "hint": ("Page layout changed. Content may have reflowed — use get_document to check."),
        }
    )


def _make_size_struct(width: int, height: int) -> Any:
    """Create a ``com.sun.star.awt.Size`` struct with the given dimensions."""
    import uno  # type: ignore[import-not-found]

    size = uno.createUnoStruct("com.sun.star.awt.Size")
    size.Width = width
    size.Height = height
    return size


TOOLS = [insert_break, set_header_footer, insert_page_numbers, set_page_setup]
