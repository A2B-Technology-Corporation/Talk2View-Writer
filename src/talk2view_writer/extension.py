"""Process-wide singleton for the Talk2View-Writer extension.

LibreOffice instantiates a new UNO job/factory object per command, so any
state we want to persist (the chat window, the UI-thread dispatcher) lives
at module level here and is fetched via :func:`get_extension`.

ADR-0030 moved chat + auth + settings into the pywebview React app, so
the Python side is now a thin shell: it owns the UI-thread dispatcher
for the tools and the singleton chat-window handle.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from com.sun.star.uno import XComponentContext

    from talk2view_writer.ui.web_window import WebWindow
    from talk2view_writer.ui_thread import UIThreadDispatcher

logger = logging.getLogger(__name__)


class Talk2ViewWriterExtension:
    """Holds long-lived state for the extension across UNO invocations.

    Owns:

    - the :class:`UIThreadDispatcher` used by every tool (lazy-init),
    - the singleton :class:`WebWindow` (per ADR-0030 — one chat
      window per process).
    """

    def __init__(self, ctx: XComponentContext) -> None:
        self.ctx = ctx
        self._lock = threading.Lock()
        self._ui_thread: UIThreadDispatcher | None = None
        self._chat_window: WebWindow | None = None
        # NOTE: render ctx via repr() at the call site rather than passing
        # the UNO proxy through %r. Python logging's fast path does
        # `isinstance(args[0], Mapping)` when there's a single positional
        # arg, which crashes on UNO proxies whose synthetic __class__
        # isn't a real Python class. Always stringify UNO objects before
        # logging them.
        logger.info(
            "Talk2ViewWriterExtension singleton created (ctx=%s). "
            "Lazy sub-systems (UIThreadDispatcher) initialise on first access.",
            repr(ctx),
        )

    # ------------------------------------------------------------------
    # UI-thread dispatcher (lazy, owned at extension lifetime)
    # ------------------------------------------------------------------

    @property
    def ui_thread(self) -> UIThreadDispatcher:
        """Lazily-instantiated :class:`UIThreadDispatcher`.

        Used by every tool implementation (via the ``ui_thread_tool``
        decorator) to marshal UNO calls onto LO's UI thread.
        """
        with self._lock:
            if self._ui_thread is None:
                from talk2view_writer.ui_thread import UIThreadDispatcher

                self._ui_thread = UIThreadDispatcher(self.ctx)
                logger.info("UIThreadDispatcher instantiated")
            return self._ui_thread

    # ------------------------------------------------------------------
    # Menu command handlers (called by Talk2ViewProtocolHandler.dispatch)
    # ------------------------------------------------------------------

    def show_chat_window(self) -> None:
        """Open (or refocus) the singleton Talk2View chat window.

        Per ADR-0030 the chat UI is a pywebview React app that runs the
        Talk2View SDK directly in the browser, talking to the engine via
        a httpx proxy in the bridge. Auth, settings, and chat all live
        in that window — no UNO dialogs are involved beyond this entry
        point.
        """
        logger.info("show_chat_window invoked (menu command)")
        with self._lock:
            if self._chat_window is None:
                from talk2view_writer.ui.web_window import WebWindow

                self._chat_window = WebWindow(self.ctx)
        self._chat_window.show()
        logger.info("show_chat_window: complete")


_INSTANCE: Talk2ViewWriterExtension | None = None
_INSTANCE_LOCK = threading.Lock()


def get_extension(ctx: XComponentContext) -> Talk2ViewWriterExtension:
    """Return the process-wide :class:`Talk2ViewWriterExtension` singleton."""
    global _INSTANCE
    with _INSTANCE_LOCK:
        if _INSTANCE is None:
            _INSTANCE = Talk2ViewWriterExtension(ctx)
        return _INSTANCE


def get_extension_or_raise() -> Talk2ViewWriterExtension:
    """Return the singleton if it has been created; otherwise raise.

    Use this from contexts that cannot supply an :class:`XComponentContext`
    — most notably tool bodies invoked through the bridge. The bridge
    server's ``__init__`` already created the singleton (it receives the
    ctx), so by the time a tool runs the singleton always exists.
    """
    if _INSTANCE is None:
        raise RuntimeError(
            "Talk2ViewWriterExtension singleton has not been initialised yet. "
            "Tool bodies must run inside a UNO dispatch that has built the "
            "extension via get_extension(ctx)."
        )
    return _INSTANCE
