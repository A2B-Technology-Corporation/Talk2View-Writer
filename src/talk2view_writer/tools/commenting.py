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
import uuid
from datetime import datetime
from typing import Any

from talk2view import tool  # type: ignore[import-not-found]

from talk2view_writer.extension import get_extension_or_raise
from talk2view_writer.tools._base import get_writer_document, ui_thread_tool
from talk2view_writer.tools._constants import lower_enum, preview

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

    Every UNO ``XTextField`` implements ``XServiceInfo.supportsService``,
    so the probe should not fail in practice. If a genuine fault does
    occur (a broken / disposed UNO proxy, a marshalling error), we log
    it via ``logger.exception`` — keeping enumeration resilient while
    surfacing the fault instead of dropping annotations silently — and
    skip the offending field.
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
            logger.exception(
                "Skipping a text field whose supportsService() failed "
                "during annotation enumeration"
            )
            continue
    return out


def _annotation_id(ann: Any) -> str:
    """Stable, persisted identifier for an Annotation TextField.

    Returns the annotation's ``Name`` — but API-created annotations come
    back with an EMPTY ``Name`` on LibreOffice 24.x/26.x (verified on
    26.2), and the previous ``str(id(ann))`` fallback used the Python UNO
    proxy id, which CHANGES on every re-enumeration. So an id handed out by
    ``get_comments`` never matched in a later ``manage_comment`` call —
    resolve / reply / edit / delete all failed with "not found", i.e. the
    whole comment-management surface was dead on these builds
    (Investigation #66).

    Fix: the first time we see a nameless annotation, assign a stable
    unique ``Name`` and persist it in the document (verified settable +
    stable across re-enumeration). Subsequent calls — including a separate
    ``manage_comment`` bridge call — read the same persisted Name, so the
    id round-trips. Idempotent: an annotation that already has a Name (ours
    from a prior call, or a UI-created comment) is returned unchanged.
    See Investigation #23 for the reply-chain history.
    """
    name = getattr(ann, "Name", "") or ""
    if name:
        return name
    new_name = f"t2v-{uuid.uuid4().hex}"
    try:
        ann.Name = new_name
    except Exception:
        # A build that rejects the Name write falls back to the old
        # (unstable) behaviour rather than crashing — manage_comment may
        # still miss it, but get_comments / add_comment keep working.
        logger.warning(
            "Could not assign a stable Name to an annotation; manage_comment "
            "may not resolve it by id on this LibreOffice build."
        )
        return str(id(ann))
    return getattr(ann, "Name", "") or new_name


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
# Authorship stamping
# ---------------------------------------------------------------------------
#
# When a human types a comment, LibreOffice fills in the Author (from
# Tools > Options > User Data) and the timestamp automatically. The UNO
# ``createInstance`` path does NOT — a comment created via the API comes
# out with a blank author and no date (Investigation #46). We replicate
# the auto-fill explicitly. See ADR-0037 for the author-string choice.


def _lo_user_full_name(ctx: Any) -> str:
    """Return the LibreOffice user-profile full name, or ``""`` if unknown.

    Reads ``givenname`` + ``sn`` from ``/org.openoffice.UserProfile/Data``
    — the same name LibreOffice stamps on a human-typed comment.

    Best-effort: a stripped or headless build may not expose the
    configuration service. We surface the failure via ``logger.warning``
    (not silently) and fall back to ``""`` so the caller can use a plain
    ``"Talk2View"`` author rather than failing the whole comment.
    """
    import uno  # type: ignore[import-not-found]

    try:
        provider = ctx.ServiceManager.createInstanceWithContext(
            "com.sun.star.configuration.ConfigurationProvider", ctx
        )
        arg = uno.createUnoStruct("com.sun.star.beans.PropertyValue")
        arg.Name = "nodepath"
        arg.Value = "/org.openoffice.UserProfile/Data"
        access = provider.createInstanceWithArguments(
            "com.sun.star.configuration.ConfigurationAccess", (arg,)
        )
        given = (access.getByName("givenname") or "").strip()
        surname = (access.getByName("sn") or "").strip()
    except Exception as exc:
        logger.warning(
            "Could not read LibreOffice user-profile name (%s); "
            "stamping comments as plain 'Talk2View'.",
            exc,
        )
        return ""
    return f"{given} {surname}".strip()


def _comment_author(ctx: Any) -> str:
    """Author string for AI-created comments (ADR-0037).

    ``"Talk2View on behalf of <user>"`` so the comment is attributable to
    the assistant yet tied to the human driving the session. Falls back
    to plain ``"Talk2View"`` when the LibreOffice user name is unknown.
    """
    name = _lo_user_full_name(ctx)
    return f"Talk2View on behalf of {name}" if name else "Talk2View"


def _now_uno_datetime() -> Any:
    """Build a ``com.sun.star.util.DateTime`` for the current local time."""
    import uno  # type: ignore[import-not-found]

    now = datetime.now()
    dt = uno.createUnoStruct("com.sun.star.util.DateTime")
    dt.Year = now.year
    dt.Month = now.month
    dt.Day = now.day
    dt.Hours = now.hour
    dt.Minutes = now.minute
    dt.Seconds = now.second
    if hasattr(dt, "NanoSeconds"):
        dt.NanoSeconds = now.microsecond * 1000
    if hasattr(dt, "IsUTC"):
        dt.IsUTC = False
    return dt


