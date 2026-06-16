"""Talk2View-Writer UNO entry point.

One UNO component is registered:

1. ``Talk2ViewProtocolHandler`` (``com.sun.star.frame.ProtocolHandler``)
   — handles menu commands from ``Addons.xcu`` via the custom
   ``vnd.com.talk2view.writer:<command>`` URL scheme. Implements
   ``XDispatchProvider`` + ``XDispatch``. Wired by ``ProtocolHandler.xcu``.

Per ADR-0029, the previous ``ChatPanelFactory`` (UIElementFactory for
the sidebar deck) has been removed: the LibreOffice 26.x sidebar
parent-window pattern is fundamentally broken from Python. The
``showPanel`` menu command now opens a floating non-modal chat window
via :class:`ChatWindow` (built with ``DialogProvider2.createDialog``).

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

import uno  # noqa: E402
import unohelper  # noqa: E402
from com.sun.star.frame import XDispatch, XDispatchProvider  # noqa: E402

# Bootstrap the persistent rotating log file as early as possible:
# every other module logged via `getLogger(__name__)` inherits the
# handlers wired up here. See src/talk2view_writer/_logging.py.
from talk2view_writer._logging import setup_logging  # noqa: E402

_LOG_PATH = setup_logging()

if TYPE_CHECKING:
    from com.sun.star.beans import PropertyValue
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

    def __init__(self, ctx: XComponentContext) -> None:
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
    ) -> XDispatch | None:
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
        descriptors: tuple[object, ...],
    ) -> tuple[XDispatch | None, ...]:
        """Batch form of queryDispatch — one entry per descriptor."""
        return tuple(
            self.queryDispatch(d.FeatureURL, d.FrameName, d.SearchFlags)
            for d in descriptors
        )

    # ----- XDispatch -------------------------------------------------

    def dispatch(
        self,
        url: object,
        args: tuple[PropertyValue, ...],
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
                # Opens the chat window. Per ADR-0030 this is a
                # pywebview React app — login, logout, and settings
                # are all handled inside the window, no separate
                # menu items needed.
                ext.show_chat_window()
            elif command == "about":
                from talk2view_writer.about import show_about

                show_about(self.ctx)
            elif command == "license":
                from talk2view_writer.about import show_license

                show_license(self.ctx)
            elif command in {"options", "settings"}:
                # "options" is the live menu item (Addons.xcu); "settings"
                # is the pre-ADR-0030 legacy URL a customised toolbar may
                # still carry. Both open the native Options dialog now that
                # one exists (ADR-0043) — preference toggles live there.
                from talk2view_writer.options import show_options

                show_options(self.ctx)
            elif command in {"login", "logout"}:
                # Legacy auth URLs from pre-ADR-0030 user profiles. The
                # menu no longer exposes them, but a user with a customised
                # toolbar might still invoke them. Funnel into the chat
                # window — that's where auth lives now.
                logger.info(
                    "dispatch: legacy command %r → opening chat window", command
                )
                ext.show_chat_window()
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
        """Surface an error to the user via an ERRORBOX message box.

        Runs inside :meth:`dispatch`'s ``except`` block, so it must
        never raise — a failure here would re-enter the dispatcher with
        a fresh exception that LibreOffice swallows silently, hiding the
        original error from the user entirely.

        When a document frame is focused, the box is parented over its
        container window. When ``getCurrentFrame()`` returns ``None``
        (e.g. the menu invoked with no focused document) — or the frame
        yields no peer/toolkit — we fall back to a FRAMELESS box created
        directly from the ``com.sun.star.awt.Toolkit`` service with a
        ``None`` parent peer, mirroring :mod:`talk2view_writer.about`.
        As a last resort, any toolkit failure is logged rather than
        propagated.

        Args:
            title: Message box title.
            message: Message box body text.
        """
        error_box = uno.Enum("com.sun.star.awt.MessageBoxType", "ERRORBOX")
        smgr = self.ctx.ServiceManager
        desktop = smgr.createInstanceWithContext(
            "com.sun.star.frame.Desktop", self.ctx
        )
        frame = desktop.getCurrentFrame()

        parent_peer = None
        toolkit = None
        if frame is not None:
            window = frame.getContainerWindow()
            if window is not None:
                parent_peer = window
                toolkit = window.getToolkit()

        try:
            if toolkit is None:
                # No focused frame (or no peer/toolkit) — fall back to a
                # frameless box from the Toolkit service, parent None.
                toolkit = smgr.createInstanceWithContext(
                    "com.sun.star.awt.Toolkit", self.ctx
                )
            msgbox = toolkit.createMessageBox(
                parent_peer,
                error_box,
                1,  # OK button
                title,
                message,
            )
            msgbox.execute()
        except Exception:
            # _show_error must never raise (it runs inside dispatch()'s
            # except block). Log the toolkit failure so the original
            # error trail is preserved even when the UI box can't render.
            logger.exception(
                "_show_error: failed to display error box (title=%r)", title
            )


# ---------------------------------------------------------------------------
# UNO component registration
# ---------------------------------------------------------------------------


g_ImplementationHelper = unohelper.ImplementationHelper()  # noqa: N816 — UNO convention
g_ImplementationHelper.addImplementation(
    Talk2ViewProtocolHandler,
    "com.talk2view.writer.ProtocolHandler",
    ("com.sun.star.frame.ProtocolHandler",),
)
