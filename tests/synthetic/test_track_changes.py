"""Synthetic-UNO tests for the track-changes envelope around AI tool calls.

These tests exercise the ``ui_thread_tool`` wrapper's redlining
behaviour against the synthetic Writer document. They verify:

- mutating tools (insert_content, format_text, search_document) flip
  ``RecordChanges`` on for the duration of the call;
- the prior ``RecordChanges`` value is restored after the call returns
  AND after the call raises;
- read-only tools (get_document, get_selection) do not touch
  ``RecordChanges`` at all;
- the ``ai_track_changes_enabled`` preference (when False) disables the
  wrap entirely.

The wrap lives in ``talk2view_writer/tools/_base.py``. ADR-0035
documents the design.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from talk2view_writer.preferences import (
    PREF_AI_TRACK_CHANGES,
    Preferences,
    _reset_singleton_for_tests,
)
from tests.synthetic.synthetic_uno import FakeParagraph, FakeTextDocument

pytestmark = pytest.mark.synthetic


@pytest.fixture
def prefs_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Per-test Preferences singleton under tmp_path.

    The wrap reads its toggle via ``get_preferences()`` so we need to
    monkey-patch the module-level singleton. Reset at teardown so the
    next test starts clean.
    """
    import talk2view_writer.preferences as prefs_mod

    path = tmp_path / "preferences.json"
    monkeypatch.setattr(prefs_mod, "_INSTANCE", Preferences(path))
    yield path
    _reset_singleton_for_tests()


class TestTrackChangesAroundInsertContent:
    def test_record_changes_is_enabled_during_insert(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
        prefs_path: Path,
    ) -> None:
        """Capture RecordChanges' value while insert_content runs."""
        from talk2view_writer.tools.writing import insert_content

        observed: list[Any] = []

        # Wrap setPropertyValue to record every redline transition.
        original_set = synthetic_doc.setPropertyValue

        def _spy(name: str, value: Any) -> None:
            if name == "RecordChanges":
                observed.append(("set", value))
            original_set(name, value)

        synthetic_doc.setPropertyValue = _spy  # type: ignore[method-assign]
        insert_content(text="hello world")
        assert ("set", True) in observed, observed
        # The final transition is the restore — RecordChanges back to
        # its prior False value.
        assert observed[-1] == ("set", False), observed

    def test_prior_record_changes_value_restored(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
        prefs_path: Path,
    ) -> None:
        """User had RecordChanges=True globally — restore to True after."""
        from talk2view_writer.tools.writing import insert_content

        synthetic_doc.setPropertyValue("RecordChanges", True)
        insert_content(text="hello")
        assert synthetic_doc.getPropertyValue("RecordChanges") is True

    def test_prior_false_value_restored(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
        prefs_path: Path,
    ) -> None:
        from talk2view_writer.tools.writing import insert_content

        synthetic_doc.setPropertyValue("RecordChanges", False)
        insert_content(text="hello")
        assert synthetic_doc.getPropertyValue("RecordChanges") is False


class TestTrackChangesAroundFormatText:
    def test_format_text_runs_inside_envelope(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
        prefs_path: Path,
    ) -> None:
        """format_text is mutating — wrap applies."""
        from talk2view_writer.tools.formatting import format_text

        synthetic_doc._text._paragraphs.clear()
        synthetic_doc._text._paragraphs.append(FakeParagraph("Hello world"))
        synthetic_doc.setPropertyValue("RecordChanges", False)

        observed_during_call: dict[str, Any] = {}
        original_set = synthetic_doc.setPropertyValue

        def _spy(name: str, value: Any) -> None:
            if name == "RecordChanges":
                observed_during_call.setdefault("transitions", []).append(value)
            original_set(name, value)

        synthetic_doc.setPropertyValue = _spy  # type: ignore[method-assign]
        format_text(query="Hello", bold=True)
        transitions = observed_during_call["transitions"]
        # Enable, then restore.
        assert transitions == [True, False], transitions


