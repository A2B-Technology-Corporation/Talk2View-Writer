"""Commenting tools — annotations in the active Writer document.

Three tools mirror ``Talk2View-Word/src/taskpane/tools/commenting.ts``:

- :func:`get_comments`     — list every annotation with replies.
- :func:`add_comment`      — anchor a new annotation to body text.
- :func:`manage_comment`   — edit / resolve / unresolve / reply / delete.

Writer's commenting model differs from Word's in a few notable ways
(flagged inline and in ``docs/investigations.md``):

  * **Comment IDs**: LibreOffice annotations expose ``Name`` (a stable
    identifier) — we surface it as ``id`` for Word parity. UNO's ``Name``
    is a string (often something like ``__Annotation__0_1234567890``);
    Word's ``id`` is numeric, but the SDK only round-trips it as a
    string, so the substitution is invisible to the agent.
  * **Replies**: The reply-chain API (parent / child via ``ParentName``)
    is LibreOffice ≥ 7.4 only. On older builds, replies degrade to
    standalone annotations with the parent's text prefixed by the
    reply author. See Investigation #23.
  * **Resolved state**: ``Resolved`` is a boolean property (LO ≥ 7.4).
    We probe with ``hasattr`` and return ``resolved: null`` when the
    field is missing.
  * **Anchor search**: Limited to body text — same restriction as Word.
    Headers / footers / tables are not searched.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from talk2view import tool  # type: ignore[import-not-found]

from talk2view_writer.extension import get_extension_or_raise
from talk2view_writer.tools._base import get_writer_document, ui_thread_tool
from talk2view_writer.tools._constants import preview

logger = logging.getLogger(__name__)


_MANAGE_ACTIONS = (
    "resolve_with_reply",
    "edit",
    "resolve",
    "unresolve",
    "reply",
    "delete",
)

_ACTIONS_REQUIRING_TEXT = ("edit", "reply", "resolve_with_reply")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iter_annotations(doc: Any) -> list[Any]:
    """Walk the document and return every Annotation TextField.

    Annotations are accessible via ``doc.getTextFields()`` — UNO exposes
    them as ``com.sun.star.text.TextField.Annotation`` services. We
    filter by ``supportsService`` because ``getTextFields()`` also
    returns hyperlinks, page numbers, etc.
    """
    out: list[Any] = []
    fields = doc.getTextFields()
    if fields is None:
        return out
    enum = fields.createEnumeration()
    while enum.hasMoreElements():
        f = enum.nextElement()
        try:
            if f.supportsService("com.sun.star.text.TextField.Annotation"):
                out.append(f)
        except Exception:
            # Some text fields may not implement supportsService; skip them.
            continue
    return out


def _annotation_id(ann: Any) -> str:
    """Stable identifier for an Annotation TextField.

    LibreOffice exposes a string ``Name`` property (LO ≥ 4.x). Older
    builds occasionally return ``""`` — we fall back to ``str(id(ann))``
    so the agent can still round-trip a handle within one session.
    See Investigation #23.
    """
    name = getattr(ann, "Name", "") or ""
    return name if name else str(id(ann))


def _annotation_resolved(ann: Any) -> bool | None:
    """Return ``Resolved`` if present (LO ≥ 7.4), else ``None``."""
    if hasattr(ann, "Resolved"):
        return bool(ann.Resolved)
    return None


def _annotation_parent_name(ann: Any) -> str:
    """Return the annotation's parent name (reply chain) or ``""``."""
    return getattr(ann, "ParentName", "") or ""


def _annotation_anchor_text(ann: Any) -> str:
    """Best-effort attempt to read the text the annotation anchors to.

    Writer's Annotation API does not directly expose the anchored
    range's string — ``getAnchor()`` returns an ``XTextRange`` which is
    typically zero-length (the insertion point). We instead read the
    annotation's content + author here; tools that need the anchored
    text approximate via :func:`add_comment`'s search result.
    """
    anchor = ann.getAnchor()
    if anchor is None:
        return ""
    try:
        return anchor.getString() or ""
    except Exception:
        return ""


def _find_by_id(doc: Any, comment_id: str) -> Any | None:
    for a in _iter_annotations(doc):
        if _annotation_id(a) == comment_id:
            return a
    return None


# ---------------------------------------------------------------------------
# get_comments
# ---------------------------------------------------------------------------


