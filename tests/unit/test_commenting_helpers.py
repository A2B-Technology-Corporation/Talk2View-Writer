"""Tests for pure-Python helpers in ``commenting.py``.

The full ``get_comments`` / ``add_comment`` / ``manage_comment`` bodies
require live UNO annotations — exercised in Phase F integration. The
helpers tested here probe the reply-chain grouping, ``Resolved`` /
``ParentName`` capability detection, and the annotation iterator.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from talk2view_writer.tools.commenting import (
    _MANAGE_ACTIONS,
    _annotation_id,
    _annotation_parent_name,
    _annotation_resolved,
    _iter_annotations,
)


def _annotation_field(name: str = "ann1") -> MagicMock:
    f = MagicMock()
    f.supportsService.side_effect = (
        lambda svc: svc == "com.sun.star.text.TextField.Annotation"
    )
    f.Name = name
    return f


def _non_annotation_field() -> MagicMock:
    f = MagicMock()
    f.supportsService.return_value = False
    return f


def _doc_with_text_fields(fields: list[MagicMock]) -> MagicMock:
    """Build a stub doc whose ``getTextFields()`` enumerates ``fields``."""
    doc = MagicMock()
    container = MagicMock()
    enum = MagicMock()
    state = {"i": 0}

    def has_more() -> bool:
        return state["i"] < len(fields)

    def next_element() -> MagicMock:
        f = fields[state["i"]]
        state["i"] += 1
        return f

    enum.hasMoreElements.side_effect = has_more
    enum.nextElement.side_effect = next_element
    container.createEnumeration.return_value = enum
    doc.getTextFields.return_value = container
    return doc


@pytest.mark.unit
class TestIterAnnotations:
    def test_returns_empty_when_no_text_fields(self) -> None:
        doc = MagicMock()
        doc.getTextFields.return_value = None
        assert _iter_annotations(doc) == []

    def test_filters_out_non_annotation_fields(self) -> None:
        anno = _annotation_field("a")
        not_anno = _non_annotation_field()
        doc = _doc_with_text_fields([not_anno, anno, not_anno])
        result = _iter_annotations(doc)
        assert result == [anno]

    def test_collects_multiple_annotations_in_order(self) -> None:
        anns = [_annotation_field(f"a{i}") for i in range(3)]
        doc = _doc_with_text_fields(anns)
        assert _iter_annotations(doc) == anns

    def test_swallows_supports_service_exceptions(self) -> None:
        bad = MagicMock()
        bad.supportsService.side_effect = AttributeError("not implemented")
        good = _annotation_field("good")
        doc = _doc_with_text_fields([bad, good])
        assert _iter_annotations(doc) == [good]


@pytest.mark.unit
class TestAnnotationId:
    def test_returns_name_when_set(self) -> None:
        ann = MagicMock()
        ann.Name = "__Annotation__0_1234"
        assert _annotation_id(ann) == "__Annotation__0_1234"

    def test_falls_back_to_object_id_when_name_empty(self) -> None:
        ann = MagicMock()
        ann.Name = ""
        result = _annotation_id(ann)
        # Should be a non-empty string corresponding to Python's id().
        assert result == str(id(ann))
        assert result != ""


@pytest.mark.unit
class TestAnnotationResolved:
    def test_returns_bool_when_property_present(self) -> None:
        ann = MagicMock(spec=["Resolved"])
        ann.Resolved = True
        assert _annotation_resolved(ann) is True
        ann.Resolved = False
        assert _annotation_resolved(ann) is False

    def test_returns_none_when_property_missing(self) -> None:
        ann = MagicMock(spec=["Content", "Author"])  # no Resolved
        assert _annotation_resolved(ann) is None


@pytest.mark.unit
class TestAnnotationParentName:
    def test_returns_empty_string_when_no_parent(self) -> None:
        ann = MagicMock()
        ann.ParentName = ""
        assert _annotation_parent_name(ann) == ""

    def test_returns_parent_name_when_set(self) -> None:
        ann = MagicMock()
        ann.ParentName = "__Annotation__0_parent"
        assert _annotation_parent_name(ann) == "__Annotation__0_parent"


@pytest.mark.unit
class TestManageActionsCatalog:
    def test_includes_all_word_supported_actions(self) -> None:
        expected = {
            "resolve_with_reply",
            "edit",
            "resolve",
            "unresolve",
            "reply",
            "delete",
        }
        assert expected == set(_MANAGE_ACTIONS)
