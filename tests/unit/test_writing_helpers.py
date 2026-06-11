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
    _resolve_image_size,
    _systempath_to_url,
)


@pytest.mark.unit
class TestResolveImageSize:
    """Omitted image dimensions are filled from the native size."""

    def test_both_omitted_uses_native_size(self) -> None:
        assert _resolve_image_size(4000, 3000, None, None) == (4000, 3000)

    def test_both_given_used_verbatim(self) -> None:
        assert _resolve_image_size(4000, 3000, 1000, 800) == (1000, 800)

    def test_width_given_height_derived_from_aspect(self) -> None:
        # native 4000x3000 (4:3); width 2000 -> height 1500.
        assert _resolve_image_size(4000, 3000, 2000, None) == (2000, 1500)

    def test_height_given_width_derived_from_aspect(self) -> None:
        # native 4000x3000; height 1500 -> width 2000.
        assert _resolve_image_size(4000, 3000, None, 1500) == (2000, 1500)

    def test_unknown_native_size_leaves_omitted_dims_none(self) -> None:
        # No usable native size -> omitted dims stay None (prior behaviour).
        assert _resolve_image_size(0, 0, None, None) == (None, None)
        assert _resolve_image_size(0, 0, 1000, None) == (1000, None)


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

    def _wire(self, *probe_strings: str) -> tuple[MagicMock, MagicMock]:
        """Return ``(text_obj, cursor)`` whose probe cursors return ``probe_strings``.

        ``_insert_paragraph_at_cursor`` calls ``createTextCursorByRange``
        first for the empty-paragraph probe, then (if non-empty) for the
        paragraph-end probe, then for the style / final cursors. The probe
        cursors are wired in order; any extra cursors needed are generic
        mocks.
        """
        text_obj = MagicMock()
        cursor = MagicMock()
        probes = []
        for s in probe_strings:
            p = MagicMock()
            p.getString.return_value = s
            probes.append(p)
        # Generous tail of generic cursors for style / final selections.
        tail = [MagicMock() for _ in range(4)]
        text_obj.createTextCursorByRange.side_effect = [*probes, *tail]
        return text_obj, cursor

    def _ops(self, text_obj: MagicMock) -> list[str]:
        return [
            c[0]
            for c in text_obj.method_calls
            if c[0] in ("insertControlCharacter", "insertString")
        ]

    def test_empty_target_paragraph_skips_paragraph_break(self) -> None:
        # Empty host paragraph (empty-probe == "").
        text_obj, cursor = self._wire("")
        _insert_paragraph_at_cursor(text_obj, cursor, "Hello", style=None, doc=None)
        text_obj.insertControlCharacter.assert_not_called()
        text_obj.insertString.assert_called_once_with(cursor, "Hello", False)

    def test_append_anchor_emits_break_then_string(self) -> None:
        """Cursor at the END of a non-empty paragraph (append) breaks first."""
        # empty-probe non-empty; end-probe empty (cursor at paragraph end).
        text_obj, cursor = self._wire("existing", "")
        _insert_paragraph_at_cursor(text_obj, cursor, "World", style=None, doc=None)
        text_obj.insertControlCharacter.assert_called_once()
        text_obj.insertString.assert_called_once_with(cursor, "World", False)
        # Break precedes the string so the text lands in the new paragraph.
        assert self._ops(text_obj) == ["insertControlCharacter", "insertString"]

    def test_before_anchor_writes_string_then_break(self) -> None:
        """Cursor at the START/MIDDLE of a non-empty paragraph writes text first.

        Real-LibreOffice-verified fix for the fusing corruption
        (investigation #62): breaking first would split the host paragraph
        at the cursor and fuse the new text into it.
        """
        # empty-probe non-empty; end-probe non-empty (text after the cursor).
        text_obj, cursor = self._wire("existing", "rest of paragraph")
        _insert_paragraph_at_cursor(text_obj, cursor, "World", style=None, doc=None)
        text_obj.insertString.assert_called_once_with(cursor, "World", False)
        text_obj.insertControlCharacter.assert_called_once()
        # The text is written BEFORE the break (the corruption fix).
        assert self._ops(text_obj) == ["insertString", "insertControlCharacter"]


class _FakeDoc:
    """Duck-typed Writer doc for suspend_record_changes (no UNO)."""

    def __init__(
        self, *, record: bool = True, readable: bool = True, writable: bool = True
    ) -> None:
        self._record = record
        self._readable = readable
        self._writable = writable
        self.sets: list[tuple[str, object]] = []

    def getPropertyValue(self, name: str) -> object:  # noqa: N802 (UNO name)
        if not self._readable:
            raise RuntimeError("cannot read property")
        return self._record

    def setPropertyValue(self, name: str, value: object) -> None:  # noqa: N802
        if not self._writable:
            raise RuntimeError("cannot write property")
        self.sets.append((name, value))


