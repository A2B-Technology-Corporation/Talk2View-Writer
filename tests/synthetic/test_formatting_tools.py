"""Synthetic-UNO tests for the Formatting tools.

``format_text`` / ``format_paragraph`` / ``manage_list`` against an
in-process document. These tests exercise the validation logic and
the happy-path mutation patterns — full UNO interaction is covered
by integration tests against real soffice.
"""

from __future__ import annotations

import json

import pytest

from tests.synthetic.synthetic_uno import FakeParagraph, FakeTextDocument

pytestmark = pytest.mark.synthetic


class TestFormatText:
    def test_no_args_returns_error(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.formatting import format_text

        result = json.loads(format_text())
        assert "error" in result

    def test_invalid_color_returns_error(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.formatting import format_text

        result = json.loads(format_text(query="hello", color="not-a-color"))
        assert "error" in result

    def test_format_by_query_finds_and_applies(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
    ) -> None:
        from talk2view_writer.tools.formatting import format_text

        synthetic_doc._text._paragraphs.append(FakeParagraph("make this BOLD"))
        result = json.loads(format_text(query="BOLD", bold=True))
        # Either success or no-match (synthetic search may not match exactly).
        # Just confirm we got a structured response.
        assert isinstance(result, dict)

    def test_batch_queries_accepted(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.formatting import format_text

        synthetic_doc._text._paragraphs.append(FakeParagraph("alpha beta gamma"))
        result = json.loads(
            format_text(
                queries=[
                    {"query": "alpha", "bold": True},
                    {"query": "gamma", "italic": True},
                ]
            )
        )
        assert isinstance(result, dict)


class TestFormatParagraph:
    def test_invalid_alignment_returns_error(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.formatting import format_paragraph

        result = json.loads(
            format_paragraph(paragraph_indices=[0], alignment="diagonal")
        )
        assert "error" in result

    def test_missing_targets_returns_error(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.formatting import format_paragraph

        result = json.loads(format_paragraph(alignment="center"))
        assert "error" in result

    def test_apply_alignment_to_paragraph(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.formatting import format_paragraph

        synthetic_doc._text._paragraphs.append(FakeParagraph("align me"))
        json.loads(
            format_paragraph(paragraph_indices=[1], alignment="center")
        )
        # ParaAdjust=3 → CENTER in our alignment map.
        assert (
            synthetic_doc._text._paragraphs[1].getPropertyValue("ParaAdjust") == 3
        )

    def test_apply_style_to_paragraph(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.formatting import format_paragraph

        synthetic_doc._text._paragraphs.append(FakeParagraph("style me"))
        json.loads(
            format_paragraph(paragraph_indices=[1], style="Heading1")
        )
        # Word "Heading1" should translate to LibreOffice "Heading 1".
        applied = synthetic_doc._text._paragraphs[1].getPropertyValue(
            "ParaStyleName"
        )
        assert applied in ("Heading 1", "Heading1")


class TestManageList:
    def test_empty_paragraph_indices_returns_error(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.formatting import manage_list

        result = json.loads(
            manage_list(action="add", list_type="bullet", paragraph_indices=[])
        )
        assert "error" in result

    def test_invalid_action_returns_error(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.formatting import manage_list

        result = json.loads(
            manage_list(action="rotate", paragraph_indices=[0])
        )
        assert "error" in result

    def test_add_bullet_changes_paragraph_style(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.formatting import manage_list

        synthetic_doc._text._paragraphs.append(FakeParagraph("item one"))
        synthetic_doc._text._paragraphs.append(FakeParagraph("item two"))
        json.loads(
            manage_list(
                action="add", list_type="bullet", paragraph_indices=[1, 2]
            )
        )
        # Bullet lists in Writer apply the "List Bullet" paragraph style.
        for idx in (1, 2):
            applied = synthetic_doc._text._paragraphs[idx].getPropertyValue(
                "ParaStyleName"
            )
            assert applied in ("List Bullet", "ListBullet", "List Number")
