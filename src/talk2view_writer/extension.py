"""Process-wide singleton for the Talk2View-Writer extension.

LibreOffice instantiates a new UNO job/factory object per command, so any
state we want to persist (auth tokens, the SDK client, open sidebar panels)
lives at module level here and is fetched via :func:`get_extension`.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from com.sun.star.awt import XWindow
    from com.sun.star.uno import XComponentContext
    from talk2view.types import User

    from talk2view_writer.sdk_client import Talk2ViewSDKClient
    from talk2view_writer.ui.sidebar_panel import Talk2ViewPanel
    from talk2view_writer.ui_thread import UIThreadDispatcher

logger = logging.getLogger(__name__)


class Talk2ViewWriterExtension:
    """Holds long-lived state for the extension across UNO invocations.

    Owns:

    - the :class:`Talk2ViewSDKClient` (lazy-init on first ``sdk`` access),
    - the list of open sidebar panels,
    - the auth-state listener that broadcasts login/logout to panels.
    """

    def __init__(self, ctx: XComponentContext) -> None:
        self.ctx = ctx
        self._lock = threading.Lock()
        self._sdk: Talk2ViewSDKClient | None = None
        self._ui_thread: UIThreadDispatcher | None = None
        self._tools_registered = False
        self._open_panels: list[Talk2ViewPanel] = []

    # ------------------------------------------------------------------
    # UI-thread dispatcher (lazy, owned at extension lifetime)
    # ------------------------------------------------------------------

    @property
    def ui_thread(self) -> UIThreadDispatcher:
        """Lazily-instantiated :class:`UIThreadDispatcher`.

        Used by every tool implementation (via the ``ui_thread_tool``
        decorator) and by the sidebar panel when streaming chat events
        from a worker thread.
        """
        with self._lock:
            if self._ui_thread is None:
                from talk2view_writer.ui_thread import UIThreadDispatcher

                self._ui_thread = UIThreadDispatcher(self.ctx)
                logger.info("UIThreadDispatcher instantiated")
            return self._ui_thread

    # ------------------------------------------------------------------
    # SDK lifecycle
    # ------------------------------------------------------------------

    @property
    def sdk(self) -> Talk2ViewSDKClient:
        """Lazily-instantiated Talk2View SDK wrapper.

        On first access this also registers every tool from
        :mod:`talk2view_writer.tools` with the SDK so the agent sees
        the full tool surface immediately.
        """
        with self._lock:
            if self._sdk is None:
                # Pre-flight: make the bundled pydantic_core wheel matching
                # the runtime Python + platform importable. See ADR-0023.
                from talk2view_writer._wheel_loader import (
                    ensure_vendored_pydantic_core,
                )

                ensure_vendored_pydantic_core()

                from talk2view_writer.sdk_client import Talk2ViewSDKClient

                self._sdk = Talk2ViewSDKClient()
                self._sdk.add_auth_listener(self._on_auth_changed)
                logger.info("SDK client instantiated")
            # Register tools once per process. Done under the same lock so
            # concurrent first-accessors don't double-register.
            if not self._tools_registered:
                from talk2view_writer.tools import all_tools

                tools = all_tools()
                self._sdk.register_tools(tools)
                self._tools_registered = True
                logger.info(
                    "Registered %d tool(s) with SDK: %s",
                    len(tools),
                    [getattr(t, "__name__", repr(t)) for t in tools],
                )
            return self._sdk

    # ------------------------------------------------------------------
    # Menu command handlers (called by Talk2ViewJob.trigger)
    # ------------------------------------------------------------------

    def show_sidebar(self) -> None:
        """Open the Talk2View sidebar deck in the current Writer window."""
        logger.info("show_sidebar")
        desktop = self.ctx.ServiceManager.createInstanceWithContext(
            "com.sun.star.frame.Desktop", self.ctx
        )
        frame = desktop.getCurrentFrame()
        if frame is None:
            raise RuntimeError("No active Writer window")

        dispatcher = self.ctx.ServiceManager.createInstanceWithContext(
            "com.sun.star.frame.DispatchHelper", self.ctx
        )
        from com.sun.star.beans import PropertyValue  # type: ignore[import-not-found]

        prop = PropertyValue()
        prop.Name = "Sidebar"
        prop.Value = "com.talk2view.writer.Deck"
        dispatcher.executeDispatch(frame, ".uno:SidebarDeck", "_self", 0, (prop,))

    def show_login_dialog(self, parent_window: XWindow | None = None) -> None:
        """Prompt for credentials and call ``sdk.login``."""
        logger.info("show_login_dialog")
        from talk2view_writer.ui.login_dialog import show_login_dialog

        creds = show_login_dialog(self.ctx, parent_window=parent_window)
        if creds is None:
            logger.info("Login cancelled by user")
            return
        email, password = creds
        # SDK errors propagate to the caller (Talk2ViewJob.trigger), which
        # surfaces them via an ERRORBOX. We deliberately do not catch
        # AuthenticationError / NetworkError here — see ADR-0014.
        user = self.sdk.login(email, password)
        logger.info("Login succeeded for %s", user.email)

    def logout(self) -> None:
        """Clear local + server-side session."""
        logger.info("logout")
        self.sdk.logout()

    def show_settings_dialog(self, parent_window: XWindow | None = None) -> None:
        """Open the (read-only) Talk2View settings status panel."""
        logger.info("show_settings_dialog")
        from talk2view_writer.ui.settings_dialog import show_settings_dialog

        show_settings_dialog(self.ctx, self.sdk, parent_window=parent_window)

    # ------------------------------------------------------------------
    # Panel lifecycle (called by ChatPanelFactory.createUIElement)
    # ------------------------------------------------------------------

    def register_panel(self, panel: Talk2ViewPanel) -> None:
        """Track an open sidebar panel and push the current auth state to it."""
        with self._lock:
            self._open_panels.append(panel)
        # Push the current auth state to the new panel so its initial
        # render reflects login status without waiting for a transition.
        user = self.sdk.current_user if self._sdk is not None else None
        try:
            panel.on_auth_changed(user)
        except Exception:
            logger.exception("Initial on_auth_changed failed for new panel")
        logger.info("Panel registered (total open: %d)", len(self._open_panels))

    def unregister_panel(self, panel: Talk2ViewPanel) -> None:
        """Remove a closed sidebar panel from the tracking list."""
        with self._lock:
            if panel in self._open_panels:
                self._open_panels.remove(panel)
        logger.info("Panel unregistered (total open: %d)", len(self._open_panels))

    # ------------------------------------------------------------------
    # Internal: auth state fan-out
    # ------------------------------------------------------------------

    def _on_auth_changed(self, user: User | None) -> None:
        """SDK auth-state callback — broadcast to all open panels."""
        with self._lock:
            panels = list(self._open_panels)
        logger.info(
            "Auth changed: user=%s; notifying %d panel(s)",
            user.email if user else None,
            len(panels),
        )
        for panel in panels:
            try:
                panel.on_auth_changed(user)
            except Exception:
                logger.exception("Panel on_auth_changed raised")


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
    of their own — tool implementations and the UI-thread dispatcher
    callbacks. By the time a tool fires the singleton must already exist
    (the sidebar panel that triggered the chat created it), so a missing
    instance indicates a real bug rather than a recoverable state.

    Raises:
        RuntimeError: If :func:`get_extension` has not yet been called.
    """
    with _INSTANCE_LOCK:
        if _INSTANCE is None:
            raise RuntimeError(
                "Talk2ViewWriterExtension is not initialised — "
                "get_extension(ctx) must be called first"
            )
        return _INSTANCE
