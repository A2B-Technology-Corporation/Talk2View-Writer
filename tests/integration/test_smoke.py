"""Minimum-viable integration tests — proves the .oxt is installed correctly.

These run on every CI matrix entry (Linux, macOS Intel + arm64,
Windows). They're deliberately small + fast: failures here mean
the .oxt installation itself is broken (not the chat / tool layer).

Anything more elaborate belongs in test_tools_*.py (TODO) where the
full UNO round-trip exercises each tool against a real document.
"""

from __future__ import annotations

from typing import Any

import pytest


@pytest.mark.integration
def test_libreoffice_can_open_blank_writer_document(blank_document: Any) -> None:
    """LibreOffice itself works + we can spawn a Writer doc via UNO."""
    assert blank_document is not None
    assert blank_document.supportsService("com.sun.star.text.TextDocument")
    # Empty doc: one paragraph, no text.
    enum = blank_document.getText().createEnumeration()
    paragraphs = []
    while enum.hasMoreElements():
        el = enum.nextElement()
        if el.supportsService("com.sun.star.text.Paragraph"):
            paragraphs.append(el)
    assert len(paragraphs) == 1
    assert paragraphs[0].getString() == ""


@pytest.mark.integration
def test_extension_services_register(oxt_installed: Any) -> None:
    """Both Talk2View services instantiate cleanly through the service manager.

    The ``oxt_installed`` fixture already asserts this, so a passing
    test just confirms the fixture itself ran (catches the case where
    the fixture short-circuits via pytest.fail and skews the count).
    """
    assert oxt_installed is not None


@pytest.mark.integration
def test_all_tools_register_with_sdk(oxt_installed: Any) -> None:
    """The full tool registry imports cleanly under the runner's Python.

    This is the cross-platform canary for the wheel-loader: if
    pydantic_core or any pure-Python dep is missing for this OS/arch,
    importing ``all_tools()`` raises before we get a count.

    Asserts the exact tool name set, not just count. A drift detected
    here (tool added, removed, renamed) requires updating
    ``EXPECTED_TOOLS`` so the canary stays load-bearing.
    """
    # The bundled extension's pythonpath/ is added to sys.path by
    # LibreOffice's pythonloader at .oxt install time. Importing
    # under the test runner's Python proves the bundled deps are
    # compatible with this interpreter.
    from talk2view_writer._wheel_loader import ensure_vendored_pydantic_core

    ensure_vendored_pydantic_core()

    from talk2view_writer.tools import all_tools

    expected_tools = {
        # Reading (3)
        "get_document",
        "get_selection",
        "select_text",
        # Writing (6)
        "insert_content",
        "insert_table",
        "insert_image",
        "undo_redo",
        "delete_content",
        "edit_table",
        # Formatting (3)
        "format_text",
        "format_paragraph",
        "manage_list",
        # Search (1)
        "search_document",
        # Structure (4)
        "insert_break",
        "set_header_footer",
        "insert_page_numbers",
        "set_page_setup",
        # Commenting (3)
        "get_comments",
        "add_comment",
        "manage_comment",
        # Preferences (1) — ADR-0035
        "manage_preferences",
    }
    tools = all_tools()
    names = {t.__name__ for t in tools}
    assert names == expected_tools, (
        f"Tool registry drift. "
        f"Missing from registry: {expected_tools - names}. "
        f"Unexpected in registry: {names - expected_tools}."
    )
    assert len(tools) == len(expected_tools), (
        f"Duplicate registrations? {len(tools)} tools vs "
        f"{len(expected_tools)} unique names."
    )
    for tool in tools:
        assert tool.__doc__, f"Tool {tool.__name__} has no docstring (SDK schema)"
        # Doc must include an Args: section if the tool takes any
        # arguments — the SDK derives its schema from the docstring.
        assert "Args:" in tool.__doc__ or tool.__code__.co_argcount == 0, (
            f"Tool {tool.__name__} takes arguments but its docstring "
            "has no Args: section"
        )
