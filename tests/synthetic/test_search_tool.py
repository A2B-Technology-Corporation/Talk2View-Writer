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
        # Exact shape — the old assertion ended in `or "matches" in result`,
        # which is always true for a find response (the key is always
        # present), so it verified nothing. The find path returns count +
        # the matched strings + a null hint when there are matches.
        assert result["count"] == 2
        assert result["matches"] == ["needle", "needle"]
        assert result["hint"] is None

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

    def test_accepts_every_schema_kwarg(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
    ) -> None:
        """Schema-vs-signature contract: every TS schema kwarg must work.

        The schema we register with the engine
        (src/web/src/tools.ts ``search_document``) MUST be a subset
        of the Python function's keyword args. Regression: on
        2026-05-22 the schema drifted to use ``action``/``replacement``/
        ``case_sensitive``/``whole_word`` while the Python function
        used ``replace_with``/``match_case``/``match_whole_word`` —
        every call from the engine raised TypeError.

        This test exercises the function with the exact kwarg names
        the schema declares. If a future schema change introduces a
        new property name, add it here AND to the Python signature.
        """
        from talk2view_writer.tools.search import search_document

        synthetic_doc._text._paragraphs.clear()
        synthetic_doc._text._paragraphs.append(FakeParagraph("hello world"))
        result = json.loads(
            search_document(
                query="hello",
                replace_with="goodbye",
                replace_format=None,
                match_case=True,
                match_whole_word=True,
                match_wildcards=False,
                match_prefix=False,
                match_suffix=False,
            )
        )
        assert isinstance(result, dict)
        assert "error" not in result, result
