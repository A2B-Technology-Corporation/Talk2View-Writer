"""Talk2View-Writer UNO entry point.

Two UNO components are registered:

1. ``Talk2ViewProtocolHandler`` (``com.sun.star.frame.ProtocolHandler``)
   — handles menu commands from ``Addons.xcu`` via the custom
   ``vnd.com.talk2view.writer:<command>`` URL scheme. Implements
   ``XDispatchProvider`` + ``XDispatch``. Wired by ``ProtocolHandler.xcu``.
2. ``ChatPanelFactory`` (``com.sun.star.ui.UIElementFactory``) — called by
   LibreOffice when the Talk2View sidebar deck is opened. Returns the
   ``Talk2ViewPanel`` UI element that contains the chat widgets.

The original ``service:com.talk2view.writer.Talk2ViewJob?<cmd>`` URL
scheme + ``XJobExecutor`` registration was abandoned because modern
LibreOffice's ``service:`` URL dispatcher does not reliably resolve
custom Python implementation names — clicking menu items silently
failed with no log output. See git history of this file for the
removed Job-based code.
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

from typing import Any  # noqa: E402

import uno  # noqa: E402
import unohelper  # noqa: E402
from com.sun.star.frame import XDispatch, XDispatchProvider  # noqa: E402
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


def _safe_repr(obj: Any) -> str:
    """UNO-safe repr — same purpose as ``_ru`` in sidebar_panel.

    Inlined here so the UNO entry module doesn't need to import the
    sidebar panel at module load time (which would drag in the UNO
    awt/ui stubs and break ``test_extension_module_loads_without_uno``).
    """
    try:
        return repr(obj)
    except Exception as exc:
        return f"<repr failed: {type(exc).__name__}>"


# ---------------------------------------------------------------------------
# Menu command handler — ProtocolHandler dispatching the
# vnd.com.talk2view.writer: URL scheme
# ---------------------------------------------------------------------------


class Talk2ViewProtocolHandler(unohelper.Base, XDispatchProvider, XDispatch):
    """ProtocolHandler for ``vnd.com.talk2view.writer:<command>`` URLs.

    Implements both XDispatchProvider and XDispatch on the same class —
    when LibreOffice asks for a dispatcher for our URL scheme,
    ``queryDispatch`` returns ``self``; when the user clicks a menu
    item, ``dispatch`` is then called with the URL + property args.

    The command name lives in ``url.Path`` (the part after the
    ``vnd.com.talk2view.writer:`` scheme prefix), e.g. ``showPanel``.

    Wired via ``ProtocolHandler.xcu`` which maps the URL scheme to
    this implementation, and ``Addons.xcu`` which puts menu items
    pointing at the scheme.
    """

    # URL scheme this handler claims. Must match ProtocolHandler.xcu's
    # `<value>vnd.com.talk2view.writer:*</value>` entry, with a
    # trailing ":" because LibreOffice's URL parser includes it in
    # `url.Protocol`.
    URL_PROTOCOL: str = "vnd.com.talk2view.writer:"

    def __init__(self, ctx: "XComponentContext") -> None:
        self.ctx = ctx
        logger.info(
            "Talk2ViewProtocolHandler constructed (ctx=%r) — "
            "menu commands now wired via %s URL scheme",
            ctx,
            self.URL_PROTOCOL,
        )

    # ----- XDispatchProvider -----------------------------------------

    def queryDispatch(  # noqa: N802 — UNO interface naming
        self,
        url: object,  # com.sun.star.util.URL struct
        target_frame_name: str,
        search_flags: int,
    ) -> "XDispatch | None":
        """Return ``self`` if we own this URL scheme; else ``None``.

        LibreOffice calls this for every URL it tries to dispatch
        (menu, toolbar, keyboard shortcut). Returning ``None`` lets
        the next dispatch provider in the chain try.
        """
        protocol = getattr(url, "Protocol", "")
        if protocol == self.URL_PROTOCOL:
            logger.debug(
                "queryDispatch: claiming %s%s (frame=%s flags=%s)",
                protocol,
                getattr(url, "Path", ""),
                target_frame_name,
                search_flags,
            )
            return self
        return None

    def queryDispatches(  # noqa: N802 — UNO interface naming
        self,
        descriptors: "tuple[object, ...]",
    ) -> "tuple[XDispatch | None, ...]":
        """Batch form of queryDispatch — one entry per descriptor."""
        return tuple(
            self.queryDispatch(d.FeatureURL, d.FrameName, d.SearchFlags)
            for d in descriptors
        )

    # ----- XDispatch -------------------------------------------------

    def dispatch(  # noqa: N802 — UNO interface naming
        self,
        url: object,
        args: "tuple[PropertyValue, ...]",
    ) -> None:
        """Execute the command encoded in the URL.

        Args:
            url: ``com.sun.star.util.URL`` struct. ``url.Path`` is the
                command name (everything after the protocol).
            args: PropertyValues (unused — our commands don't accept
                parameters today, but the interface mandates the slot).

        Errors are caught + surfaced via an ERRORBOX so the user sees
        them. Without this, a thrown exception vanishes silently from
        the dispatcher (LibreOffice doesn't propagate it to any UI
        surface).
        """
        command = getattr(url, "Path", "")
        logger.info(
            "dispatch: command=%r url=%s%s args_count=%d",
            command,
            getattr(url, "Protocol", ""),
            command,
            len(args),
        )
        try:
            from talk2view_writer.extension import get_extension

            ext = get_extension(self.ctx)
            if command == "showPanel":
                ext.show_sidebar()
            elif command == "login":
                ext.show_login_dialog()
            elif command == "logout":
                ext.logout()
            elif command == "settings":
                ext.show_settings_dialog()
            else:
                raise ValueError(f"Unknown command: {command!r}")
            logger.info("dispatch: command=%r completed cleanly", command)
        except Exception as exc:
            logger.exception("dispatch: command %r failed", command)
            self._show_error("Talk2View", str(exc))

    def addStatusListener(  # noqa: N802 — UNO interface naming
        self,
        listener: object,
        url: object,
    ) -> None:
        """XDispatch: no-op — our commands have no status to broadcast.

        Required by the interface but irrelevant for menu commands
        that just fire-and-forget. A real implementation would
        notify listeners when (e.g.) the login command becomes
        unavailable after a successful login.
        """

    def removeStatusListener(  # noqa: N802 — UNO interface naming
        self,
        listener: object,
        url: object,
    ) -> None:
        """XDispatch: no-op (see addStatusListener)."""

    # ----- helpers ---------------------------------------------------

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
        # See note in extension.py — repr() the UNO proxy at the call
        # site to avoid Python logging's single-arg Mapping fast path,
        # which crashes on objects with a synthetic __class__.
        logger.info(
            "ChatPanelFactory constructed (ctx=%s) — sidebar deck registered, "
            "createUIElement will fire when user opens the Talk2View tab",
            repr(ctx),
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
            RuntimeError: If ParentWindow PropertyValue is missing.
        """
        arg_summary = [
            f"{getattr(p, 'Name', '?')}={_safe_repr(getattr(p, 'Value', None))}"
            for p in args
        ]
        logger.info(
            "createUIElement called: resource_url=%s arg_count=%d args=%s",
            resource_url,
            len(args),
            arg_summary,
        )
        try:
            parent_window: "XWindow | None" = None
            frame: "XFrame | None" = None
            for prop in args:
                if prop.Name == "ParentWindow":
                    parent_window = prop.Value
                elif prop.Name == "Frame":
                    frame = prop.Value
            if parent_window is None:
                raise RuntimeError(
                    "Talk2View sidebar: ParentWindow PropertyValue not "
                    "supplied by LibreOffice — cannot build panel"
                )

            from talk2view_writer.ui.sidebar_panel import (
                _log_window_state,
                build_chat_panel,
            )

            _log_window_state("createUIElement.parent_window", parent_window)
            panel = build_chat_panel(self.ctx, parent_window, frame, resource_url)
            logger.info(
                "createUIElement returned XUIElement type=%s",
                type(panel).__name__,
            )
            return panel
        except Exception:
            logger.exception(
                "createUIElement: failed building panel for %s — re-raising",
                resource_url,
            )
            raise


# ---------------------------------------------------------------------------
# UNO component registration
# ---------------------------------------------------------------------------


g_ImplementationHelper = unohelper.ImplementationHelper()  # noqa: N816 — UNO convention
g_ImplementationHelper.addImplementation(
    Talk2ViewProtocolHandler,
    "com.talk2view.writer.ProtocolHandler",
    ("com.sun.star.frame.ProtocolHandler",),
)
g_ImplementationHelper.addImplementation(
    ChatPanelFactory,
    "com.talk2view.writer.ChatPanelFactory",
    ("com.sun.star.ui.UIElementFactory",),
)
