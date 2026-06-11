"""Live-UNO end-to-end tests for the ``formatting`` tools.

Every test calls the REAL ``@tool`` function through the ``tool_doc``
harness (which wires the extension singleton + a live Writer document)
and then asserts the concrete real-LibreOffice document state — the
char/paragraph UNO properties over the affected range — NOT the tool's
JSON response shape. The response is only checked for the absence of an
``error`` key; the proof of correctness is always read back out of the
document via UNO.

This is the coverage layer that the synthetic tests cannot provide: it
exercises actual LO C++ behaviour, the gap that hid the commenting bugs
(Investigations #38, #66) and the list/numbering gaps (#37, #50).

Covered:
  * format_text — bold / italic / underline / underline_style /
    strikethrough / superscript / subscript / color / size / font /
    highlight, targeted via ``query`` (with ``match_index``) and via
    ``paragraph_index``, plus the ``queries=[...]`` batch path and the
    "remove formatting" (False) variations.
  * format_paragraph — style, alignment (-> ParaAdjust), space
    before/after (-> ParaTopMargin / ParaBottomMargin), line_spacing
    (-> ParaLineSpacing), indents (-> ParaLeftMargin / ParaRightMargin /
    ParaFirstLineIndent), single and batch (``paragraph_indices``).
  * manage_list — make paragraph(s) a bullet / number list, levels, the
    fused indent, and removal.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

pytestmark = pytest.mark.integration

# UNO length unit conversion (1/100 mm per point), matching
# talk2view_writer.tools._constants.points_to_hmm so the tests assert the
# exact value the tool wrote.
_POINTS_TO_HMM = 2540.0 / 72.0


def _points_to_hmm(points: float) -> int:
    return round(points * _POINTS_TO_HMM)


def _paras(doc: Any) -> list[Any]:
    """Return the top-level paragraph objects in document order."""
    out: list[Any] = []
    en = doc.getText().createEnumeration()
    while en.hasMoreElements():
        el = en.nextElement()
        if el.supportsService("com.sun.star.text.Paragraph"):
            out.append(el)
    return out


def _is_italic(cursor: Any) -> bool:
    """True if the cursor's run is italic, tolerating PyUNO's representation.

    The tool writes ``CharPosture = 2`` (com.sun.star.awt.FontSlant.ITALIC),
    but PyUNO may read it back as the ``FontSlant`` enum (``.value ==
    "ITALIC"``) OR as the plain int ``2`` depending on build. Accept both.
    """
    cp = cursor.CharPosture
    val = getattr(cp, "value", cp)  # enum -> "ITALIC"; int stays the int
    return val in ("ITALIC", 2)


def _para_index_of(doc: Any, needle: str) -> int:
    """Index of the first paragraph whose text contains ``needle``."""
    for i, p in enumerate(_paras(doc)):
        if needle in p.getString():
            return i
    raise AssertionError(f"no paragraph contains {needle!r}")


def _cursor_over_match(doc: Any, needle: str, match_index: int = 0) -> Any:
    """A text cursor spanning the ``match_index``-th occurrence of ``needle``."""
    s = doc.createSearchDescriptor()
    s.SearchString = needle
    s.SearchCaseSensitive = False
    found = doc.findAll(s)
    assert found.getCount() > match_index, (
        f"{needle!r} not found (count={found.getCount()})"
    )
    rng = found.getByIndex(match_index)
    return rng.getText().createTextCursorByRange(rng)


def _cursor_over_paragraph(doc: Any, idx: int) -> Any:
    """A text cursor spanning the whole paragraph at ``idx``."""
    para = _paras(doc)[idx]
    text_obj = doc.getText()
    cur = text_obj.createTextCursorByRange(para.getStart())
    cur.gotoEndOfParagraph(True)
    return cur


# ---------------------------------------------------------------------------
# format_text — single-target via query
# ---------------------------------------------------------------------------


class TestFormatTextViaQuery:
    def test_bold_sets_char_weight(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.formatting import format_text
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="alpha beta gamma", location="end")
        res = json.loads(format_text(query="beta", bold=True))
        assert "error" not in res, res

        cur = _cursor_over_match(tool_doc, "beta")
        assert cur.CharWeight == pytest.approx(150.0)
        # The surrounding words stay at normal weight.
        assert _cursor_over_match(tool_doc, "alpha").CharWeight == pytest.approx(100.0)

    def test_bold_false_removes_weight(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.formatting import format_text
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="make me plain", location="end")
        format_text(query="plain", bold=True)
        assert _cursor_over_match(tool_doc, "plain").CharWeight == pytest.approx(150.0)
        res = json.loads(format_text(query="plain", bold=False))
        assert "error" not in res, res
        assert _cursor_over_match(tool_doc, "plain").CharWeight == pytest.approx(100.0)

    def test_italic_sets_char_posture(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.formatting import format_text
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="slant this word", location="end")
        res = json.loads(format_text(query="slant", italic=True))
        assert "error" not in res, res
        # com.sun.star.awt.FontSlant.ITALIC == 2.
        assert _is_italic(_cursor_over_match(tool_doc, "slant"))

    def test_underline_sets_char_underline(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.formatting import format_text
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="underline target", location="end")
        res = json.loads(format_text(query="target", underline=True))
        assert "error" not in res, res
        # FontUnderline.SINGLE == 1.
        assert _cursor_over_match(tool_doc, "target").CharUnderline == 1

    def test_underline_style_double(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.formatting import format_text
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="double underline here", location="end")
        res = json.loads(format_text(query="double", underline_style="double"))
        assert "error" not in res, res
        # FontUnderline.DOUBLE == 2 (see UNDERLINE_STYLE_UNO).
        assert _cursor_over_match(tool_doc, "double").CharUnderline == 2

    def test_underline_style_wave(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.formatting import format_text
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="wavy line text", location="end")
        res = json.loads(format_text(query="wavy", underline_style="wave"))
        assert "error" not in res, res
        # FontUnderline.WAVE == 10.
        assert _cursor_over_match(tool_doc, "wavy").CharUnderline == 10

    def test_strikethrough_sets_char_strikeout(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.formatting import format_text
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="strike this out", location="end")
        res = json.loads(format_text(query="strike", strikethrough=True))
        assert "error" not in res, res
        # FontStrikeout.SINGLE == 1.
        assert _cursor_over_match(tool_doc, "strike").CharStrikeout == 1

    def test_color_sets_char_color_int(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.formatting import format_text
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="colour this red", location="end")
        res = json.loads(format_text(query="red", color="FF0000"))
        assert "error" not in res, res
        # CharColor is an 0xRRGGBB int; FF0000 == 16711680.
        assert _cursor_over_match(tool_doc, "red").CharColor == 0xFF0000

    def test_size_sets_char_height(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.formatting import format_text
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="big small text", location="end")
        res = json.loads(format_text(query="big", size=24))
        assert "error" not in res, res
        assert _cursor_over_match(tool_doc, "big").CharHeight == pytest.approx(24.0)

    def test_font_sets_char_font_name(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.formatting import format_text
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="font family swap", location="end")
        res = json.loads(format_text(query="font", font="Liberation Mono"))
        assert "error" not in res, res
        assert _cursor_over_match(tool_doc, "font").CharFontName == "Liberation Mono"

    def test_superscript_sets_positive_escapement(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.formatting import format_text
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="x squared notation", location="end")
        res = json.loads(format_text(query="squared", superscript=True))
        assert "error" not in res, res
        cur = _cursor_over_match(tool_doc, "squared")
        assert cur.CharEscapement == 33
        assert cur.CharEscapementHeight == 58

    def test_subscript_sets_negative_escapement(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.formatting import format_text
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="water H2O molecule", location="end")
        res = json.loads(format_text(query="H2O", subscript=True))
        assert "error" not in res, res
        cur = _cursor_over_match(tool_doc, "H2O")
        assert cur.CharEscapement == -33
        assert cur.CharEscapementHeight == 58

    def test_superscript_true_subscript_false_keeps_superscript(
        self, tool_doc: Any
    ) -> None:
        # Regression for the both-flags-in-one-payload bug: the natural
        # {superscript: true, subscript: false} payload must NOT reset to
        # baseline (the comment in _apply_inline_formatting).
        from talk2view_writer.tools.formatting import format_text
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="raise this token", location="end")
        res = json.loads(
            format_text(query="token", superscript=True, subscript=False)
        )
        assert "error" not in res, res
        assert _cursor_over_match(tool_doc, "token").CharEscapement == 33

    def test_highlight_sets_char_highlight(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.formatting import format_text
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="highlight yellow region", location="end")
        res = json.loads(format_text(query="yellow", highlight="Yellow"))
        assert "error" not in res, res
        # HIGHLIGHT_COLOR_RGB["Yellow"] == 0xFFFF00.
        assert _cursor_over_match(tool_doc, "yellow").CharHighlight == 0xFFFF00

    def test_combined_properties_one_call(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.formatting import format_text
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="emphasise this strongly", location="end")
        res = json.loads(
            format_text(query="strongly", bold=True, italic=True, color="0000FF")
        )
        assert "error" not in res, res
        cur = _cursor_over_match(tool_doc, "strongly")
        assert cur.CharWeight == pytest.approx(150.0)
        assert _is_italic(cur)
        assert cur.CharColor == 0x0000FF

    def test_match_index_targets_second_occurrence(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.formatting import format_text
        from talk2view_writer.tools.writing import insert_content

        insert_content(text=" run run run", location="end")
        res = json.loads(format_text(query="run", match_index=1, bold=True))
        assert "error" not in res, res
        # Only the second 'run' is bold; the first and third stay normal.
        assert _cursor_over_match(tool_doc, "run", 0).CharWeight == pytest.approx(100.0)
        assert _cursor_over_match(tool_doc, "run", 1).CharWeight == pytest.approx(150.0)
        assert _cursor_over_match(tool_doc, "run", 2).CharWeight == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# format_text — single-target via paragraph_index
# ---------------------------------------------------------------------------


class TestFormatTextViaParagraphIndex:
    def test_paragraph_index_bolds_whole_paragraph(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.formatting import format_text
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="entire paragraph bold", location="end")
        idx = _para_index_of(tool_doc, "entire paragraph bold")
        res = json.loads(format_text(paragraph_index=idx, bold=True))
        assert "error" not in res, res
        # Every word in the paragraph is bold, including the last one.
        assert _cursor_over_match(tool_doc, "entire").CharWeight == pytest.approx(150.0)
        assert _cursor_over_match(tool_doc, "bold").CharWeight == pytest.approx(150.0)

    def test_paragraph_index_color_and_size(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.formatting import format_text
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="recolour and resize me", location="end")
        idx = _para_index_of(tool_doc, "recolour and resize me")
        res = json.loads(format_text(paragraph_index=idx, color="00AA00", size=18))
        assert "error" not in res, res
        cur = _cursor_over_paragraph(tool_doc, idx)
        assert cur.CharColor == 0x00AA00
        assert cur.CharHeight == pytest.approx(18.0)


# ---------------------------------------------------------------------------
# format_text — batch mode (queries=[...])
# ---------------------------------------------------------------------------


class TestFormatTextBatch:
    def test_batch_applies_distinct_formatting_per_region(
        self, tool_doc: Any
    ) -> None:
        from talk2view_writer.tools.formatting import format_text
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="one two three", location="end")
        res = json.loads(
            format_text(
                queries=[
                    {"query": "one", "bold": True},
                    {"query": "two", "italic": True},
                    {"query": "three", "color": "FF0000", "underline": True},
                ]
            )
        )
        assert "error" not in res, res
        assert res.get("formatted") == 3, res

        assert _cursor_over_match(tool_doc, "one").CharWeight == pytest.approx(150.0)
        assert _is_italic(_cursor_over_match(tool_doc, "two"))
        three = _cursor_over_match(tool_doc, "three")
        assert three.CharColor == 0xFF0000
        assert three.CharUnderline == 1

    def test_batch_mixes_query_and_paragraph_index_targets(
        self, tool_doc: Any
    ) -> None:
        from talk2view_writer.tools.formatting import format_text
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="first line text", location="end")
        insert_content(text="second line text", location="end")
        idx2 = _para_index_of(tool_doc, "second line text")
        res = json.loads(
            format_text(
                queries=[
                    {"query": "first", "bold": True},
                    {"paragraph_index": idx2, "italic": True},
                ]
            )
        )
        assert "error" not in res, res
        assert _cursor_over_match(tool_doc, "first").CharWeight == pytest.approx(150.0)
        assert _is_italic(_cursor_over_paragraph(tool_doc, idx2))


# ---------------------------------------------------------------------------
# format_paragraph — style / alignment / spacing / indents
# ---------------------------------------------------------------------------


class TestFormatParagraphStyle:
    def test_heading_style_sets_para_style_name(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.formatting import format_paragraph
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="A heading line", location="end")
        idx = _para_index_of(tool_doc, "A heading line")
        res = json.loads(format_paragraph(paragraph_index=idx, style="Heading1"))
        assert "error" not in res, res
        # Word 'Heading1' maps to LibreOffice 'Heading 1' (with a space).
        assert _paras(tool_doc)[idx].ParaStyleName == "Heading 1"

    def test_quote_style_maps_to_quotations(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.formatting import format_paragraph
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="quotable wisdom", location="end")
        idx = _para_index_of(tool_doc, "quotable wisdom")
        res = json.loads(format_paragraph(paragraph_index=idx, style="Quote"))
        assert "error" not in res, res
        # Word 'Quote' maps to LibreOffice 'Quotations'.
        assert _paras(tool_doc)[idx].ParaStyleName == "Quotations"


class TestFormatParagraphAlignment:
    def test_center_sets_para_adjust(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.formatting import format_paragraph
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="centre me please", location="end")
        idx = _para_index_of(tool_doc, "centre me please")
        res = json.loads(format_paragraph(paragraph_index=idx, alignment="center"))
        assert "error" not in res, res
        # ParagraphAdjust.CENTER == 3.
        assert int(_paras(tool_doc)[idx].ParaAdjust) == 3

    def test_right_sets_para_adjust(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.formatting import format_paragraph
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="push me right", location="end")
        idx = _para_index_of(tool_doc, "push me right")
        res = json.loads(format_paragraph(paragraph_index=idx, alignment="right"))
        assert "error" not in res, res
        # ParagraphAdjust.RIGHT == 1.
        assert int(_paras(tool_doc)[idx].ParaAdjust) == 1

    def test_justified_sets_para_adjust(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.formatting import format_paragraph
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="stretch this line across", location="end")
        idx = _para_index_of(tool_doc, "stretch this line across")
        res = json.loads(format_paragraph(paragraph_index=idx, alignment="justified"))
        assert "error" not in res, res
        # ParagraphAdjust.BLOCK == 2 (justified maps to block).
        assert int(_paras(tool_doc)[idx].ParaAdjust) == 2

    def test_left_sets_para_adjust(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.formatting import format_paragraph
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="back to the left", location="end")
        idx = _para_index_of(tool_doc, "back to the left")
        # Centre it first so the left write is observable.
        format_paragraph(paragraph_index=idx, alignment="center")
        res = json.loads(format_paragraph(paragraph_index=idx, alignment="left"))
        assert "error" not in res, res
        # ParagraphAdjust.LEFT == 0.
        assert int(_paras(tool_doc)[idx].ParaAdjust) == 0


class TestFormatParagraphSpacing:
    def test_space_before_after_set_margins(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.formatting import format_paragraph
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="space around me", location="end")
        idx = _para_index_of(tool_doc, "space around me")
        res = json.loads(
            format_paragraph(paragraph_index=idx, space_before=12, space_after=6)
        )
        assert "error" not in res, res
        para = _paras(tool_doc)[idx]
        assert para.ParaTopMargin == _points_to_hmm(12)
        assert para.ParaBottomMargin == _points_to_hmm(6)

    def test_line_spacing_sets_fixed_height(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.formatting import format_paragraph
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="spread the lines", location="end")
        idx = _para_index_of(tool_doc, "spread the lines")
        res = json.loads(format_paragraph(paragraph_index=idx, line_spacing=24))
        assert "error" not in res, res
        ls = _paras(tool_doc)[idx].ParaLineSpacing
        # Mode FIX == 3, Height in 1/100 mm.
        assert ls.Mode == 3
        assert ls.Height == _points_to_hmm(24)


class TestFormatParagraphIndents:
    def test_left_and_right_indent_set_margins(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.formatting import format_paragraph
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="indent both sides", location="end")
        idx = _para_index_of(tool_doc, "indent both sides")
        res = json.loads(
            format_paragraph(paragraph_index=idx, left_indent=36, right_indent=18)
        )
        assert "error" not in res, res
        para = _paras(tool_doc)[idx]
        assert para.ParaLeftMargin == _points_to_hmm(36)
        assert para.ParaRightMargin == _points_to_hmm(18)

    def test_first_line_indent_set(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.formatting import format_paragraph
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="indent the first line", location="end")
        idx = _para_index_of(tool_doc, "indent the first line")
        res = json.loads(format_paragraph(paragraph_index=idx, first_line_indent=20))
        assert "error" not in res, res
        assert _paras(tool_doc)[idx].ParaFirstLineIndent == _points_to_hmm(20)

    def test_hanging_first_line_indent_negative(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.formatting import format_paragraph
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="hanging indent here", location="end")
        idx = _para_index_of(tool_doc, "hanging indent here")
        res = json.loads(format_paragraph(paragraph_index=idx, first_line_indent=-15))
        assert "error" not in res, res
        assert _paras(tool_doc)[idx].ParaFirstLineIndent == _points_to_hmm(-15)


class TestFormatParagraphBatch:
    def test_paragraph_indices_center_multiple(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.formatting import format_paragraph
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="line uno", location="end")
        insert_content(text="line dos", location="end")
        i1 = _para_index_of(tool_doc, "line uno")
        i2 = _para_index_of(tool_doc, "line dos")
        res = json.loads(
            format_paragraph(paragraph_indices=[i1, i2], alignment="center")
        )
        assert "error" not in res, res
        assert int(_paras(tool_doc)[i1].ParaAdjust) == 3
        assert int(_paras(tool_doc)[i2].ParaAdjust) == 3


# ---------------------------------------------------------------------------
# manage_list
# ---------------------------------------------------------------------------


class TestManageList:
    def test_add_bullet_sets_numbering_rules(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.formatting import manage_list
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="first bullet item", location="end")
        idx = _para_index_of(tool_doc, "first bullet item")
        res = json.loads(
            manage_list(action="add", paragraph_indices=[idx], list_type="bullet")
        )
        assert "error" not in res, res
        para = _paras(tool_doc)[idx]
        # The numbering-rule object is what actually renders the marker.
        assert para.NumberingRules is not None
        assert para.NumberingIsNumber is True

    def test_add_number_sets_numbering_rules(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.formatting import manage_list
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="ordered step one", location="end")
        idx = _para_index_of(tool_doc, "ordered step one")
        res = json.loads(
            manage_list(action="add", paragraph_indices=[idx], list_type="number")
        )
        assert "error" not in res, res
        para = _paras(tool_doc)[idx]
        assert para.NumberingRules is not None
        assert para.NumberingIsNumber is True

    def test_add_multiple_paragraphs_share_one_list(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.formatting import manage_list
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="item alpha", location="end")
        insert_content(text="item bravo", location="end")
        insert_content(text="item charlie", location="end")
        i1 = _para_index_of(tool_doc, "item alpha")
        i2 = _para_index_of(tool_doc, "item bravo")
        i3 = _para_index_of(tool_doc, "item charlie")
        res = json.loads(
            manage_list(
                action="add",
                paragraph_indices=[i1, i2, i3],
                list_type="number",
            )
        )
        assert "error" not in res, res
        assert res.get("paragraphs_affected") == 3, res
        for i in (i1, i2, i3):
            assert _paras(tool_doc)[i].NumberingRules is not None
            assert _paras(tool_doc)[i].NumberingIsNumber is True

    def test_add_with_level_sets_numbering_level(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.formatting import manage_list
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="nested item", location="end")
        idx = _para_index_of(tool_doc, "nested item")
        res = json.loads(
            manage_list(
                action="add",
                paragraph_indices=[idx],
                list_type="bullet",
                level=2,
            )
        )
        assert "error" not in res, res
        para = _paras(tool_doc)[idx]
        assert para.NumberingRules is not None
        assert para.NumberingLevel == 2

    def test_add_with_fused_left_indent(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.formatting import manage_list
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="indented bullet", location="end")
        idx = _para_index_of(tool_doc, "indented bullet")
        res = json.loads(
            manage_list(
                action="add",
                paragraph_indices=[idx],
                list_type="bullet",
                left_indent=24,
            )
        )
        assert "error" not in res, res
        para = _paras(tool_doc)[idx]
        assert para.NumberingRules is not None
        assert para.ParaLeftMargin == _points_to_hmm(24)

    def test_remove_clears_numbering(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.formatting import manage_list
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="temporarily a list", location="end")
        idx = _para_index_of(tool_doc, "temporarily a list")
        manage_list(action="add", paragraph_indices=[idx], list_type="bullet")
        assert _paras(tool_doc)[idx].NumberingRules is not None

        res = json.loads(manage_list(action="remove", paragraph_indices=[idx]))
        assert "error" not in res, res
        para = _paras(tool_doc)[idx]
        # The tool sets NumberingRules=None, but real LO reads back an EMPTY
        # rules object (not None) — the meaningful "no longer a list item"
        # signal is NumberingIsNumber == False.
        assert para.NumberingIsNumber is False
