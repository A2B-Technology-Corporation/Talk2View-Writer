"""Tests for shared helpers in ``talk2view_writer.tools._constants``.

These are pure functions with no UNO dependency, so they exercise the
core conversion logic that every tool relies on.
"""

from __future__ import annotations

import pytest

from talk2view_writer.tools._constants import (
    HIGHLIGHT_COLOR_RGB,
    HIGHLIGHT_COLORS,
    UNDERLINE_STYLE_UNO,
    VALID_STYLES,
    hex_to_rgb_int,
    lower_enum,
    normalize_header_footer_type,
    points_to_hmm,
    preview,
)


@pytest.mark.unit
class TestPointsToHmm:
    """``points_to_hmm`` must convert Word's point unit to UNO 1/100 mm."""

    @pytest.mark.parametrize(
        "points,expected",
        [
            (0, 0),
            (72, 2540),     # 1 inch
            (36, 1270),     # 0.5 inch
            (144, 5080),    # 2 inches
            (1, 35),        # 1 point ≈ 35.28 → rounds to 35
        ],
    )
    def test_known_conversions(self, points: float, expected: int) -> None:
        assert points_to_hmm(points) == expected

    def test_returns_int(self) -> None:
        result = points_to_hmm(12.5)
        assert isinstance(result, int)

    def test_negative_points_supported(self) -> None:
        # Negative margins are uncommon but UNO accepts them (overhang).
        assert points_to_hmm(-72) == -2540


@pytest.mark.unit
class TestHexToRgbInt:
    @pytest.mark.parametrize(
        "hex_str,expected",
        [
            ("000000", 0x000000),
            ("FFFFFF", 0xFFFFFF),
            ("FF0000", 0xFF0000),
            ("00FF00", 0x00FF00),
            ("0000FF", 0x0000FF),
            ("ABCDEF", 0xABCDEF),
            ("abcdef", 0xABCDEF),  # case-insensitive
        ],
    )
    def test_known_colors(self, hex_str: str, expected: int) -> None:
        assert hex_to_rgb_int(hex_str) == expected

    def test_invalid_hex_raises(self) -> None:
        with pytest.raises(ValueError):
            hex_to_rgb_int("not-hex")


@pytest.mark.unit
class TestPreview:
    def test_short_text_unchanged(self) -> None:
        assert preview("short", 80) == "short"

    def test_truncates_long_text_with_ellipsis(self) -> None:
        text = "x" * 100
        result = preview(text, 80)
        assert len(result) == 83  # 80 + "..."
        assert result.endswith("...")

    def test_custom_max_chars(self) -> None:
        assert preview("abcdefghij", 5) == "abcde..."

    def test_empty_string(self) -> None:
        assert preview("", 80) == ""

    def test_exact_length_not_truncated(self) -> None:
        text = "x" * 80
        assert preview(text, 80) == text


@pytest.mark.unit
class TestConstantsCoverage:
    """The lookup tables must keep Word's parity intact."""

    def test_highlight_colors_all_have_rgb_mapping(self) -> None:
        for name in HIGHLIGHT_COLORS:
            assert name in HIGHLIGHT_COLOR_RGB, f"missing RGB for {name}"

    def test_no_color_maps_to_minus_one(self) -> None:
        # UNO's "auto / inherit" sentinel — must not be a valid colour.
        assert HIGHLIGHT_COLOR_RGB["NoColor"] == -1

    def test_underline_styles_match_uno_font_underline_enum(self) -> None:
        # 0 = NONE per com.sun.star.awt.FontUnderline.
        assert UNDERLINE_STYLE_UNO["none"] == 0
        # Sanity: every named style maps to a non-negative int.
        for name, value in UNDERLINE_STYLE_UNO.items():
            assert isinstance(value, int), f"{name} → {value!r}"
            assert value >= 0, f"{name} → {value}"

    def test_valid_styles_includes_word_defaults(self) -> None:
        expected = {"Normal", "Heading1", "Heading2", "Heading3", "Title"}
        assert expected.issubset(set(VALID_STYLES))


@pytest.mark.unit
class TestEnumNormalization:
    """Case-insensitive enum-arg normalisation (Writer #5).

    The tool schemas dropped JSON-Schema ``enum`` constraints because the
    SDK validates them case-sensitively before the handler runs, so a
    Title-cased value double-fires. These helpers move the normalisation
    into the handlers.
    """

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Landscape", "landscape"),
            ("PORTRAIT", "portrait"),
            ("bullet", "bullet"),
            ("Add", "add"),
            ("Resolve_With_Reply", "resolve_with_reply"),
        ],
    )
    def test_lower_enum_lowercases_strings(self, raw: str, expected: str) -> None:
        assert lower_enum(raw) == expected

    def test_lower_enum_passes_through_none(self) -> None:
        assert lower_enum(None) is None

    def test_lower_enum_passes_through_non_string(self) -> None:
        # Defensive: an int slipping through must not crash.
        assert lower_enum(5) == 5  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("primary", "primary"),
            ("Primary", "primary"),
            ("firstPage", "firstPage"),
            ("firstpage", "firstPage"),
            ("FIRSTPAGE", "firstPage"),
            ("evenPages", "evenPages"),
            ("evenpages", "evenPages"),
        ],
    )
    def test_header_footer_type_maps_to_canonical_camelcase(
        self, raw: str, expected: str
    ) -> None:
        # Unlike lower_enum, this preserves the camelCase the handler
        # compares against (== "firstPage" / "evenPages").
        assert normalize_header_footer_type(raw) == expected

    def test_header_footer_type_unknown_passes_through(self) -> None:
        # Unknown value survives so the handler emits its own error.
        assert normalize_header_footer_type("sidebar") == "sidebar"

    def test_header_footer_type_none_passes_through(self) -> None:
        assert normalize_header_footer_type(None) is None