@pytest.mark.unit
class TestSuspendRecordChanges:
    """Class-based CM so a UNO exception in the body propagates cleanly.

    Regression for the broken tool-error path: a ``@contextmanager``
    generator's ``__exit__`` did ``exc.__traceback__ = tb`` on the pyuno
    exception struct, which crashed with "'traceback' object has no
    attribute 'getTypes'" and masked the real error.
    """

    def test_suspends_then_restores(self) -> None:
        from talk2view_writer.tools._base import suspend_record_changes

        doc = _FakeDoc(record=True)
        with suspend_record_changes(doc):
            assert ("RecordChanges", False) in doc.sets
        assert doc.sets[-1] == ("RecordChanges", True)

    def test_noop_when_already_off(self) -> None:
        from talk2view_writer.tools._base import suspend_record_changes

        doc = _FakeDoc(record=False)
        with suspend_record_changes(doc):
            pass
        assert doc.sets == []

    def test_noop_when_unreadable(self) -> None:
        from talk2view_writer.tools._base import suspend_record_changes

        doc = _FakeDoc(readable=False)
        with suspend_record_changes(doc):
            pass
        assert doc.sets == []

    def test_body_exception_propagates_unchanged_and_restores(self) -> None:
        from talk2view_writer.tools._base import suspend_record_changes

        doc = _FakeDoc(record=True)
        sentinel = ValueError("boom")
        with pytest.raises(ValueError) as info, suspend_record_changes(doc):
            raise sentinel
        # Same object — not wrapped/mangled (the bug re-raised a different
        # UNO conversion error instead of the original).
        assert info.value is sentinel
        # Restored despite the exception.
        assert doc.sets[-1] == ("RecordChanges", True)


@pytest.mark.unit
class TestStyleAssignmentResilience:
    """insert_content degrades gracefully when ParaStyleName is rejected.

    LO can reject a ParaStyleName write even with RecordChanges
    suspended. Style-first ordering (investigation #53) attempts the
    write on the still-empty paragraph; if the build still rejects it we
    log + keep the inherited style rather than failing the whole tool.
    """

    def test_para_style_runtimeexception_is_swallowed(self) -> None:
        from com.sun.star.uno import RuntimeException

        class _RaisingParaCursor:
            def gotoStartOfParagraph(self, expand: bool) -> None:  # noqa: N802
                pass

            def gotoEndOfParagraph(self, expand: bool) -> None:  # noqa: N802
                pass

            def __setattr__(self, name: str, value: object) -> None:
                if name == "ParaStyleName":
                    raise RuntimeException("redline constraint")
                object.__setattr__(self, name, value)

        text_obj = MagicMock()
        cursor = MagicMock()
        empty_probe = MagicMock()
        empty_probe.getString.return_value = "existing"  # non-empty host
        end_probe = MagicMock()
        end_probe.getString.return_value = ""  # cursor at paragraph end -> append
        style_cursor = _RaisingParaCursor()
        returned = MagicMock()
        # Append branch calls createTextCursorByRange four times: (1) the
        # empty-paragraph probe, (2) the paragraph-end probe, (3) the style
        # cursor written BEFORE the text insert — its ParaStyleName write
        # raises — and (4) the cursor over the just-written paragraph.
        text_obj.createTextCursorByRange.side_effect = [
            empty_probe,
            end_probe,
            style_cursor,
            returned,
        ]

        # Must NOT raise — graceful degradation, returns the new cursor.
        result = _insert_paragraph_at_cursor(
            text_obj, cursor, "Hello", style="Heading 1", doc=None
        )
        assert result is returned


@pytest.mark.unit
class TestInsertParagraphSkipsRedundantStyleWrite:
    """Skip-if-equal guard (investigation #53) skips a redundant style write.

    When the empty target paragraph already carries the resolved style,
    ``_insert_paragraph_at_cursor`` must NOT re-write ``ParaStyleName`` —
    re-asserting the same collection can itself trip the LO 26.2 rejection,
    and it is a no-op anyway; the text insert still happens. 'Normal' resolves
    to 'Text body', so a style cursor already reporting 'Text body' must be
    left untouched.
    """

    def test_redundant_style_write_is_skipped(self) -> None:
        wrote: list[str] = []

        class _StyleCursor:
            def gotoStartOfParagraph(self, expand: bool) -> None:  # noqa: N802
                pass

            def gotoEndOfParagraph(self, expand: bool) -> None:  # noqa: N802
                pass

            @property
            def ParaStyleName(self) -> str:  # noqa: N802
                # Already the resolved target for 'Normal' (word_to_libreoffice
                # _style('Normal') == 'Text body'), so the guard must skip.
                return "Text body"

            @ParaStyleName.setter
            def ParaStyleName(self, value: str) -> None:  # noqa: N802
                wrote.append(value)

        text_obj = MagicMock()
        cursor = MagicMock()
        probe = MagicMock()
        probe.getString.return_value = ""  # empty paragraph -> no leading break
        returned = MagicMock()
        text_obj.createTextCursorByRange.side_effect = [probe, _StyleCursor(), returned]

        result = _insert_paragraph_at_cursor(
            text_obj, cursor, "the body text", style="Normal", doc=None
        )

        # The redundant ParaStyleName write was skipped...
        assert wrote == [], "redundant ParaStyleName write must be skipped"
        # ...but the text insert and the empty-paragraph (no-break) path ran.
        text_obj.insertControlCharacter.assert_not_called()
        text_obj.insertString.assert_called_once_with(cursor, "the body text", False)
        assert result is returned
