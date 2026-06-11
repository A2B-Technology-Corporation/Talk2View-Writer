"""Live-UNO end-to-end tests for the ``writing`` tool module.

Every test drives the REAL ``@tool`` functions from
``talk2view_writer.tools.writing`` through the ``tool_doc`` harness (a live
headless Writer document with the extension singleton wired to it) and then
asserts the resulting **real-LibreOffice document state** — paragraph text +
count, ``ParaStyleName``, table cell strings, and table row/column counts —
rather than the brittle JSON response shape. This is the coverage layer that
exercises actual LO C++ behaviour, the gap that hid the commenting bugs
(Investigations #38, #66).

Covered tools / variations:

- ``insert_content`` — location end / start / before_paragraph /
  after_paragraph / target_query (replace), ``style=``, ``blocks=`` (with and
  without per-block + fallback styles), and paragraph-format options.
- ``insert_table`` — with and without initial ``data``, at start / end.
- ``edit_table`` — edit_cell, add_rows, delete_rows, add_columns, delete_columns.
- ``delete_content`` — by ``query`` and by paragraph range (``start_index`` /
  ``end_index``).
- ``undo_redo`` — undo reverses an insert, redo restores it.

Reality of the harness (important for the assertion style here):

* The ``writing`` tools mutate the document returned by the production
  resolver ``get_writer_document(ext.ctx)`` — i.e. the desktop's *current*
  component — NOT the ``tool_doc`` parameter directly. We therefore read state
  back through that same resolver (:func:`_live_doc`) so the reader sees
  exactly the document the tool wrote to.
* That live document is shared across the soffice process and may already
  carry residue (paragraphs / tables) from earlier work. So assertions use
  UNIQUE per-test tokens and assert membership / relative ordering / counts of
  *our own* tokens, never full-document equality.
* Deleting a paragraph whose trailing break abuts a table (or the final
  paragraph) is fragile in LibreOffice — :func:`talk2view_writer.tools.writing._delete_paragraph`
  documents this. The delete tests below therefore only ever remove *interior*
  uniquely-tokened paragraphs they inserted themselves.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Document-state readers — read from the SAME document the tools wrote to.
# ---------------------------------------------------------------------------


def _live_doc() -> Any:
    """Return the document the ``writing`` tools actually mutate.

    The tools call ``get_writer_document(ext.ctx)`` (the desktop's current
    component), so read state back through the same resolver rather than the
    ``tool_doc`` handle, which can diverge when other documents are open in
    the shared soffice process.
    """
    from talk2view_writer.extension import get_extension_or_raise
    from talk2view_writer.tools._base import get_writer_document

    ext = get_extension_or_raise()
    return get_writer_document(ext.ctx)


def _para_objs(doc: Any) -> list[Any]:
    out: list[Any] = []
    en = doc.getText().createEnumeration()
    while en.hasMoreElements():
        el = en.nextElement()
        if el.supportsService("com.sun.star.text.Paragraph"):
            out.append(el)
    return out


def _paras(doc: Any) -> list[str]:
    """Return the top-level paragraph strings in document order."""
    return [p.getString() for p in _para_objs(doc)]


def _index_of(doc: Any, needle: str) -> int:
    """Return the index of the first paragraph whose text equals ``needle``."""
    for i, text in enumerate(_paras(doc)):
        if text == needle:
            return i
    raise AssertionError(f"paragraph {needle!r} not found; paras={_paras(doc)}")


def _style_of(doc: Any, needle: str) -> str:
    """Return the ParaStyleName of the first paragraph containing ``needle``."""
    for p in _para_objs(doc):
        if needle in p.getString():
            return str(p.ParaStyleName)
    raise AssertionError(f"no paragraph contains {needle!r}; paras={_paras(doc)}")


def _tok() -> str:
    """A unique, search-safe token to tag this test's own content."""
    return "TOK" + uuid.uuid4().hex[:8].upper()


def _last_table(doc: Any) -> Any:
    """Return the most recently inserted table (insert_* appends to the end)."""
    tables = doc.getTextTables()
    assert tables.getCount() >= 1, "no tables in document"
    return tables.getByIndex(tables.getCount() - 1)


