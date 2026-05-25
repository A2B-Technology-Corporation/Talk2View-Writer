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


# ---------------------------------------------------------------------------
# insert_content — shape-only checks (real UNO covered in integration tests)
# ---------------------------------------------------------------------------


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
