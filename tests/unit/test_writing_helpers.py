"""Tests for pure-Python helpers in ``writing.py``.

The helpers exercised here do not touch UNO and can run under the
test stubs. The full tool bodies (``insert_content``, ``insert_table``,
etc.) require a live LibreOffice instance — integration tests cover
those in Phase F.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from talk2view_writer.tools.writing import (
    _ALIGNMENT_MAP,
    _cursor_at_empty_paragraph,
    _insert_paragraph_at_cursor,
    _is_uniform_table,
    _systempath_to_url,
)


@pytest.mark.unit
class TestAlignmentMap:
    @pytest.mark.parametrize(
        "name,expected_uno",
        [
            ("left", 0),
            ("right", 1),
            ("block", 2),
            ("center", 3),
            ("justified", 2),  # alias for "block"
        ],
    )
    def test_known_alignments(self, name: str, expected_uno: int) -> None:
        assert _ALIGNMENT_MAP[name] == expected_uno

    def test_justified_and_block_collide_to_same_value(self) -> None:
        """Word distinguishes them but UNO has only one — document the alias."""
        assert _ALIGNMENT_MAP["justified"] == _ALIGNMENT_MAP["block"]


@pytest.mark.unit
class TestIsUniformTable:
    def _table(self, rows: int, cols: int, cell_count: int) -> MagicMock:
        t = MagicMock()
        t.getRows.return_value.getCount.return_value = rows
        t.getColumns.return_value.getCount.return_value = cols
        t.getCellNames.return_value = ["A1"] * cell_count
        return t

    def test_uniform_2x3_table(self) -> None:
        assert _is_uniform_table(self._table(2, 3, 6)) is True

    def test_table_with_merged_cell_not_uniform(self) -> None:
        # 2x3 = 6, but a merged cell drops the count to 5.
        assert _is_uniform_table(self._table(2, 3, 5)) is False

    def test_single_cell_table_uniform(self) -> None:
        assert _is_uniform_table(self._table(1, 1, 1)) is True


@pytest.mark.unit
class TestSystempathToUrl:
    def test_uses_uno_converter_when_available(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import sys
        import types

        fake_uno = types.ModuleType("uno")
        fake_uno.systemPathToFileUrl = lambda p: f"file:///fake/{p.lstrip('/')}"  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "uno", fake_uno)

        result = _systempath_to_url("/tmp/example.png")
        assert result == "file:///fake/tmp/example.png"

    def test_falls_back_to_prefix_when_converter_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import sys
        import types

        fake_uno = types.ModuleType("uno")
        # No systemPathToFileUrl attribute.
        monkeypatch.setitem(sys.modules, "uno", fake_uno)

        result = _systempath_to_url("/tmp/example.png")
        assert result == "file:///tmp/example.png"


@pytest.mark.unit
class TestCursorAtEmptyParagraph:
    """Regression for Writer #3 — empty-paragraph probe.

    The probe is the gate that decides whether
    `_insert_paragraph_at_cursor` emits a leading PARAGRAPH_BREAK. It
    follows the canonical UNO XParagraphCursor selection trick: copy
    cursor, snap to paragraph start, extend to paragraph end, read
    string. Synthetic FakeText doesn't model the selection extension
    faithfully, so verify the call shape with mocks.
    """

    def test_returns_true_when_probe_string_empty(self) -> None:
        text_obj = MagicMock()
        cursor = MagicMock()
        probe = MagicMock()
        text_obj.createTextCursorByRange.return_value = probe
        probe.getString.return_value = ""

        assert _cursor_at_empty_paragraph(text_obj, cursor) is True
        text_obj.createTextCursorByRange.assert_called_once_with(cursor.getStart())
        probe.gotoStartOfParagraph.assert_called_once_with(False)
        probe.gotoEndOfParagraph.assert_called_once_with(True)

    def test_returns_false_when_probe_string_has_text(self) -> None:
        text_obj = MagicMock()
        cursor = MagicMock()
        probe = MagicMock()
        text_obj.createTextCursorByRange.return_value = probe
        probe.getString.return_value = "existing content"

        assert _cursor_at_empty_paragraph(text_obj, cursor) is False


@pytest.mark.unit
class TestInsertParagraphAtCursorSkipsLeadingBreakWhenEmpty:
    """Regression for Writer #3 — phantom-paragraph fix.

    Pre-fix: `_insert_paragraph_at_cursor` unconditionally emitted
    `insertControlCharacter(PARAGRAPH_BREAK)` before `insertString`,
    which split an empty target paragraph in two — leaving a phantom
    blank above the inserted text. Affected `location="start"` /
    `"end"` on fresh docs and `target_query` / `replace_selection`
    after the matched range was cleared.

    Post-fix: probe the cursor's host paragraph; skip the break when
    it's empty, write directly. Subsequent calls land in the now-non-
    empty paragraph and re-introduce the break for separation.
    """

    def _wire_text_obj(self, probe_string: str) -> tuple[MagicMock, MagicMock]:
        """Return ``(text_obj, cursor)`` whose first probe sees ``probe_string``.

        The two `createTextCursorByRange` calls inside
        `_insert_paragraph_at_cursor` are wired in order: first the
        empty-paragraph probe, then the paragraph-style cursor.
        """
        text_obj = MagicMock()
        cursor = MagicMock()
        probe = MagicMock()
        probe.getString.return_value = probe_string
        para_cursor = MagicMock()
        text_obj.createTextCursorByRange.side_effect = [probe, para_cursor]
        return text_obj, cursor

    def test_empty_target_paragraph_skips_paragraph_break(self) -> None:
        text_obj, cursor = self._wire_text_obj(probe_string="")

        _insert_paragraph_at_cursor(text_obj, cursor, "Hello", style=None, doc=None)

        text_obj.insertControlCharacter.assert_not_called()
        text_obj.insertString.assert_called_once_with(cursor, "Hello", False)

    def test_nonempty_target_paragraph_emits_paragraph_break(self) -> None:
        text_obj, cursor = self._wire_text_obj(probe_string="existing")

        _insert_paragraph_at_cursor(text_obj, cursor, "World", style=None, doc=None)

        text_obj.insertControlCharacter.assert_called_once()
        text_obj.insertString.assert_called_once_with(cursor, "World", False)

    def test_call_order_is_break_then_string(self) -> None:
        """Break must precede insertString when both are emitted.

        Otherwise the new text lands in the previous paragraph instead
        of in the freshly-created one.
        """
        text_obj, cursor = self._wire_text_obj(probe_string="existing")

        _insert_paragraph_at_cursor(text_obj, cursor, "World", style=None, doc=None)

        # First two method_calls on text_obj after the probe are
        # insertControlCharacter, then insertString.
        op_names = [
            c[0]
            for c in text_obj.method_calls
            if c[0] in ("insertControlCharacter", "insertString")
        ]
        assert op_names == ["insertControlCharacter", "insertString"]
