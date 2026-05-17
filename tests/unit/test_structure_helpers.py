"""Tests for pure-Python helpers in ``structure.py``.

The tool bodies require a live LibreOffice instance and exercise the
page-style enumeration via UNO. The helpers tested here are reachable
with stub objects.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from talk2view_writer.tools.structure import (
    _PAGE_SETUP_KEYS,
    _PAPER_SIZES_HMM,
    _get_page_style,
    _list_page_styles_in_use,
)


def _paragraph(style_name: str = "Default Page Style") -> MagicMock:
    p = MagicMock()
    p.supportsService.return_value = True
    p.PageDescName = style_name
    return p


def _non_paragraph() -> MagicMock:
    el = MagicMock()
    el.supportsService.return_value = False
    return el


def _doc_with_paragraphs(paragraphs: list[MagicMock]) -> MagicMock:
    """Build a stub doc whose text enumeration yields ``paragraphs``."""
    doc = MagicMock()
    enum = MagicMock()
    state = {"i": 0}

    def has_more() -> bool:
        return state["i"] < len(paragraphs)

    def next_element() -> MagicMock:
        el = paragraphs[state["i"]]
        state["i"] += 1
        return el

    enum.hasMoreElements.side_effect = has_more
    enum.nextElement.side_effect = next_element
    doc.getText.return_value.createEnumeration.return_value = enum
    return doc


@pytest.mark.unit
class TestListPageStylesInUse:
    def test_returns_default_when_no_paragraphs(self) -> None:
        doc = _doc_with_paragraphs([])
        assert _list_page_styles_in_use(doc) == ["Default Page Style"]

    def test_returns_default_when_paragraph_has_no_pagedescname(self) -> None:
        # PageDescName="" → treated as the implicit default.
        doc = _doc_with_paragraphs([_paragraph("")])
        assert _list_page_styles_in_use(doc) == ["Default Page Style"]

    def test_collects_distinct_page_styles_in_document_order(self) -> None:
        doc = _doc_with_paragraphs(
            [
                _paragraph("First Page"),
                _paragraph("Default Page Style"),
                _paragraph("Landscape"),
                _paragraph("Default Page Style"),  # duplicate
            ]
        )
        result = _list_page_styles_in_use(doc)
        assert result == ["First Page", "Default Page Style", "Landscape"]

    def test_skips_non_paragraph_elements(self) -> None:
        doc = _doc_with_paragraphs(
            [_paragraph("Foo"), _non_paragraph(), _paragraph("Bar")]
        )
        assert _list_page_styles_in_use(doc) == ["Foo", "Bar"]


@pytest.mark.unit
class TestGetPageStyle:
    def _doc_with_families(self, page_styles_map: dict[str, MagicMock]) -> MagicMock:
        doc = _doc_with_paragraphs([_paragraph(name) for name in page_styles_map])
        families = MagicMock()
        page_styles = MagicMock()
        page_styles.hasByName.side_effect = lambda n: n in page_styles_map
        page_styles.getByName.side_effect = lambda n: page_styles_map[n]
        page_styles.getElementNames.return_value = list(page_styles_map.keys())
        families.getByName.return_value = page_styles
        doc.getStyleFamilies.return_value = families
        return doc

    def test_returns_first_style_for_index_zero(self) -> None:
        first = MagicMock(name="first-style")
        doc = self._doc_with_families({"First Page": first})
        assert _get_page_style(doc, 0) is first

    def test_returns_none_for_negative_index(self) -> None:
        doc = self._doc_with_families({"Default Page Style": MagicMock()})
        assert _get_page_style(doc, -1) is None

    def test_returns_none_for_out_of_range_index(self) -> None:
        doc = self._doc_with_families({"Default Page Style": MagicMock()})
        assert _get_page_style(doc, 5) is None


@pytest.mark.unit
class TestPaperSizesHmm:
    def test_a4_dimensions_are_iso216(self) -> None:
        w, h = _PAPER_SIZES_HMM["a4"]
        # A4 is 210 x 297 mm = 21000 x 29700 in 1/100 mm.
        assert (w, h) == (21000, 29700)

    def test_letter_dimensions(self) -> None:
        w, h = _PAPER_SIZES_HMM["letter"]
        # 8.5 x 11 in = 21.59 x 27.94 cm.
        assert (w, h) == (21590, 27940)

    def test_all_paper_sizes_have_portrait_aspect(self) -> None:
        # Width < Height for portrait — the orientation flip happens in
        # set_page_setup, not in the table.
        for size, (w, h) in _PAPER_SIZES_HMM.items():
            assert w < h, f"{size} not in portrait orientation: {w}x{h}"


@pytest.mark.unit
class TestPageSetupKeys:
    def test_page_setup_keys_include_all_margins(self) -> None:
        for margin in ("top_margin", "bottom_margin", "left_margin", "right_margin"):
            assert margin in _PAGE_SETUP_KEYS

    def test_page_setup_keys_include_orientation_and_paper(self) -> None:
        assert "orientation" in _PAGE_SETUP_KEYS
        assert "paper_size" in _PAGE_SETUP_KEYS
