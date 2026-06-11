"""Live-UNO end-to-end tests for the ``structure`` tool module.

Each test calls the REAL ``@tool`` function through the ``tool_doc`` harness
(a live Writer document with the extension singleton wired) and asserts the
resulting real-LibreOffice document state — never the brittle JSON response
shape. Headers / footers / page setup live on **page styles** in LibreOffice
(there is no Word-style "section"), so the proofs read back the relevant
page-style properties: ``HeaderText`` / ``FooterText`` strings, ``HeaderIsOn``
/ ``FooterIsOn`` toggles, embedded ``PageNumber`` / ``PageCount`` text fields,
``ParaAdjust`` alignment, ``IsLandscape`` orientation, the margin properties,
and the page ``Size`` struct.

This is the coverage layer that the synthetic / mocked-UNO tests cannot
provide: it exercises the actual LO C++ behaviour behind ``structure.py``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers — resolve and read back page-style state the same way the tool does.
# ---------------------------------------------------------------------------


def _paras(doc: Any) -> list[str]:
    out: list[str] = []
    en = doc.getText().createEnumeration()
    while en.hasMoreElements():
        el = en.nextElement()
        if el.supportsService("com.sun.star.text.Paragraph"):
            out.append(el.getString())
    return out


def _para_count(doc: Any) -> int:
    n = 0
    en = doc.getText().createEnumeration()
    while en.hasMoreElements():
        el = en.nextElement()
        if el.supportsService("com.sun.star.text.Paragraph"):
            n += 1
    return n


def _page_style(doc: Any) -> Any:
    """Resolve the page style that the structure tools edit for section 0.

    A fresh blank Writer document uses the implicit default page style,
    whose in-family name is ``Standard`` (UI label "Default Page Style").
    The tools resolve section 0 to that style via
    ``_list_page_styles_in_use`` + a name fallback; reading it back the
    same way keeps the test in lock-step with the production resolver.
    """
    families = doc.getStyleFamilies()
    page_styles = families.getByName("PageStyles")
    for name in ("Default Page Style", "Standard"):
        if page_styles.hasByName(name):
            return page_styles.getByName(name)
    # Fall back to the first available page style.
    return page_styles.getByIndex(0)


def _header_field_types(text_obj: Any) -> list[str]:
    """Return the UNO service names of every text field embedded in a header/footer."""
    types_seen: list[str] = []
    enum = text_obj.createEnumeration()
    while enum.hasMoreElements():
        para = enum.nextElement()
        if not para.supportsService("com.sun.star.text.Paragraph"):
            continue
        portions = para.createEnumeration()
        while portions.hasMoreElements():
            portion = portions.nextElement()
            if portion.TextPortionType == "TextField":
                field = portion.TextField
                for svc in (
                    "com.sun.star.text.TextField.PageNumber",
                    "com.sun.star.text.TextField.PageCount",
                ):
                    if field.supportsService(svc):
                        types_seen.append(svc)
    return types_seen


# ---------------------------------------------------------------------------
# set_header_footer
# ---------------------------------------------------------------------------


class TestSetHeaderFooterLive:
    def test_sets_header_text_and_turns_it_on(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.structure import set_header_footer

        res = json.loads(set_header_footer(type="header", text="Confidential"))
        assert "error" not in res, res

        style = _page_style(tool_doc)
        assert style.HeaderIsOn is True
        assert style.HeaderText.getString() == "Confidential"

    def test_sets_footer_text_and_turns_it_on(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.structure import set_header_footer

        res = json.loads(set_header_footer(type="footer", text="(c) ACME 2026"))
        assert "error" not in res, res

        style = _page_style(tool_doc)
        assert style.FooterIsOn is True
        assert style.FooterText.getString() == "(c) ACME 2026"

    def test_header_and_footer_are_independent(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.structure import set_header_footer

        assert "error" not in json.loads(
            set_header_footer(type="header", text="Top line")
        )
        assert "error" not in json.loads(
            set_header_footer(type="footer", text="Bottom line")
        )

        style = _page_style(tool_doc)
        assert style.HeaderText.getString() == "Top line"
        assert style.FooterText.getString() == "Bottom line"

    def test_replaces_existing_header_content(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.structure import set_header_footer

        set_header_footer(type="header", text="First version")
        res = json.loads(set_header_footer(type="header", text="Second version"))
        assert "error" not in res, res

        style = _page_style(tool_doc)
        # REPLACES, not appends — only the new text survives.
        assert style.HeaderText.getString() == "Second version"

    def test_case_insensitive_type(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.structure import set_header_footer

        res = json.loads(set_header_footer(type="HEADER", text="Mixed case type"))
        assert "error" not in res, res

        style = _page_style(tool_doc)
        assert style.HeaderIsOn is True
        assert style.HeaderText.getString() == "Mixed case type"

    def test_clear_with_single_space(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.structure import set_header_footer

        set_header_footer(type="footer", text="Some footer")
        # A single space is the documented "clear" sentinel (empty text is rejected).
        res = json.loads(set_header_footer(type="footer", text=" "))
        assert "error" not in res, res

        style = _page_style(tool_doc)
        assert style.FooterText.getString() == " "

    def test_explicit_section_index_zero(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.structure import set_header_footer

        res = json.loads(
            set_header_footer(type="header", text="Section zero", section_index=0)
        )
        assert "error" not in res, res

        style = _page_style(tool_doc)
        assert style.HeaderText.getString() == "Section zero"

    def test_out_of_range_section_does_not_mutate_default(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.structure import set_header_footer

        res = json.loads(
            set_header_footer(type="header", text="Nope", section_index=9)
        )
        # Single-section doc only has section 0; an out-of-range request errors.
        assert "error" in res, res
        # And the default page style header stays off / empty.
        style = _page_style(tool_doc)
        assert style.HeaderIsOn is False


# ---------------------------------------------------------------------------
# insert_page_numbers
# ---------------------------------------------------------------------------


class TestInsertPageNumbersLive:
    def test_default_inserts_field_into_footer(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.structure import insert_page_numbers

        res = json.loads(insert_page_numbers())
        assert "error" not in res, res

        style = _page_style(tool_doc)
        assert style.FooterIsOn is True
        fields = _header_field_types(style.FooterText)
        assert "com.sun.star.text.TextField.PageNumber" in fields

    def test_header_location(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.structure import insert_page_numbers

        res = json.loads(insert_page_numbers(location="header"))
        assert "error" not in res, res

        style = _page_style(tool_doc)
        assert style.HeaderIsOn is True
        fields = _header_field_types(style.HeaderText)
        assert "com.sun.star.text.TextField.PageNumber" in fields

    def test_page_of_numpages_inserts_both_fields(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.structure import insert_page_numbers

        res = json.loads(
            insert_page_numbers(
                location="footer", format="Page {PAGE} of {NUMPAGES}"
            )
        )
        assert "error" not in res, res

        style = _page_style(tool_doc)
        footer = style.FooterText
        fields = _header_field_types(footer)
        assert "com.sun.star.text.TextField.PageNumber" in fields
        assert "com.sun.star.text.TextField.PageCount" in fields
        # The literal template text surrounds the fields.
        rendered = footer.getString()
        assert "Page " in rendered
        assert " of " in rendered

    def test_prefix_and_suffix_text_present(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.structure import insert_page_numbers

        res = json.loads(
            insert_page_numbers(
                location="footer",
                format="{PAGE}",
                prefix_text="[ ",
                suffix_text=" ]",
            )
        )
        assert "error" not in res, res

        footer = _page_style(tool_doc).FooterText
        rendered = footer.getString()
        assert rendered.startswith("[ ")
        assert rendered.endswith(" ]")
        assert "com.sun.star.text.TextField.PageNumber" in _header_field_types(footer)

    @pytest.mark.parametrize(
        ("alignment", "expected_paraadjust"),
        [("left", 0), ("center", 3), ("right", 1)],
    )
    def test_alignment_sets_paraadjust(
        self, tool_doc: Any, alignment: str, expected_paraadjust: int
    ) -> None:
        from talk2view_writer.tools.structure import insert_page_numbers

        res = json.loads(
            insert_page_numbers(location="footer", alignment=alignment)
        )
        assert "error" not in res, res

        footer = _page_style(tool_doc).FooterText
        enum = footer.createEnumeration()
        assert enum.hasMoreElements()
        first_para = enum.nextElement()
        assert first_para.supportsService("com.sun.star.text.Paragraph")
        assert first_para.ParaAdjust == expected_paraadjust

    def test_call_clears_previous_footer_content(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.structure import insert_page_numbers, set_header_footer

        set_header_footer(type="footer", text="Old footer text")
        res = json.loads(insert_page_numbers(location="footer", format="{PAGE}"))
        assert "error" not in res, res

        footer = _page_style(tool_doc).FooterText
        # The page-number call CLEARS the target first; the old literal is gone.
        assert "Old footer text" not in footer.getString()
        assert "com.sun.star.text.TextField.PageNumber" in _header_field_types(footer)

    def test_out_of_range_section_errors(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.structure import insert_page_numbers

        res = json.loads(insert_page_numbers(location="footer", section_index=5))
        assert "error" in res, res
        # Footer stays off because the only section is out of range.
        assert _page_style(tool_doc).FooterIsOn is False


# ---------------------------------------------------------------------------
# set_page_setup
# ---------------------------------------------------------------------------


class TestSetPageSetupLive:
    def test_landscape_orientation(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.structure import set_page_setup

        res = json.loads(set_page_setup(orientation="landscape"))
        assert "error" not in res, res
        assert _page_style(tool_doc).IsLandscape is True

    def test_portrait_orientation(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.structure import set_page_setup

        # Flip to landscape first, then back to portrait, to prove the toggle.
        set_page_setup(orientation="landscape")
        res = json.loads(set_page_setup(orientation="portrait"))
        assert "error" not in res, res
        assert _page_style(tool_doc).IsLandscape is False

    def test_case_insensitive_orientation(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.structure import set_page_setup

        res = json.loads(set_page_setup(orientation="Landscape"))
        assert "error" not in res, res
        assert _page_style(tool_doc).IsLandscape is True

    def test_margins_converted_points_to_hmm(self, tool_doc: Any) -> None:
        from talk2view_writer.tools._constants import points_to_hmm
        from talk2view_writer.tools.structure import set_page_setup

        res = json.loads(
            set_page_setup(
                top_margin=72,
                bottom_margin=36,
                left_margin=90,
                right_margin=18,
            )
        )
        assert "error" not in res, res

        style = _page_style(tool_doc)
        # 72pt = 1 inch = 2540 (1/100 mm). Assert against the same converter.
        assert style.TopMargin == points_to_hmm(72)
        assert style.BottomMargin == points_to_hmm(36)
        assert style.LeftMargin == points_to_hmm(90)
        assert style.RightMargin == points_to_hmm(18)

    def test_partial_margin_only_changes_included_property(self, tool_doc: Any) -> None:
        from talk2view_writer.tools._constants import points_to_hmm
        from talk2view_writer.tools.structure import set_page_setup

        style = _page_style(tool_doc)
        original_bottom = style.BottomMargin

        res = json.loads(set_page_setup(top_margin=100))
        assert "error" not in res, res

        # Only the included property changes; the others are left untouched.
        assert style.TopMargin == points_to_hmm(100)
        assert style.BottomMargin == original_bottom

    def test_paper_size_a4_portrait_dimensions(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.structure import set_page_setup

        res = json.loads(set_page_setup(paper_size="a4"))
        assert "error" not in res, res

        size = _page_style(tool_doc).Size
        # A4 portrait: 21000 x 29700 (1/100 mm). Real LO round-trips the
        # width through twips and reads it back as 21001 (off-by-one), so
        # assert within 1 unit rather than demanding the exact constant.
        assert abs(size.Width - 21000) <= 1
        assert abs(size.Height - 29700) <= 1

    def test_paper_size_letter_dimensions(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.structure import set_page_setup

        res = json.loads(set_page_setup(paper_size="letter"))
        assert "error" not in res, res

        size = _page_style(tool_doc).Size
        # Letter portrait: 21590 x 27940 (1/100 mm).
        assert size.Width == 21590
        assert size.Height == 27940

    def test_landscape_paper_size_swaps_dimensions(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.structure import set_page_setup

        # Orientation + paper size in one call: landscape A4 swaps W/H.
        res = json.loads(set_page_setup(orientation="landscape", paper_size="a4"))
        assert "error" not in res, res

        style = _page_style(tool_doc)
        assert style.IsLandscape is True
        size = style.Size
        # Landscape A4: width/height swapped -> 29700 x 21000. (LO's twip
        # round-trip nudges the 21000 dimension to 21001; tolerate +-1.)
        assert abs(size.Width - 29700) <= 1
        assert abs(size.Height - 21000) <= 1

    def test_us_spelling_paper_size_letter(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.structure import set_page_setup

        # The model commonly emits the US Title-cased spelling "Letter".
        res = json.loads(set_page_setup(paper_size="Letter"))
        assert "error" not in res, res

        size = _page_style(tool_doc).Size
        assert size.Width == 21590
        assert size.Height == 27940

    def test_combined_orientation_and_margins(self, tool_doc: Any) -> None:
        from talk2view_writer.tools._constants import points_to_hmm
        from talk2view_writer.tools.structure import set_page_setup

        res = json.loads(
            set_page_setup(orientation="landscape", left_margin=54, right_margin=54)
        )
        assert "error" not in res, res

        style = _page_style(tool_doc)
        assert style.IsLandscape is True
        assert style.LeftMargin == points_to_hmm(54)
        assert style.RightMargin == points_to_hmm(54)

    def test_out_of_range_section_errors(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.structure import set_page_setup

        res = json.loads(set_page_setup(section_index=7, orientation="landscape"))
        assert "error" in res, res
        # The default page style must NOT have been flipped to landscape.
        assert _page_style(tool_doc).IsLandscape is False


# ---------------------------------------------------------------------------
# insert_break
# ---------------------------------------------------------------------------


class TestInsertBreakLive:
    def test_page_break_at_end_adds_paragraph(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.structure import insert_break
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="Before the break", location="end")
        before = _para_count(tool_doc)

        res = json.loads(insert_break(type="page", location="end"))
        assert "error" not in res, res
        # A page break is realised as a new paragraph carrying BreakType.
        assert _para_count(tool_doc) == before + 1

    def test_page_break_sets_breaktype_on_new_paragraph(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.structure import insert_break
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="Page one content", location="end")
        res = json.loads(insert_break(type="page", location="end"))
        assert "error" not in res, res

        # Walk the paragraphs; at least one must carry a page-before break.
        # PyUNO returns BreakType as a com.sun.star.style.BreakType *enum
        # instance* (whose .value is 4 == PAGE_BEFORE), NOT the bare int 4 —
        # comparing against the int silently never matches. Match on the
        # enum's value / repr instead.
        found = False
        en = tool_doc.getText().createEnumeration()
        while en.hasMoreElements():
            el = en.nextElement()
            if el.supportsService("com.sun.star.text.Paragraph"):
                bt = getattr(el, "BreakType", None)
                if bt is not None and (
                    getattr(bt, "value", None) == "PAGE_BEFORE" or "PAGE_BEFORE" in str(bt)
                ):
                    found = True
        assert found, "no paragraph carried PAGE_BEFORE BreakType after page break"

    def test_section_next_page_break(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.structure import insert_break
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="Section content", location="end")
        before = _para_count(tool_doc)

        res = json.loads(insert_break(type="section_next_page", location="end"))
        assert "error" not in res, res
        assert _para_count(tool_doc) == before + 1

    def test_section_continuous_degrades_to_paragraph_break(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.structure import insert_break
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="Continuous content", location="end")
        before = _para_count(tool_doc)

        res = json.loads(insert_break(type="section_continuous", location="end"))
        assert "error" not in res, res
        # No page-style concept inline -> a plain paragraph break.
        assert _para_count(tool_doc) == before + 1

    def test_case_insensitive_type(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.structure import insert_break
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="Some text", location="end")
        before = _para_count(tool_doc)

        res = json.loads(insert_break(type="PAGE", location="END"))
        assert "error" not in res, res
        assert _para_count(tool_doc) == before + 1
