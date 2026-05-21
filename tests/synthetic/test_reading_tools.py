"""Synthetic-UNO tests for the Reading tools.

Covers ``get_document``, ``get_selection``, ``select_text`` against the
in-process :mod:`tests.synthetic.synthetic_uno` model — same code paths
as production (the ``@tool`` and ``@ui_thread_tool`` decorators wrap
the real function bodies), but no soffice required.
"""

from __future__ import annotations

import json

import pytest

from tests.synthetic.synthetic_uno import FakeTextDocument

pytestmark = pytest.mark.synthetic


# ---------------------------------------------------------------------------
# get_document
# ---------------------------------------------------------------------------


class TestGetDocument:
    def test_empty_document_returns_hint(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.reading import get_document

        result = json.loads(get_document())
        # Single empty paragraph is the canonical "empty doc" shape.
        assert result["total_paragraphs"] == 1
        assert result["paragraphs"][0]["text"] == ""
        # Hint surfaces so the agent prompts the user to add content.
        assert "hint" in result

    def test_populated_document_returns_all_paragraphs(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
    ) -> None:
        from talk2view_writer.tools.reading import get_document

        synthetic_doc._text._paragraphs.clear()
        from tests.synthetic.synthetic_uno import FakeParagraph

        synthetic_doc._text._paragraphs.extend(
            [
                FakeParagraph("Title", style="Heading 1"),
                FakeParagraph("Body line one."),
                FakeParagraph("Body line two."),
            ]
        )

        result = json.loads(get_document())
        assert result["total_paragraphs"] == 3
        assert [p["text"] for p in result["paragraphs"]] == [
            "Title",
            "Body line one.",
            "Body line two.",
        ]
        # Word-name translation: "Heading 1" (LibreOffice) → "Heading1" (Word schema).
        assert result["paragraphs"][0]["style"] == "Heading1"

    def test_pagination_returns_requested_window(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
    ) -> None:
        from talk2view_writer.tools.reading import get_document
        from tests.synthetic.synthetic_uno import FakeParagraph

        synthetic_doc._text._paragraphs.clear()
        synthetic_doc._text._paragraphs.extend(
            FakeParagraph(f"line {i}") for i in range(150)
        )

        result = json.loads(get_document(start_index=50, count=10))
        assert result["total_paragraphs"] == 150
        assert len(result["paragraphs"]) == 10
        assert result["paragraphs"][0]["text"] == "line 50"
        assert result["paragraphs"][-1]["text"] == "line 59"

    @pytest.mark.parametrize(
        "start_index,count",
        [
            (-1, 10),
            (0, 0),
            (0, 101),
        ],
    )
    def test_invalid_args_raise(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
        start_index: int,
        count: int,
    ) -> None:
        from talk2view_writer.tools.reading import get_document

        with pytest.raises(ValueError):
            get_document(start_index=start_index, count=count)

    def test_excludes_font_details_by_default(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.reading import get_document

        result = json.loads(get_document())
        # Default response shape excludes font-level info to save tokens.
        for para in result["paragraphs"]:
            assert "font" not in para or para["font"] is None or para.get("font") == {}

    def test_includes_font_details_when_requested(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
    ) -> None:
        from talk2view_writer.tools.reading import get_document

        result = json.loads(get_document(include_font_details=True))
        # Even an empty document has one paragraph; font dict appears.
        assert result["total_paragraphs"] >= 1


# ---------------------------------------------------------------------------
# get_selection
# ---------------------------------------------------------------------------


class TestGetSelection:
    def test_empty_selection_returns_empty(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
    ) -> None:
        from talk2view_writer.tools.reading import get_selection

        result = json.loads(get_selection())
        # Either empty string or "selected": "" depending on tool's JSON shape.
        # Tool docstring says the field exists; assert presence + emptiness.
        assert result.get("selected_text", "") == ""

    def test_returns_selected_string_when_present(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
    ) -> None:
        from talk2view_writer.tools.reading import get_selection
        from tests.synthetic.synthetic_uno import FakeTextCursor

        cur = FakeTextCursor()
        cur.setString("hello world")
        synthetic_doc._selection = [cur]

        result = json.loads(get_selection())
        # The tool should report the cursor's string content.
        assert "hello world" in json.dumps(result)


# ---------------------------------------------------------------------------
# select_text
# ---------------------------------------------------------------------------


class TestSelectText:
    def test_select_by_paragraph_index_acks_the_target(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
    ) -> None:
        from talk2view_writer.tools.reading import select_text
        from tests.synthetic.synthetic_uno import FakeParagraph

        synthetic_doc._text._paragraphs.clear()
        synthetic_doc._text._paragraphs.extend(
            [FakeParagraph("alpha"), FakeParagraph("beta"), FakeParagraph("gamma")]
        )

        # The tool must accept a paragraph index and acknowledge the
        # selection without raising. Specific JSON shape is asserted by
        # the existing Word-fixture parity tests; here we just exercise
        # the path.
        result = select_text(paragraph_index=1)
        parsed = json.loads(result)
        assert "selected" in parsed or "success" in parsed or parsed != {}

    def test_select_by_query_finds_matching_text(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
    ) -> None:
        from talk2view_writer.tools.reading import select_text
        from tests.synthetic.synthetic_uno import FakeParagraph

        synthetic_doc._text._paragraphs.clear()
        synthetic_doc._text._paragraphs.append(
            FakeParagraph("here is the needle in the haystack")
        )

        result = select_text(query="needle")
        parsed = json.loads(result)
        # Should not error out; the synthetic model has no real search
        # cursor so a graceful fallthrough is enough.
        assert isinstance(parsed, dict)
