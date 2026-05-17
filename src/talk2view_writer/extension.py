"""Process-wide singleton for the Talk2View-Writer extension.

LibreOffice instantiates a new UNO job/factory object per command, so any
state we want to persist (auth tokens, the SDK client, open sidebar panels)
lives at module level here and is fetched via :func:`get_extension`.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from com.sun.star.awt import XWindow
    from com.sun.star.uno import XComponentContext

    from talk2view.types import User

    from talk2view_writer.sdk_client import Talk2ViewSDKClient
    from talk2view_writer.ui.sidebar_panel import Talk2ViewPanel

logger = logging.getLogger(__name__)


class Talk2ViewWriterExtension:
    """Holds long-lived state for the extension across UNO invocations.

    Owns:

    - the :class:`Talk2ViewSDKClient` (lazy-init on first ``sdk`` access),
    - the list of open sidebar panels,
    - the auth-state listener that broadcasts login/logout to panels.
    """

    def __init__(self, ctx: "XComponentContext") -> None:
        self.ctx = ctx
        self._lock = threading.Lock()
        self._sdk: Optional["Talk2ViewSDKClient"] = None
        self._open_panels: List["Talk2ViewPanel"] = []

    # ------------------------------------------------------------------
    # SDK lifecycle
    # ------------------------------------------------------------------

    @property
    def sdk(self) -> "Talk2ViewSDKClient":
        """Lazily-instantiated Talk2View SDK wrapper."""
        with self._lock:
            if self._sdk is None:
                from talk2view_writer.sdk_client import Talk2ViewSDKClient

                self._sdk = Talk2ViewSDKClient()
                self._sdk.add_auth_listener(self._on_auth_changed)
                logger.info("SDK client instantiated")
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
        dispatcher.executeDispatch(
            frame, ".uno:SidebarDeck", "_self", 0, (prop,)
        )

    def show_login_dialog(
        self, parent_window: "Optional[XWindow]" = None
    ) -> None:
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

    def show_settings_dialog(self) -> None:
        """Phase F: model picker, partner key override, etc."""
        logger.info("show_settings_dialog (stub)")
        raise NotImplementedError("Settings not yet implemented — Phase F")

    # ------------------------------------------------------------------
    # Panel lifecycle (called by ChatPanelFactory.createUIElement)
    # ------------------------------------------------------------------

    def register_panel(self, panel: "Talk2ViewPanel") -> None:
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

    def unregister_panel(self, panel: "Talk2ViewPanel") -> None:
        with self._lock:
            if panel in self._open_panels:
                self._open_panels.remove(panel)
        logger.info("Panel unregistered (total open: %d)", len(self._open_panels))

    # ------------------------------------------------------------------
    # Internal: auth state fan-out
    # ------------------------------------------------------------------

    def _on_auth_changed(self, user: "Optional[User]") -> None:
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


_INSTANCE: Optional[Talk2ViewWriterExtension] = None
_INSTANCE_LOCK = threading.Lock()


def get_extension(ctx: "XComponentContext") -> Talk2ViewWriterExtension:
    """Return the process-wide :class:`Talk2ViewWriterExtension` singleton."""
    global _INSTANCE
    with _INSTANCE_LOCK:
        if _INSTANCE is None:
            _INSTANCE = Talk2ViewWriterExtension(ctx)
        return _INSTANCE
