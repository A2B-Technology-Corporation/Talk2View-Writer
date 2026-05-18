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
        start = time.monotonic()
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
