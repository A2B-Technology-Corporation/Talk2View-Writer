"""Live-UNO end-to-end tests for the ``search_document`` tool.

Every test drives the REAL ``@tool`` function through the ``tool_doc``
harness (live document + wired extension singleton) and asserts the
resulting real-LibreOffice document state — for replaces — or the
``count`` / ``matches`` the tool reports — for finds. This is the
coverage layer that exercises actual LO C++ ``SearchDescriptor``
semantics (SearchWords, SearchRegularExpression, SearchCaseSensitive),
which the UNO-stubbed unit tests cannot reach.

Conventions (per the harness contract):
  * Assert on DOCUMENT STATE for any operation that mutates the doc.
    For the JSON response, at most assert ``"error" not in res``.
  * For pure finds, ``count`` + ``matches`` ARE the observable outcome,
    so those are fair game to assert.
  * Each test builds its own precondition in a fresh ``tool_doc``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers — read live document state via UNO.
# ---------------------------------------------------------------------------


def _paras(doc: Any) -> list[str]:
    """Body paragraph strings, in document order."""
    out: list[str] = []
    en = doc.getText().createEnumeration()
    while en.hasMoreElements():
        el = en.nextElement()
        if el.supportsService("com.sun.star.text.Paragraph"):
            out.append(el.getString())
    return out


def _body_text(doc: Any) -> str:
    """Whole-body text as a single string."""
    return doc.getText().getString()


def _cursor_over_first_match(doc: Any, needle: str) -> Any:
    """Return a text cursor spanning the first occurrence of ``needle``.

    Uses a fresh (default-options) search descriptor so the assertion
    layer is independent of whatever options the tool used.
    """
    s = doc.createSearchDescriptor()
    s.SearchString = needle
    found = doc.findAll(s)
    assert found.getCount() >= 1, f"expected to find {needle!r} in document"
    rng = found.getByIndex(0)
    return rng.getText().createTextCursorByRange(rng)


# ---------------------------------------------------------------------------
# Plain find — count + matches.
# ---------------------------------------------------------------------------


class TestPlainFind:
    def test_find_counts_all_occurrences(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.search import search_document
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="the cat sat near the cat by the cat", location="end")
        res = json.loads(search_document(query="cat"))
        assert "error" not in res, res
        assert res["count"] == 3, res
        assert res["matches"] == ["cat", "cat", "cat"], res

    def test_find_returns_actual_matched_strings(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.search import search_document
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="alpha beta gamma", location="end")
        res = json.loads(search_document(query="beta"))
        assert "error" not in res, res
        assert res["count"] == 1, res
        assert res["matches"] == ["beta"], res

    def test_find_no_match_returns_zero_with_hint(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.search import search_document
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="nothing relevant here", location="end")
        res = json.loads(search_document(query="zebra"))
        assert "error" not in res, res
        assert res["count"] == 0, res
        assert res["matches"] == [], res
        assert res["hint"], "a no-match find should carry a recovery hint"

    def test_find_does_not_mutate_document(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.search import search_document
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="immutable content", location="end")
        before = _body_text(tool_doc)
        search_document(query="content")
        assert _body_text(tool_doc) == before


# ---------------------------------------------------------------------------
# match_case -> SearchCaseSensitive.
# ---------------------------------------------------------------------------


class TestMatchCase:
    def test_default_is_case_insensitive(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.search import search_document
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="Cat cat CAT", location="end")
        res = json.loads(search_document(query="cat"))
        assert "error" not in res, res
        assert res["count"] == 3, res

    def test_case_sensitive_matches_only_exact_case(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.search import search_document
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="Cat cat CAT", location="end")
        res = json.loads(search_document(query="cat", match_case=True))
        assert "error" not in res, res
        assert res["count"] == 1, res
        assert res["matches"] == ["cat"], res


# ---------------------------------------------------------------------------
# match_whole_word -> SearchWords. Substring must NOT match.
# ---------------------------------------------------------------------------


class TestMatchWholeWord:
    def test_whole_word_excludes_substring(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.search import search_document
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="cat in the category of scatter", location="end")
        res = json.loads(search_document(query="cat", match_whole_word=True))
        assert "error" not in res, res
        # Only the standalone "cat" — not "category", not "scatter".
        assert res["count"] == 1, res
        assert res["matches"] == ["cat"], res

    def test_substring_matches_without_whole_word(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.search import search_document
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="cat in the category of scatter", location="end")
        res = json.loads(search_document(query="cat"))
        assert "error" not in res, res
        # "cat" + "cat"egory + s"cat"ter = 3 substring hits.
        assert res["count"] == 3, res

    def test_whole_word_replace_leaves_substrings_intact(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.search import search_document
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="cat and category", location="end")
        res = json.loads(
            search_document(query="cat", match_whole_word=True, replace_with="dog")
        )
        assert "error" not in res, res
        body = _body_text(tool_doc)
        # The standalone word became "dog"; "category" must be untouched.
        assert body == "dog and category", body


# ---------------------------------------------------------------------------
# match_wildcards -> SearchRegularExpression.
# ---------------------------------------------------------------------------


class TestMatchWildcards:
    def test_character_class_regex_finds_variants(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.search import search_document
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="the cat and the cot in a cut", location="end")
        # c[ao]t matches "cat" and "cot" but not "cut".
        res = json.loads(search_document(query="c[ao]t", match_wildcards=True))
        assert "error" not in res, res
        assert res["count"] == 2, res
        assert res["matches"] == ["cat", "cot"], res

    def test_regex_literal_treated_literally_without_flag(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.search import search_document
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="the cat and the cot", location="end")
        # Without match_wildcards the brackets are literal text, so no match.
        res = json.loads(search_document(query="c[ao]t"))
        assert "error" not in res, res
        assert res["count"] == 0, res

    def test_regex_replace_mutates_all_matches(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.search import search_document
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="cat cot cut", location="end")
        res = json.loads(
            search_document(query="c[ao]t", match_wildcards=True, replace_with="pet")
        )
        assert "error" not in res, res
        body = _body_text(tool_doc)
        # cat -> pet, cot -> pet, cut unchanged.
        assert body == "pet pet cut", body


# ---------------------------------------------------------------------------
# replace_with -> replacements + document state.
# ---------------------------------------------------------------------------


class TestReplace:
    def test_single_replace_updates_document(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.search import search_document
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="the quick brown fox", location="end")
        res = json.loads(search_document(query="quick", replace_with="slow"))
        assert "error" not in res, res
        assert res["replacements"] == 1, res
        body = _body_text(tool_doc)
        assert body == "the slow brown fox", body

    def test_replace_all_occurrences(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.search import search_document
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="red red red", location="end")
        res = json.loads(search_document(query="red", replace_with="blue"))
        assert "error" not in res, res
        assert res["replacements"] == 3, res
        body = _body_text(tool_doc)
        assert body == "blue blue blue", body
        assert "red" not in body

    def test_replace_with_empty_string_deletes_matches(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.search import search_document
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="keep DELETE keep", location="end")
        res = json.loads(search_document(query="DELETE", replace_with=""))
        assert "error" not in res, res
        assert res["replacements"] == 1, res
        body = _body_text(tool_doc)
        assert "DELETE" not in body, body
        assert "keep" in body, body

    def test_replace_no_match_reports_zero_and_leaves_doc(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.search import search_document
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="unchanged body text", location="end")
        before = _body_text(tool_doc)
        res = json.loads(search_document(query="absent", replace_with="x"))
        assert "error" not in res, res
        assert res["replacements"] == 0, res
        assert _body_text(tool_doc) == before

    def test_replace_preserves_paragraph_style(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.formatting import format_paragraph
        from talk2view_writer.tools.search import search_document
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="heading word here", location="end")
        idx = len(_paras(tool_doc)) - 1
        # "Heading1" is the canonical VALID_STYLES name (no space).
        fp = json.loads(format_paragraph(paragraph_index=idx, style="Heading1"))
        assert "error" not in fp, fp
        # Capture the live style name before the replace.
        para = tool_doc.getText().createEnumeration()
        style_before = None
        while para.hasMoreElements():
            el = para.nextElement()
            if el.supportsService("com.sun.star.text.Paragraph") and "word" in el.getString():
                style_before = el.ParaStyleName
        assert style_before, "precondition: styled paragraph not found"

        res = json.loads(search_document(query="word", replace_with="token"))
        assert "error" not in res, res
        assert res["replacements"] == 1, res

        # The replaced paragraph keeps its paragraph style.
        para2 = tool_doc.getText().createEnumeration()
        style_after = None
        while para2.hasMoreElements():
            el = para2.nextElement()
            if el.supportsService("com.sun.star.text.Paragraph") and "token" in el.getString():
                style_after = el.ParaStyleName
        assert style_after == style_before, (style_before, style_after)

    def test_case_sensitive_replace_only_touches_exact_case(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.search import search_document
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="Foo foo FOO", location="end")
        res = json.loads(
            search_document(query="foo", replace_with="bar", match_case=True)
        )
        assert "error" not in res, res
        assert res["replacements"] == 1, res
        body = _body_text(tool_doc)
        # Only the exact-case "foo" became "bar".
        assert body == "Foo bar FOO", body


# ---------------------------------------------------------------------------
# replace_format -> replace + apply inline formatting (asserted on doc).
# ---------------------------------------------------------------------------


class TestReplaceFormat:
    def test_replace_format_applies_bold_to_new_text(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.search import search_document
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="plain target text", location="end")
        res = json.loads(
            search_document(
                query="target",
                replace_with="TARGET",
                replace_format={"bold": True},
            )
        )
        assert "error" not in res, res
        assert res["replacements"] == 1, res
        assert _body_text(tool_doc) == "plain TARGET text"
        cur = _cursor_over_first_match(tool_doc, "TARGET")
        # UNO CharWeight: BOLD == 150.0.
        assert cur.CharWeight == pytest.approx(150.0)

    def test_replace_format_applies_color(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.search import search_document
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="colour me here", location="end")
        res = json.loads(
            search_document(
                query="here",
                replace_with="THERE",
                replace_format={"color": "FF0000"},
            )
        )
        assert "error" not in res, res
        cur = _cursor_over_first_match(tool_doc, "THERE")
        # 0xFF0000 == 16711680.
        assert cur.CharColor == 0xFF0000

    def test_replace_format_applies_italic_and_size(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.search import search_document
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="emphasise this phrase", location="end")
        res = json.loads(
            search_document(
                query="phrase",
                replace_with="PHRASE",
                replace_format={"italic": True, "size": 18},
            )
        )
        assert "error" not in res, res
        cur = _cursor_over_first_match(tool_doc, "PHRASE")
        # PyUNO reads CharPosture back as a com.sun.star.awt.FontSlant enum
        # (the production code set it to the int 2 == ITALIC).
        assert cur.CharPosture.value == "ITALIC"
        assert cur.CharHeight == pytest.approx(18.0)

    def test_replace_format_records_applied_payload(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.search import search_document
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="echo me", location="end")
        res = json.loads(
            search_document(
                query="echo",
                replace_with="ECHO",
                replace_format={"bold": True, "color": "00FF00"},
            )
        )
        assert "error" not in res, res
        # The tool echoes the applied formatting; document state still the proof.
        assert res.get("replace_format_applied") == {"bold": True, "color": "00FF00"}
        cur = _cursor_over_first_match(tool_doc, "ECHO")
        assert cur.CharWeight == pytest.approx(150.0)
        assert cur.CharColor == 0x00FF00

    def test_replace_format_on_all_matches(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.search import search_document
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="x and x and x", location="end")
        res = json.loads(
            search_document(
                query="x",
                replace_with="Y",
                replace_format={"bold": True},
            )
        )
        assert "error" not in res, res
        assert res["replacements"] == 3, res
        assert _body_text(tool_doc) == "Y and Y and Y"
        # Every replaced run is bold.
        s = tool_doc.createSearchDescriptor()
        s.SearchString = "Y"
        found = tool_doc.findAll(s)
        assert found.getCount() == 3
        for i in range(found.getCount()):
            rng = found.getByIndex(i)
            cur = rng.getText().createTextCursorByRange(rng)
            assert cur.CharWeight == pytest.approx(150.0), f"match {i} not bold"


# ---------------------------------------------------------------------------
# match_prefix / match_suffix -> regex word-boundary anchors.
# ---------------------------------------------------------------------------


class TestPrefixSuffix:
    def test_match_prefix_anchors_at_word_start(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.search import search_document
        from talk2view_writer.tools.writing import insert_content

        # "pre" is a word-start in "pretend" and "prefix" but mid-word in
        # "unprepared" (after "un"), so the \b prefix anchor excludes it.
        insert_content(text="pretend prefix unprepared", location="end")
        res = json.loads(search_document(query="pre", match_prefix=True))
        assert "error" not in res, res
        assert res["count"] == 2, res
        assert res["matches"] == ["pre", "pre"], res

    def test_match_suffix_anchors_at_word_end(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.search import search_document
        from talk2view_writer.tools.writing import insert_content

        # "ing" ends "running" and "jumping" (word end) but is mid-word in
        # "ingest", so the \b suffix anchor excludes "ingest".
        insert_content(text="running jumping ingest", location="end")
        res = json.loads(search_document(query="ing", match_suffix=True))
        assert "error" not in res, res
        assert res["count"] == 2, res
        assert res["matches"] == ["ing", "ing"], res

    def test_match_prefix_replace_only_at_word_start(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.search import search_document
        from talk2view_writer.tools.writing import insert_content

        insert_content(text="pretend unprepared", location="end")
        res = json.loads(
            search_document(query="pre", match_prefix=True, replace_with="POST")
        )
        assert "error" not in res, res
        assert res["replacements"] == 1, res
        body = _body_text(tool_doc)
        # Only the word-start "pre" in "pretend" is replaced.
        assert body == "POSTtend unprepared", body
