"""Synthetic-UNO tests for the Writing tools.

Covers ``undo_redo`` (deterministic), ``delete_content``, and the basic
``insert_content`` shape. ``insert_table`` and ``insert_image`` happy
paths are exercised in tests/integration/ against real soffice; the
synthetic model approximates them but the exact UNO interaction is
broad enough that we mostly check the tool's argument-validation
behaviour here.
"""

from __future__ import annotations

import json

import pytest

from tests.synthetic.synthetic_uno import FakeParagraph, FakeTextDocument

pytestmark = pytest.mark.synthetic


# ---------------------------------------------------------------------------
# undo_redo
# ---------------------------------------------------------------------------


class TestUndoRedo:
    def test_undo_calls_undomanager_undo(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.writing import undo_redo

        result = json.loads(undo_redo("undo"))
        assert result["success"] is True
        assert result["action"] == "undo"
        assert synthetic_doc.getUndoManager().undo_calls == 1

    def test_redo_calls_undomanager_redo(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.writing import undo_redo

        result = json.loads(undo_redo("redo"))
        assert result["success"] is True
        assert result["action"] == "redo"
        assert synthetic_doc.getUndoManager().redo_calls == 1

    def test_count_parameter_loops_n_times(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.writing import undo_redo

        json.loads(undo_redo("undo", count=5))
        assert synthetic_doc.getUndoManager().undo_calls == 5

    def test_invalid_action_returns_error(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.writing import undo_redo

        result = json.loads(undo_redo("nope"))
        assert "error" in result
        assert "recovery" in result
        assert synthetic_doc.getUndoManager().undo_calls == 0
        assert synthetic_doc.getUndoManager().redo_calls == 0

    def test_negative_count_treated_as_one(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.writing import undo_redo

        json.loads(undo_redo("undo", count=-99))
        assert synthetic_doc.getUndoManager().undo_calls == 1

    def test_response_includes_paragraph_count_before_after(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.writing import undo_redo

        result = json.loads(undo_redo("undo"))
        pc = result["paragraph_count"]
        assert "before" in pc
        assert "after" in pc

    def test_success_reports_steps_applied(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.writing import undo_redo

        result = json.loads(undo_redo("undo", count=3))
        assert result["success"] is True
        assert result["steps_requested"] == 3
        assert result["steps_applied"] == 3

    def test_zero_available_steps_reports_failure(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        """A redo with nothing to redo must report success:false, not true.

        Previously undo_redo returned success:true with a 'may be a
        formatting-only change' hint even when zero steps ran.
        """
        from talk2view_writer.tools.writing import undo_redo

        # Make redo impossible on the synthetic undo manager.
        undo_manager = synthetic_doc.getUndoManager()
        undo_manager.isRedoPossible = lambda: False  # type: ignore[method-assign]
        result = json.loads(undo_redo("redo", count=2))
        assert result["success"] is False
        assert result["steps_applied"] == 0
        assert "error" in result
        assert undo_manager.redo_calls == 0


# ---------------------------------------------------------------------------
# delete_content
# ---------------------------------------------------------------------------


class TestDeleteContent:
    def test_delete_by_paragraph_index_removes_target(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.writing import delete_content

        synthetic_doc._text._paragraphs.clear()
        synthetic_doc._text._paragraphs.extend(
            [
                FakeParagraph("keep one"),
                FakeParagraph("delete me"),
                FakeParagraph("keep two"),
            ]
        )
        result = json.loads(delete_content(paragraph_index=1))
        assert result.get("success") is True or "deleted" in json.dumps(result).lower()
        texts = [p.getString() for p in synthetic_doc._text._paragraphs]
        assert "delete me" not in texts

    def test_delete_by_range_removes_inclusive(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.writing import delete_content

        synthetic_doc._text._paragraphs.clear()
        synthetic_doc._text._paragraphs.extend(
            FakeParagraph(f"para {i}") for i in range(5)
        )
        json.loads(delete_content(start_index=1, end_index=3))
        # Inclusive range [1,3] should remove 3 paragraphs (indices 1, 2, 3).
        remaining = [p.getString() for p in synthetic_doc._text._paragraphs]
        assert "para 0" in remaining
        assert "para 4" in remaining
        # Three paragraphs were removed.
        assert len(remaining) == 2

    def test_delete_out_of_range_returns_structured_error(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.writing import delete_content

        synthetic_doc._text._paragraphs.clear()
        synthetic_doc._text._paragraphs.append(FakeParagraph("only one"))
        result = json.loads(delete_content(paragraph_index=99))
        assert "error" in result or "recovery" in result

    def test_no_args_returns_error(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.writing import delete_content

        result = json.loads(delete_content())
        assert "error" in result

    def test_real_removal_reports_index_shift(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        """Redlining off — the paragraph really goes; indices shift."""
        from talk2view_writer.tools.writing import delete_content

        synthetic_doc._text._paragraphs.clear()
        synthetic_doc._text._paragraphs.extend(
            [FakeParagraph("a"), FakeParagraph("b"), FakeParagraph("c")]
        )
        # Default synthetic _delete_paragraph actually removes the
        # paragraph (models RecordChanges=False), so the count drops.
        result = json.loads(delete_content(paragraph_index=1))
        assert result["tracked_change"] is False
        assert "indices have shifted" in result["warning"].lower()
        assert "hint" not in result

    def test_tracked_deletion_reports_pending_acceptance(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Redlining on — report a tracked change, not a false index shift.

        The struck-through paragraph still enumerates until accepted, so the
        count is unchanged and the tool must report a tracked change pending
        acceptance.
        """
        from talk2view_writer.tools import writing

        synthetic_doc._text._paragraphs.clear()
        synthetic_doc._text._paragraphs.extend(
            [FakeParagraph("a"), FakeParagraph("b"), FakeParagraph("c")]
        )
        # Redlining is ON — this is what makes an unchanged count a genuine
        # tracked deletion rather than a stray empty paragraph.
        synthetic_doc.setPropertyValue("RecordChanges", True)

        # Model a tracked deletion: LibreOffice records a redline but the
        # paragraph keeps enumerating until accepted, so the deletion call
        # leaves the paragraph list (and hence the count) unchanged.
        def _tracked_delete(text_obj: object, para: object) -> None:
            return None

        monkeypatch.setattr(writing, "_delete_paragraph", _tracked_delete)

        result = json.loads(writing.delete_content(paragraph_index=1))
        assert result["tracked_change"] is True
        # No false "indices have shifted" guidance.
        assert "warning" not in result
        hint = result["hint"].lower()
        assert "tracked change" in hint
        assert "not shifted" in hint
        # Count is genuinely unchanged — the paragraph still enumerates.
        assert len(synthetic_doc._text._paragraphs) == 3

    def test_emptied_node_with_redlining_off_is_not_a_tracked_change(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: object,
    ) -> None:
        """Count unchanged + redlining OFF == emptied last paragraph, not a redline.

        Previously delete_content inferred a tracked change purely from the
        unchanged count, so deleting the last paragraph (whose trailing
        break can't be swallowed) falsely reported a pending tracked change
        even with track changes off.
        """
        import talk2view_writer.preferences as prefs_mod
        from talk2view_writer.preferences import (
            PREF_AI_TRACK_CHANGES,
            Preferences,
            get_preferences,
        )
        from talk2view_writer.tools import writing

        # Disable the AI track-changes envelope so it does not force
        # RecordChanges=True for the call — model a user with redlining off.
        monkeypatch.setattr(
            prefs_mod, "_INSTANCE", Preferences(tmp_path / "preferences.json")  # type: ignore[operator]
        )
        get_preferences().set(PREF_AI_TRACK_CHANGES, False)

        synthetic_doc._text._paragraphs.clear()
        synthetic_doc._text._paragraphs.extend(
            [FakeParagraph("a"), FakeParagraph("b")]
        )
        synthetic_doc.setPropertyValue("RecordChanges", False)

        # Model the last-paragraph case: text emptied, node remains.
        def _empty_in_place(text_obj: object, para: object) -> None:
            return None

        monkeypatch.setattr(writing, "_delete_paragraph", _empty_in_place)

        result = json.loads(writing.delete_content(paragraph_index=1))
        assert result["tracked_change"] is False
        assert "hint" not in result
        assert "empty paragraph node remains" in result["warning"].lower()

    def test_range_tracked_deletion_reports_pending_acceptance(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Range mode under redlining reports the tracked-change semantics."""
        from talk2view_writer.tools import writing

        synthetic_doc._text._paragraphs.clear()
        synthetic_doc._text._paragraphs.extend(
            FakeParagraph(f"para {i}") for i in range(5)
        )

        synthetic_doc.setPropertyValue("RecordChanges", True)

        def _tracked_delete(text_obj: object, para: object) -> None:
            return None

        monkeypatch.setattr(writing, "_delete_paragraph", _tracked_delete)

        result = json.loads(writing.delete_content(start_index=1, end_index=3))
        assert result["tracked_change"] is True
        assert "warning" not in result
        assert "tracked change" in result["hint"].lower()


# ---------------------------------------------------------------------------
# insert_content — shape-only checks (real UNO covered in integration tests)
# ---------------------------------------------------------------------------


class TestInsertContentValidation:
    """insert_content must reject bad args BEFORE mutating the document.

    Each of these used to either raise a raw exception after the content
    was already inserted (alignment), silently place content at the wrong
    spot (unknown location, negative index), or skip validation of the
    blocks-mode fallback style.
    """

    def _para_count(self, doc: FakeTextDocument) -> int:
        return len(doc._text._paragraphs)

    def test_bad_alignment_returns_error_without_mutating(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.writing import insert_content

        before = self._para_count(synthetic_doc)
        result = json.loads(
            insert_content(text="Body", location="end", alignment="middle")
        )
        assert "error" in result
        assert "alignment" in result["error"].lower()
        # No paragraph was inserted — validation ran before mutation.
        assert self._para_count(synthetic_doc) == before

    def test_titlecased_alignment_is_accepted(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.writing import insert_content

        result = json.loads(
            insert_content(text="Centered", location="end", alignment="Center")
        )
        assert result.get("success") is True

    def test_unknown_location_returns_error(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.writing import insert_content

        before = self._para_count(synthetic_doc)
        result = json.loads(insert_content(text="Disclaimer", location="top"))
        assert "error" in result
        assert "location" in result["error"].lower()
        assert self._para_count(synthetic_doc) == before

    def test_negative_paragraph_index_returns_error(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.writing import insert_content

        result = json.loads(
            insert_content(
                text="Appendix", location="before_paragraph", paragraph_index=-1
            )
        )
        assert "error" in result
        assert "paragraph_index" in result["error"]

    def test_blocks_mode_bad_fallback_style_returns_error(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.writing import insert_content

        before = self._para_count(synthetic_doc)
        result = json.loads(
            insert_content(blocks=["Methods"], style="BogusStyle", location="end")
        )
        assert "error" in result
        assert "style" in result["error"].lower()
        assert self._para_count(synthetic_doc) == before


class TestInsertContent:
    def test_insert_with_text_does_not_raise(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.writing import insert_content

        result = json.loads(insert_content(text="Hello, world."))
        # Either success or a structured error — never an unhandled exception.
        assert isinstance(result, dict)

    def test_insert_empty_text_is_handled(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.writing import insert_content

        # The tool may either accept empty text (no-op) or return an error.
        result = json.loads(insert_content(text=""))
        assert isinstance(result, dict)

    def test_blocks_as_plain_strings_is_normalised(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        """Coerce string-shaped blocks to ``{text, style?}`` dicts.

        Engine LLMs sometimes emit ``blocks`` as an array of strings
        rather than ``{text, style?}`` dicts (observed with
        gemini-3-pro on 2026-05-22 — caused AttributeError at
        tools/writing.py:286). Coerce strings to dicts before
        validation runs.
        """
        from talk2view_writer.tools.writing import insert_content

        result = json.loads(
            insert_content(
                blocks=[
                    "The Majesty of Trees",
                    "Trees are vital to the health of our planet.",
                    "Beyond their ecological importance, ...",
                ],
                location="end",
            )
        )
        assert isinstance(result, dict)
        assert "error" not in result, result
        assert result.get("success") is True
        assert result.get("blocks_inserted") == 3

    def test_blocks_mixed_strings_and_dicts(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        """Heterogeneous block list: mix of strings and dicts.

        A heterogeneous list (some strings, some dicts) is the
        worst case for the coercion — make sure each block keeps its
        intended style when supplied.
        """
        from talk2view_writer.tools.writing import insert_content

        result = json.loads(
            insert_content(
                blocks=[
                    {"text": "Section 1", "style": "Heading1"},
                    "Body paragraph one.",
                    {"text": "Section 2", "style": "Heading1"},
                ],
                location="end",
            )
        )
        assert isinstance(result, dict)
        assert "error" not in result, result
        assert result.get("blocks_inserted") == 3

    def test_top_level_style_applies_to_all_string_blocks(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
    ) -> None:
        """Top-level ``style`` applies to all string blocks without per-block style.

        Regression guard: penguin_story scenario observed the engine
        call ``insert_content(blocks=['The Frosty March'], style='Title',
        location='start')`` expecting Title to apply to the single
        block. Previously the code only honoured per-block ``style``
        on dict blocks; string blocks lost the top-level style entirely
        and the preview reported "plain text". CI run 26388503344
        caught it. We assert on the preview field because the synthetic
        cursor doesn't propagate ParaStyleName back to the paragraph
        (production UNO does — the integration tests verify the LO
        side).
        """
        from talk2view_writer.tools.writing import insert_content

        result = json.loads(
            insert_content(
                blocks=["The Frosty March"],
                style="Title",
                location="start",
            )
        )
        # Style fell through from the top-level arg.
        assert result["previews"][0]["style"] == "Title", result

    def test_per_block_style_overrides_top_level_style(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
    ) -> None:
        """Per-block style still wins when both are set.

        Mixed list: per-block styles are explicit; top-level is the
        default for blocks without one. Assert via the preview field.
        """
        from talk2view_writer.tools.writing import insert_content

        result = json.loads(
            insert_content(
                blocks=[
                    {"text": "Heading", "style": "Heading1"},
                    "Body with inherited Title",
                ],
                style="Title",
                location="start",
            )
        )
        previews = result["previews"]
        # Per-block "Heading1" wins; second block inherits "Title".
        assert previews[0]["style"] == "Heading1"
        assert previews[1]["style"] == "Title"

    def test_libreoffice_display_style_names_are_accepted(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
    ) -> None:
        """LO display names the engine echoes back validate, not 400.

        Regression for Writer #2: get_document reports body paragraphs as
        'Text body' and headings as 'Heading 2'; the model then re-sends
        those raw LibreOffice names. They must normalise to the Word
        vocabulary ('Normal' / 'Heading2') instead of returning
        "unknown style" and burning a retry (observed in the 2026-06-09
        live log). The previews echo the canonical Word name so the
        write/read round-trip stays consistent.
        """
        from talk2view_writer.tools.writing import insert_content

        result = json.loads(
            insert_content(
                blocks=[
                    {"text": "A chapter heading", "style": "Heading 2"},
                    {"text": "Some body prose.", "style": "Text body"},
                ],
                location="end",
            )
        )
        assert "error" not in result, result
        previews = result["previews"]
        assert previews[0]["style"] == "Heading2", result
        assert previews[1]["style"] == "Normal", result

    def test_top_level_libreoffice_style_name_is_accepted(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
    ) -> None:
        """The single-``text`` path also folds an LO display name to Word."""
        from talk2view_writer.tools.writing import insert_content

        result = json.loads(
            insert_content(text="A chapter heading", style="Heading 2", location="end")
        )
        assert "error" not in result, result
        # The single-text path echoes the resolved style on the top-level key.
        assert result["style"] == "Heading2", result


class TestEditTableValidation:
    """edit_table must reject bad row/column/count BEFORE touching the table.

    A negative index used to reach getCellByPosition / removeByIndex and
    raise a raw UNO error, and count=0 was silently clamped to 1 — deleting
    a row the caller asked to leave alone.
    """

    def test_count_zero_is_rejected_not_clamped(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.writing import edit_table

        result = json.loads(
            edit_table(table_index=0, action="delete_rows", row=2, count=0)
        )
        assert "error" in result
        assert "count" in result["error"]

    def test_negative_row_is_rejected(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.writing import edit_table

        result = json.loads(
            edit_table(table_index=0, action="edit_cell", row=-1, column=0, value="x")
        )
        assert "error" in result
        assert "row" in result["error"]

    def test_negative_column_is_rejected(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.writing import edit_table

        result = json.loads(
            edit_table(
                table_index=0, action="delete_columns", column=-2, count=1
            )
        )
        assert "error" in result
        assert "column" in result["error"]