# ---------------------------------------------------------------------------
# insert_content
# ---------------------------------------------------------------------------


class TestInsertContentLocations:
    def test_append_at_end_adds_paragraph_with_text(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.writing import insert_content

        tok = _tok()
        res = json.loads(insert_content(text=f"{tok} alpha line", location="end"))
        assert "error" not in res, res
        doc = _live_doc()
        assert any(tok in p for p in _paras(doc)), _paras(doc)

    def test_two_appends_are_ordered(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.writing import insert_content

        a, b = _tok(), _tok()
        insert_content(text=f"{a} first", location="end")
        insert_content(text=f"{b} second", location="end")
        doc = _live_doc()
        paras = _paras(doc)
        first = next(i for i, p in enumerate(paras) if a in p)
        second = next(i for i, p in enumerate(paras) if b in p)
        assert first < second, paras

    def test_insert_at_start_prepends(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.writing import insert_content

        body, top = _tok(), _tok()
        insert_content(text=f"{body} body", location="end")
        res = json.loads(insert_content(text=f"{top} top", location="start"))
        assert "error" not in res, res
        doc = _live_doc()
        paras = _paras(doc)
        # The start insert lands at index 0 of the document.
        assert top in paras[0], paras
        top_i = next(i for i, p in enumerate(paras) if top in p)
        body_i = next(i for i, p in enumerate(paras) if body in p)
        assert top_i < body_i, paras

    def test_before_paragraph_inserts_ahead_of_target(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.writing import insert_content

        one, two, ins = _tok(), _tok(), _tok()
        insert_content(text=f"{one} one", location="end")
        insert_content(text=f"{two} two", location="end")
        doc = _live_doc()
        target = _index_of(doc, f"{two} two")
        res = json.loads(
            insert_content(
                text=f"{ins} inserted",
                location="before_paragraph",
                paragraph_index=target,
            )
        )
        assert "error" not in res, res
        paras = _paras(_live_doc())
        i_one = next(i for i, p in enumerate(paras) if one in p)
        i_ins = next(i for i, p in enumerate(paras) if ins in p)
        i_two = next(i for i, p in enumerate(paras) if two in p)
        assert i_one < i_ins < i_two, paras

    def test_after_paragraph_inserts_following_target(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.writing import insert_content

        one, two, mid = _tok(), _tok(), _tok()
        insert_content(text=f"{one} one", location="end")
        insert_content(text=f"{two} two", location="end")
        doc = _live_doc()
        target = _index_of(doc, f"{one} one")
        res = json.loads(
            insert_content(
                text=f"{mid} middle",
                location="after_paragraph",
                paragraph_index=target,
            )
        )
        assert "error" not in res, res
        paras = _paras(_live_doc())
        i_one = next(i for i, p in enumerate(paras) if one in p)
        i_mid = next(i for i, p in enumerate(paras) if mid in p)
        i_two = next(i for i, p in enumerate(paras) if two in p)
        assert i_one < i_mid < i_two, paras


class TestInsertContentTargetQuery:
    def test_target_query_replaces_matched_text(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.writing import insert_content

        keep, gone, new = _tok(), _tok(), _tok()
        insert_content(text=f"{keep} keep then {gone} remove", location="end")
        res = json.loads(insert_content(text=f"{new} replacement", target_query=gone))
        assert "error" not in res, res
        joined = " ".join(_paras(_live_doc()))
        assert new in joined, joined
        assert gone not in joined, joined
        assert keep in joined, joined

    def test_target_query_not_found_is_error_and_no_new_text(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.writing import insert_content

        present, new = _tok(), _tok()
        insert_content(text=f"{present} original", location="end")
        res = json.loads(insert_content(text=f"{new} x", target_query=f"ABSENT{_tok()}"))
        assert "error" in res, res
        joined = " ".join(_paras(_live_doc()))
        assert new not in joined, joined
        assert present in joined, joined


class TestInsertContentStyle:
    def test_style_heading1_maps_to_lo_heading_1(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.writing import insert_content

        tok = _tok()
        res = json.loads(insert_content(text=f"{tok} heading", location="end", style="Heading1"))
        assert "error" not in res, res
        assert _style_of(_live_doc(), tok) == "Heading 1"

    def test_style_title_maps_to_lo_title(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.writing import insert_content

        tok = _tok()
        insert_content(text=f"{tok} the title", location="end", style="Title")
        assert _style_of(_live_doc(), tok) == "Title"

    def test_style_quote_maps_to_lo_quotations(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.writing import insert_content

        tok = _tok()
        insert_content(text=f"{tok} pithy quote", location="end", style="Quote")
        assert _style_of(_live_doc(), tok) == "Quotations"

    def test_unknown_style_rejected_without_inserting(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.writing import insert_content

        tok = _tok()
        res = json.loads(insert_content(text=f"{tok} nope", location="end", style="Bogus"))
        assert "error" in res, res
        assert not any(tok in p for p in _paras(_live_doc())), _paras(_live_doc())


class TestInsertContentBlocks:
    def test_blocks_insert_multiple_ordered_paragraphs(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.writing import insert_content

        a, b, c = _tok(), _tok(), _tok()
        res = json.loads(
            insert_content(
                blocks=[{"text": f"{a} one"}, {"text": f"{b} two"}, {"text": f"{c} three"}],
                location="end",
            )
        )
        assert "error" not in res, res
        paras = _paras(_live_doc())
        ia = next(i for i, p in enumerate(paras) if a in p)
        ib = next(i for i, p in enumerate(paras) if b in p)
        ic = next(i for i, p in enumerate(paras) if c in p)
        assert ia < ib < ic, paras

    def test_blocks_apply_per_block_styles(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.writing import insert_content

        head, body = _tok(), _tok()
        insert_content(
            blocks=[
                {"text": f"{head} heading", "style": "Heading1"},
                {"text": f"{body} body", "style": "Normal"},
            ],
            location="end",
        )
        doc = _live_doc()
        assert _style_of(doc, head) == "Heading 1"
        # 'Normal' is routed to the named 'Text body' style (investigation #53).
        assert _style_of(doc, body) == "Text body"

    def test_blocks_fallback_to_top_level_style(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.writing import insert_content

        tok = _tok()
        # A block without its own style falls back to the top-level style arg.
        insert_content(blocks=[{"text": f"{tok} fallback"}], style="Heading2", location="end")
        assert _style_of(_live_doc(), tok) == "Heading 2"

    def test_blocks_as_plain_strings_are_coerced(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.writing import insert_content

        a, b = _tok(), _tok()
        # Engine LLMs sometimes emit blocks as bare strings (gemini-3-pro).
        res = json.loads(insert_content(blocks=[f"{a} plain", f"{b} plain"], location="end"))
        assert "error" not in res, res
        paras = _paras(_live_doc())
        assert any(a in p for p in paras) and any(b in p for p in paras), paras


class TestInsertContentParagraphFormat:
    def test_alignment_center_applied_to_inserted_paragraph(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.writing import insert_content

        tok = _tok()
        insert_content(text=f"{tok} centered", location="end", alignment="center")
        # com.sun.star.style.ParagraphAdjust.CENTER == 3.
        adjust = None
        for p in _para_objs(_live_doc()):
            if tok in p.getString():
                adjust = p.ParaAdjust
        assert adjust == 3, adjust

    def test_mutually_exclusive_text_and_blocks_rejected(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.writing import insert_content

        a, b = _tok(), _tok()
        res = json.loads(insert_content(text=f"{a} x", blocks=[{"text": f"{b} y"}], location="end"))
        assert "error" in res, res
        paras = _paras(_live_doc())
        assert not any(a in p for p in paras), paras
        assert not any(b in p for p in paras), paras


# ---------------------------------------------------------------------------
# insert_table
# ---------------------------------------------------------------------------


class TestInsertTable:
    def test_empty_table_has_correct_dimensions(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.writing import insert_table

        res = json.loads(insert_table(rows=3, columns=4, location="end"))
        assert "error" not in res, res
        tbl = _last_table(_live_doc())
        assert tbl.getRows().getCount() == 3
        assert tbl.getColumns().getCount() == 4

    def test_table_populated_from_data(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.writing import insert_table

        a, b = _tok(), _tok()
        res = json.loads(
            insert_table(
                rows=2,
                columns=3,
                location="end",
                data=[[f"{a}H1", "H2", "H3"], [f"{b}a", "b", "c"]],
            )
        )
        assert "error" not in res, res
        tbl = _last_table(_live_doc())
        assert tbl.getRows().getCount() == 2
        assert tbl.getColumns().getCount() == 3
        # getCellByName("A1") == row 0, col 0.
        assert tbl.getCellByName("A1").getString() == f"{a}H1"
        assert tbl.getCellByName("C1").getString() == "H3"
        assert tbl.getCellByName("A2").getString() == f"{b}a"
        assert tbl.getCellByName("C2").getString() == "c"

    def test_ragged_data_leaves_missing_cells_empty(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.writing import insert_table

        z = _tok()
        json.loads(insert_table(rows=2, columns=2, location="end", data=[["x", "y"], [f"{z}z"]]))
        tbl = _last_table(_live_doc())
        assert tbl.getCellByName("A2").getString() == f"{z}z"
        assert tbl.getCellByName("B2").getString() == ""

    def test_table_at_start(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.writing import insert_table

        top = _tok()
        res = json.loads(insert_table(rows=1, columns=1, location="start", data=[[f"{top}top"]]))
        assert "error" not in res, res
        # Table at start: the first table in the document carries our token.
        tables = _live_doc().getTextTables()
        assert tables.getByIndex(0).getCellByName("A1").getString() == f"{top}top"

    def test_data_exceeding_columns_rejected(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.writing import insert_table

        before = _live_doc().getTextTables().getCount()
        res = json.loads(insert_table(rows=1, columns=2, location="end", data=[["a", "b", "c"]]))
        assert "error" in res, res
        assert _live_doc().getTextTables().getCount() == before


# ---------------------------------------------------------------------------
# edit_table
# ---------------------------------------------------------------------------


def _seed_table(rows: int = 2, columns: int = 2) -> tuple[int, str]:
    """Insert a uniquely-tagged table; return its index + token prefix.

    Cells are filled with ``{tok}r{r}c{c}`` so a later read-back can assert on
    exactly this table's cells even if the shared document holds other tables.
    """
    from talk2view_writer.tools.writing import insert_table

    tok = _tok()
    data = [[f"{tok}r{r}c{c}" for c in range(columns)] for r in range(rows)]
    insert_table(rows=rows, columns=columns, location="end", data=data)
    return _live_doc().getTextTables().getCount() - 1, tok


class TestEditTableCell:
    def test_edit_cell_sets_string(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.writing import edit_table

        idx, tok = _seed_table(rows=2, columns=2)
        res = json.loads(
            edit_table(table_index=idx, action="edit_cell", row=1, column=1, value=f"{tok}EDITED")
        )
        assert "error" not in res, res
        tbl = _live_doc().getTextTables().getByIndex(idx)
        # row 1, col 1 == cell B2.
        assert tbl.getCellByName("B2").getString() == f"{tok}EDITED"
        # An untouched cell is unchanged.
        assert tbl.getCellByName("A1").getString() == f"{tok}r0c0"

    def test_edit_cell_out_of_range_rejected(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.writing import edit_table

        idx, _ = _seed_table(rows=2, columns=2)
        res = json.loads(
            edit_table(table_index=idx, action="edit_cell", row=5, column=0, value="x")
        )
        assert "error" in res, res


class TestEditTableRows:
    def test_add_rows_at_end_grows_table(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.writing import edit_table

        idx, tok = _seed_table(rows=2, columns=2)
        res = json.loads(
            edit_table(table_index=idx, action="add_rows", count=2, insert_location="end")
        )
        assert "error" not in res, res
        tbl = _live_doc().getTextTables().getByIndex(idx)
        assert tbl.getRows().getCount() == 4
        # Original first-row data preserved.
        assert tbl.getCellByName("A1").getString() == f"{tok}r0c0"

    def test_add_rows_at_start_pushes_existing_down(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.writing import edit_table

        idx, tok = _seed_table(rows=2, columns=2)
        json.loads(edit_table(table_index=idx, action="add_rows", count=1, insert_location="start"))
        tbl = _live_doc().getTextTables().getByIndex(idx)
        assert tbl.getRows().getCount() == 3
        # New blank row at the top; the original first row is now in row 2.
        assert tbl.getCellByName("A1").getString() == ""
        assert tbl.getCellByName("A2").getString() == f"{tok}r0c0"

    def test_delete_rows_shrinks_table(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.writing import edit_table

        idx, tok = _seed_table(rows=3, columns=2)
        res = json.loads(edit_table(table_index=idx, action="delete_rows", row=0, count=1))
        assert "error" not in res, res
        tbl = _live_doc().getTextTables().getByIndex(idx)
        assert tbl.getRows().getCount() == 2
        # Row 0 (r0*) gone; what was row 1 is now row 0.
        assert tbl.getCellByName("A1").getString() == f"{tok}r1c0"

    def test_delete_rows_beyond_bounds_rejected(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.writing import edit_table

        idx, _ = _seed_table(rows=2, columns=2)
        res = json.loads(edit_table(table_index=idx, action="delete_rows", row=1, count=5))
        assert "error" in res, res
        assert _live_doc().getTextTables().getByIndex(idx).getRows().getCount() == 2


class TestEditTableColumns:
    def test_add_columns_at_end_grows_table(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.writing import edit_table

        idx, tok = _seed_table(rows=2, columns=2)
        res = json.loads(
            edit_table(table_index=idx, action="add_columns", count=1, insert_location="end")
        )
        assert "error" not in res, res
        tbl = _live_doc().getTextTables().getByIndex(idx)
        assert tbl.getColumns().getCount() == 3
        assert tbl.getCellByName("A1").getString() == f"{tok}r0c0"

    def test_delete_columns_shrinks_table(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.writing import edit_table

        idx, tok = _seed_table(rows=2, columns=3)
        res = json.loads(edit_table(table_index=idx, action="delete_columns", column=0, count=1))
        assert "error" not in res, res
        tbl = _live_doc().getTextTables().getByIndex(idx)
        assert tbl.getColumns().getCount() == 2
        # Column 0 (c0) removed; old column 1 (c1) is now column 0.
        assert tbl.getCellByName("A1").getString() == f"{tok}r0c1"

    def test_bad_table_index_rejected(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.writing import edit_table

        bad = _live_doc().getTextTables().getCount() + 100
        res = json.loads(
            edit_table(table_index=bad, action="edit_cell", row=0, column=0, value="x")
        )
        assert "error" in res, res


# ---------------------------------------------------------------------------
# delete_content
# ---------------------------------------------------------------------------
#
# These only ever remove INTERIOR uniquely-tokened paragraphs the test itself
# inserted, never the final paragraph or one abutting a table — deleting those
# is fragile in LibreOffice (see writing._delete_paragraph).


class TestDeleteContent:
    def test_delete_by_query_removes_matched_text(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.writing import delete_content, insert_content

        keep_a, gone, keep_b = _tok(), _tok(), _tok()
        insert_content(text=f"{keep_a} alpha {gone} omega {keep_b}", location="end")
        res = json.loads(delete_content(query=gone))
        assert "error" not in res, res
        joined = " ".join(_paras(_live_doc()))
        assert gone not in joined, joined
        # Paragraph structure preserved — surrounding text on the same line stays.
        assert keep_a in joined and keep_b in joined, joined

    def test_delete_query_no_match_leaves_my_text_intact(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.writing import delete_content, insert_content

        present = _tok()
        insert_content(text=f"{present} nothing to remove", location="end")
        res = json.loads(delete_content(query=f"ABSENT{_tok()}"))
        assert "error" not in res, res
        assert any(present in p for p in _paras(_live_doc())), _paras(_live_doc())

    def test_delete_paragraph_range_removes_interior_paragraphs(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.writing import delete_content, insert_content

        p0, p1, p2, p3 = _tok(), _tok(), _tok(), _tok()
        for tok in (p0, p1, p2, p3):
            insert_content(text=f"{tok} line", location="end")
        doc = _live_doc()
        # Resolve live indices of the two INTERIOR paragraphs to delete (p1, p2).
        start = _index_of(doc, f"{p1} line")
        end = _index_of(doc, f"{p2} line")
        res = json.loads(delete_content(start_index=start, end_index=end))
        assert "error" not in res, res
        paras = _paras(_live_doc())
        joined = " ".join(paras)
        assert p1 not in joined and p2 not in joined, paras
        assert any(p0 in p for p in paras) and any(p3 in p for p in paras), paras

    def test_delete_single_interior_paragraph_by_index(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.writing import delete_content, insert_content

        keep0, drop1, keep2 = _tok(), _tok(), _tok()
        for tok in (keep0, drop1, keep2):
            insert_content(text=f"{tok} line", location="end")
        doc = _live_doc()
        target = _index_of(doc, f"{drop1} line")
        res = json.loads(delete_content(paragraph_index=target))
        assert "error" not in res, res
        paras = _paras(_live_doc())
        joined = " ".join(paras)
        assert drop1 not in joined, paras
        assert any(keep0 in p for p in paras) and any(keep2 in p for p in paras), paras

    def test_range_start_after_end_rejected(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.writing import delete_content, insert_content

        a, b, c = _tok(), _tok(), _tok()
        for tok in (a, b, c):
            insert_content(text=f"{tok} line", location="end")
        doc = _live_doc()
        i_a = _index_of(doc, f"{a} line")
        i_c = _index_of(doc, f"{c} line")
        res = json.loads(delete_content(start_index=i_c, end_index=i_a))
        assert "error" in res, res
        paras = _paras(_live_doc())
        for tok in (a, b, c):
            assert any(tok in p for p in paras), (tok, paras)


# ---------------------------------------------------------------------------
# undo_redo
# ---------------------------------------------------------------------------


class TestUndoRedo:
    def test_undo_reverses_insert_then_redo_restores(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.writing import insert_content, undo_redo

        tok = _tok()
        insert_content(text=f"{tok} reversible", location="end")
        assert any(tok in p for p in _paras(_live_doc()))

        undo_res = json.loads(undo_redo(action="undo"))
        assert undo_res.get("success") is True, undo_res
        assert not any(tok in p for p in _paras(_live_doc())), _paras(_live_doc())

        redo_res = json.loads(undo_redo(action="redo"))
        assert redo_res.get("success") is True, redo_res
        assert any(tok in p for p in _paras(_live_doc())), _paras(_live_doc())

    def test_undo_multiple_steps(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.writing import insert_content, undo_redo

        a, b = _tok(), _tok()
        insert_content(text=f"{a} step one", location="end")
        insert_content(text=f"{b} step two", location="end")
        assert any(a in p for p in _paras(_live_doc()))
        assert any(b in p for p in _paras(_live_doc()))

        res = json.loads(undo_redo(action="undo", count=2))
        assert res.get("success") is True, res
        joined = " ".join(_paras(_live_doc()))
        assert a not in joined and b not in joined, joined

    def test_redo_with_empty_stack_reports_failure(self, tool_doc: Any) -> None:
        from talk2view_writer.tools.writing import insert_content, undo_redo

        # Insert then fully undo so the undo stack is drained, then redo twice:
        # the second redo has nothing left and must report failure.
        tok = _tok()
        insert_content(text=f"{tok} only", location="end")
        undo_redo(action="undo")
        undo_redo(action="redo")
        res = json.loads(undo_redo(action="redo"))
        assert res.get("success") is False, res
        assert res.get("steps_applied") == 0, res
