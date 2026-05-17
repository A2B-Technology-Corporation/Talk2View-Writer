"""LibreOffice Writer sidebar panel for Talk2View.

Implements the ``XUIElement`` returned from ``ChatPanelFactory`` in
``extension/talk2view_writer.py``. Phase A renders a placeholder layout
(status label + Log-in button) sufficient to verify the deck registers
and the panel mounts. Phase B replaces this with the chat composer + history.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, List, Optional

import uno
import unohelper
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
        XWindowPeer,
    )
    from com.sun.star.frame import XFrame
    from com.sun.star.lang import EventObject
    from com.sun.star.uno import XComponentContext

logger = logging.getLogger(__name__)

# Pixel constants for the placeholder layout. Values picked to look
# sensible at the default sidebar width (~250-320 px).
_PADDING = 8
_BUTTON_HEIGHT = 28
_LABEL_HEIGHT = 22


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_chat_panel(
    ctx: "XComponentContext",
    parent_window: "XWindow",
    frame: "Optional[XFrame]",
    resource_url: str,
) -> XUIElement:
    """Build the Talk2View sidebar panel and return it as an XUIElement.

    Called by ``ChatPanelFactory.createUIElement`` when LibreOffice opens
    the Talk2View deck.
    """
    panel = Talk2ViewPanel(ctx, parent_window, frame, resource_url)

    # Register the panel with the extension singleton so future phases
    # can route SDK events to all open panels.
    from talk2view_writer.extension import get_extension

    try:
        get_extension(ctx).register_panel(panel)
    except Exception:  # registration failure should not break the panel
        logger.exception("Failed to register panel with extension singleton")

    return panel


# ---------------------------------------------------------------------------
# XUIElement implementation
# ---------------------------------------------------------------------------


class Talk2ViewPanel(unohelper.Base, XUIElement, XComponent):
    """XUIElement wrapping the Talk2View sidebar panel.

    The "real interface" returned to LibreOffice is the container
    XWindow that holds the panel's widgets. LibreOffice will dock that
    window inside the sidebar deck.
    """

    def __init__(
        self,
        ctx: "XComponentContext",
        parent_window: "XWindow",
        frame: "Optional[XFrame]",
        resource_url: str,
    ) -> None:
        self.ctx = ctx
        self._parent_window = parent_window
        self._frame = frame
        self._resource_url = resource_url
        self._listeners: List[object] = []

        self._container_window: "Optional[XWindow]" = None
        self._control_container: "Optional[XControlContainer]" = None
        self._status_label: "Optional[XControl]" = None
        self._login_button: "Optional[XControl]" = None
        self._window_listener: "Optional[_PanelResizeListener]" = None

        self._build_window()

    # ----- XUIElement -----------------------------------------------------

    def getResourceURL(self) -> str:  # noqa: N802 — UNO interface naming
        return self._resource_url

    def getType(self) -> int:  # noqa: N802
        return UIElementType.TOOLPANEL

    def getFrame(self) -> "Optional[XFrame]":  # noqa: N802
        return self._frame

    def getRealInterface(self) -> "XWindow":  # noqa: N802
        assert self._container_window is not None  # noqa: S101
        return self._container_window

    def setSettings(self, settings: object) -> None:  # noqa: N802
        # Tool panels don't carry menu/toolbar settings.
        pass

    def getSettings(self, write: bool) -> None:  # noqa: N802, ARG002
        return None

    # ----- XComponent -----------------------------------------------------

    def dispose(self) -> None:
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
                listener.disposing(event)
            except Exception:
                logger.exception("Listener.disposing raised")

        if self._container_window is not None:
            try:
                if self._window_listener is not None:
                    self._container_window.removeWindowListener(
                        self._window_listener
                    )
                self._container_window.dispose()
            except Exception:
                logger.exception("Container window dispose failed")

    def addEventListener(self, listener: object) -> None:  # noqa: N802
        self._listeners.append(listener)

    def removeEventListener(self, listener: object) -> None:  # noqa: N802
        if listener in self._listeners:
            self._listeners.remove(listener)

    # ----- Internal: build the widget tree ----------------------------------

    def _build_window(self) -> None:
        """Create a container XWindow with placeholder widgets.

        Uses the awt ``ContainerWindow`` service which provides an
        ``XControlContainer`` we can attach individual controls
        (FixedText, Button) to. The container honours the parent's
        bounds; we reposition children manually on resize via
        :class:`_PanelResizeListener`.
        """
        toolkit = self._parent_window.getToolkit()

        # Step 1: create the container window as a child of the sidebar parent.
        descriptor = uno.createUnoStruct("com.sun.star.awt.WindowDescriptor")
        descriptor.Type = SIMPLE
        descriptor.WindowServiceName = "dockingwindow"
        descriptor.ParentIndex = -1
        descriptor.Parent = self._parent_window
        descriptor.Bounds = uno.createUnoStruct("com.sun.star.awt.Rectangle")
        descriptor.WindowAttributes = 0  # default attributes

        container_peer = toolkit.createWindow(descriptor)
        container_window: "XWindow" = container_peer  # type: ignore[assignment]
        self._container_window = container_window

        # Step 2: create the control container that hosts our widgets.
        control_container_model = self._create_service(
            "com.sun.star.awt.UnoControlContainerModel"
        )
        control_container = self._create_service(
            "com.sun.star.awt.UnoControlContainer"
        )
        control_container.setModel(control_container_model)
        control_container.createPeer(toolkit, container_peer)
        # Make the container window fill the deck panel area.
        control_container_window: "XWindow" = control_container  # type: ignore[assignment]
        control_container_window.setPosSize(
            0,
            0,
            self._parent_window.getPosSize().Width,
            self._parent_window.getPosSize().Height,
            POSSIZE,
        )
        self._control_container = control_container

        # Step 3: status label.
        self._status_label = self._add_label(
            control_container,
            "Talk2View — placeholder panel (Phase A)",
            name="status_label",
        )

        # Step 4: login button.
        self._login_button = self._add_button(
            control_container,
            "Log in…",
            name="login_button",
            on_click=self._on_login_clicked,
        )

        # Step 5: listen for parent resizes so we can reflow children.
        self._window_listener = _PanelResizeListener(self)
        self._parent_window.addWindowListener(self._window_listener)
        self._layout_children()

        logger.info("Talk2View sidebar panel built")

    def _create_service(self, service_name: str) -> object:
        return self.ctx.ServiceManager.createInstanceWithContext(
            service_name, self.ctx
        )

    def _add_label(
        self,
        container: "XControlContainer",
        text: str,
        *,
        name: str,
    ) -> "XControl":
        model = self._create_service("com.sun.star.awt.UnoControlFixedTextModel")
        model.setPropertyValue("Label", text)
        model.setPropertyValue("Name", name)
        control = self._create_service("com.sun.star.awt.UnoControlFixedText")
        control.setModel(model)
        container.addControl(name, control)
        return control

    def _add_button(
        self,
        container: "XControlContainer",
        text: str,
        *,
        name: str,
        on_click,
    ) -> "XControl":
        model = self._create_service("com.sun.star.awt.UnoControlButtonModel")
        model.setPropertyValue("Label", text)
        model.setPropertyValue("Name", name)
        control = self._create_service("com.sun.star.awt.UnoControlButton")
        control.setModel(model)
        container.addControl(name, control)
        control.addActionListener(_ActionForwarder(on_click))
        return control

    # ----- Internal: layout / event handlers --------------------------------

    def _layout_children(self) -> None:
        """Reposition the placeholder widgets to fill the panel width."""
        if self._control_container is None:
            return
        parent_size = self._parent_window.getPosSize()
        width = max(parent_size.Width - 2 * _PADDING, 100)
        y = _PADDING
        if self._status_label is not None:
            label_window: "XWindow" = self._status_label  # type: ignore[assignment]
            label_window.setPosSize(_PADDING, y, width, _LABEL_HEIGHT, POSSIZE)
            y += _LABEL_HEIGHT + _PADDING
        if self._login_button is not None:
            button_window: "XWindow" = self._login_button  # type: ignore[assignment]
            button_window.setPosSize(_PADDING, y, width, _BUTTON_HEIGHT, POSSIZE)

    def _on_login_clicked(self) -> None:
        logger.info("Login button clicked (placeholder)")
        # Phase B will route this to extension.show_login_dialog().
        try:
            from talk2view_writer.extension import get_extension

            get_extension(self.ctx).show_login_dialog()
        except NotImplementedError:
            # Expected in Phase A — surface to the user.
            self._show_message("Talk2View", "Login arrives in Phase B.")
        except Exception as exc:
            logger.exception("Login flow failed")
            self._show_message("Talk2View — error", str(exc))

    def _show_message(self, title: str, message: str) -> None:
        if self._frame is None:
            logger.warning("No frame; cannot show message: %s", message)
            return
        window = self._frame.getContainerWindow()
        toolkit = window.getToolkit()
        msgbox = toolkit.createMessageBox(
            window,
            uno.Enum("com.sun.star.awt.MessageBoxType", "INFOBOX"),
            1,
            title,
            message,
        )
        msgbox.execute()


# ---------------------------------------------------------------------------
# Helper listener implementations
# ---------------------------------------------------------------------------


class _ActionForwarder(unohelper.Base, XActionListener):
    """Tiny shim: forwards UNO action events to a Python callable."""

    def __init__(self, callback) -> None:
        self._callback = callback

    def actionPerformed(self, event: "ActionEvent") -> None:  # noqa: N802, ARG002
        self._callback()

    def disposing(self, event: "EventObject") -> None:  # noqa: ARG002
        pass


class _PanelResizeListener(unohelper.Base, XWindowListener):
    """Reflows the panel children when the parent sidebar window resizes."""

    def __init__(self, panel: Talk2ViewPanel) -> None:
        self._panel = panel

    def windowResized(self, event: "WindowEvent") -> None:  # noqa: N802, ARG002
        self._panel._layout_children()

    def windowMoved(self, event: "WindowEvent") -> None:  # noqa: N802, ARG002
        pass

    def windowShown(self, event: "EventObject") -> None:  # noqa: N802, ARG002
        self._panel._layout_children()

    def windowHidden(self, event: "EventObject") -> None:  # noqa: N802, ARG002
        pass

    def disposing(self, event: "EventObject") -> None:  # noqa: ARG002
        pass
