"""Live-UNO regression tests for comment anchoring (Investigation #38).

These run against a real headless soffice (the integration marker), so
they exercise the actual LibreOffice C++ code path that the synthetic
model cannot reproduce.

Background: ``add_comment`` used to anchor a comment by *range-absorb* —
``text.insertTextContent(range, annotation, True)``. On LibreOffice 24.x
AND 26.x that raises ``com.sun.star.uno.RuntimeException: no SwTextAttr
inserted?`` and, worse, leaves an ORPHANED annotation behind even though
it raised — so the model retried and produced DUPLICATE comments. The
fix (:func:`_anchor_comment`) anchors at a collapsed cursor at the start
of the match (``bAbsorb=False``), which never raises and creates exactly
one annotation per call.

The positive tests guard the fix; ``test_range_absorb_is_broken`` is a
canary that documents the upstream defect — if a future LibreOffice fixes
it, that test fails loudly and we can revisit using the (nicer,
range-highlighting) range-absorb form again.
"""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Iterator
from typing import Any

import pytest

pytestmark = pytest.mark.integration

_STORY = (
    "Captain Elena Vance drifted through Sector 7 in silence. "
    "The dead moon of Aethel began to sing through the hull.\n"
    "Character Profiles\n"
    "Captain Elena Vance is the aging protagonist of the story.\n"
)


@pytest.fixture
def comment_doc(desktop: Any, uno_context: Any) -> Iterator[Any]:
    """A fresh hidden Writer doc preloaded with story text.

    Uses ``desktop`` directly (not ``blank_document``) so the test does
    NOT require the .oxt to be installed — it exercises pure UNO comment
    anchoring, independent of the extension.
    """
    from com.sun.star.beans import PropertyValue  # type: ignore[import-not-found]

    hidden = PropertyValue()
    hidden.Name = "Hidden"
    hidden.Value = True
    doc = desktop.loadComponentFromURL(
        "private:factory/swriter", "_blank", 0, (hidden,)
    )
    text = doc.getText()
    text.insertString(text.createTextCursor(), _STORY, False)
    try:
        yield doc
    finally:
        with contextlib.suppress(Exception):
            doc.close(False)


def _annotations(doc: Any) -> list[Any]:
    fields = doc.getTextFields()
    if fields is None:
        return []
    enum = fields.createEnumeration()
    out: list[Any] = []
    while enum.hasMoreElements():
        f = enum.nextElement()
        if f.supportsService("com.sun.star.text.TextField.Annotation"):
            out.append(f)
    return out


def _find_first(doc: Any, anchor: str) -> Any:
    s = doc.createSearchDescriptor()
    s.SearchString = anchor
    results = doc.findAll(s)
    assert results is not None and results.getCount() >= 1, f"{anchor!r} not found"
    return results.getByIndex(0)


def _new_annotation(doc: Any, content: str) -> Any:
    ann = doc.createInstance("com.sun.star.text.TextField.Annotation")
    ann.Content = content
    ann.Author = "Talk2View"
    return ann


class TestAnchorComment:
    """The production ``_anchor_comment`` helper against real LibreOffice."""

    def test_anchors_without_swtextattr_error_and_creates_one(
        self, comment_doc: Any
    ) -> None:
        from talk2view_writer.tools.commenting import _anchor_comment

        assert _annotations(comment_doc) == []
        rng = _find_first(comment_doc, "dead moon of Aethel")
        ann = _new_annotation(comment_doc, "A musical moon.")

        # Must NOT raise "no SwTextAttr inserted" (Investigation #38).
        _anchor_comment(rng.getText(), rng, ann)

        anns = _annotations(comment_doc)
        assert len(anns) == 1, "exactly one annotation expected"
        assert anns[0].Content == "A musical moon."

    def test_repeated_anchor_phrase_creates_exactly_one_no_duplicate(
        self, comment_doc: Any
    ) -> None:
        """The user's exact scenario: the phrase appears twice in the doc.

        With the old range-absorb path, the failed insert left an orphan
        and the model's retry produced a duplicate. The point-anchor path
        must create exactly ONE annotation for one call.
        """
        from talk2view_writer.tools.commenting import _anchor_comment

        # "Captain Elena Vance" appears in both the story and the profile.
        rng = _find_first(comment_doc, "Captain Elena Vance")
        _anchor_comment(
            rng.getText(), rng, _new_annotation(comment_doc, "Protagonist.")
        )
        assert len(_annotations(comment_doc)) == 1

    def test_multiple_comments_create_exactly_that_many(
        self, comment_doc: Any
    ) -> None:
        """Three distinct anchors → exactly three annotations, no orphans."""
        from talk2view_writer.tools.commenting import _anchor_comment

        for anchor, body in (
            ("Captain Elena Vance", "Protagonist."),
            ("dead moon of Aethel", "Musical moon."),
            ("Character Profiles", "Reference section."),
        ):
            rng = _find_first(comment_doc, anchor)
            _anchor_comment(
                rng.getText(), rng, _new_annotation(comment_doc, body)
            )

        anns = _annotations(comment_doc)
        assert len(anns) == 3, f"expected 3 annotations, got {len(anns)}"
        assert {a.Content for a in anns} == {
            "Protagonist.",
            "Musical moon.",
            "Reference section.",
        }


