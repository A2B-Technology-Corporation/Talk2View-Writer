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
