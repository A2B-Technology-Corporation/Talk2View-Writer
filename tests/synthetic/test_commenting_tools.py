"""Synthetic-UNO tests for the Commenting tools.

Covers ``get_comments``, ``add_comment``, ``manage_comment``. The
synthetic doc holds annotations in a :class:`FakeAnnotationsContainer`
exposed via ``getTextFields()`` — same surface ``_iter_annotations``
walks in production.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from tests.synthetic.synthetic_uno import (
    FakeAnnotation,
    FakeParagraph,
    FakeTextDocument,
)

pytestmark = pytest.mark.synthetic


# ---------------------------------------------------------------------------
# get_comments
# ---------------------------------------------------------------------------


class TestGetComments:
    def test_empty_returns_hint(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.commenting import get_comments

        result = json.loads(get_comments())
        assert result["total"] == 0
        assert result["comments"] == []
        assert "hint" in result

    def test_returns_all_annotations(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.commenting import get_comments

        synthetic_doc.add_annotation(
            name="ann1",
            content="Needs work.",
            author="Alice",
            anchor_text="this paragraph",
        )
        synthetic_doc.add_annotation(
            name="ann2",
            content="Good point!",
            author="Bob",
            anchor_text="another part",
        )

        result = json.loads(get_comments())
        assert result["total"] == 2
        ids = {c["id"] for c in result["comments"]}
        assert ids == {"ann1", "ann2"}
        authors = {c["author"] for c in result["comments"]}
        assert authors == {"Alice", "Bob"}

    def test_groups_replies_under_parent(
        self, patched_extension: object, synthetic_doc: FakeTextDocument
    ) -> None:
        from talk2view_writer.tools.commenting import get_comments

        synthetic_doc.add_annotation(
            name="parent1",
            content="Top-level question.",
            author="Alice",
        )
        synthetic_doc.add_annotation(
            name="reply1",
            content="Here's my answer.",
            author="Bob",
            parent_name="parent1",
        )
        result = json.loads(get_comments())
        # Reply chain collapses into the parent — total of top-level
        # comments is 1, with the reply nested.
        assert result["total"] == 1
        assert result["comments"][0]["id"] == "parent1"
        replies = result["comments"][0].get("replies", [])
        assert len(replies) == 1
        assert replies[0]["author"] == "Bob"


# ---------------------------------------------------------------------------
# add_comment — input validation
# ---------------------------------------------------------------------------


class TestAddComment:
    def test_empty_anchor_returns_error(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
    ) -> None:
        from talk2view_writer.tools.commenting import add_comment

        result = json.loads(add_comment(anchor="   ", comment="hi"))
        assert "error" in result

    def test_empty_comment_returns_error(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
    ) -> None:
        from talk2view_writer.tools.commenting import add_comment

        synthetic_doc._text._paragraphs.append(FakeParagraph("the text"))
        result = json.loads(add_comment(anchor="the text", comment=""))
        assert "error" in result

    def test_anchor_not_found_returns_error(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
    ) -> None:
        from talk2view_writer.tools.commenting import add_comment

        synthetic_doc._text._paragraphs.append(FakeParagraph("only this"))
        result = json.loads(add_comment(anchor="missing phrase", comment="hi"))
        assert "error" in result
        # "not found" message in the error or recovery text.
        assert "not found" in json.dumps(result).lower()

    def test_success_creates_one_comment_visible_via_get_comments(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
    ) -> None:
        """Anchor a comment and read it back via get_comments — exactly one.

        This happy path had NO coverage before, which is how the
        range-absorb defect (Investigation #38) reached production.
        """
        from talk2view_writer.tools.commenting import add_comment, get_comments

        synthetic_doc._text._paragraphs.append(
            FakeParagraph("Captain Elena Vance drifted through Sector 7.")
        )
        result = json.loads(
            add_comment(anchor="Captain Elena Vance", comment="Protagonist.")
        )
        assert result["success"] is True
        assert result["comment_id"]

        comments = json.loads(get_comments())
        assert comments["total"] == 1
        assert comments["comments"][0]["comment"] == "Protagonist."

    def test_uses_point_anchor_not_range_absorb(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
    ) -> None:
        """Regression guard for Investigation #38.

        The annotation MUST be inserted with ``bAbsorb=False`` (collapsed
        point anchor). The range-absorb form (``True``) raises
        ``no SwTextAttr inserted`` on real LibreOffice 24.x/26.x and leaves
        an orphan the model duplicates — so a regression back to it must
        fail here, fast, without needing soffice.
        """
        from talk2view_writer.tools.commenting import add_comment

        synthetic_doc._text._paragraphs.append(
            FakeParagraph("the quick brown fox jumps over the lazy dog")
        )
        add_comment(anchor="lazy dog", comment="hi")

        ann_calls = [
            (content, absorb)
            for content, absorb in synthetic_doc._text._inserted_content_calls
            if isinstance(content, FakeAnnotation)
        ]
        assert len(ann_calls) == 1, "exactly one annotation insert expected"
        _content, absorb = ann_calls[0]
        assert absorb is False, (
            "add_comment must point-anchor (bAbsorb=False); range-absorb "
            "(True) is broken on real LO — Investigation #38"
        )

    def test_repeated_anchor_phrase_creates_single_comment(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
    ) -> None:
        """The user's scenario: an anchor phrase that appears twice.

        One add_comment call → exactly one annotation (no orphan-driven
        duplicate). add_comment attaches to the first match.
        """
        from talk2view_writer.tools.commenting import add_comment, get_comments

        synthetic_doc._text._paragraphs.append(
            FakeParagraph("Captain Elena Vance leads the crew.")
        )
        synthetic_doc._text._paragraphs.append(
            FakeParagraph("Profile: Captain Elena Vance, protagonist.")
        )
        result = json.loads(
            add_comment(anchor="Captain Elena Vance", comment="Lead.")
        )
        assert result["success"] is True
        assert result["matches_found"] == 2  # phrase appears twice...
        assert json.loads(get_comments())["total"] == 1  # ...but one comment

    def test_swtextattr_exception_translates_to_structured_error(
        self,
    ) -> None:
        """LO 'no SwTextAttr inserted' surfaces as a structured error.

        Regression guard for Investigation #38. When LO raises the
        C++ RuntimeException at insertTextContent time, the
        translation helper must return a JSON error with a useful
        ``recovery`` hint instead of propagating the exception
        (which would crash the bridge).
        """
        from talk2view_writer.tools.commenting import (
            _structured_error_for_known_lo_bug,
        )

        exc = RuntimeError("no SwTextAttr inserted? at unofield.cxx:1976")
        translated = _structured_error_for_known_lo_bug(exc, anchor="fox")
        assert translated is not None
        result = json.loads(translated)
        assert "error" in result
        assert "recovery" in result
        assert "Investigation #38" in result["error"]

    def test_non_swtextattr_exception_returns_none_so_caller_can_reraise(
        self,
    ) -> None:
        """Only SwTextAttr is swallowed — other errors must bubble.

        The translation helper must return ``None`` for unrelated
        exception messages so the caller knows to re-raise.
        """
        from talk2view_writer.tools.commenting import (
            _structured_error_for_known_lo_bug,
        )

        for unrelated in (
            RuntimeError("permission denied"),
            ValueError("bad argument"),
            RuntimeError("something else entirely"),
        ):
            assert _structured_error_for_known_lo_bug(unrelated, anchor="x") is None


# ---------------------------------------------------------------------------
# Authorship stamping (Investigation #46 / ADR-0037)
# ---------------------------------------------------------------------------


def _ctx_with_user(given: str, surname: str) -> MagicMock:
    """A fake ctx whose UserProfile config resolves to ``given``/``surname``."""
    access = MagicMock(name="ConfigurationAccess")
    access.getByName.side_effect = lambda key: {
        "givenname": given,
        "sn": surname,
    }[key]
    provider = MagicMock(name="ConfigurationProvider")
    provider.createInstanceWithArguments.return_value = access
    ctx = MagicMock(name="ctx")
    ctx.ServiceManager.createInstanceWithContext.return_value = provider
    return ctx


def _ctx_without_config() -> MagicMock:
    """A fake ctx whose configuration service is unavailable."""
    ctx = MagicMock(name="ctx")
    ctx.ServiceManager.createInstanceWithContext.side_effect = RuntimeError(
        "no configuration service on this build"
    )
    return ctx


class TestCommentAuthorship:
    def test_author_includes_lo_user_name(self) -> None:
        from talk2view_writer.tools.commenting import _comment_author

        author = _comment_author(_ctx_with_user("Ben", "Zwick"))
        assert author == "Talk2View on behalf of Ben Zwick"

    def test_author_falls_back_when_config_unavailable(self) -> None:
        """A stripped build → plain 'Talk2View', not a crash (graceful)."""
        from talk2view_writer.tools.commenting import _comment_author

        assert _comment_author(_ctx_without_config()) == "Talk2View"

    def test_author_falls_back_when_user_name_blank(self) -> None:
        from talk2view_writer.tools.commenting import _comment_author

        assert _comment_author(_ctx_with_user("", "")) == "Talk2View"

    def test_now_uno_datetime_carries_current_date(self) -> None:
        from datetime import datetime

        from talk2view_writer.tools.commenting import _now_uno_datetime

        before = datetime.now()
        dt = _now_uno_datetime()
        after = datetime.now()
        # createUnoStruct is a MagicMock in tests; the fields we set are
        # readable back. Confirm the date matches today (date can't roll
        # between before/after except at midnight — assert the year/month).
        assert dt.Year == before.year == after.year
        assert dt.Month == before.month

    def test_stamp_authorship_sets_author_initials_and_date(self) -> None:
        from talk2view_writer.tools.commenting import _stamp_authorship

        ann = FakeAnnotation(name="x", content="hi")
        _stamp_authorship(_ctx_with_user("Ben", "Zwick"), ann)
        assert ann.Author == "Talk2View on behalf of Ben Zwick"
        assert ann.Initials == "T2V"
        assert ann.DateTimeValue is not None


# ---------------------------------------------------------------------------
# manage_comment — input validation
# ---------------------------------------------------------------------------


class TestManageComment:
    def test_invalid_action_returns_error(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
    ) -> None:
        from talk2view_writer.tools.commenting import manage_comment

        result = json.loads(
            manage_comment(action="unknown", comment_id="x")
        )
        assert "error" in result

    def test_empty_comment_id_returns_error(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
    ) -> None:
        from talk2view_writer.tools.commenting import manage_comment

        result = json.loads(manage_comment(action="resolve", comment_id=""))
        assert "error" in result

    def test_unknown_id_returns_error(
        self,
        patched_extension: object,
        synthetic_doc: FakeTextDocument,
    ) -> None:
        from talk2view_writer.tools.commenting import manage_comment

        synthetic_doc.add_annotation(name="exists", content="hi")
        result = json.loads(
            manage_comment(action="resolve", comment_id="ghost")
        )
        assert "error" in result
