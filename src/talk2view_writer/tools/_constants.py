"""Constants + tiny helpers shared by tool modules.

Mirrors ``Talk2View-Word/src/taskpane/tools/constants.ts``. Keep this
in sync when Word's constants change (see ``docs/investigations.md`` #8
for the cross-host sync cadence).
"""

from __future__ import annotations

# Word's built-in paragraph styles. Tools that accept a ``style`` argument
# validate against this set so the agent gets the same "Unknown style"
# error on Writer as it does on Word.
VALID_STYLES: tuple[str, ...] = (
    "Normal",
    "Heading1",
    "Heading2",
    "Heading3",
    "Heading4",
    "Title",
    "Subtitle",
    "Quote",
    "IntenseQuote",
    "ListParagraph",
    "NoSpacing",
)

# Word's named highlight colours. Used by format_text and related tools.
HIGHLIGHT_COLORS: tuple[str, ...] = (
    "Yellow",
    "Green",
    "Turquoise",
    "Pink",
    "Blue",
    "Red",
    "DarkBlue",
    "Teal",
    "Violet",
    "DarkRed",
    "DarkYellow",
    "Gray25",
    "Gray50",
    "Black",
    "White",
    "NoColor",
)


def preview(text: str, max_chars: int = 80) -> str:
    """Truncate ``text`` to ``max_chars`` with an ellipsis on overflow."""
    return text[:max_chars] + "..." if len(text) > max_chars else text


# UNO uses 1/100 mm for nearly all length properties. Word uses points.
# 1 point = 1/72 inch = 2540/72 ≈ 35.278 (1/100 mm).
_POINTS_TO_HMM_FACTOR = 2540.0 / 72.0


def points_to_hmm(points: float) -> int:
    """Convert points to UNO's 1/100-mm length units, rounded to int."""
    return round(points * _POINTS_TO_HMM_FACTOR)


# Word's named highlight colours mapped to UNO `CharHighlight` int values
# (0xRRGGBB). ``NoColor`` maps to ``-1`` which UNO interprets as "remove
# highlighting" (Char* properties accept -1 for "auto / inherit").
HIGHLIGHT_COLOR_RGB: dict[str, int] = {
    "Yellow": 0xFFFF00,
    "Green": 0x00FF00,
    "Turquoise": 0x40E0D0,
    "Pink": 0xFFC0CB,
    "Blue": 0x0000FF,
    "Red": 0xFF0000,
    "DarkBlue": 0x00008B,
    "Teal": 0x008080,
    "Violet": 0xEE82EE,
    "DarkRed": 0x8B0000,
    "DarkYellow": 0x808000,
    "Gray25": 0xBFBFBF,
    "Gray50": 0x808080,
    "Black": 0x000000,
    "White": 0xFFFFFF,
    "NoColor": -1,
}


def hex_to_rgb_int(hex_str: str) -> int:
    """Parse a 6-char hex colour (no leading ``#``) into an ``0xRRGGBB`` int."""
    return int(hex_str, 16)


# com.sun.star.awt.FontUnderline values for the underline styles Word exposes.
UNDERLINE_STYLE_UNO: dict[str, int] = {
    "none": 0,
    "single": 1,
    "double": 2,
    "dotted": 3,
    "dashed": 5,
    "wave": 10,
}


def lower_enum(value: str | None) -> str | None:
    """Lowercase a string-enum argument so handler comparisons are case-insensitive.

    The tool schemas dropped their JSON-Schema ``enum`` constraints
    because the SDK validates them case-sensitively *before* the handler
    runs — a Title-cased value from the model (``"Landscape"``) gets
    rejected client-side and the model retries, producing a phantom
    "double tool call" (Writer #5 / joy-matrix `a38b8eb`). With the enum
    gone, normalisation moves here: lowercase the value so the
    downstream ``== "landscape"`` / ``not in (...)`` checks match
    regardless of the casing the model emitted. Non-strings (e.g.
    ``None`` for an optional arg) pass through untouched.
    """
    return value.lower() if isinstance(value, str) else value


# header_footer_type is the one enum whose canonical values are camelCase
# (``firstPage`` / ``evenPages``), so a blind ``.lower()`` would break the
# downstream equality checks. Map the lowercased input back to canonical.
_HEADER_FOOTER_TYPE_CANONICAL: dict[str, str] = {
    "primary": "primary",
    "firstpage": "firstPage",
    "evenpages": "evenPages",
}


def normalize_header_footer_type(value: str | None) -> str | None:
    """Case-insensitively map a header/footer-type arg to its canonical camelCase.

    Unknown values pass through unchanged so the handler still emits its
    own "unknown variant" error with candidates.
    """
    if not isinstance(value, str):
        return value
    return _HEADER_FOOTER_TYPE_CANONICAL.get(value.lower(), value)
