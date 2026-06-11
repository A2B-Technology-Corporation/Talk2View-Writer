"""Live-UNO end-to-end tests for the Writer tools (harness smoke seed).

Each test calls the REAL ``@tool`` function through the ``tool_doc`` harness
(which wires the extension singleton + a live document) and asserts the
resulting real-LibreOffice document state. This is the coverage layer that
the synthetic tests cannot provide — it exercises actual LO C++ behaviour,
the gap that hid the commenting bugs (Investigations #38, #66).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

pytestmark = pytest.mark.integration


def _paras(doc: Any) -> list[str]:
    out: list[str] = []
    en = doc.getText().createEnumeration()
    while en.hasMoreElements():
        el = en.nextElement()
        if el.supportsService("com.sun.star.text.Paragraph"):
            out.append(el.getString())
    return out


class TestWritingLive:
    def test_insert_content_appends_paragraph(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.writing import insert_content

        res = json.loads(insert_content(text="Hello live world.", location="end"))
        assert res.get("success") is True, res
        assert any("Hello live world." in p for p in _paras(tool_doc))

    def test_insert_table_creates_table_with_data(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.writing import insert_table

        res = json.loads(
            insert_table(
                rows=2, columns=2, location="end",
                data=[["H1", "H2"], ["a", "b"]],
            )
        )
        assert res.get("success") is True, res
        tables = tool_doc.getTextTables()
        assert tables.getCount() == 1
        tbl = tables.getByIndex(0)
        assert tbl.getCellByName("A1").getString() == "H1"
        assert tbl.getCellByName("B2").getString() == "b"


class TestFormattingLive:
    def test_format_text_sets_bold(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.formatting import format_text
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="alpha beta gamma", location="end")
        res = json.loads(format_text(query="beta", bold=True))
        assert res.get("success") is True, res
        # Verify the run is actually bold in the document.
        s = tool_doc.createSearchDescriptor()
        s.SearchString = "beta"
        rng = tool_doc.findAll(s).getByIndex(0)
        cur = rng.getText().createTextCursorByRange(rng)
        assert cur.CharWeight == pytest.approx(150.0)

    def test_format_paragraph_sets_heading_style(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.formatting import format_paragraph
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="A heading line", location="end")
        # The inserted line is the last paragraph; find its index.
        idx = len(_paras(tool_doc)) - 1
        res = json.loads(format_paragraph(paragraph_index=idx, style="Heading 1"))
        assert res.get("success") is True, res


class TestSearchLive:
    def test_search_finds_and_replaces(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.search import search_document
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="the cat sat on the mat", location="end")
        res = json.loads(search_document(query="cat", replace_with="dog"))
        assert "error" not in res, res
        # Assert on DOCUMENT STATE — the real proof, not the response shape.
        assert any("dog" in p for p in _paras(tool_doc))
        assert not any("cat" in p for p in _paras(tool_doc))

    def test_whole_word_does_not_match_substring(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.search import search_document
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="cat in the category", location="end")
        res = json.loads(search_document(query="cat", match_whole_word=True))
        # Only the standalone 'cat' matches, not 'category' (SearchWords).
        assert res.get("count") == 1, res
        assert res.get("matches") == ["cat"], res


class TestReadingLive:
    def test_get_document_returns_inserted_text(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.reading import get_document
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="readable content here", location="end")
        doc_json = json.loads(get_document())
        assert "readable content here" in json.dumps(doc_json)
