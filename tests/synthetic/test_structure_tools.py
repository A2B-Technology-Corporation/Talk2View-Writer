"""Synthetic-UNO tests for the Structure tools.

``insert_break``, ``set_header_footer``, ``insert_page_numbers``,
``set_page_setup``. These tools manipulate page-style and frame
properties, so the tests focus on input validation + the shape of
the JSON response. Header/footer/page-number / page-setup mutations
on a real document are covered by the integration suite against
soffice.
"""

from __future__ import annotations

import json

import pytest

from tests.synthetic.synthetic_uno import FakeTextDocument

pytestmark = pytest.mark.synthetic


class TestInsertBreak:
    def test_invalid_type_returns_error(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.structure import insert_break

        result = json.loads(insert_break(type="diagonal", location="end"))
        assert "error" in result

    def test_invalid_location_returns_error(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.structure import insert_break

        result = json.loads(insert_break(type="page", location="middle"))
        assert "error" in result

    def test_page_break_at_end_succeeds(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.structure import insert_break

        result = json.loads(insert_break(type="page", location="end"))
        assert result.get("success") is True
        assert result.get("break_type") == "page"

    def test_section_break_emits_hint(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.structure import insert_break

        result = json.loads(insert_break(type="section_next_page", location="end"))
        assert result.get("success") is True
        # Section breaks include guidance for following up with
        # set_header_footer / set_page_setup.
        assert result.get("hint") is not None


class TestSetHeaderFooter:
    def test_invalid_type_returns_error(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.structure import set_header_footer

        result = json.loads(
            set_header_footer(type="sidebar", text="x")
        )
        assert "error" in result

    def test_header_text_returns_dict(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
    ) -> None:
        from talk2view_writer.tools.structure import set_header_footer

        # The synthetic doc's PageStyle doesn't expose the full
        # HeaderText / FooterText UNO surface, so the tool may fall
        # through to a graceful error. We just confirm the tool
        # returns a structured response (no unhandled exception).
        result = json.loads(set_header_footer(type="header", text="Top of page"))
        assert isinstance(result, dict)


class TestInsertPageNumbers:
    def test_invalid_alignment_returns_error(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.structure import insert_page_numbers

        result = json.loads(
            insert_page_numbers(location="footer", alignment="diagonal")
        )
        assert "error" in result

    def test_invalid_location_returns_error(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.structure import insert_page_numbers

        result = json.loads(
            insert_page_numbers(location="sidebar", alignment="center")
        )
        assert "error" in result

    def test_footer_centered_returns_dict(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
    ) -> None:
        from talk2view_writer.tools.structure import insert_page_numbers

        result = json.loads(
            insert_page_numbers(location="footer", alignment="center")
        )
        assert isinstance(result, dict)


class TestSetPageSetup:
    def test_invalid_orientation_returns_error(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
    ) -> None:
        from talk2view_writer.tools.structure import set_page_setup

        result = json.loads(set_page_setup(orientation="rotated"))
        assert "error" in result

    def test_invalid_paper_size_returns_error(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
    ) -> None:
        from talk2view_writer.tools.structure import set_page_setup

        result = json.loads(set_page_setup(paper_size="MegaWide"))
        assert "error" in result

    def test_negative_margin_returns_error(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
    ) -> None:
        from talk2view_writer.tools.structure import set_page_setup

        result = json.loads(set_page_setup(left_margin=-5))
        assert "error" in result

    def test_landscape_orientation_returns_dict(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
    ) -> None:
        from talk2view_writer.tools.structure import set_page_setup

        result = json.loads(set_page_setup(orientation="landscape"))
        assert isinstance(result, dict)

    def test_orientation_is_case_insensitive(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
    ) -> None:
        """Title-cased orientation must not error (Writer #5).

        Pre-fix, the SDK rejected "Landscape" against the lowercase
        enum and the model retried — a phantom double-call. The enum
        is gone; the handler now lowercases, so "Landscape" reaches
        the same success path as "landscape".
        """
        from talk2view_writer.tools.structure import set_page_setup

        result = json.loads(set_page_setup(orientation="Landscape"))
        assert "error" not in result, result
        assert result["applied"]["orientation"] == "landscape"

    def test_header_type_camelcase_args_dont_raise(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
    ) -> None:
        """Title-cased `type` + lowercased camelCase `header_footer_type`
        normalise cleanly (Writer #5).

        The synthetic doc doesn't expose the full HeaderText UNO surface
        (same limitation as test_header_text_returns_dict), so the tool
        may fall through to a graceful error — we just confirm the
        case-insensitive args don't raise an unhandled exception and the
        tool returns a structured dict. The exact camelCase mapping is
        unit-tested in test_constants.py::TestEnumNormalization.
        """
        from talk2view_writer.tools.structure import set_header_footer

        result = json.loads(
            set_header_footer(
                type="Header", text="Confidential", header_footer_type="firstpage"
            )
        )
        assert isinstance(result, dict)