class TestCommentLifecycleIds:
    """Investigation #66: get_comments ids must survive to manage_comment.

    API-created annotations have an EMPTY Name on real LO 24.x/26.x, so the
    old ``str(id(ann))`` fallback id changed on every re-enumeration and
    manage_comment could never find a comment by the id get_comments handed
    out — resolve/reply/edit/delete were all dead. ``_annotation_id`` now
    assigns a stable, persisted ``t2v-…`` Name. These tests exercise the
    real C++ Name-assignment path the synthetic model cannot.
    """

    def _add(self, doc: Any, anchor: str, body: str) -> None:
        from talk2view_writer.tools.commenting import _anchor_comment

        rng = _find_first(doc, anchor)
        _anchor_comment(rng.getText(), rng, _new_annotation(doc, body))

    def test_ids_are_stable_distinct_and_survive_re_enumeration(
        self, comment_doc: Any
    ) -> None:
        from talk2view_writer.tools.commenting import (
            _annotation_id,
            _iter_annotations,
        )

        self._add(comment_doc, "Captain Elena Vance", "protagonist")
        self._add(comment_doc, "dead moon of Aethel", "setting")

        ids1 = [_annotation_id(a) for a in _iter_annotations(comment_doc)]
        ids2 = [_annotation_id(a) for a in _iter_annotations(comment_doc)]
        assert all(i for i in ids1), "ids must be non-empty"
        assert len(set(ids1)) == 2, "ids must be distinct"
        assert ids1 == ids2, "ids must be stable across re-enumeration"
        # And NOT the old unstable python-proxy-id fallback.
        assert all(i.startswith("t2v-") for i in ids1)

    def test_manage_comment_round_trip_resolve_and_delete_by_id(
        self, comment_doc: Any
    ) -> None:
        from talk2view_writer.tools.commenting import (
            _annotation_id,
            _find_by_id,
            _iter_annotations,
        )

        self._add(comment_doc, "Captain Elena Vance", "protagonist")
        self._add(comment_doc, "dead moon of Aethel", "setting")

        # "get_comments" hands out ids; a SEPARATE "manage_comment" finds them.
        ids = [_annotation_id(a) for a in _iter_annotations(comment_doc)]
        targets = [_find_by_id(comment_doc, cid) for cid in ids]
        assert all(t is not None for t in targets), "every id must resolve"

        # resolve by id
        targets[0].Resolved = True
        assert _find_by_id(comment_doc, ids[0]).Resolved is True

        # delete by id (the manage_comment delete path)
        tgt = _find_by_id(comment_doc, ids[1])
        anchor_text = tgt.getAnchor().getText() if tgt.getAnchor() else comment_doc.getText()
        anchor_text.removeTextContent(tgt)
        remaining = [_annotation_id(a) for a in _iter_annotations(comment_doc)]
        assert ids[1] not in remaining
        assert ids[0] in remaining

    def test_reply_nests_under_parent(self, comment_doc: Any, uno_context: Any) -> None:
        from talk2view_writer.tools.commenting import (
            _annotation_id,
            _find_by_id,
            _insert_reply,
            _iter_annotations,
        )

        self._add(comment_doc, "Captain Elena Vance", "protagonist")
        parent_id = _annotation_id(_iter_annotations(comment_doc)[0])
        parent = _find_by_id(comment_doc, parent_id)

        _insert_reply(uno_context, comment_doc, parent, "still needs work")

        # The reply carries ParentName == the parent's id (nested), not "".
        parent_names = {
            _annotation_id(a): getattr(a, "ParentName", "")
            for a in _iter_annotations(comment_doc)
        }
        assert parent_id in parent_names.values(), (
            "reply did not nest — its ParentName should equal the parent id"
        )


class TestRangeAbsorbCanary:
    """Document WHY we use point-anchor: range-absorb is broken upstream."""

    @pytest.mark.skipif(
        sys.platform != "linux",
        reason=(
            "range-absorb defect verified on Linux LO 24.x/26.x; its "
            "behaviour on macOS/Windows LO is unconfirmed, so only assert "
            "where reproduced (the point-anchor fix is what's portable)."
        ),
    )
    def test_range_absorb_raises_swtextattr_on_this_lo(
        self, comment_doc: Any
    ) -> None:
        """Canary for Investigation #38's root cause.

        ``insertTextContent(range, ann, True)`` (range-absorb) raises
        ``no SwTextAttr inserted`` on LibreOffice 24.x/26.x. If a future
        LibreOffice fixes this, this test fails — a deliberate signal to
        revisit using range-absorb for the nicer range-highlight UX.
        """
        rng = _find_first(comment_doc, "dead moon of Aethel")
        ann = _new_annotation(comment_doc, "via range-absorb")
        with pytest.raises(Exception) as exc_info:
            rng.getText().insertTextContent(rng, ann, True)
        assert "SwTextAttr" in str(exc_info.value), (
            "range-absorb no longer raises SwTextAttr — LO may have fixed "
            "the bug; revisit add_comment (Investigation #38)."
        )
