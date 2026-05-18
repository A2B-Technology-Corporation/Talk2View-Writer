"""Talk2View-Writer UNO entry point.

Two UNO components are registered:

1. ``Talk2ViewJob`` (``com.sun.star.task.Job``) — handles menu commands
   from ``Addons.xcu`` (Login, Logout, Settings, Show Panel).
2. ``ChatPanelFactory`` (``com.sun.star.ui.UIElementFactory``) — called by
   LibreOffice when the Talk2View sidebar deck is opened. Returns the
   ``Talk2ViewPanel`` UI element that contains the chat widgets.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

# Make bundled pythonpath visible before any talk2view_writer imports.
_EXT_DIR = Path(__file__).parent
_PYTHONPATH = _EXT_DIR / "pythonpath"
if _PYTHONPATH.exists() and str(_PYTHONPATH) not in sys.path:
    sys.path.insert(0, str(_PYTHONPATH))

import uno  # noqa: E402
import unohelper  # noqa: E402
from com.sun.star.task import XJobExecutor  # noqa: E402
from com.sun.star.ui import XUIElementFactory  # noqa: E402

# Bootstrap the persistent rotating log file as early as possible:
# every other module logged via `getLogger(__name__)` inherits the
# handlers wired up here. See src/talk2view_writer/_logging.py.
from talk2view_writer._logging import setup_logging  # noqa: E402

_LOG_PATH = setup_logging()

if TYPE_CHECKING:
    from com.sun.star.awt import XWindow
    from com.sun.star.beans import PropertyValue
    from com.sun.star.frame import XFrame
    from com.sun.star.ui import XUIElement
    from com.sun.star.uno import XComponentContext

# Logger named after the UNO entry — distinct from "talk2view_writer.*"
# so log filtering can target host-shim vs package code separately.
logger = logging.getLogger("talk2view_writer.uno_entry")
logger.info(
    "UNO entry module loaded. pythonpath=%s log_file=%s",
    _PYTHONPATH,
    _LOG_PATH,
)


# ---------------------------------------------------------------------------
# Menu command handler
# ---------------------------------------------------------------------------


class Talk2ViewJob(unohelper.Base, XJobExecutor):
    """UNO job executor for Talk2View menu commands.

    LibreOffice invokes ``trigger(args)`` with the command name from the
    Addons.xcu URL (``service:...?<command>``).
    """

    def __init__(self, ctx: "XComponentContext") -> None:
        self.ctx = ctx
        logger.info(
            "Talk2ViewJob constructed (ctx=%r) — menu commands now wired",
            ctx,
        )

    def trigger(self, args: str) -> None:
        """Dispatch a menu command.

        Args:
            args: Command name from ``Addons.xcu`` URL parameter.

        Raises:
            ValueError: If the command name is not recognised. The error is
                surfaced via a UNO message box so the user sees it.
        """
        logger.info("Talk2ViewJob.trigger: %s", args)
        try:
            from talk2view_writer.extension import get_extension

            ext = get_extension(self.ctx)
            if args == "showPanel":
                ext.show_sidebar()
            elif args == "login":
                ext.show_login_dialog()
            elif args == "logout":
                ext.logout()
            elif args == "settings":
                ext.show_settings_dialog()
            else:
                raise ValueError(f"Unknown command: {args}")
        except Exception as exc:  # surfaced to user via dialog — see _show_error
            logger.exception("Command '%s' failed", args)
            self._show_error("Talk2View", str(exc))

    def _show_error(self, title: str, message: str) -> None:
        desktop = self.ctx.ServiceManager.createInstanceWithContext(
            "com.sun.star.frame.Desktop", self.ctx
        )
        frame = desktop.getCurrentFrame()
        if frame is None:
            return
        window = frame.getContainerWindow()
        toolkit = window.getToolkit()
        msgbox = toolkit.createMessageBox(
            window,
            uno.Enum("com.sun.star.awt.MessageBoxType", "ERRORBOX"),
            1,  # OK button
            title,
            message,
        )
        msgbox.execute()


# ---------------------------------------------------------------------------
# Sidebar panel factory
# ---------------------------------------------------------------------------


class ChatPanelFactory(unohelper.Base, XUIElementFactory):
    """Factory that builds the Talk2View sidebar panel on demand.

    Sidebar.xcu declares this implementation under
    ``ImplementationURL = private:resource/toolpanel/com.talk2view.writer.ChatPanelFactory/Chat``.
    When the user opens the Talk2View deck, LibreOffice calls
    ``createUIElement(resource_url, args)`` and we return an XUIElement
    wrapping a panel built in ``talk2view_writer.ui.sidebar_panel``.
    """

    def __init__(self, ctx: "XComponentContext") -> None:
        self.ctx = ctx
        logger.info(
            "ChatPanelFactory constructed (ctx=%r) — sidebar deck registered, "
            "createUIElement will fire when user opens the Talk2View tab",
            ctx,
        )

    def createUIElement(  # noqa: N802 — UNO interface naming
        self,
        resource_url: str,
        args: "tuple[PropertyValue, ...]",
    ) -> "XUIElement":
        """Build the sidebar XUIElement.

        Args:
            resource_url: The ``private:resource/toolpanel/...`` URL.
            args: PropertyValues including ``ParentWindow`` (XWindow) and
                ``Frame`` (XFrame) supplied by LibreOffice.

        Returns:
            An ``XUIElement`` whose ``getRealInterface()`` is the
            ``Talk2ViewPanel`` UNO panel object.

        Raises:
            RuntimeError: If ParentWindow PropertyValue is missing or
                if ``build_chat_panel`` fails to construct the panel.
                Both paths are logged with full traceback so a
                broken sidebar leaves diagnostic evidence behind.
        """
        # LibreOffice silently absorbs exceptions thrown out of
        # createUIElement — the sidebar slot just stays empty with
        # no UI feedback. Wrap everything in a try/except that logs
        # the full traceback so "panel doesn't appear" bug reports
        # are actually diagnosable from the log file.
        logger.info(
            "createUIElement called: resource_url=%s arg_count=%d arg_names=%s",
            resource_url,
            len(args),
            [getattr(p, "Name", "?") for p in args],
        )
        try:
            parent_window: "XWindow | None" = None
            frame: "XFrame | None" = None
            for prop in args:
                if prop.Name == "ParentWindow":
                    parent_window = prop.Value
                elif prop.Name == "Frame":
                    frame = prop.Value
            logger.debug(
                "createUIElement: parent_window=%r frame=%r",
                parent_window,
                frame,
            )
            if parent_window is None:
                raise RuntimeError(
                    "Talk2View sidebar: ParentWindow PropertyValue not supplied "
                    "by LibreOffice — cannot build panel"
                )

            from talk2view_writer.ui.sidebar_panel import build_chat_panel

            panel = build_chat_panel(self.ctx, parent_window, frame, resource_url)
            logger.info(
                "createUIElement returned XUIElement type=%s",
                type(panel).__name__,
            )
            return panel
        except Exception:
            logger.exception(
                "createUIElement FAILED for resource_url=%s — sidebar will be empty",
                resource_url,
            )
            raise


# ---------------------------------------------------------------------------
# UNO component registration
# ---------------------------------------------------------------------------


g_ImplementationHelper = unohelper.ImplementationHelper()  # noqa: N816 — UNO convention
g_ImplementationHelper.addImplementation(
    Talk2ViewJob,
    "com.talk2view.writer.Talk2ViewJob",
    ("com.sun.star.task.Job",),
)
g_ImplementationHelper.addImplementation(
    ChatPanelFactory,
    "com.talk2view.writer.ChatPanelFactory",
    ("com.sun.star.ui.UIElementFactory",),
)
