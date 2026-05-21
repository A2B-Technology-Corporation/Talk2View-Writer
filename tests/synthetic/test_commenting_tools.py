"""Synthetic-UNO tests for the Commenting tools.

Covers ``get_comments``, ``add_comment``, ``manage_comment``. The
synthetic doc holds annotations in a :class:`FakeAnnotationsContainer`
exposed via ``getTextFields()`` — same surface ``_iter_annotations``
walks in production.
"""

from __future__ import annotations

import json

import pytest

from tests.synthetic.synthetic_uno import FakeParagraph, FakeTextDocument

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