class TestReadOnlyToolsSkipWrap:
    def test_get_document_does_not_touch_record_changes(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
        prefs_path: Path,
    ) -> None:
        """Read-only tools never touch RecordChanges."""
        from talk2view_writer.tools.reading import get_document

        observed: list[Any] = []
        original_set = synthetic_doc.setPropertyValue

        def _spy(name: str, value: Any) -> None:
            if name == "RecordChanges":
                observed.append(value)
            original_set(name, value)

        synthetic_doc.setPropertyValue = _spy  # type: ignore[method-assign]
        get_document()
        assert observed == [], (
            f"get_document is read-only — it must not touch "
            f"RecordChanges. Got: {observed}"
        )

    def test_get_selection_does_not_touch_record_changes(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
        prefs_path: Path,
    ) -> None:
        from talk2view_writer.tools.reading import get_selection

        observed: list[Any] = []
        original_set = synthetic_doc.setPropertyValue

        def _spy(name: str, value: Any) -> None:
            if name == "RecordChanges":
                observed.append(value)
            original_set(name, value)

        synthetic_doc.setPropertyValue = _spy  # type: ignore[method-assign]
        get_selection()
        assert observed == []

    def test_undo_redo_does_not_touch_record_changes(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
        prefs_path: Path,
    ) -> None:
        """Undo/redo intentionally bypasses the envelope.

        The user might invoke undo to revert an AI change; if the
        undo itself ran inside the envelope, the reverted state
        would show up as a NEW tracked change rather than restoring
        the pre-AI state.
        """
        from talk2view_writer.tools.writing import undo_redo

        observed: list[Any] = []
        original_set = synthetic_doc.setPropertyValue

        def _spy(name: str, value: Any) -> None:
            if name == "RecordChanges":
                observed.append(value)
            original_set(name, value)

        synthetic_doc.setPropertyValue = _spy  # type: ignore[method-assign]
        undo_redo("undo")
        assert observed == []


class TestPreferenceTogglesWrap:
    def test_disabling_pref_skips_wrap_entirely(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
        prefs_path: Path,
    ) -> None:
        """When ai_track_changes_enabled=False, no envelope at all."""
        from talk2view_writer.preferences import get_preferences
        from talk2view_writer.tools.writing import insert_content

        get_preferences().set(PREF_AI_TRACK_CHANGES, False)
        synthetic_doc.setPropertyValue("RecordChanges", False)

        observed: list[Any] = []
        original_set = synthetic_doc.setPropertyValue

        def _spy(name: str, value: Any) -> None:
            if name == "RecordChanges":
                observed.append(value)
            original_set(name, value)

        synthetic_doc.setPropertyValue = _spy  # type: ignore[method-assign]
        insert_content(text="hello")
        assert observed == [], (
            f"Wrap must skip when preference is off. Got transitions: {observed}"
        )

    def test_explicit_enable_runs_wrap(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
        prefs_path: Path,
    ) -> None:
        from talk2view_writer.preferences import get_preferences
        from talk2view_writer.tools.writing import insert_content

        get_preferences().set(PREF_AI_TRACK_CHANGES, True)
        synthetic_doc.setPropertyValue("RecordChanges", False)

        observed: list[Any] = []
        original_set = synthetic_doc.setPropertyValue

        def _spy(name: str, value: Any) -> None:
            if name == "RecordChanges":
                observed.append(value)
            original_set(name, value)

        synthetic_doc.setPropertyValue = _spy  # type: ignore[method-assign]
        insert_content(text="hello")
        assert observed == [True, False]


class TestSuspendRecordChanges:
    """Direct tests of the ``suspend_record_changes`` helper.

    The helper is what protects ParaStyleName assignments from
    LibreOffice's RuntimeException when redlining is on. We verify
    its bookkeeping: it disables only if currently on, restores on
    exit, no-ops if already off, no-ops if the property is unreadable.
    """

    def test_suspends_when_record_changes_is_on(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
        prefs_path: Path,
    ) -> None:
        from talk2view_writer.tools._base import suspend_record_changes

        synthetic_doc.setPropertyValue("RecordChanges", True)
        observed: list[Any] = []
        observed.append(synthetic_doc.getPropertyValue("RecordChanges"))
        with suspend_record_changes(synthetic_doc):
            observed.append(synthetic_doc.getPropertyValue("RecordChanges"))
        observed.append(synthetic_doc.getPropertyValue("RecordChanges"))
        assert observed == [True, False, True]

    def test_no_op_when_already_off(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
        prefs_path: Path,
    ) -> None:
        from talk2view_writer.tools._base import suspend_record_changes

        synthetic_doc.setPropertyValue("RecordChanges", False)
        with suspend_record_changes(synthetic_doc):
            assert synthetic_doc.getPropertyValue("RecordChanges") is False
        assert synthetic_doc.getPropertyValue("RecordChanges") is False


