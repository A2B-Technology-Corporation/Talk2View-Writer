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
    "Normal": "Default Paragraph Style",
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
