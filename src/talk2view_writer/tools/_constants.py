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
