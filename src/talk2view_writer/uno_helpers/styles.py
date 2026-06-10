"""Paragraph style name translation between Word and LibreOffice.

The cloud agent has been trained on Word's style naming (``Normal``,
``Heading1``, ``Heading2``, ``Title``, ``Quote``, …). LibreOffice's
built-in styles use different names (``Default Paragraph Style``,
``Heading 1`` with a space, ``Title``, ``Quotations``, …). The
agent will send Word-style names; our tools translate to the
LibreOffice equivalent before calling UNO, and back to Word names
when returning data to the agent.

See ``docs/investigations.md`` #13 for the limits of this mapping
(several Word styles have no exact LibreOffice equivalent).
"""

from __future__ import annotations

# Word → LibreOffice
_WORD_TO_LO = {
    # 'Normal' maps to the NAMED body style 'Text body', NOT the pool-default
    # collection 'Default Paragraph Style'. LibreOffice 26.2 rejects a
    # ``ParaStyleName`` write of the pool default onto a paragraph in certain
    # document states with a message-less RuntimeException — observed both on
    # inserts under the track-changes envelope and, in the 2026-06-09 live log,
    # with ``RecordChanges`` already off (so the trigger is broader than an
    # active insert-redline; named collections like 'Heading 2' are accepted in
    # the same call). 'Text body' is also the style LibreOffice's heading
    # Next-Style cascade already lands body paragraphs on, so routing 'Normal'
    # through it makes the style write succeed AND keeps the write/read-back
    # round-trip symmetric (see investigations #53).
    "Normal": "Text body",
    "Heading1": "Heading 1",
    "Heading2": "Heading 2",
    "Heading3": "Heading 3",
    "Heading4": "Heading 4",
    "Heading5": "Heading 5",
    "Heading6": "Heading 6",
    "Title": "Title",
    "Subtitle": "Subtitle",
    "Quote": "Quotations",
    "IntenseQuote": "Quotations",  # LibreOffice has no IntenseQuote
    "ListParagraph": "List Bullet",
    "NoSpacing": "Default Paragraph Style",  # no exact equivalent
}

# LibreOffice → Word (computed from the above, with manual disambiguation
# of styles that map to "Default Paragraph Style" from multiple sources)
_LO_TO_WORD = {
    "Default Paragraph Style": "Normal",
    # LibreOffice reports body paragraphs as 'Text body' (the heading
    # Next-Style cascade, and our own 'Normal' → 'Text body' write). Fold it
    # back to 'Normal' so get_document never surfaces a raw LO name the agent's
    # vocabulary doesn't contain (which it would then re-send and get rejected).
    "Text body": "Normal",
    "Heading 1": "Heading1",
    "Heading 2": "Heading2",
    "Heading 3": "Heading3",
    "Heading 4": "Heading4",
    "Heading 5": "Heading5",
    "Heading 6": "Heading6",
    "Title": "Title",
    "Subtitle": "Subtitle",
    "Quotations": "Quote",
    "List Bullet": "ListParagraph",
}


def word_to_libreoffice_style(name: str) -> str:
    """Translate a Word-flavoured style name into LibreOffice's equivalent.

    Returns the input unchanged if the name is not recognised — UNO will
    raise its own error if the style doesn't exist in the document.
    """
    return _WORD_TO_LO.get(name, name)


def libreoffice_to_word_style(name: str) -> str:
    """Translate a LibreOffice style name into Word's equivalent.

    Returns the input unchanged for custom styles (which keep their
    original names).
    """
    return _LO_TO_WORD.get(name, name)


# LibreOffice display name (lower-cased) → Word name. Lets the tool validators
# accept a style name the engine sometimes echoes back from ``get_document``
# (e.g. 'Text body', 'Heading 2') instead of the Word vocabulary, rather than
# rejecting it with "unknown style" and forcing the model to retry — observed
# in the 2026-06-09 live log, where a ``style: "Text body"`` block cost a wasted
# round-trip before the model fell back to 'Normal'. Built from ``_LO_TO_WORD``
# plus the pool default's internal name 'Standard'.
_LO_DISPLAY_TO_WORD: dict[str, str] = {
    lo_name.lower(): word_name for lo_name, word_name in _LO_TO_WORD.items()
}
_LO_DISPLAY_TO_WORD["standard"] = "Normal"  # internal name of the pool default


def canonical_style_name(name: str) -> str:
    """Normalise an incoming paragraph-style name to its canonical Word name.

    Word names ('Normal', 'Heading2', 'Title', …) pass through unchanged.
    Known LibreOffice display names ('Text body', 'Heading 2', 'Default
    Paragraph Style', 'Standard') — matched case-insensitively — fold back to
    the Word name the agent's vocabulary uses, so validators accept them.
    Unknown names (genuine custom styles) pass through unchanged; UNO raises
    its own error later if the style is truly absent from the document.
    """
    return _LO_DISPLAY_TO_WORD.get(name.strip().lower(), name)
