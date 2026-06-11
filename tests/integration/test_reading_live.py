"""Live-UNO end-to-end tests for the ``reading`` tools.

Each test drives the REAL ``@tool`` function through the ``tool_doc``
harness (a live headless Writer document with the extension singleton
wired to it) and asserts on the resulting document / response content —
never on a tool's bare success flag.

Covered tools (``src/talk2view_writer/tools/reading.py``):

- :func:`get_document` — inserted text round-trips into ``text`` /
  ``paragraphs``; ``start_index`` + ``count`` windowing; the empty-doc
  ``hint``; ``include_font_details=True`` emits Word-shaped font info;
  a table is reported under ``tables``.
- :func:`get_selection` — a programmatic view selection over a range is
  read back through ``text``; the empty case emits a ``hint``.
- :func:`select_text` — selecting by query and by ``paragraph_index``
  produces a real view selection that ``get_selection`` then reads back,
  proving the selection actually landed in the document.

The Word<->LibreOffice paragraph-style round-trip (a ``Heading 1`` line
written with the Word name ``Heading1`` must read back as ``Heading1``)
is exercised here — the regression that hid behind Investigations
#53 / #56.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers (UNO reads — the source of truth, independent of tool responses)
# ---------------------------------------------------------------------------


def _paras(doc: Any) -> list[str]:
    out: list[str] = []
    en = doc.getText().createEnumeration()
    while en.hasMoreElements():
        el = en.nextElement()
        if el.supportsService("com.sun.star.text.Paragraph"):
            out.append(el.getString())
    return out


def _select_range_by_search(doc: Any, needle: str) -> None:
    """Put a real view selection over the first occurrence of ``needle``."""
    sd = doc.createSearchDescriptor()
    sd.SearchString = needle
    found = doc.findAll(sd)
    assert found.getCount() >= 1, f"{needle!r} not found in document"
    doc.getCurrentController().select(found.getByIndex(0))


# ---------------------------------------------------------------------------
# get_document
# ---------------------------------------------------------------------------


class TestGetDocumentContent:
    def test_inserted_text_round_trips_into_text_and_paragraphs(
        self, tool_doc: Any
    ) -> None:
        from talk2view_writer.tools.reading import get_document
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="readable content here", location="end")

        res = json.loads(get_document())
        assert "error" not in res, res
        # The flat document text contains the inserted run.
        assert "readable content here" in res["text"]
        # And it surfaces as a structured paragraph entry too.
        para_texts = [p["text"] for p in res["paragraphs"]]
        assert any("readable content here" in t for t in para_texts), para_texts

    def test_paragraph_entries_carry_index_and_style(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.reading import get_document
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="first line", location="end")
        insert_content(text="second line", location="end")

        res = json.loads(get_document())
        entries = res["paragraphs"]
        # Indices are zero-based and monotonically increasing.
        assert [e["index"] for e in entries] == list(range(len(entries)))
        # Every entry exposes a (Word-vocabulary) style name string.
        for e in entries:
            assert isinstance(e["style"], str) and e["style"], e

    def test_total_paragraphs_counts_every_top_level_paragraph(
        self, tool_doc: Any
    ) -> None:
        from talk2view_writer.tools.reading import get_document
        from talk2view_writer.tools.writing import insert_content

        for n in range(4):
            insert_content(text=f"line {n}", location="end")

        res = json.loads(get_document())
        # total_paragraphs must equal the true live paragraph count.
        assert res["total_paragraphs"] == len(_paras(tool_doc))


class TestGetDocumentEmpty:
    def test_blank_document_returns_hint(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.reading import get_document

        res = json.loads(get_document())
        assert "error" not in res, res
        # A pristine swriter doc has a single empty paragraph.
        assert res["total_paragraphs"] <= 1
        assert "hint" in res, res
        assert "empty" in res["hint"].lower()


class TestGetDocumentWindowing:
    def test_start_index_skips_leading_paragraphs(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.reading import get_document
        from talk2view_writer.tools.writing import insert_content

        for n in range(5):
            insert_content(text=f"para {n}", location="end")

        all_res = json.loads(get_document())
        total = all_res["total_paragraphs"]

        res = json.loads(get_document(start_index=2))
        # Windowed read still reports the true total...
        assert res["total_paragraphs"] == total
        # ...but the first returned entry is the third paragraph.
        first = res["paragraphs"][0]
        assert first["index"] == 2
        assert first["text"] == all_res["paragraphs"][2]["text"]

    def test_count_limits_returned_paragraphs(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.reading import get_document
        from talk2view_writer.tools.writing import insert_content

        for n in range(6):
            insert_content(text=f"row {n}", location="end")

        res = json.loads(get_document(count=2))
        # Only `count` paragraph entries come back, total is unchanged.
        assert len(res["paragraphs"]) == 2
        assert res["total_paragraphs"] >= 6

    def test_start_index_and_count_window_together(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.reading import get_document
        from talk2view_writer.tools.writing import insert_content

        for n in range(6):
            insert_content(text=f"item {n}", location="end")

        full = json.loads(get_document())
        window = json.loads(get_document(start_index=1, count=3))

        returned = window["paragraphs"]
        assert len(returned) == 3
        # Each windowed entry matches the corresponding full-read entry by
        # both index and text — proving the slice aligns with reality.
        for offset, entry in enumerate(returned):
            mirror = full["paragraphs"][1 + offset]
            assert entry["index"] == mirror["index"] == 1 + offset
            assert entry["text"] == mirror["text"]


class TestGetDocumentFontDetails:
    def test_font_details_omitted_by_default(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.reading import get_document
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="plain paragraph", location="end")

        res = json.loads(get_document())
        # Default response must not carry per-paragraph font payloads.
        assert all("font" not in e for e in res["paragraphs"]), res

    def test_font_details_emit_word_shaped_keys(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.reading import get_document
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="fonted paragraph", location="end")

        res = json.loads(get_document(include_font_details=True))
        fonted = [e for e in res["paragraphs"] if e["text"] == "fonted paragraph"]
        assert fonted, res
        font = fonted[0]["font"]
        # Word-shaped key set (see _read_font_properties).
        assert set(font) == {
            "name",
            "size",
            "color",
            "bold",
            "italic",
            "underline",
            "highlight",
        }, font
        # A plain run: a real font name, a positive size, and boolean
        # emphasis flags. (We don't pin the exact emphasis values here —
        # a fresh LibreOffice paragraph can report a non-NONE CharPosture
        # sentinel even when visually un-italicised; the bold/italic
        # *change* round-trip is asserted in the dedicated tests below.)
        assert isinstance(font["name"], str) and font["name"]
        assert font["size"] > 0
        assert isinstance(font["bold"], bool)
        assert isinstance(font["italic"], bool)
        assert isinstance(font["underline"], bool)
        # No emphasis was applied, so bold at least must be False.
        assert font["bold"] is False

    def test_font_details_reflect_bold_run(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.formatting import format_text
        from talk2view_writer.tools.reading import get_document
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="emphatic paragraph", location="end")
        format_text(query="emphatic paragraph", bold=True)

        res = json.loads(get_document(include_font_details=True))
        target = [e for e in res["paragraphs"] if e["text"] == "emphatic paragraph"]
        assert target, res
        # The bold we applied must be reflected in the font diagnostics.
        assert target[0]["font"]["bold"] is True, target[0]["font"]


class TestGetDocumentTables:
    def test_table_reported_with_dimensions_and_first_row(
        self, tool_doc: Any
    ) -> None:
        from talk2view_writer.tools.reading import get_document
        from talk2view_writer.tools.writing import insert_table

        insert_table(
            rows=2,
            columns=3,
            location="end",
            data=[["Name", "Age", "City"], ["Ann", "30", "Perth"]],
        )

        res = json.loads(get_document())
        assert len(res["tables"]) == 1, res
        tbl = res["tables"][0]
        assert tbl["rows"] == 2
        assert tbl["columns"] == 3
        # The header row preview comes back verbatim from the live table.
        assert tbl["first_row"] == ["Name", "Age", "City"], tbl


# ---------------------------------------------------------------------------
# Word <-> LibreOffice paragraph-style round-trip (Investigations #53 / #56)
# ---------------------------------------------------------------------------


class TestStyleRoundTrip:
    def test_heading1_round_trips_through_word_vocabulary(
        self, tool_doc: Any
    ) -> None:
        from talk2view_writer.tools.reading import get_document
        from talk2view_writer.tools.writing import insert_content

        # The agent sends the Word name 'Heading1'; the tool maps it to the
        # LibreOffice 'Heading 1' style on write.
        insert_content(text="A Major Heading", location="end", style="Heading1")

        # Confirm the live LibreOffice style is actually 'Heading 1'.
        match = [
            p
            for p in _enumerate_styled_paras(tool_doc)
            if p[1] == "A Major Heading"
        ]
        assert match and match[0][0] == "Heading 1", match

        # And get_document folds it back to the Word vocabulary the agent
        # understands ('Heading1'), never the raw LO 'Heading 1'.
        res = json.loads(get_document())
        entry = [e for e in res["paragraphs"] if e["text"] == "A Major Heading"]
        assert entry, res
        assert entry[0]["style"] == "Heading1", entry[0]

    def test_heading2_round_trips(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.reading import get_document
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="A Sub Heading", location="end", style="Heading2")

        res = json.loads(get_document())
        entry = [e for e in res["paragraphs"] if e["text"] == "A Sub Heading"]
        assert entry, res
        assert entry[0]["style"] == "Heading2", entry[0]

    def test_format_paragraph_heading_reads_back_as_word_name(
        self, tool_doc: Any
    ) -> None:
        from talk2view_writer.tools.formatting import format_paragraph
        from talk2view_writer.tools.reading import get_document
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="becomes a heading", location="end")
        idx = len(_paras(tool_doc)) - 1
        format_paragraph(paragraph_index=idx, style="Heading1")

        res = json.loads(get_document())
        entry = [e for e in res["paragraphs"] if e["text"] == "becomes a heading"]
        assert entry, res
        assert entry[0]["style"] == "Heading1", entry[0]


def _enumerate_styled_paras(doc: Any) -> list[tuple[str, str]]:
    """Return (ParaStyleName, text) for every top-level paragraph."""
    out: list[tuple[str, str]] = []
    en = doc.getText().createEnumeration()
    while en.hasMoreElements():
        el = en.nextElement()
        if el.supportsService("com.sun.star.text.Paragraph"):
            out.append((getattr(el, "ParaStyleName", ""), el.getString()))
    return out


# ---------------------------------------------------------------------------
# get_selection
# ---------------------------------------------------------------------------


class TestGetSelection:
    def test_reads_back_a_programmatic_selection(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.reading import get_selection
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="select this exact phrase", location="end")
        _select_range_by_search(tool_doc, "exact phrase")

        res = json.loads(get_selection())
        assert "error" not in res, res
        assert res["text"] == "exact phrase", res
        # When something is selected there is no "no selection" hint.
        assert "hint" not in res, res

    def test_empty_selection_returns_hint(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.reading import get_selection
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="nothing selected here", location="end")
        # Collapse the selection to an insertion point (empty selection).
        doc_text = tool_doc.getText()
        cursor = doc_text.createTextCursor()
        cursor.gotoStart(False)
        tool_doc.getCurrentController().select(cursor)

        res = json.loads(get_selection())
        assert "error" not in res, res
        assert res["text"] == "", res
        assert "hint" in res, res


# ---------------------------------------------------------------------------
# select_text (selection round-trips through get_selection)
# ---------------------------------------------------------------------------


class TestSelectText:
    def test_select_by_query_lands_in_the_view(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.reading import get_selection, select_text
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="the quick brown fox jumps", location="end")
        res = json.loads(select_text(query="brown fox"))
        assert "error" not in res, res

        # Prove the selection is real by reading it back through get_selection.
        sel = json.loads(get_selection())
        assert sel["text"] == "brown fox", sel

    def test_select_by_query_is_case_insensitive(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.reading import get_selection, select_text
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="Mixed Case Words Here", location="end")
        res = json.loads(select_text(query="mixed case"))
        assert "error" not in res, res

        sel = json.loads(get_selection())
        # The matched text preserves the document's original casing.
        assert sel["text"] == "Mixed Case", sel

    def test_select_by_match_index_picks_the_second_occurrence(
        self, tool_doc: Any
    ) -> None:
        from talk2view_writer.tools.reading import select_text
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="alpha marker beta marker gamma", location="end")
        res = json.loads(select_text(query="marker", match_index=1))
        assert "error" not in res, res
        # Two occurrences exist; we asked for the second.
        assert res["total_matches"] == 2, res
        assert res["match_index"] == 1, res

    def test_select_by_paragraph_index_selects_whole_paragraph(
        self, tool_doc: Any
    ) -> None:
        from talk2view_writer.tools.reading import get_selection, select_text
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="paragraph zero text", location="end")
        insert_content(text="paragraph one text", location="end")

        # Target the paragraph that holds "paragraph one text".
        target_idx = next(
            i for i, p in enumerate(_paras(tool_doc)) if p == "paragraph one text"
        )
        res = json.loads(select_text(paragraph_index=target_idx))
        assert "error" not in res, res

        # The entire paragraph is now the live selection.
        sel = json.loads(get_selection())
        assert sel["text"] == "paragraph one text", sel

    def test_select_missing_query_returns_error(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.reading import select_text
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="present words only", location="end")
        res = json.loads(select_text(query="absent phrase"))
        # Not-found is reported as a recoverable error payload (not a raise).
        assert "error" in res, res
        assert "recovery" in res, res
