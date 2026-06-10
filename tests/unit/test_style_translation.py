"""Tests for Word ↔ LibreOffice style name translation."""

from __future__ import annotations

import pytest

from talk2view_writer.uno_helpers.styles import (
    canonical_style_name,
    libreoffice_to_word_style,
    word_to_libreoffice_style,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("word_name", "lo_name"),
    [
        # 'Normal' maps to the NAMED 'Text body', not the pool default — the
        # pool default's ParaStyleName write is rejected on LO 26.2 under
        # track-changes / redline states (investigation #53).
        ("Normal", "Text body"),
        ("Heading1", "Heading 1"),
        ("Heading2", "Heading 2"),
        ("Heading3", "Heading 3"),
        ("Title", "Title"),
        ("Subtitle", "Subtitle"),
        ("Quote", "Quotations"),
        ("ListParagraph", "List Bullet"),
    ],
)
def test_word_to_libreoffice_known_styles(word_name: str, lo_name: str) -> None:
    assert word_to_libreoffice_style(word_name) == lo_name


@pytest.mark.unit
@pytest.mark.parametrize(
    ("lo_name", "word_name"),
    [
        ("Default Paragraph Style", "Normal"),
        # LibreOffice reports body paragraphs as 'Text body' (heading
        # Next-Style cascade + our own 'Normal' write); fold it back to 'Normal'
        # so get_document never surfaces a raw LO name the model can't re-send.
        ("Text body", "Normal"),
        ("Heading 1", "Heading1"),
        ("Heading 2", "Heading2"),
        ("Title", "Title"),
        ("Subtitle", "Subtitle"),
        ("Quotations", "Quote"),
        ("List Bullet", "ListParagraph"),
    ],
)
def test_libreoffice_to_word_known_styles(lo_name: str, word_name: str) -> None:
    assert libreoffice_to_word_style(lo_name) == word_name


@pytest.mark.unit
def test_normal_round_trips_through_text_body() -> None:
    """'Normal' → 'Text body' → 'Normal' is stable (no drift)."""
    lo = word_to_libreoffice_style("Normal")
    assert lo == "Text body"
    assert libreoffice_to_word_style(lo) == "Normal"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("incoming", "expected"),
    [
        # Word names pass through unchanged.
        ("Normal", "Normal"),
        ("Heading2", "Heading2"),
        ("Title", "Title"),
        # LibreOffice display names the engine echoes back from get_document
        # fold to the Word name (Writer #2 — avoids the wasted "unknown style"
        # round-trip seen in the 2026-06-09 live log).
        ("Text body", "Normal"),
        ("Default Paragraph Style", "Normal"),
        ("Standard", "Normal"),  # internal name of the pool default
        ("Heading 2", "Heading2"),
        ("Quotations", "Quote"),
        # Case-insensitive on the LO side.
        ("text body", "Normal"),
        ("TEXT BODY", "Normal"),
        ("heading 1", "Heading1"),
        # Whitespace tolerated.
        ("  Text body  ", "Normal"),
        # Genuine custom styles pass through untouched.
        ("MyCustom", "MyCustom"),
    ],
)
def test_canonical_style_name(incoming: str, expected: str) -> None:
    assert canonical_style_name(incoming) == expected


@pytest.mark.unit
def test_unknown_style_passes_through() -> None:
    """Custom user styles keep their original name in both directions."""
    assert word_to_libreoffice_style("MyCustom") == "MyCustom"
    assert libreoffice_to_word_style("MyCustom") == "MyCustom"


@pytest.mark.unit
def test_lossy_roundtrip_documented() -> None:
    """IntenseQuote and NoSpacing have no LibreOffice equivalent."""
    # Both map to LibreOffice styles that map back to different Word
    # names — round-tripping IntenseQuote loses information. This test
    # exists to make the lossiness visible / regression-trip if we
    # add proper LibreOffice equivalents later.
    assert word_to_libreoffice_style("IntenseQuote") == "Quotations"
    assert libreoffice_to_word_style("Quotations") == "Quote"  # not IntenseQuote

    assert word_to_libreoffice_style("NoSpacing") == "Default Paragraph Style"
    assert libreoffice_to_word_style("Default Paragraph Style") == "Normal"  # not NoSpacing