class TestInsertContentAppliesStyleUnderTrackChanges:
    """Regression for the empty-Title-paragraph crash from 2026-05-23.

    insert_content with blocks=[{text, style}] must succeed end-to-end
    when RecordChanges=True. The original failure was the engine retrying
    8 times and leaving the document full of empty Title paragraphs.

    On real LibreOffice, ParaStyleName assignment raises a UNO
    RuntimeException when applied to a paragraph whose content was just
    inserted as a redline. The synthetic FakeTextDocument doesn't model
    that quirk, so this test mainly proves the call path runs cleanly
    and that the style does land on the paragraph; the LO-quirk fix is
    proven by manual soffice verification.
    """

    def test_insert_blocks_with_styles_completes(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
        prefs_path: Path,
    ) -> None:
        import json

        from talk2view_writer.tools.writing import insert_content

        synthetic_doc.setPropertyValue("RecordChanges", False)
        result = json.loads(
            insert_content(
                blocks=[
                    {"text": "The March of the Emperor", "style": "Title"},
                    {"text": "Body paragraph here.", "style": "Normal"},
                    {"text": "The Long Wait", "style": "Heading1"},
                ],
                location="end",
            )
        )
        assert "error" not in result, result
        assert result.get("success") is True
        assert result.get("blocks_inserted") == 3
        # The track-changes envelope must have restored RecordChanges
        # to its prior value (False) by tool exit.
        assert synthetic_doc.getPropertyValue("RecordChanges") is False


