"""LibreOffice Writer sidebar panel for Talk2View.

Phase B layout:

    +-----------------------------------------+
    |  Logged in as you@example.com           |  status label
    |  [ Log in... ] (only when logged out)   |  login button
    |                                         |
    |  +-----------------------------------+  |
    |  | chat history (multiline, ro)      |  |
    |  |                                   |  |
    |  +-----------------------------------+  |
    |                                         |
    |  +-----------------------------------+  |
    |  | composer (multiline)              |  |
    |  +-----------------------------------+  |
    |  [ Send ]                               |
    +-----------------------------------------+

The panel registers with the extension singleton so it receives
:meth:`on_auth_changed` callbacks when login/logout state changes.

Threading: the send-button handler spawns a worker thread that
iterates ``sdk.chat(text)``. Every UNO call the worker makes —
widget Text updates, label updates, enabled-state toggles — is
marshalled through :class:`UIThreadDispatcher` (Phase C). The
Phase B direct-write relaxation (ADR-0017) is now superseded by
ADR-0018.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import uno  # type: ignore[import-not-found]
import unohelper  # type: ignore[import-not-found]
from com.sun.star.awt import (  # type: ignore[import-not-found]
    XActionListener,
    XWindowListener,
)
from com.sun.star.awt.PosSize import POSSIZE  # type: ignore[import-not-found]
from com.sun.star.awt.WindowClass import SIMPLE  # type: ignore[import-not-found]
from com.sun.star.lang import XComponent  # type: ignore[import-not-found]
from com.sun.star.ui import UIElementType, XUIElement  # type: ignore[import-not-found]

if TYPE_CHECKING:
    from com.sun.star.awt import (
        ActionEvent,
        WindowEvent,
        XControl,
        XControlContainer,
        XWindow,
    )
    from com.sun.star.frame import XFrame
    from com.sun.star.lang import EventObject
    from com.sun.star.uno import XComponentContext
    from talk2view.types import User

logger = logging.getLogger(__name__)

_PADDING = 6
_BUTTON_HEIGHT = 26
_STATUS_HEIGHT = 20
_LOGIN_BUTTON_HEIGHT = 26
_COMPOSER_HEIGHT = 60
_HISTORY_MIN_HEIGHT = 80


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_chat_panel(
    ctx: XComponentContext,
    parent_window: XWindow,
    frame: XFrame | None,
    resource_url: str,
) -> XUIElement:
    """Build the Talk2View sidebar panel and return it as an XUIElement.

    Called by ``ChatPanelFactory.createUIElement`` when LibreOffice opens
    the Talk2View deck.
    """
    logger.info(
        "build_chat_panel starting: resource_url=%s parent_window=%r frame=%r",
        resource_url,
        parent_window,
        frame,
    )
    panel = Talk2ViewPanel(ctx, parent_window, frame, resource_url)
    logger.info("build_chat_panel: Talk2ViewPanel construction returned")

    from talk2view_writer.extension import get_extension

    try:
        get_extension(ctx).register_panel(panel)
        logger.info("build_chat_panel: panel registered with extension singleton")
    except Exception:
        # Don't raise — the panel itself is still usable even if
        # the singleton registration failed; we just won't get
        # auth-state broadcast updates.
        logger.exception("Failed to register panel with extension singleton")

    logger.info(
        "build_chat_panel complete — returning XUIElement to LibreOffice"
    )
    return panel


# ---------------------------------------------------------------------------
# XUIElement implementation
# ---------------------------------------------------------------------------


class Talk2ViewPanel(unohelper.Base, XUIElement, XComponent):
    """Talk2View chat panel for LibreOffice Writer's sidebar deck."""

    def __init__(
        self,
        ctx: XComponentContext,
        parent_window: XWindow,
        frame: XFrame | None,
        resource_url: str,
    ) -> None:
        self.ctx = ctx
        self._parent_window = parent_window
        self._frame = frame
        self._resource_url = resource_url
        self._listeners: list[object] = []

        # Widgets
        self._container_window: XWindow | None = None
        self._control_container: XControlContainer | None = None
        self._status_label: XControl | None = None
        self._login_button: XControl | None = None
        self._history_field: XControl | None = None
        self._composer_field: XControl | None = None
        self._send_button: XControl | None = None
        self._window_listener: _PanelResizeListener | None = None

        # Auth + chat state
        self._user: User | None = None
        self._busy = threading.Event()  # set while a worker thread is running

        logger.info(
            "Talk2ViewPanel.__init__: about to call _build_window "
            "(parent_window=%r resource_url=%s)",
            parent_window,
            resource_url,
        )
        try:
            self._build_window()
        except Exception:
            # A failure here means the sidebar slot will be EMPTY for
            # the user — log the full traceback so a "panel doesn't
            # appear" report is debuggable from the log file alone.
            logger.exception(
                "_build_window FAILED — sidebar deck will appear empty. "
                "Common causes: malformed UNO control properties, "
                "parent_window invalid, com.sun.star.* service missing."
            )
            raise
        logger.info(
            "Talk2ViewPanel.__init__: _build_window complete — "
            "container_window=%r send_button=%r",
            self._container_window,
            self._send_button,
        )

    # ----- XUIElement -----------------------------------------------------

    def getResourceURL(self) -> str:  # noqa: N802
        """XUIElement: the panel's ``private:resource/toolpanel/...`` URL."""
        return self._resource_url

    def getType(self) -> int:  # noqa: N802
        """XUIElement: ``UIElementType.TOOLPANEL`` for sidebar panels."""
        # UIElementType.TOOLPANEL is a UNO constant — annotate the cast.
        return int(UIElementType.TOOLPANEL)

    def getFrame(self) -> XFrame | None:  # noqa: N802
        """XUIElement: the frame this panel is docked into."""
        return self._frame

    def getRealInterface(self) -> XWindow:  # noqa: N802
        """XUIElement: the container window LibreOffice docks in the deck."""
        assert self._container_window is not None
        return self._container_window

    def setSettings(self, settings: object) -> None:  # noqa: N802
        """XUIElement: no-op — tool panels do not carry settings."""

    def getSettings(self, write: bool) -> None:  # noqa: N802
        """XUIElement: no-op — tool panels do not carry settings."""
        return None

    # ----- XComponent -----------------------------------------------------

    def dispose(self) -> None:
        """XComponent: tear down listeners and the container window."""
        logger.info("Talk2ViewPanel.dispose")
        from talk2view_writer.extension import get_extension

        try:
            get_extension(self.ctx).unregister_panel(self)
        except Exception:
            logger.exception("Failed to unregister panel from singleton")

        event = uno.createUnoStruct("com.sun.star.lang.EventObject")
        event.Source = self
        for listener in list(self._listeners):
            try:
                # Listeners are duck-typed XEventListener instances.
                listener.disposing(event)  # type: ignore[attr-defined]
            except Exception:
                logger.exception("Listener.disposing raised")

        if self._container_window is not None:
            try:
                if self._window_listener is not None:
                    self._container_window.removeWindowListener(self._window_listener)
                self._container_window.dispose()
            except Exception:
                logger.exception("Container window dispose failed")

    def addEventListener(self, listener: object) -> None:  # noqa: N802
        """XComponent: subscribe to ``disposing`` notifications."""
        self._listeners.append(listener)

    def removeEventListener(self, listener: object) -> None:  # noqa: N802
        """XComponent: unsubscribe a previously-added listener."""
        if listener in self._listeners:
            self._listeners.remove(listener)

    # ----- Public: auth state callback ------------------------------------

    def on_auth_changed(self, user: User | None) -> None:
        """Called by the extension singleton on every login/logout."""
        self._user = user
        self._apply_auth_state()

    # ----- Internal: build the widget tree --------------------------------

    def _build_window(self) -> None:
        # The ParentWindow passed in by the sidebar deck is a bare XWindow
        # — it supports only XWindow / XComponent / XTypeProvider / XWeak.
        # XWindow has no getToolkit() (that lives on XControl /
        # XWindow2), so we can't ask the parent for one. The portable
        # way to obtain a Toolkit is to instantiate the
        # com.sun.star.awt.Toolkit singleton service from the context
        # we already hold. It's the same toolkit the parent window is
        # using, so newly-created peers will share its display, theme,
        # etc.
        toolkit = self._create_service("com.sun.star.awt.Toolkit")

        # Container window (docks inside the sidebar panel area).
        descriptor = uno.createUnoStruct("com.sun.star.awt.WindowDescriptor")
        descriptor.Type = SIMPLE
        descriptor.WindowServiceName = "dockingwindow"
        descriptor.ParentIndex = -1
        descriptor.Parent = self._parent_window
        descriptor.Bounds = uno.createUnoStruct("com.sun.star.awt.Rectangle")
        descriptor.WindowAttributes = 0
        container_peer = toolkit.createWindow(descriptor)
        self._container_window = container_peer

        # Control container, sized to fill the deck area.
        cc_model = self._create_service("com.sun.star.awt.UnoControlContainerModel")
        cc = self._create_service("com.sun.star.awt.UnoControlContainer")
        cc.setModel(cc_model)
        cc.createPeer(toolkit, container_peer)
        parent_size = self._parent_window.getPosSize()
        cc_window: XWindow = cc  # type: ignore[assignment]
        cc_window.setPosSize(0, 0, parent_size.Width, parent_size.Height, POSSIZE)
        self._control_container = cc

        # Children.
        self._status_label = self._add_label(cc, "Talk2View — not logged in", name="status_label")
        self._login_button = self._add_button(
            cc, "Log in...", name="login_button", on_click=self._on_login_clicked
        )
        self._history_field = self._add_edit(
            cc,
            name="history_field",
            multiline=True,
            read_only=True,
            v_scroll=True,
        )
        self._composer_field = self._add_edit(
            cc,
            name="composer_field",
            multiline=True,
            read_only=False,
            v_scroll=True,
        )
        self._send_button = self._add_button(
            cc, "Send", name="send_button", on_click=self._on_send_clicked
        )

        self._window_listener = _PanelResizeListener(self)
        self._parent_window.addWindowListener(self._window_listener)

        self._apply_auth_state()
        self._layout_children()
        logger.info("Talk2View chat panel built")

    # ----- Widget factories -----------------------------------------------

    def _create_service(self, service_name: str) -> Any:
        # Returns whatever UNO service is named; callers cast implicitly
        # by calling UNO methods on the result.
        return self.ctx.ServiceManager.createInstanceWithContext(service_name, self.ctx)

    def _add_label(self, container: XControlContainer, text: str, *, name: str) -> XControl:
        model = self._create_service("com.sun.star.awt.UnoControlFixedTextModel")
        model.setPropertyValue("Label", text)
        model.setPropertyValue("Name", name)
        control = self._create_service("com.sun.star.awt.UnoControlFixedText")
        control.setModel(model)
        container.addControl(name, control)
        return control

    def _add_button(
        self,
        container: XControlContainer,
        text: str,
        *,
        name: str,
        on_click: Callable[[], None],
    ) -> XControl:
        model = self._create_service("com.sun.star.awt.UnoControlButtonModel")
        model.setPropertyValue("Label", text)
        model.setPropertyValue("Name", name)
        control = self._create_service("com.sun.star.awt.UnoControlButton")
        control.setModel(model)
        container.addControl(name, control)
        control.addActionListener(_ActionForwarder(on_click))
        return control

    def _add_edit(
        self,
        container: XControlContainer,
        *,
        name: str,
        multiline: bool,
        read_only: bool,
        v_scroll: bool,
    ) -> XControl:
        model = self._create_service("com.sun.star.awt.UnoControlEditModel")
        model.setPropertyValue("Name", name)
        model.setPropertyValue("MultiLine", multiline)
        model.setPropertyValue("ReadOnly", read_only)
        model.setPropertyValue("VScroll", v_scroll)
        model.setPropertyValue("HScroll", False)
        control = self._create_service("com.sun.star.awt.UnoControlEdit")
        control.setModel(model)
        container.addControl(name, control)
        return control

    # ----- Layout ---------------------------------------------------------

    def _layout_children(self) -> None:
        if self._control_container is None:
            return
        size = self._parent_window.getPosSize()
        width = max(size.Width - 2 * _PADDING, 100)
        x = _PADDING
        y = _PADDING

        if self._status_label is not None:
            (self._status_label).setPosSize(  # type: ignore[union-attr]
                x, y, width, _STATUS_HEIGHT, POSSIZE
            )
            y += _STATUS_HEIGHT + _PADDING

        login_visible = self._user is None
        if self._login_button is not None:
            (self._login_button).setVisible(login_visible)  # type: ignore[attr-defined]
            if login_visible:
                (self._login_button).setPosSize(  # type: ignore[union-attr]
                    x, y, width, _LOGIN_BUTTON_HEIGHT, POSSIZE
                )
                y += _LOGIN_BUTTON_HEIGHT + _PADDING

        # Composer + Send anchored to the bottom; history fills the rest.
        bottom_block_h = _COMPOSER_HEIGHT + _PADDING + _BUTTON_HEIGHT + _PADDING
        history_h = max(size.Height - y - bottom_block_h - _PADDING, _HISTORY_MIN_HEIGHT)
        if self._history_field is not None:
            (self._history_field).setPosSize(  # type: ignore[union-attr]
                x, y, width, history_h, POSSIZE
            )
            y += history_h + _PADDING

        if self._composer_field is not None:
            (self._composer_field).setPosSize(  # type: ignore[union-attr]
                x, y, width, _COMPOSER_HEIGHT, POSSIZE
            )
            y += _COMPOSER_HEIGHT + _PADDING

        if self._send_button is not None:
            (self._send_button).setPosSize(  # type: ignore[union-attr]
                x, y, width, _BUTTON_HEIGHT, POSSIZE
            )

    # ----- Auth state application -----------------------------------------

    def _apply_auth_state(self) -> None:
        """Update labels + enabled state to match ``self._user``."""
        if self._status_label is not None:
            text = (
                f"Logged in as {self._user.email}"
                if self._user is not None
                else "Talk2View — not logged in"
            )
            self._status_label.getModel().setPropertyValue("Label", text)

        is_auth = self._user is not None

        if self._composer_field is not None:
            self._composer_field.getModel().setPropertyValue("Enabled", is_auth)
        if self._send_button is not None:
            self._send_button.getModel().setPropertyValue("Enabled", is_auth)

        # Show/hide the login button — layout has to re-run so other
        # children fill the freed space.
        self._layout_children()

    # ----- Event handlers -------------------------------------------------

    def _on_login_clicked(self) -> None:
        from talk2view_writer.extension import get_extension

        parent = self._frame.getContainerWindow() if self._frame is not None else None
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
        message = str(self._composer_field.getModel().getPropertyValue("Text") or "").strip()
        if not message:
            return

        # Clear composer, append user message to history, mark busy.
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
            logger.info(
                "chat_worker finished cleanly after %d events", event_count
            )
        except Exception as exc:
            logger.exception(
                "chat_worker FAILED: %s: %s", type(exc).__name__, exc
            )
            self._append_history(f"\n[error] {exc}\n")
        finally:
            self._set_busy(False)
            logger.debug("chat_worker exit — busy flag cleared")

    def _handle_chat_event(self, event: Any) -> None:
        """Map a ``ChatEvent`` to UI updates.

        Phase B handles the text-only event stream. Phase C/D add
        ``tool_call`` event handling once tools land.

        ``event`` is annotated as ``Any`` because :class:`ChatEvent`
        lives in the ``talk2view`` SDK, which we don't import at
        module top-level (the SDK is bundled only at runtime).
        """
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
    #
    # All UNO calls these methods make are marshalled to the UI thread
    # via the extension singleton's UIThreadDispatcher — see ADR-0018.
    # ``_dispatch_ui`` may be called from any thread; the wrapped
    # callable runs on the UI thread and the calling thread blocks
    # until it returns. Methods named ``_*_ui`` are the UI-thread-only
    # halves.

    def _dispatch_ui(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Run ``fn(*args, **kwargs)`` on the UI thread.

        Thin wrapper around :meth:`UIThreadDispatcher.run_sync` that
        avoids importing the extension at module top-level.
        """
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
        if busy:
            self._status_label.getModel().setPropertyValue("Label", "Thinking…")  # type: ignore[union-attr]
        else:
            # Restore the per-auth-state status text directly here so
            # we stay on the UI thread without re-dispatching.
            self._apply_auth_state()

    # ----- Misc -----------------------------------------------------------

    def _show_message(self, title: str, message: str) -> None:
        if self._frame is None:
            logger.warning("No frame; cannot show message: %s", message)
            return
        window = self._frame.getContainerWindow()
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
# Helper listener implementations
# ---------------------------------------------------------------------------


class _ActionForwarder(unohelper.Base, XActionListener):
    """Forward UNO action events to a Python callable."""

    def __init__(self, callback: Callable[[], None]) -> None:
        self._callback = callback

    def actionPerformed(self, event: ActionEvent) -> None:  # noqa: N802
        self._callback()

    def disposing(self, event: EventObject) -> None:
        pass


class _PanelResizeListener(unohelper.Base, XWindowListener):
    """Re-flow the panel when the parent sidebar window resizes."""

    def __init__(self, panel: Talk2ViewPanel) -> None:
        self._panel = panel

    def windowResized(self, event: WindowEvent) -> None:  # noqa: N802
        self._panel._layout_children()

    def windowMoved(self, event: WindowEvent) -> None:  # noqa: N802
        pass

    def windowShown(self, event: EventObject) -> None:  # noqa: N802
        self._panel._layout_children()

    def windowHidden(self, event: EventObject) -> None:  # noqa: N802
        pass

    def disposing(self, event: EventObject) -> None:
        pass
