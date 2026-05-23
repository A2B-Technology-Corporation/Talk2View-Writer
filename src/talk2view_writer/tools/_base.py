"""Shared helpers for Talk2View-Writer tool implementations.

- ``ui_thread_tool``: decorator that marshals a tool function body onto
  the LibreOffice UI thread before executing. Combine with the SDK's
  ``@tool`` decorator: ``@tool`` outermost (so it introspects the
  preserved signature), ``@ui_thread_tool`` innermost.
- ``get_writer_document``: fetch the currently-active Writer document.
  Call from UI-thread context only — use ``ui_thread_tool`` to
  guarantee that.
- ``WriterDocumentRequiredError``: raised when no Writer document is active
  (e.g. user has only a Calc spreadsheet open). Tool wrappers catch
  this and return a structured error message the agent can interpret.

Track-changes envelope (ADR-0035): every mutating AI tool call runs
inside a save → enable RecordChanges → run → restore pair so AI edits
land as redlines the user can review without changing the document's
persistent track-changes setting. Gated by the
``ai_track_changes_enabled`` preference (default True).
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from com.sun.star.text import XTextDocument
    from com.sun.star.uno import XComponentContext

logger = logging.getLogger(__name__)

_F = TypeVar("_F", bound=Callable[..., Any])


class WriterDocumentRequiredError(RuntimeError):
    """Raised when a tool needs an active Writer document but none is open."""


# Tools whose bodies mutate the document. The track-changes envelope is
# only applied for these — read-only tools (get_document, get_selection)
# and state-restoring tools (undo_redo) skip the wrap so the user's
# Undo of an AI change reverts to the pre-AI state rather than logging
# the revert itself as a tracked insertion.
_MUTATING_TOOL_NAMES: frozenset[str] = frozenset({
    "insert_content",
    "insert_table",
    "insert_image",
    "delete_content",
    "edit_table",
    "format_text",
    "format_paragraph",
    "manage_list",
    "search_document",
    "insert_break",
    "set_header_footer",
    "insert_page_numbers",
    "set_page_setup",
    "add_comment",
    "manage_comment",
})


def ui_thread_tool(fn: _F) -> _F:
    """Marshal a tool body onto LibreOffice's UI thread.

    Use this on tool functions that touch UNO document state. The
    wrapped function's signature is preserved via :func:`functools.wraps`,
    so the SDK's schema introspection still sees the original signature.

    Decorator stacking (outermost → innermost):

        @tool
        @ui_thread_tool
        def insert_content(content: str) -> str:
            ...

    The body of ``insert_content`` runs on the UI thread; everything
    else runs on the SDK worker thread.

    Raises:
        RuntimeError: If called before the extension singleton has been
            initialised. Should never happen in normal flow because
            tools are only invoked while the chat panel is active.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        import time

        from talk2view_writer.extension import get_extension_or_raise
        from talk2view_writer.preferences import (
            PREF_AI_TRACK_CHANGES,
            get_preferences,
        )

        ext = get_extension_or_raise()
        tool_name = getattr(fn, "__name__", "<unknown>")
        # Summarise args without dumping potentially-huge payloads:
        # show types + lengths for strings/lists; full repr otherwise.
        def _summary(v: Any) -> str:
            if isinstance(v, str):
                return f"str(len={len(v)})"
            if isinstance(v, (list, tuple, dict)):
                return f"{type(v).__name__}(len={len(v)})"
            return repr(v)[:60]

        arg_summary = [_summary(a) for a in args]
        kwarg_summary = {k: _summary(v) for k, v in kwargs.items()}
        logger.info(
            "tool %s called: args=%s kwargs=%s",
            tool_name,
            arg_summary,
            kwarg_summary,
        )
        # Decide whether to wrap this call in the track-changes
        # envelope. Read the preference before marshalling so a
        # disabled toggle skips the UI-thread overhead of resolving
        # the document.
        track_changes = (
            tool_name in _MUTATING_TOOL_NAMES
            and bool(get_preferences().get(PREF_AI_TRACK_CHANGES))
        )
        start = time.monotonic()
        if track_changes:
            result = ext.ui_thread.run_sync(
                _run_with_track_changes, fn, ext.ctx, args, kwargs
            )
        else:
            result = ext.ui_thread.run_sync(fn, *args, **kwargs)
        elapsed_ms = (time.monotonic() - start) * 1000
        result_summary = _summary(result)
        logger.info(
            "tool %s returned in %.1fms: %s",
            tool_name,
            elapsed_ms,
            result_summary,
        )
        return result

    return wrapper  # type: ignore[return-value]


def _run_with_track_changes(
    fn: Callable[..., Any],
    ctx: XComponentContext,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    """Run ``fn(*args, **kwargs)`` with document redlining temporarily on.

    Must be invoked on the LibreOffice UI thread — UNO property access
    is not thread-safe.

    The Writer model's ``RecordChanges`` property is the global "Track
    Changes" toggle the user sees under Edit -> Track Changes -> Record.
    We save its current value, force it on, run the tool body, and
    restore the original value in a ``finally`` block so a tool raising
    mid-edit doesn't leave the document in a different track-changes
    mode than the user started with.

    If no Writer document is currently active (e.g. only a Calc
    spreadsheet is open) we skip the wrap and call the tool directly
    — the tool itself will raise :class:`WriterDocumentRequiredError`
    with a clearer message than anything we could synthesise here.
    """
    try:
        doc = get_writer_document(ctx)
    except WriterDocumentRequiredError:
        return fn(*args, **kwargs)
    try:
        prior = doc.getPropertyValue("RecordChanges")
    except Exception:
        # Some embeddings (older LO builds, headless contexts) refuse
        # to read the property. Log the full traceback and skip the
        # wrap — failing the whole tool call over redlining would be
        # worse than not redlining.
        logger.exception(
            "Could not read RecordChanges; skipping track-changes wrap"
        )
        return fn(*args, **kwargs)
    try:
        doc.setPropertyValue("RecordChanges", True)
    except Exception:
        logger.exception(
            "Could not enable RecordChanges; skipping track-changes wrap"
        )
        return fn(*args, **kwargs)
    try:
        return fn(*args, **kwargs)
    finally:
        try:
            doc.setPropertyValue("RecordChanges", bool(prior))
        except Exception:
            logger.exception(
                "Could not restore RecordChanges to %r — document is "
                "now in track-changes=True even though the user's prior "
                "setting was different",
                prior,
            )


def get_writer_document(ctx: XComponentContext) -> XTextDocument:
    """Return the currently-active Writer document.

    Call from the UI thread only.

    Raises:
        WriterDocumentRequiredError: If no document is active or the active
            document is not a Writer text document.
    """
    desktop = ctx.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
    component = desktop.getCurrentComponent()
    if component is None:
        raise WriterDocumentRequiredError("No document is currently open")
    if not hasattr(component, "supportsService") or not component.supportsService(
        "com.sun.star.text.TextDocument"
    ):
        raise WriterDocumentRequiredError("The active document is not a Writer text document")
    return component  # type: ignore[no-any-return]