class TestEnvelopeOnFailure:
    def test_record_changes_restored_when_tool_raises(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
        prefs_path: Path,
    ) -> None:
        """Even if the tool body raises, the prior value is restored.

        Without the finally-block, a raise mid-edit would leave the
        document in track-changes=True forever — leaking AI state
        into the user's own subsequent edits.
        """
        import talk2view_writer.tools.writing as writing_mod

        synthetic_doc.setPropertyValue("RecordChanges", False)

        # Force the body of insert_content to raise.
        original = writing_mod.insert_content

        def _exploding_insert(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("tool body bomb")

        # Re-wrap by hand so the wrapper still runs.
        from talk2view_writer.tools._base import ui_thread_tool

        wrapped = ui_thread_tool(_exploding_insert)
        wrapped.__name__ = "insert_content"  # type: ignore[attr-defined]
        with pytest.raises(RuntimeError, match="bomb"):
            wrapped()
        assert synthetic_doc.getPropertyValue("RecordChanges") is False
        # The pre-bomb transition to True did happen, but the restore
        # in finally returned us to the prior value. (Test the
        # round-trip; we don't care if observed contains [True, False].)
        del original


class TestInsertParagraphStyleFirstOrdering:
    """Regression for investigation #53 — the redline ParaStyleName fix.

    ``_insert_paragraph_at_cursor`` must assign the paragraph style to the
    still-empty paragraph BEFORE inserting its text, so the style write
    never lands on a paragraph that already carries an insert-redline —
    one of the states LibreOffice 26.2 rejects a ``ParaStyleName`` write in.
    'Normal' now resolves to the NAMED 'Text body' (``word_to_libreoffice_
    style``) rather than the pool default, which is the collection that gets
    rejected; this test pins both the operation order AND that resolved name.

    The synthetic document models insert loosely (insertString does not
    write styles back to real paragraphs), so this asserts the OPERATION
    ORDER directly with recording fakes: a styled block's ParaStyleName
    must be set (to 'Text body') before its text is inserted. A revert to
    insert-then-style flips the order and fails this test; a revert of the
    'Normal' → 'Text body' mapping changes the recorded style name and also
    fails it.
    """

    def test_style_is_applied_before_text_insert(self) -> None:
        from talk2view_writer.tools.writing import _insert_paragraph_at_cursor

        events: list[tuple[str, Any]] = []

        class _RecCursor:
            def __init__(self, probe_text: str = "") -> None:
                self._style: str | None = None
                self._probe_text = probe_text

            def getString(self) -> str:  # noqa: N802
                return self._probe_text

            def getStart(self) -> _RecCursor:  # noqa: N802
                return self

            def goLeft(self, count: int, expand: bool) -> bool:  # noqa: N802
                return True

            def gotoStartOfParagraph(self, expand: bool) -> bool:  # noqa: N802
                return True

            def gotoEndOfParagraph(self, expand: bool) -> bool:  # noqa: N802
                return True

            @property
            def ParaStyleName(self) -> str | None:  # noqa: N802
                return self._style

            @ParaStyleName.setter
            def ParaStyleName(self, value: str) -> None:  # noqa: N802
                self._style = value
                events.append(("style", value))

        class _RecText:
            # Append case: non-empty host paragraph, cursor AT its end, so
            # the style is applied to the freshly-broken (still-empty)
            # paragraph BEFORE the text insert (investigation #53). Probe
            # order: empty-paragraph probe (non-empty "anchor"), then the
            # paragraph-end probe (empty -> at end -> append branch).
            def __init__(self) -> None:
                self._probes = ["anchor", ""]
                self._i = 0

            def insertControlCharacter(  # noqa: N802
                self, cur: Any, ch: Any, absorb: bool
            ) -> None:
                events.append(("break", None))

            def insertString(  # noqa: N802
                self, cur: Any, text: str, absorb: bool
            ) -> None:
                events.append(("insert", text))

            def createTextCursorByRange(self, rng: Any) -> _RecCursor:  # noqa: N802
                probe = self._probes[self._i] if self._i < len(self._probes) else ""
                self._i += 1
                return _RecCursor(probe_text=probe)

        class _RecDoc:
            """Minimal doc so suspend_record_changes runs with redlining on."""

            def __init__(self) -> None:
                self._rec = True

            def getPropertyValue(self, name: str) -> Any:  # noqa: N802
                return self._rec if name == "RecordChanges" else None

            def setPropertyValue(self, name: str, value: Any) -> None:  # noqa: N802
                if name == "RecordChanges":
                    self._rec = value

        _insert_paragraph_at_cursor(
            _RecText(), _RecCursor(), "the body text", style="Normal", doc=_RecDoc()
        )

        assert ("style", "Text body") in events, events
        assert ("insert", "the body text") in events, events
        assert events.index(("style", "Text body")) < events.index(
            ("insert", "the body text")
        ), f"style must be applied before the text insert: {events}"


class TestUndoContextGrouping:
    """Every mutating tool call runs inside exactly one balanced undo context.

    Without grouping, insert_content with N blocks produces N+ raw undo
    steps, so a single Ctrl+Z reverts only a fragment of the AI edit
    (ADR-0035 / writing-bug undo-grouping).
    """

    def test_mutating_tool_opens_one_balanced_context(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
        prefs_path: Path,
    ) -> None:
        from talk2view_writer.tools.writing import insert_content

        um = synthetic_doc.getUndoManager()
        insert_content(blocks=["one", "two", "three"], location="end")
        # Exactly one context, named for the tool, and balanced (depth 0).
        assert um.undo_contexts == ["Talk2View: insert_content"]
        assert um._context_depth == 0

    def test_context_is_left_even_when_tool_raises(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
        prefs_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from talk2view_writer.tools import writing

        um = synthetic_doc.getUndoManager()

        def _boom(*_a: Any, **_k: Any) -> Any:
            raise RuntimeError("mid-edit failure")

        # Force the inner insertion to raise after the context is opened.
        monkeypatch.setattr(writing, "_insert_paragraph_at_cursor", _boom)
        with pytest.raises(RuntimeError, match="mid-edit failure"):
            writing.insert_content(text="x", location="end")
        # The context was opened then left (depth back to 0) despite the raise.
        assert um.undo_contexts == ["Talk2View: insert_content"]
        assert um._context_depth == 0

    def test_read_only_tool_opens_no_context(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
        prefs_path: Path,
    ) -> None:
        from talk2view_writer.tools.reading import get_document

        um = synthetic_doc.getUndoManager()
        get_document()
        assert um.undo_contexts == []
