"""Process-wide singleton for the Talk2View-Writer extension.

LibreOffice instantiates a new UNO job/factory object per command, so any
state we want to persist (auth tokens, the SDK client, open sidebar panels)
lives at module level here and is fetched via :func:`get_extension`.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from com.sun.star.uno import XComponentContext

logger = logging.getLogger(__name__)


class Talk2ViewWriterExtension:
    """Holds long-lived state for the extension across UNO invocations."""

    def __init__(self, ctx: "XComponentContext") -> None:
        self.ctx = ctx
        self._lock = threading.Lock()
        self._sdk_client = None  # populated in Phase B
        self._open_panels: list = []  # ChatPanel instances; weak refs in Phase B

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

        # Activate the Talk2View deck via the standard sidebar dispatcher.
        # The URL ".uno:Sidebar" toggles it; ShowDeck switches to a specific deck.
        dispatcher = self.ctx.ServiceManager.createInstanceWithContext(
            "com.sun.star.frame.DispatchHelper", self.ctx
        )
        import uno
        from com.sun.star.beans import PropertyValue

        prop = PropertyValue()
        prop.Name = "Sidebar"
        prop.Value = "com.talk2view.writer.Deck"
        dispatcher.executeDispatch(
            frame, ".uno:SidebarDeck", "_self", 0, (prop,)
        )
        _ = uno  # silence unused-import linter for now

    def show_login_dialog(self) -> None:
        """Phase B: prompt for email/password and call ``t2v.auth.login``."""
        logger.info("show_login_dialog (stub)")
        raise NotImplementedError("Login dialog not yet implemented — Phase B")

    def logout(self) -> None:
        """Phase B: clear stored tokens and reset the SDK client."""
        logger.info("logout (stub)")
        raise NotImplementedError("Logout not yet implemented — Phase B")

    def show_settings_dialog(self) -> None:
        """Phase F: model picker, partner key override, etc."""
        logger.info("show_settings_dialog (stub)")
        raise NotImplementedError("Settings not yet implemented — Phase F")

    # ------------------------------------------------------------------
    # Panel lifecycle (called by ChatPanelFactory.createUIElement)
    # ------------------------------------------------------------------

    def register_panel(self, panel: object) -> None:
        """Track an open sidebar panel so we can broadcast events to it."""
        with self._lock:
            self._open_panels.append(panel)
        logger.info("Panel registered (total open: %d)", len(self._open_panels))

    def unregister_panel(self, panel: object) -> None:
        """Remove a closed panel from the tracking list."""
        with self._lock:
            if panel in self._open_panels:
                self._open_panels.remove(panel)
        logger.info("Panel unregistered (total open: %d)", len(self._open_panels))


_INSTANCE: Optional[Talk2ViewWriterExtension] = None
_INSTANCE_LOCK = threading.Lock()


def get_extension(ctx: "XComponentContext") -> Talk2ViewWriterExtension:
    """Return the process-wide :class:`Talk2ViewWriterExtension` singleton."""
    global _INSTANCE
    with _INSTANCE_LOCK:
        if _INSTANCE is None:
            _INSTANCE = Talk2ViewWriterExtension(ctx)
        return _INSTANCE
