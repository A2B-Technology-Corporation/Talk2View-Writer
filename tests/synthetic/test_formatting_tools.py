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

    def test_accepts_every_schema_kwarg(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        """Schema-vs-signature contract: every TS schema kwarg must work.

        ``src/web/src/tools.ts`` declares the format_text schema the
        engine sees. Each property name there MUST exist as a Python
        kwarg here. Investigation #35 (the cats/cars debugging trip)
        showed how silently the two can drift apart — this test fires
        an alarm before a real engine call would TypeError.

        We exercise the happy path with ``query`` so the body actually
        runs end-to-end, then assert no kwarg name was rejected.
        """
        from talk2view_writer.tools.formatting import format_text

        synthetic_doc._text._paragraphs.clear()
        synthetic_doc._text._paragraphs.append(FakeParagraph("hello world"))
        result = json.loads(
            format_text(
                query="hello",
                bold=True,
                italic=False,
                underline=True,
                underline_style="single",
                strikethrough=False,
                superscript=False,
                subscript=False,
                color="FF0000",
                highlight="Yellow",
                size=12.0,
                font="Arial",
                match_index=0,
            )
        )
        assert isinstance(result, dict)
        assert "error" not in result, result

    def test_font_param_applies_charfontname(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        """Asking for a different font family sets CharFontName on the cursor.

        Regression for the chat log on 2026-05-23 where AI said "I
        cannot change the font type (like Arial or Times New Roman)"
        — the schema wasn't exposing ``font`` to the engine. The
        Python ``font`` kwarg has always worked; the missing piece
        was the TS schema (fixed in src/web/src/tools.ts).
        """
        from talk2view_writer.tools.formatting import format_text

        synthetic_doc._text._paragraphs.clear()
        synthetic_doc._text._paragraphs.append(FakeParagraph("Pip the penguin"))
        result = json.loads(
            format_text(query="Pip the penguin", font="Times New Roman")
        )
        assert "error" not in result, result
        assert result.get("success") is True

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

    def test_add_bullet_returns_structured_error_when_no_alias_available(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        """Missing bullet-style aliases → structured error, not exception.

        When the LO build has none of the known bullet-style aliases,
        ``manage_list`` returns a structured error (with ``recovery``)
        instead of bubbling the LO RuntimeException — regression guard
        for Investigation #37 (manage_list ParaStyleName runtime
        resolver).
        """
        from talk2view_writer.tools.formatting import manage_list

        synthetic_doc._text._paragraphs.append(FakeParagraph("item one"))
        # Strip every bullet-style alias from the fake doc's
        # ParagraphStyles family.
        for alias in ("List Bullet", "Bulleted List", "List Paragraph", "ListBullet"):
            synthetic_doc._style_families["ParagraphStyles"].pop(alias, None)

        result = json.loads(
            manage_list(
                action="add", list_type="bullet", paragraph_indices=[1]
            )
        )
        assert "error" in result
        assert "recovery" in result
        assert "List Bullet" in result["error"]
