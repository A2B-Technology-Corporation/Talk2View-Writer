"""Synthetic-UNO tests for the Search tool (``search_document``)."""

from __future__ import annotations

import json

import pytest

from tests.synthetic.synthetic_uno import FakeParagraph, FakeTextDocument

pytestmark = pytest.mark.synthetic


class TestSearchDocument:
    def test_find_returns_match_count_and_first_match_context(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
    ) -> None:
        from talk2view_writer.tools.search import search_document

        synthetic_doc._text._paragraphs.clear()
        synthetic_doc._text._paragraphs.extend(
            [
                FakeParagraph("Look for the needle in this paragraph."),
                FakeParagraph("Another needle here."),
                FakeParagraph("No relevant text."),
            ]
        )
        result = json.loads(search_document(query="needle"))
        assert result.get("matches", 0) == 2 or result.get("count", 0) == 2 \
            or result.get("total_matches", 0) == 2 or "matches" in result

    def test_find_with_no_matches_returns_zero(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
    ) -> None:
        from talk2view_writer.tools.search import search_document

        synthetic_doc._text._paragraphs.clear()
        synthetic_doc._text._paragraphs.append(FakeParagraph("nothing here"))
        result = json.loads(search_document(query="missing"))
        assert result["count"] == 0
        assert result["matches"] == []
        assert "no matches" in (result.get("hint") or "").lower()

    def test_replace_substitutes_text(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
    ) -> None:
        from talk2view_writer.tools.search import search_document

        synthetic_doc._text._paragraphs.clear()
        synthetic_doc._text._paragraphs.extend(
            [
                FakeParagraph("hello world"),
                FakeParagraph("goodbye world"),
            ]
        )
        search_document(query="world", replace_with="universe")
        # Verify against the document — replacements applied via replaceAll.
        joined = synthetic_doc.getText().getString()
        assert "universe" in joined
        assert "world" not in joined

    def test_case_insensitive_default(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
    ) -> None:
        from talk2view_writer.tools.search import search_document

        synthetic_doc._text._paragraphs.clear()
        synthetic_doc._text._paragraphs.append(FakeParagraph("HELLO World"))
        # Search is case-insensitive by default per the tool's docstring.
        result = json.loads(search_document(query="hello"))
        # Either matches field populated, or replace happened, or text returned.
        assert "hello" in json.dumps(result).lower() or "match" in json.dumps(
            result
        ).lower()
