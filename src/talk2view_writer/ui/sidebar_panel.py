"""LibreOffice Writer sidebar panel for Talk2View.

Implements the *canonical* Python sidebar pattern from LibreOffice's
SDK example ``odk/examples/python/toolpanel/toolpanel.py``:

  1. The sidebar deck calls ``ChatPanelFactory.createUIElement`` (in
     ``extension/talk2view_writer.py``) with a ``ParentWindow`` XWindow
     and an XFrame.
  2. We return a :class:`Talk2ViewPanel` (XUIElement) — at this point
     **no window is created**. ``Frame``, ``ResourceURL``, ``Type``
     are exposed as direct instance attributes per the PyUNO IDL-to-
     attribute mapping.
  3. LibreOffice calls ``getRealInterface()``. *Now* we lazily build the
     panel window via
     ``com.sun.star.awt.ContainerWindowProvider.createContainerWindow``,
     loading the layout from ``panels/chat_panel.xdl`` shipped in the
     ``.oxt``. The provider accepts the bare XWindow as the parent
     and handles all peer creation internally — this is the workaround
     for the sidebar's ParentWindow not exposing XWindowPeer.
  4. ``getRealInterface()`` returns a :class:`Talk2ViewToolPanel`
     (XToolPanel) whose ``.PanelWindow`` / ``.Window`` attributes
     point at the loaded container. The sidebar dock code uses those
     to slot the panel into the deck.

Why this pattern: the manual ``UnoControlContainer + createPeer``
approach we tried before segfaulted soffice on every dock attempt
(see git log 2026-05-18). The dock code expects a VCL-bridged window
from ``ContainerWindowProvider``; an unbridged UnoControlContainer
is for embedded dialogs, not sidebar panels.

Threading: the send-button handler spawns a worker thread that
iterates ``sdk.chat(text)``. Every UNO call from the worker is
marshalled to the UI thread via :class:`UIThreadDispatcher`
(see ADR-0018).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import uno  # type: ignore[import-not-found]
import unohelper  # type: ignore[import-not-found]
from com.sun.star.awt import XActionListener  # type: ignore[import-not-found]
from com.sun.star.lang import XComponent  # type: ignore[import-not-found]
from com.sun.star.ui import (  # type: ignore[import-not-found]
    UIElementType,
    XToolPanel,
    XUIElement,
)

if TYPE_CHECKING:
    from com.sun.star.awt import ActionEvent, XWindow
    from com.sun.star.frame import XFrame
    from com.sun.star.lang import EventObject
    from com.sun.star.uno import XComponentContext
    from talk2view.types import User

logger = logging.getLogger(__name__)

# Must match the identifier in extension/description.xml — looked up
# at runtime via the deployment singleton to find the extension's
# install path on disk.
_EXTENSION_ID = "com.talk2view.writer"
_XDL_PATH = "panels/chat_panel.xdl"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_chat_panel(
    ctx: XComponentContext,
    parent_window: XWindow,
    frame: XFrame | None,
    resource_url: str,
) -> XUIElement:
    """Construct the Talk2View XUIElement.

    Called from ``ChatPanelFactory.createUIElement``. Window creation
    is deferred to ``getRealInterface`` per the canonical pattern;
    this function just builds the XUIElement wrapper and registers
    it with the extension singleton.
    """
    logger.info(
        "build_chat_panel: resource_url=%s frame=%s",
        resource_url,
        "present" if frame is not None else "None",
    )
    panel = Talk2ViewPanel(ctx, frame, parent_window, resource_url)

    from talk2view_writer.extension import get_extension

    get_extension(ctx).register_panel(panel)
    return panel


# ---------------------------------------------------------------------------
# XToolPanel — wraps the loaded container window for the sidebar dock
# ---------------------------------------------------------------------------


class Talk2ViewToolPanel(unohelper.Base, XToolPanel):
    """The XToolPanel returned from XUIElement.getRealInterface.

    The sidebar dock code reads ``.Window`` / ``.PanelWindow`` to slot
    the panel into the deck, and ``createAccessible`` to wire it into
    the AT-SPI tree. Mirrors LibreOffice SDK's ``pocToolPanel``.
    """

    def __init__(self, panel_window: Any, ctx: XComponentContext) -> None:
        self.ctx = ctx
        self.PanelWindow = panel_window
        self.Window = panel_window

    def createAccessible(self, parent_accessible: object) -> Any:  # noqa: N802
        """XToolPanel: return our panel window as its own accessible root."""
        return self.PanelWindow


# ---------------------------------------------------------------------------
# XUIElement — the object returned from createUIElement
# ---------------------------------------------------------------------------


class Talk2ViewPanel(unohelper.Base, XUIElement, XComponent):
    """Talk2View chat panel.

    XUIElement attributes (``Frame``, ``ResourceURL``, ``Type``) are
    set as direct Python attributes — PyUNO's attribute synthesis
    binds them to the IDL-declared read-only attributes. This is how
    LibreOffice's SDK toolpanel example does it.
    """

    def __init__(
        self,
        ctx: XComponentContext,
        frame: XFrame | None,
        parent_window: XWindow,
        resource_url: str,
    ) -> None:
        self.ctx = ctx
        self._frame_ref = frame  # used by handlers; XUIElement.Frame is set below
        self._parent_window = parent_window

        # XUIElement attributes (PyUNO maps these to the IDL attributes).
        self.Frame = frame
        self.ResourceURL = resource_url
        self.Type = int(UIElementType.TOOLPANEL)

        # Lazy-built on first getRealInterface() call.
        self._tool_panel: Talk2ViewToolPanel | None = None
        self._panel_window: Any | None = None  # ContainerWindowProvider result

        # Widget refs — bound after panel_window is created.
        self._status_label: Any | None = None
        self._login_button: Any | None = None
        self._history_field: Any | None = None
        self._composer_field: Any | None = None
        self._send_button: Any | None = None

        # Auth + chat state.
        self._user: User | None = None
        self._busy = threading.Event()

        # XComponent listeners.
        self._listeners: list[object] = []

    # ----- XUIElement -----------------------------------------------------

    def getRealInterface(self) -> Any:  # noqa: N802
        """Lazily build the panel window + return an XToolPanel wrapping it."""
        if self._tool_panel is None:
            window = self._create_panel_window()
            self._bind_controls(window)
            self._apply_auth_state()
            self._tool_panel = Talk2ViewToolPanel(window, self.ctx)
            logger.info("Talk2View panel window created and bound")
        return self._tool_panel

    def setSettings(self, settings: object) -> None:  # noqa: N802
        """XUIElement: no-op — tool panels don't carry settings."""

    def getSettings(self, write: bool) -> object | None:  # noqa: N802
        """XUIElement: no-op — tool panels don't carry settings."""
        return None

    # ----- Window construction --------------------------------------------

    def _create_panel_window(self) -> Any:
        """Load chat_panel.xdl via ContainerWindowProvider.

        The provider accepts the bare XWindow we received from the
        sidebar (which doesn't expose XWindowPeer) and handles all
        the peer-bridging plumbing internally — this is precisely why
        we use it instead of building a UnoControlContainer manually.
        """
        pip = self.ctx.getValueByName(
            "/singletons/com.sun.star.deployment.PackageInformationProvider"
        )
        extension_root = pip.getPackageLocation(_EXTENSION_ID)
        dialog_url = f"{extension_root}/{_XDL_PATH}"
        logger.info("Loading chat panel layout from %s", dialog_url)

        provider = self.ctx.ServiceManager.createInstanceWithContext(
            "com.sun.star.awt.ContainerWindowProvider", self.ctx
        )
        window = provider.createContainerWindow(
            dialog_url, "", self._parent_window, None
        )
        self._panel_window = window
        return window

    def _bind_controls(self, window: Any) -> None:
        """Resolve XDL control ids to control references + wire actions."""
        self._status_label = window.getControl("status_label")
        self._login_button = window.getControl("login_button")
        self._history_field = window.getControl("history_field")
        self._composer_field = window.getControl("composer_field")
        self._send_button = window.getControl("send_button")

        self._login_button.addActionListener(_ActionForwarder(self._on_login_clicked))
        self._send_button.addActionListener(_ActionForwarder(self._on_send_clicked))

    # ----- XComponent -----------------------------------------------------

    def dispose(self) -> None:
        """XComponent: tear down listeners and the panel window."""
        logger.info("Talk2ViewPanel.dispose")
        from talk2view_writer.extension import get_extension

        get_extension(self.ctx).unregister_panel(self)

        event = uno.createUnoStruct("com.sun.star.lang.EventObject")
        event.Source = self
        for listener in list(self._listeners):
            listener.disposing(event)  # type: ignore[attr-defined]

        if self._panel_window is not None:
            self._panel_window.dispose()
            self._panel_window = None
        self._tool_panel = None

    def addEventListener(self, listener: object) -> None:  # noqa: N802
        """XComponent: subscribe to disposing notifications."""
        self._listeners.append(listener)

    def removeEventListener(self, listener: object) -> None:  # noqa: N802
        """XComponent: unsubscribe a previously-added listener."""
        if listener in self._listeners:
            self._listeners.remove(listener)

    # ----- Public: auth state callback ------------------------------------

    def on_auth_changed(self, user: User | None) -> None:
        """Called by the extension singleton on every login/logout."""
        self._user = user
        if self._tool_panel is not None:
            # Only update widgets once the panel has actually been built.
            self._apply_auth_state()

    # ----- Auth state application -----------------------------------------

    def _apply_auth_state(self) -> None:
        """Update labels + enabled state to match ``self._user``."""
        is_auth = self._user is not None

        if self._status_label is not None:
            text = (
                f"Logged in as {self._user.email}"
                if self._user is not None
                else "Talk2View — not logged in"
            )
            self._status_label.getModel().setPropertyValue("Label", text)

        if self._login_button is not None:
            self._login_button.getModel().setPropertyValue("EnableVisible", not is_auth)

        if self._composer_field is not None:
            self._composer_field.getModel().setPropertyValue("Enabled", is_auth)
        if self._send_button is not None:
            self._send_button.getModel().setPropertyValue("Enabled", is_auth)

    # ----- Event handlers -------------------------------------------------

    def _on_login_clicked(self) -> None:
        from talk2view_writer.extension import get_extension

        parent = (
            self._frame_ref.getContainerWindow() if self._frame_ref is not None else None
        )
        try:
            get_extension(self.ctx).show_login_dialog(parent_window=parent)
        except Exception as exc:
            logger.exception("Login flow failed")
            self._show_message("Talk2View — login failed", str(exc))

    def _on_send_clicked(self) -> None:
        if self._busy.is_set():
            logger.info("Send pressed while busy — ignoring")
            return
        if self._composer_field is None or self._history_field is None:
            return
        message = str(
            self._composer_field.getModel().getPropertyValue("Text") or ""
        ).strip()
        if not message:
            return

        self._composer_field.getModel().setPropertyValue("Text", "")
        self._append_history(f"You: {message}\n")
        self._append_history("Talk2View: ")
        self._set_busy(True)

        thread = threading.Thread(target=self._chat_worker, args=(message,), daemon=True)
        thread.start()

    # ----- Chat worker (background thread) --------------------------------

    def _chat_worker(self, message: str) -> None:
        from talk2view_writer.extension import get_extension
        from talk2view_writer.system_prompt import load_system_prompt

        logger.info(
            "chat_worker started in thread=%s for message len=%d",
            threading.current_thread().name,
            len(message),
        )
        try:
            sdk = get_extension(self.ctx).sdk
            system_prompt = load_system_prompt()
            event_count = 0
            for event in sdk.chat(message, system_prompt=system_prompt):
                event_count += 1
                self._handle_chat_event(event)
            self._append_history("\n")
            logger.info("chat_worker finished cleanly after %d events", event_count)
        except Exception as exc:
            # UI boundary: catching `Exception` here is justified because
            # the chat-worker thread has no other way to surface failure
            # to the user; we write the error into the history field
            # (visible UI) before exiting cleanly. Re-raising would
            # crash the thread silently.
            logger.exception("chat_worker failed")
            self._append_history(f"\n[error] {exc}\n")
        finally:
            self._set_busy(False)

    def _handle_chat_event(self, event: Any) -> None:
        etype = getattr(event, "type", None)
        if etype == "text" and event.content:
            self._append_history(event.content)
        elif etype == "status":
            self._set_status(event.message or "")
        elif etype == "error":
            self._append_history(f"\n[error] {event.message}\n")
        elif etype == "done":
            return
        elif etype == "tool_call":
            tool_name = getattr(event, "tool_name", "?")
            self._append_history(f"\n[tool: {tool_name}] (Phase C)\n")
        else:
            logger.debug("Unhandled ChatEvent type: %s", etype)

    # ----- Cross-thread widget writers ------------------------------------

    def _dispatch_ui(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        from talk2view_writer.extension import get_extension

        return get_extension(self.ctx).ui_thread.run_sync(fn, *args, **kwargs)

    def _append_history(self, text: str) -> None:
        if self._history_field is None:
            return
        self._dispatch_ui(self._append_history_ui, text)

    def _append_history_ui(self, text: str) -> None:
        if self._history_field is None:
            return
        model = self._history_field.getModel()
        current = model.getPropertyValue("Text") or ""
        model.setPropertyValue("Text", current + text)

    def _set_status(self, text: str) -> None:
        if self._status_label is None:
            return
        self._dispatch_ui(self._set_status_ui, text)

    def _set_status_ui(self, text: str) -> None:
        if self._status_label is None:
            return
        self._status_label.getModel().setPropertyValue("Label", text)

    def _set_busy(self, busy: bool) -> None:
        if busy:
            self._busy.set()
        else:
            self._busy.clear()
        self._dispatch_ui(self._set_busy_ui, busy)

    def _set_busy_ui(self, busy: bool) -> None:
        if self._composer_field is not None:
            self._composer_field.getModel().setPropertyValue(
                "Enabled", not busy and self._user is not None
            )
        if self._send_button is not None:
            self._send_button.getModel().setPropertyValue(
                "Enabled", not busy and self._user is not None
            )
        if busy and self._status_label is not None:
            self._status_label.getModel().setPropertyValue("Label", "Thinking…")
        elif not busy:
            self._apply_auth_state()

    # ----- Misc -----------------------------------------------------------

    def _show_message(self, title: str, message: str) -> None:
        if self._frame_ref is None:
            logger.warning("No frame; cannot show message: %s", message)
            return
        window = self._frame_ref.getContainerWindow()
        toolkit = window.getToolkit()
        msgbox = toolkit.createMessageBox(
            window,
            uno.Enum("com.sun.star.awt.MessageBoxType", "ERRORBOX"),
            1,
            title,
            message,
        )
        msgbox.execute()


# ---------------------------------------------------------------------------
# Helper: bridge between UNO XActionListener and a plain Python callable
# ---------------------------------------------------------------------------


class _ActionForwarder(unohelper.Base, XActionListener):
    """Forward UNO action events to a Python callable."""

    def __init__(self, callback: Callable[[], None]) -> None:
        self._callback = callback

    def actionPerformed(self, event: ActionEvent) -> None:  # noqa: N802
        self._callback()

    def disposing(self, event: EventObject) -> None:
        pass
