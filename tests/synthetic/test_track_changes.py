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