@tool
@ui_thread_tool
def get_comments() -> str:
    """Get all comments in the document with replies and resolution status.

    Call this BEFORE manage_comment to get current comment IDs — IDs can
    change between sessions. Also call before add_comment to check
    existing comments. For document text, use get_document.

    Returns:
        JSON string with ``comments`` (array of ``{id, anchor_text,
        comment, author, resolved, replies?}``) and ``total``.

    Raises:
        WriterDocumentRequiredError: If no Writer document is active.
    """
    ext = get_extension_or_raise()
    doc = get_writer_document(ext.ctx)

    annotations = _iter_annotations(doc)
    if not annotations:
        return json.dumps(
            {
                "comments": [],
                "total": 0,
                "hint": (
                    "No comments in the document. Use add_comment to add one "
                    "(call get_document first to find anchor text)."
                ),
            }
        )

    # Group replies under their parent. LibreOffice reply chains use
    # ``ParentName`` (LO >= 7.4); older docs treat every annotation as a
    # top-level comment.
    by_id: dict[str, Any] = {_annotation_id(a): a for a in annotations}
    parents: list[Any] = []
    replies_for: dict[str, list[Any]] = {}
    for a in annotations:
        parent_name = _annotation_parent_name(a)
        if parent_name and parent_name in by_id:
            replies_for.setdefault(parent_name, []).append(a)
        else:
            parents.append(a)

    result: list[dict[str, Any]] = []
    for ann in parents:
        ann_id = _annotation_id(ann)
        entry: dict[str, Any] = {
            "id": ann_id,
            "anchor_text": _annotation_anchor_text(ann),
            "comment": getattr(ann, "Content", "") or "",
            "author": getattr(ann, "Author", "") or "",
            "resolved": _annotation_resolved(ann),
        }
        replies = replies_for.get(ann_id, [])
        if replies:
            entry["replies"] = [
                {
                    "text": getattr(r, "Content", "") or "",
                    "author": getattr(r, "Author", "") or "",
                }
                for r in replies
            ]
        result.append(entry)

    return json.dumps({"comments": result, "total": len(result)})


# ---------------------------------------------------------------------------
# add_comment
# ---------------------------------------------------------------------------


@tool
@ui_thread_tool
def add_comment(
    anchor: str,
    comment: str,
    match_case: bool = False,
) -> str:
    """Add a comment anchored to specific text in the document body.

    PREREQUISITE: Call get_document first to find exact anchor text
    (5-15 unique words from the target sentence). The anchor text is
    searched in the document body and the comment is attached to the
    first match. Cannot attach comments to text in headers, footers, or
    tables — body text only.

    Args:
        anchor: Exact text from the document to anchor the comment to.
            Must match existing text in the document body. Use 5-15
            unique words from the target sentence. Get the exact text
            from get_document first — even small differences in spacing
            or punctuation cause "not found" errors.
        comment: The comment text to attach. Write actionable comments
            that say WHAT to change and WHY.
        match_case: Set to true for case-sensitive anchor matching.
            Defaults to false.

    Returns:
        JSON string with ``success``, ``comment_id``, ``anchor``,
        ``comment``, ``matches_found``, optional ``hint``.

    Raises:
        WriterDocumentRequiredError: If no Writer document is active.
    """
    if not anchor or not anchor.strip():
        return json.dumps(
            {
                "error": "anchor text is empty.",
                "recovery": (
                    "Provide text from the document to anchor the comment to. "
                    "Use get_document to find exact text."
                ),
            }
        )
    if not comment or not comment.strip():
        return json.dumps(
            {"error": "comment text is empty.", "recovery": "Provide the comment content."}
        )

    ext = get_extension_or_raise()
    doc = get_writer_document(ext.ctx)

    searcher = doc.createSearchDescriptor()
    searcher.SearchString = anchor
    searcher.SearchCaseSensitive = match_case
    results = doc.findAll(searcher)
    total = results.getCount() if results is not None else 0

    if total == 0:
        return json.dumps(
            {
                "error": f'Text "{preview(anchor, 60)}" not found in the document.',
                "recovery": (
                    "Use get_document to check the exact text. Even small "
                    "differences in spacing or punctuation cause mismatches. "
                    "The search only covers body text, not headers/footers/tables."
                ),
            }
        )

    target_range = results.getByIndex(0)

    # Create an Annotation text field and attach it to the matched range.
    annotation = doc.createInstance("com.sun.star.text.TextField.Annotation")
    annotation.Content = comment
    # ``Author`` and ``Initials`` default to the LibreOffice user — leaving
    # them unset preserves Writer's normal behaviour. Setting an explicit
    # author here would diverge from how comments appear when a human user
    # types them.

    text_obj = target_range.getText()
    # ``True`` for the second arg replaces the range with the annotation
    # (anchoring it as a "comment range"). On Writer ≥ 7.x this gives
    # the highlighted-anchor behaviour Word users expect.
    text_obj.insertTextContent(target_range, annotation, True)

    ann_id = _annotation_id(annotation)
    anchor_preview = preview(anchor, 60)

    response: dict[str, Any] = {
        "success": True,
        "comment_id": ann_id,
        "anchor": anchor_preview,
        "comment": comment,
        "matches_found": total,
    }
    if total > 1:
        response["hint"] = (
            f"Warning: {total} matches found for this anchor text. Comment was "
            f"attached to the first match. Use a longer, more unique anchor to "
            f"target a specific location."
        )
    return json.dumps(response)


# ---------------------------------------------------------------------------
# manage_comment
# ---------------------------------------------------------------------------


