"""Tests for the tool registry aggregation.

These tests cover *only* the registration plumbing — they confirm the
expected tools are present and callable. The tool bodies themselves are
covered by integration tests (Phase F) since they require a running
LibreOffice instance and a real Writer document.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def fake_talk2view(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the ``talk2view`` module + its ``tool`` decorator.

    The real decorator does schema introspection; we just need a
    pass-through so the import succeeds and the tool functions remain
    callable in tests.
    """
    fake_module = types.ModuleType("talk2view")
    fake_module.tool = lambda fn: fn  # type: ignore[attr-defined]
    fake_types_module = types.ModuleType("talk2view.types")
    fake_types_module.User = MagicMock()  # type: ignore[attr-defined]
    fake_types_module.ChatEvent = MagicMock()  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "talk2view", fake_module)
    monkeypatch.setitem(sys.modules, "talk2view.types", fake_types_module)

    # Force fresh imports of the tool modules under the fake decorator.
    for mod in list(sys.modules):
        if mod.startswith("talk2view_writer.tools"):
            monkeypatch.delitem(sys.modules, mod, raising=False)


@pytest.mark.unit
def test_all_tools_includes_expected_phase_c_set(fake_talk2view: None) -> None:
    from talk2view_writer.tools import all_tools

    names = {fn.__name__ for fn in all_tools()}
    # Phase C ships exactly two proof tools.
    assert names == {"get_document", "insert_content"}


@pytest.mark.unit
def test_all_tools_returns_fresh_list_each_call(fake_talk2view: None) -> None:
    from talk2view_writer.tools import all_tools

    first = all_tools()
    second = all_tools()
    assert first is not second  # different list instances
    assert first == second  # same contents


@pytest.mark.unit
def test_tool_modules_export_TOOLS_list(fake_talk2view: None) -> None:  # noqa: N802
    """Each tool sub-module must expose a ``TOOLS`` list for aggregation."""
    from talk2view_writer.tools import reading, writing

    assert isinstance(reading.TOOLS, list) and reading.TOOLS
    assert isinstance(writing.TOOLS, list) and writing.TOOLS


@pytest.mark.unit
def test_tool_functions_have_docstrings(fake_talk2view: None) -> None:
    """Every tool must have a docstring — the SDK derives the schema description from it."""
    from talk2view_writer.tools import all_tools

    for fn in all_tools():
        assert fn.__doc__, f"{fn.__name__} has no docstring"
        assert "Args:" in fn.__doc__ or fn.__code__.co_argcount == 0, (
            f"{fn.__name__} takes arguments but its docstring has no Args: section"
        )
