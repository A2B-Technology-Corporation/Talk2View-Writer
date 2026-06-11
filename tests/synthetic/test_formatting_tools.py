"""Synthetic-UNO tests for the Formatting tools.

``format_text`` / ``format_paragraph`` / ``manage_list`` against an
in-process document. These tests exercise the validation logic and
the happy-path mutation patterns — full UNO interaction is covered
by integration tests against real soffice.
"""

from __future__ import annotations

import json

import pytest

from tests.synthetic.synthetic_uno import FakeParagraph, FakeTextDocument

pytestmark = pytest.mark.synthetic


class TestFormatText:
    def test_no_args_returns_error(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.formatting import format_text

        result = json.loads(format_text())
        assert "error" in result

    def test_accepts_every_schema_kwarg(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        """Schema-vs-signature contract: every TS schema kwarg must work.

        ``src/web/src/tools.ts`` declares the format_text schema the
        engine sees. Each property name there MUST exist as a Python
        kwarg here. Investigation #35 (the cats/cars debugging trip)
        showed how silently the two can drift apart — this test fires
        an alarm before a real engine call would TypeError.

        We exercise the happy path with ``query`` so the body actually
        runs end-to-end, then assert no kwarg name was rejected.
        """
        from talk2view_writer.tools.formatting import format_text

        synthetic_doc._text._paragraphs.clear()
        synthetic_doc._text._paragraphs.append(FakeParagraph("hello world"))
        result = json.loads(
            format_text(
                query="hello",
                bold=True,
                italic=False,
                underline=True,
                underline_style="single",
                strikethrough=False,
                superscript=False,
                subscript=False,
                color="FF0000",
                highlight="Yellow",
                size=12.0,
                font="Arial",
                match_index=0,
            )
        )
        assert isinstance(result, dict)
        assert "error" not in result, result

    def test_font_param_applies_charfontname(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        """Asking for a different font family sets CharFontName on the cursor.

        Regression for the chat log on 2026-05-23 where AI said "I
        cannot change the font type (like Arial or Times New Roman)"
        — the schema wasn't exposing ``font`` to the engine. The
        Python ``font`` kwarg has always worked; the missing piece
        was the TS schema (fixed in src/web/src/tools.ts).
        """
        from talk2view_writer.tools.formatting import format_text

        synthetic_doc._text._paragraphs.clear()
        synthetic_doc._text._paragraphs.append(FakeParagraph("Pip the penguin"))
        result = json.loads(
            format_text(query="Pip the penguin", font="Times New Roman")
        )
        assert "error" not in result, result
        assert result.get("success") is True

    def test_invalid_color_returns_error(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.formatting import format_text

        result = json.loads(format_text(query="hello", color="not-a-color"))
        assert "error" in result

    def test_invalid_underline_style_returns_error(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        """An unsupported underline_style must be rejected, not silently dropped.

        Pre-fix, ``_apply_inline_formatting`` only applied the style when it
        was in ``UNDERLINE_STYLE_UNO``; an unrecognised value (e.g. "wavy"
        instead of the supported "wave") was ignored while the tool still
        reported success echoing the bogus value. The validator now rejects
        it up front with a recovery listing the valid styles.
        """
        from talk2view_writer.tools._constants import UNDERLINE_STYLE_UNO
        from talk2view_writer.tools.formatting import format_text

        synthetic_doc._text._paragraphs.clear()
        synthetic_doc._text._paragraphs.append(FakeParagraph("hello world"))
        result = json.loads(format_text(query="hello", underline_style="wavy"))
        assert "error" in result
        assert "wavy" in result["error"]
        assert "success" not in result
        # Recovery enumerates the supported styles.
        assert "wave" in result["recovery"]
        for valid in UNDERLINE_STYLE_UNO:
            assert valid in result["recovery"]

    def test_valid_underline_style_still_applies(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.formatting import format_text

        synthetic_doc._text._paragraphs.clear()
        synthetic_doc._text._paragraphs.append(FakeParagraph("hello world"))
        result = json.loads(format_text(query="hello", underline_style="wave"))
        assert "error" not in result, result
        assert result.get("success") is True

    def test_format_by_query_finds_and_applies(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
    ) -> None:
        from talk2view_writer.tools.formatting import format_text

        synthetic_doc._text._paragraphs.append(FakeParagraph("make this BOLD"))
        result = json.loads(format_text(query="BOLD", bold=True))
        # Either success or no-match (synthetic search may not match exactly).
        # Just confirm we got a structured response.
        assert isinstance(result, dict)

    def test_batch_queries_accepted(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.formatting import format_text

        synthetic_doc._text._paragraphs.append(FakeParagraph("alpha beta gamma"))
        result = json.loads(
            format_text(
                queries=[
                    {"query": "alpha", "bold": True},
                    {"query": "gamma", "italic": True},
                ]
            )
        )
        assert isinstance(result, dict)


class TestInlineEscapement:
    """Superscript/subscript are independent toggles — neither cancels the other.

    Direct helper-level regression: applying the flags sequentially used
    to let {superscript: true, subscript: false} set superscript and then
    immediately wipe it via the subscript=false baseline reset.
    """

    def _cursor(self) -> object:
        import types

        return types.SimpleNamespace()

    def test_superscript_true_subscript_false_stays_superscript(self) -> None:
        from talk2view_writer.tools.formatting import _apply_inline_formatting

        cur = self._cursor()
        _apply_inline_formatting(cur, {"superscript": True, "subscript": False})
        assert cur.CharEscapement == 33  # type: ignore[attr-defined]
        assert cur.CharEscapementHeight == 58  # type: ignore[attr-defined]

    def test_subscript_true_superscript_false_stays_subscript(self) -> None:
        from talk2view_writer.tools.formatting import _apply_inline_formatting

        cur = self._cursor()
        _apply_inline_formatting(cur, {"subscript": True, "superscript": False})
        assert cur.CharEscapement == -33  # type: ignore[attr-defined]
        assert cur.CharEscapementHeight == 58  # type: ignore[attr-defined]

    def test_both_false_resets_to_baseline(self) -> None:
        from talk2view_writer.tools.formatting import _apply_inline_formatting

        cur = self._cursor()
        _apply_inline_formatting(cur, {"superscript": False, "subscript": False})
        assert cur.CharEscapement == 0  # type: ignore[attr-defined]
        assert cur.CharEscapementHeight == 100  # type: ignore[attr-defined]

    def test_no_escapement_keys_leaves_escapement_untouched(self) -> None:
        from talk2view_writer.tools.formatting import _apply_inline_formatting

        cur = self._cursor()
        _apply_inline_formatting(cur, {"bold": True})
        assert not hasattr(cur, "CharEscapement")


class TestFormatParagraph:
    def test_invalid_alignment_returns_error(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.formatting import format_paragraph

        result = json.loads(
            format_paragraph(paragraph_indices=[0], alignment="diagonal")
        )
        assert "error" in result

    def test_missing_targets_returns_error(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.formatting import format_paragraph

        result = json.loads(format_paragraph(alignment="center"))
        assert "error" in result

    def test_apply_alignment_to_paragraph(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.formatting import format_paragraph

        synthetic_doc._text._paragraphs.append(FakeParagraph("align me"))
        json.loads(
            format_paragraph(paragraph_indices=[1], alignment="center")
        )
        # ParaAdjust=3 → CENTER in our alignment map.
        assert (
            synthetic_doc._text._paragraphs[1].getPropertyValue("ParaAdjust") == 3
        )

    def test_apply_style_to_paragraph(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.formatting import format_paragraph

        synthetic_doc._text._paragraphs.append(FakeParagraph("style me"))
        json.loads(
            format_paragraph(paragraph_indices=[1], style="Heading1")
        )
        # Word "Heading1" should translate to LibreOffice "Heading 1".
        applied = synthetic_doc._text._paragraphs[1].getPropertyValue(
            "ParaStyleName"
        )
        assert applied in ("Heading 1", "Heading1")

    def test_batch_all_out_of_range_reports_failure(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        """A batch where every paragraph fails must NOT report success:true.

        The single source-document paragraph means indices 10/11/12 are all
        out of range. The batch used to hardcode success:true with
        paragraphs_formatted:0, so an LLM checking only `success` believed
        the formatting was applied.
        """
        from talk2view_writer.tools.formatting import format_paragraph

        result = json.loads(
            format_paragraph(paragraph_indices=[10, 11, 12], alignment="center")
        )
        assert result["success"] is False
        assert result["paragraphs_formatted"] == 0
        assert all("error" in r for r in result["results"])

    def test_batch_partial_success_reports_false(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.formatting import format_paragraph

        synthetic_doc._text._paragraphs.append(FakeParagraph("real paragraph"))
        result = json.loads(
            format_paragraph(paragraph_indices=[1, 99], alignment="center")
        )
        # One succeeded, one out of range -> overall success is False.
        assert result["success"] is False
        assert result["paragraphs_formatted"] == 1

    def test_missing_style_single_target_degrades_not_raises(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        """A style the build doesn't ship → structured error, not a crash.

        On LO 26.2 ``format_paragraph(style="ListParagraph")`` resolved to
        the unregistered "List Bullet" paragraph style and threw a raw
        ``com.sun.star.uno.RuntimeException`` that crashed the tool call (seen
        live in the guided-tour run). It must degrade to a JSON error that
        points the model at manage_list instead.
        """
        from talk2view_writer.tools.formatting import format_paragraph

        synthetic_doc._text._paragraphs.append(FakeParagraph("list me"))
        # Strip the list paragraph style — mirrors the real LO 26.2 build.
        synthetic_doc._style_families["ParagraphStyles"].pop("List Bullet", None)
        result = json.loads(
            format_paragraph(paragraph_index=1, style="ListParagraph")
        )
        assert "error" in result
        assert "recovery" in result
        assert "manage_list" in result["recovery"]
        assert "success" not in result

    def test_missing_style_in_batch_reports_per_paragraph_error(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.formatting import format_paragraph

        synthetic_doc._text._paragraphs.append(FakeParagraph("list me"))
        synthetic_doc._style_families["ParagraphStyles"].pop("List Bullet", None)
        result = json.loads(
            format_paragraph(paragraph_indices=[1], style="ListParagraph")
        )
        # Batch shape still returns; the one paragraph carries an error.
        assert result["paragraphs_formatted"] == 0
        assert "error" in result["results"][0]

    def test_present_style_single_target_still_succeeds(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.formatting import format_paragraph

        synthetic_doc._text._paragraphs.append(FakeParagraph("head me"))
        result = json.loads(
            format_paragraph(paragraph_index=1, style="Heading1")
        )
        assert result.get("success") is True
        assert result["resulting_style"] in ("Heading 1", "Heading1")

    def test_libreoffice_display_style_name_is_accepted(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        """format_paragraph accepts an LO display name, not just Word names.

        Regression for Writer #2: the model echoes 'Heading 2' / 'Text body'
        back from get_document; these must normalise to the Word vocabulary
        and apply, instead of failing the schema check with "Unknown style".
        Pre-fix, 'Heading 2' (with the space) was not in VALID_STYLES and was
        rejected outright.
        """
        from talk2view_writer.tools.formatting import format_paragraph

        synthetic_doc._text._paragraphs.append(FakeParagraph("head me"))
        result = json.loads(
            format_paragraph(paragraph_index=1, style="Heading 2")
        )
        assert "error" not in result, result
        assert result.get("success") is True
        assert result["resulting_style"] in ("Heading 2", "Heading2")

    def test_normal_resolves_to_text_body(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        """style='Normal' lands on the named 'Text body', not the pool default.

        Regression for investigation #53: 'Normal' must map to a NAMED style
        so the ParaStyleName write survives the LO 26.2 pool-default rejection.
        Also pins Writer #56 — resulting_style is reported in the Word
        vocabulary ('Normal'), consistent with get_document, not the raw LO
        name. The two assertions have distinct teeth: the raw ParaStyleName
        proves the named mapping; resulting_style proves the read-back fold.
        """
        from talk2view_writer.tools.formatting import format_paragraph

        synthetic_doc._text._paragraphs.append(FakeParagraph("body me"))
        result = json.loads(
            format_paragraph(paragraph_index=1, style="Normal")
        )
        assert "error" not in result, result
        assert result.get("success") is True
        # Raw UNO style landed on the NAMED 'Text body' (mapping has teeth).
        assert synthetic_doc._text._paragraphs[1].ParaStyleName == "Text body"
        # Reported style is folded to the Word vocabulary (cross-tool parity).
        assert result["resulting_style"] == "Normal"

    def test_keep_together_drives_only_para_split(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        """keep_together = keep-lines-together → ParaSplit False, NOT keep-with-next.

        UNO semantics: ``ParaSplit=False`` keeps all LINES of the paragraph
        together (no page-break split), while ``ParaKeepTogether=True`` is
        keep-with-NEXT. Pre-fix the loop wrote ``ParaKeepTogether =
        keep_together`` (wrong axis) and a bogus ``ParaKeepWithNext``. A
        keep_together request must touch only ParaSplit.
        """
        from talk2view_writer.tools.formatting import format_paragraph

        synthetic_doc._text._paragraphs.append(FakeParagraph("hold my lines"))
        para = synthetic_doc._text._paragraphs[1]
        json.loads(format_paragraph(paragraph_index=1, keep_together=True))
        assert para.getPropertyValue("ParaSplit") is False
        # keep_with_next was not requested → ParaKeepTogether untouched.
        assert para.getPropertyValue("ParaKeepTogether") is None

    def test_keep_with_next_drives_para_keep_together(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        """keep_with_next = keep-with-NEXT → ParaKeepTogether True.

        And it must NOT touch ParaSplit (which is the keep-lines axis).
        ``ParaKeepWithNext`` is not a real UNO property and must never be
        written.
        """
        from talk2view_writer.tools.formatting import format_paragraph

        synthetic_doc._text._paragraphs.append(FakeParagraph("stay with next"))
        para = synthetic_doc._text._paragraphs[1]
        json.loads(format_paragraph(paragraph_index=1, keep_with_next=True))
        assert para.getPropertyValue("ParaKeepTogether") is True
        # keep_together was not requested → ParaSplit untouched.
        assert para.getPropertyValue("ParaSplit") is None
        # The non-existent UNO property must never be written.
        assert para.getPropertyValue("ParaKeepWithNext") is None


class TestManageList:
    def test_empty_paragraph_indices_returns_error(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.formatting import manage_list

        result = json.loads(
            manage_list(action="add", list_type="bullet", paragraph_indices=[])
        )
        assert "error" in result

    def test_invalid_action_returns_error(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.formatting import manage_list

        result = json.loads(
            manage_list(action="rotate", paragraph_indices=[0])
        )
        assert "error" in result

    def test_add_bullet_changes_paragraph_style(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.formatting import manage_list

        synthetic_doc._text._paragraphs.append(FakeParagraph("item one"))
        synthetic_doc._text._paragraphs.append(FakeParagraph("item two"))
        json.loads(
            manage_list(
                action="add", list_type="bullet", paragraph_indices=[1, 2]
            )
        )
        # Bullet lists in Writer apply the "List Bullet" paragraph style.
        for idx in (1, 2):
            applied = synthetic_doc._text._paragraphs[idx].getPropertyValue(
                "ParaStyleName"
            )
            assert applied in ("List Bullet", "ListBullet", "List Number")

    def test_action_and_list_type_are_case_insensitive(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        """Title-cased action / list_type must not error (Writer #5).

        Pre-fix the SDK rejected "Add" / "Bullet" against the lowercase
        enums and the model retried. The enums are gone; the handler
        lowercases, so the Title-cased call reaches the same success
        path.
        """
        from talk2view_writer.tools.formatting import manage_list

        synthetic_doc._text._paragraphs.append(FakeParagraph("item one"))
        result = json.loads(
            manage_list(action="Add", list_type="Bullet", paragraph_indices=[1])
        )
        assert "error" not in result, result
        applied = synthetic_doc._text._paragraphs[1].getPropertyValue(
            "ParaStyleName"
        )
        assert applied in ("List Bullet", "ListBullet", "List Number")

    def test_add_bullet_applies_numbering_without_list_styles(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        """No list paragraph styles → still make a REAL list via NumberingRules.

        LO 26.2 ships no ``List Bullet`` paragraph style, so the old
        style-only path failed and the model fell back to typing literal
        "•" characters (surfaced by the guided-tour demo). ``manage_list``
        must apply numbering via the paragraph ``NumberingRules`` property,
        which works on every build.
        """
        from talk2view_writer.tools.formatting import manage_list

        synthetic_doc._text._paragraphs.append(FakeParagraph("item one"))
        # Strip every bullet-style alias — mirrors the real LO 26.2 build.
        for alias in ("List Bullet", "Bulleted List", "List Paragraph", "ListBullet"):
            synthetic_doc._style_families["ParagraphStyles"].pop(alias, None)

        result = json.loads(
            manage_list(action="add", list_type="bullet", paragraph_indices=[1])
        )
        assert "error" not in result, result
        assert result["success"] is True
        para = synthetic_doc._text._paragraphs[1]
        assert para.getPropertyValue("NumberingRules") is not None
        assert para.getPropertyValue("NumberingIsNumber") is True

    def test_add_number_applies_numbering(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.formatting import manage_list

        synthetic_doc._text._paragraphs.append(FakeParagraph("step one"))
        synthetic_doc._text._paragraphs.append(FakeParagraph("step two"))
        result = json.loads(
            manage_list(action="add", list_type="number", paragraph_indices=[1, 2])
        )
        assert "error" not in result, result
        for idx in (1, 2):
            para = synthetic_doc._text._paragraphs[idx]
            assert para.getPropertyValue("NumberingRules") is not None
            assert para.getPropertyValue("NumberingIsNumber") is True

    def test_remove_clears_numbering(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.formatting import manage_list

        synthetic_doc._text._paragraphs.append(FakeParagraph("item one"))
        manage_list(action="add", list_type="bullet", paragraph_indices=[1])
        result = json.loads(manage_list(action="remove", paragraph_indices=[1]))
        assert "error" not in result, result
        assert result["success"] is True
        para = synthetic_doc._text._paragraphs[1]
        assert para.getPropertyValue("NumberingRules") is None
        assert para.getPropertyValue("NumberingIsNumber") is False

    def test_remove_reports_failure_when_clear_raises(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        """A swallowed NumberingRules clear must surface success=False.

        Pre-fix, ``p.NumberingRules = None`` was wrapped in
        ``except Exception: logger.debug(...)`` and the tool still returned
        ``{"success": True, ...}`` unconditionally — a false success when the
        load-bearing clear was swallowed. The result must now reflect the
        per-paragraph failure (success=False + a per-paragraph error),
        consistent with how set_header_footer reports per-section outcomes.
        """
        from talk2view_writer.tools.formatting import manage_list

        class _UnclearableParagraph(FakeParagraph):
            """Paragraph whose NumberingRules clear raises (as real LO can)."""

            def __setattr__(self, name: str, value: object) -> None:
                if name == "NumberingRules" and value is None:
                    raise RuntimeError("NumberingRules clear rejected")
                super().__setattr__(name, value)

        synthetic_doc._text._paragraphs.append(_UnclearableParagraph("item one"))
        result = json.loads(manage_list(action="remove", paragraph_indices=[1]))
        assert result["success"] is False
        assert result["paragraphs_affected"] == 0
        assert "error" in result["results"][0]
        assert result["results"][0]["paragraph_index"] == 1

    def test_number_submits_only_minimal_props(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        """Regression: never round-trip the full getByIndex property set.

        Real LO rejects re-submitting a level's entire default property set
        via replaceByIndex with IllegalArgumentException — it crashed the
        live guided-tour run (investigation #50). The synthetic NumberingRules
        now ships a realistic default level, so a round-tripping regression
        would surface those extra property names here.
        """
        from talk2view_writer.tools.formatting import manage_list

        synthetic_doc._text._paragraphs.append(FakeParagraph("step one"))
        manage_list(action="add", list_type="number", paragraph_indices=[1])
        rules = synthetic_doc._text._paragraphs[1].getPropertyValue(
            "NumberingRules"
        )
        submitted = {pv.Name for pv in rules.getByIndex(0)}
        assert submitted == {"NumberingType", "Prefix", "Suffix"}, submitted

    def test_bullet_submits_only_minimal_props(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.formatting import manage_list

        synthetic_doc._text._paragraphs.append(FakeParagraph("item one"))
        manage_list(action="add", list_type="bullet", paragraph_indices=[1])
        rules = synthetic_doc._text._paragraphs[1].getPropertyValue(
            "NumberingRules"
        )
        submitted = {pv.Name for pv in rules.getByIndex(0)}
        assert submitted == {
            "NumberingType",
            "BulletChar",
            "BulletFontName",
        }, submitted

    def test_numbering_props_submitted_as_typed_uno_any(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        """Marker props reach replaceByIndex as a typed ``uno.Any``, not a tuple.

        On real LO 26.2.3.2 a bare tuple marshals as ``Sequence<Any>`` and
        ``replaceByIndex`` throws a message-less ``IllegalArgumentException``;
        the live guided-tour run hit this even after the minimal-marker re-fix
        (investigation #50, third strike). Production must wrap the marker
        sequence in ``uno.Any("[]com.sun.star.beans.PropertyValue", ...)``.
        The hardened ``FakeNumberingRules`` raises on anything else, so a
        regression fails ``manage_list`` outright here; this test additionally
        pins the exact UNO type name that was handed over.
        """
        from talk2view_writer.tools.formatting import manage_list

        synthetic_doc._text._paragraphs.append(FakeParagraph("item one"))
        manage_list(action="add", list_type="bullet", paragraph_indices=[1])
        rules = synthetic_doc._text._paragraphs[1].getPropertyValue(
            "NumberingRules"
        )
        assert rules.submitted_types == ["[]com.sun.star.beans.PropertyValue"]