@tool
@ui_thread_tool
def manage_comment(
    comment_id: str,
    action: str,
    text: str | None = None,
) -> str:
    """Manage an existing comment.

    PREREQUISITE: call get_comments first.

    STANDARD CLOSURE PATTERN after fixing an issue flagged by a reviewer:
    use ``action='resolve_with_reply'`` with text describing what you
    did — that's ONE atomic call that replies AND resolves. Only use
    ``action='delete'`` when the user explicitly asks to REMOVE
    comments — never to "close" a fixed one (delete erases the audit
    trail).

    Other actions: ``resolve`` / ``unresolve`` change status; ``reply``
    adds a reply without resolving; ``edit`` rewrites the original
    comment text.

    Args:
        comment_id: The comment ID from get_comments. Always fetch fresh
            IDs before calling this — IDs may change between sessions.
        action: One of ``resolve_with_reply``, ``edit``, ``resolve``,
            ``unresolve``, ``reply``, ``delete``. See description for
            usage guidance.
        text: Required for ``resolve_with_reply``, ``reply``, ``edit``.
            Describe what you did (e.g., "Replaced 'utilize' with
            'use' in paragraphs 2, 5, 8.").

    Returns:
        JSON string with ``success``, ``comment_id``, ``action``,
        ``detail``.

    Raises:
        WriterDocumentRequiredError: If no Writer document is active.
    """
    if action not in _MANAGE_ACTIONS:
        return json.dumps(
            {
                "error": f"Unknown action '{action}'.",
                "recovery": f"Use one of: {', '.join(_MANAGE_ACTIONS)}.",
            }
        )

    if action in _ACTIONS_REQUIRING_TEXT and not isinstance(text, str):
        recovery = (
            "Provide the new comment content."
            if action == "edit"
            else "Provide a short reply describing what was done."
        )
        return json.dumps(
            {
                "error": f"'text' is required when action is '{action}'.",
                "recovery": recovery,
            }
        )

    ext = get_extension_or_raise()
    doc = get_writer_document(ext.ctx)

    target = _find_by_id(doc, comment_id)
    if target is None:
        return json.dumps(
            {
                "error": f"Comment with ID '{comment_id}' not found.",
                "recovery": (
                    "Comment IDs may have changed. Call get_comments to get "
                    "current IDs and retry."
                ),
            }
        )

    text_preview = preview(text, 60) if isinstance(text, str) else ""

    if action == "edit":
        assert isinstance(text, str)
        target.Content = text
        detail = f'Comment updated to: "{text_preview}"'

    elif action == "resolve":
        if not hasattr(target, "Resolved"):
            return json.dumps(
                {
                    "error": (
                        "This LibreOffice build does not support the Resolved "
                        "property on annotations."
                    ),
                    "recovery": (
                        "Upgrade to LibreOffice 7.4 or later, or use "
                        "action='reply' to acknowledge instead."
                    ),
                }
            )
        target.Resolved = True
        detail = "Comment marked as resolved."

    elif action == "unresolve":
        if not hasattr(target, "Resolved"):
            return json.dumps(
                {
                    "error": (
                        "This LibreOffice build does not support the Resolved "
                        "property on annotations."
                    ),
                    "recovery": "Upgrade to LibreOffice 7.4 or later.",
                }
            )
        target.Resolved = False
        detail = "Comment reopened."

    elif action == "reply":
        assert isinstance(text, str)
        _insert_reply(doc, target, text)
        detail = f'Reply added: "{text_preview}"'

    elif action == "resolve_with_reply":
        assert isinstance(text, str)
        _insert_reply(doc, target, text)
        if hasattr(target, "Resolved"):
            target.Resolved = True
            detail = f'Reply added and comment resolved: "{text_preview}"'
        else:
            detail = (
                f'Reply added: "{text_preview}". '
                "Resolved flag not supported on this LibreOffice build."
            )

    elif action == "delete":
        # Annotation TextFields are XTextContent — remove via the
        # document's text container.
        anchor_text_obj = target.getAnchor().getText() if target.getAnchor() else doc.getText()
        anchor_text_obj.removeTextContent(target)
        detail = "Comment deleted permanently."
    else:  # pragma: no cover — guarded by _MANAGE_ACTIONS check above
        raise AssertionError(f"Unreachable: action={action!r}")

    return json.dumps(
        {
            "success": True,
            "comment_id": comment_id,
            "action": action,
            "detail": detail,
        }
    )


def _insert_reply(doc: Any, parent: Any, content: str) -> None:
    """Insert a reply annotation anchored to the parent's range.

    LibreOffice ≥ 7.4 supports the reply chain via the ``ParentName``
    property. We set ``ParentName`` to the parent's ``Name`` so the
    Writer UI groups them. On older builds the reply just shows up as
    a standalone annotation — flagged in Investigation #23.
    """
    reply = doc.createInstance("com.sun.star.text.TextField.Annotation")
    reply.Content = content

    parent_name = getattr(parent, "Name", "") or ""
    if parent_name and hasattr(reply, "ParentName"):
        reply.ParentName = parent_name

    anchor = parent.getAnchor()
    if anchor is None:
        text_obj = doc.getText()
        cursor = text_obj.createTextCursor()
        text_obj.insertTextContent(cursor, reply, False)
    else:
        text_obj = anchor.getText()
        text_obj.insertTextContent(anchor, reply, False)


TOOLS = [get_comments, add_comment, manage_comment]