def _stamp_authorship(ctx: Any, annotation: Any) -> None:
    """Populate Author / Initials / DateTimeValue on a new annotation.

    Without this, comments created through the UNO API show a blank
    author and no date (Investigation #46 / ADR-0037).
    """
    annotation.Author = _comment_author(ctx)
    annotation.Initials = "T2V"
    if hasattr(annotation, "DateTimeValue"):
        annotation.DateTimeValue = _now_uno_datetime()


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


def _anchor_comment(text_obj: Any, target_range: Any, annotation: Any) -> None:
    """Attach ``annotation`` at the start of ``target_range`` (point anchor).

    Collapses a cursor to the START of the matched range and inserts the
    annotation with ``bAbsorb=False``.

    Why NOT range-absorb (``insertTextContent(target_range, annotation,
    True)``), which would highlight the whole range the way Word does:
    that form raises ``com.sun.star.uno.RuntimeException: no SwTextAttr
    inserted?`` (``sw/source/core/unocore/unofield.cxx``) on LibreOffice
    24.x AND 26.x — reproduced for both unique and repeated anchors, so
    it is not anchor-specific but a universal defect on current builds
    (Investigation #38). Worse, the failed range-absorb call STILL leaves
    an orphaned annotation in the document, and ``removeTextContent`` does
    not reliably remove it — so the model, seeing the error, retries and
    the orphan plus the retry produce duplicate comments.

    A collapsed point anchor sidesteps the C++ defect entirely: it never
    raises and creates exactly one annotation per call. The only cosmetic
    difference is the comment marks a point at the start of the anchor
    text rather than highlighting the span — and range-absorb does not
    work at all on current builds, so this is strictly better.

    Raises:
        Exception: Propagates any UNO error from ``insertTextContent`` so
            the caller can surface it. Point-anchor insertion has not been
            observed to fail on LO 24.x/26.x, so this is a defensive path.
    """
    cursor = text_obj.createTextCursorByRange(target_range.getStart())
    text_obj.insertTextContent(cursor, annotation, False)


def _structured_error_for_known_lo_bug(exc: Exception, anchor: str) -> str | None:
    """Translate a residual LO insertion failure into a structured JSON error.

    The primary path (:func:`_anchor_comment`, point anchor) does not hit
    the ``no SwTextAttr inserted?`` defect that range-absorb did
    (Investigation #38). This remains a defensive net: if some build still
    rejects the point-anchor insert with that C++ ``RuntimeException``
    (which is not a Python-typed class we can catch precisely, so we match
    on the message text), surface a clear error instead of crashing the
    bridge.

    Returns:
        A JSON-encoded error string if exc matches the known LO bug
        signature (caller should return it); ``None`` if exc is
        something else (caller should re-raise).
    """
    if "SwTextAttr" not in str(exc):
        return None
    logger.warning(
        "add_comment: insertTextContent raised SwTextAttr error for "
        "anchor=%r (LO bug — Investigation #38)",
        preview(anchor, 60),
    )
    return json.dumps(
        {
            "error": (
                f'LibreOffice could not attach a comment at "{preview(anchor, 60)}". '
                "This is a known LO bug for anchors near certain text "
                "containers (Investigation #38: SwTextAttr insertion failure)."
            ),
            "recovery": (
                "Try a different anchor — pick 5-15 unique words from the "
                "middle of a body sentence (not in a header, footer, or "
                "table)."
            ),
        }
    )


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
    _stamp_authorship(ext.ctx, annotation)

    text_obj = target_range.getText()
    # Point-anchor at the start of the match (see _anchor_comment for why
    # NOT range-absorb — the range form is broken on LO 24.x/26.x and
    # leaves orphans that the model duplicates; Investigation #38).
    try:
        _anchor_comment(text_obj, target_range, annotation)
    except Exception as exc:
        handled = _structured_error_for_known_lo_bug(exc, anchor)
        if handled is not None:
            return handled
        raise

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
    # Case-insensitive enum arg (schema enum dropped — see Writer #5).
    action = lower_enum(action) or ""

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
        _insert_reply(ext.ctx, doc, target, text)
        detail = f'Reply added: "{text_preview}"'

    elif action == "resolve_with_reply":
        assert isinstance(text, str)
        _insert_reply(ext.ctx, doc, target, text)
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


def _insert_reply(ctx: Any, doc: Any, parent: Any, content: str) -> None:
    """Insert a reply annotation anchored to the parent's range.

    LibreOffice ≥ 7.4 supports the reply chain via the ``ParentName``
    property. We set ``ParentName`` to the parent's ``Name`` so the
    Writer UI groups them. On older builds the reply just shows up as
    a standalone annotation — flagged in Investigation #23.
    """
    reply = doc.createInstance("com.sun.star.text.TextField.Annotation")
    reply.Content = content
    _stamp_authorship(ctx, reply)

    # Use _annotation_id (not raw getattr) so the parent gets a stable,
    # persisted Name if it lacked one — otherwise ParentName would be set
    # to "" and the reply would NOT nest under the parent in the Writer UI
    # (the parent's Name is empty for API-created comments on LO 24.x/26.x;
    # Investigation #66 / #23).
    parent_name = _annotation_id(parent)
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
