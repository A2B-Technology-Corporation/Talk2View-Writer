"""Integration test — actually constructs the Talk2View panel window.

Earlier revisions of this test dispatched ``.uno:SidebarDeck`` against
a ``Hidden=True`` frame and called it done when soffice was still
alive a few seconds later. That was useless: on a hidden frame the
dock framework defers panel construction indefinitely, so the
``createUIElement → _create_panel_window → createContainerWindow``
path never ran. The test was unconditionally green even when the
real bug — a silent soffice exit during ``createContainerWindow``
— was active on every visible Writer launch.

This rewrite invokes the panel-construction path **directly**
through the same UNO service the dock framework calls, with the
same arguments the dock framework supplies. If panel construction
crashes soffice, the UNO bridge sees the socket close on the very
next request and the test fails with a clear "soffice died during
createUIElement" message.

Three checks:

  1. ``ChatPanelFactory.createUIElement(resource_url, args)``
     succeeds and returns an XUIElement.
  2. ``XUIElement.getRealInterface()`` (which triggers
     ``_create_panel_window`` → ``createContainerWindow``)
     succeeds and returns a non-null XToolPanel.
  3. The bridge is still alive afterwards (panel window construction
     didn't crash soffice). If steps 1 or 2 produce a remote object,
     the bridge survives by definition.
"""

from __future__ import annotations

import contextlib
import time
from typing import Any

import pytest


def _make_visible_writer_doc(uno_context: Any, desktop: Any) -> Any:
    """Open a visible Writer doc so the sidebar actually builds panels.

    The default ``blank_document`` fixture uses ``Hidden=True`` for
    isolation between tests, which short-circuits the very code path
    this test needs to exercise. Open our own visible doc here.
    """
    from com.sun.star.beans import PropertyValue  # type: ignore[import-not-found]

    hidden = PropertyValue()
    hidden.Name = "Hidden"
    hidden.Value = False
    return desktop.loadComponentFromURL(
        "private:factory/swriter", "_blank", 0, (hidden,)
    )


@pytest.mark.integration
def test_chat_panel_factory_constructs_panel_window(
    desktop: Any,
    oxt_installed: Any,
    uno_context: Any,
) -> None:
    """Invoke ChatPanelFactory directly + force panel-window construction.

    Bypasses ``.uno:SidebarDeck`` dispatch (which is async + only
    fires on visible frames) and calls the factory exactly the way
    the dock framework does. Forces ``getRealInterface()`` to run
    while we hold the reference, so the ``createContainerWindow``
    call inside ``_create_panel_window`` is exercised in this
    process — the call that has been silently exiting soffice on
    every real launch.
    """
    from com.sun.star.beans import PropertyValue  # type: ignore[import-not-found]

    def _prop(name: str, value: Any) -> Any:
        p = PropertyValue()
        p.Name = name
        p.Value = value
        return p

    doc = _make_visible_writer_doc(uno_context, desktop)
    try:
        controller = doc.getCurrentController()
        assert controller is not None
        frame = controller.getFrame()
        assert frame is not None
        parent_window = frame.getContainerWindow()
        assert parent_window is not None, (
            "frame has no container window — can't supply ParentWindow "
            "to ChatPanelFactory.createUIElement"
        )

        # Instantiate the factory the same way the sidebar framework
        # does — via the service manager, by its registered service
        # name. If our Factories.xcu wiring is wrong this fails here.
        factory = uno_context.ServiceManager.createInstanceWithContext(
            "com.talk2view.writer.ChatPanelFactory", uno_context
        )
        assert factory is not None, "ChatPanelFactory service did not instantiate"

        # ``XUIElementFactory.createUIElement`` declares
        # ``sequence<com.sun.star.beans.PropertyValue>`` for its second
        # arg — not ``NamedValue``. PyUNO is strict about this:
        # passing the wrong struct type raises CannotConvertException
        # before any of our extension code runs.
        args = (
            _prop("ParentWindow", parent_window),
            _prop("Frame", frame),
            _prop("Controller", controller),
            _prop("Module", "com.sun.star.text.TextDocument"),
        )
        resource_url = (
            "private:resource/toolpanel/com.talk2view.writer.ChatPanelFactory/Chat"
        )
        ui_element = factory.createUIElement(resource_url, args)
        assert ui_element is not None, "createUIElement returned None"

        # `getRealInterface()` is the call that triggers
        # `_create_panel_window` -> `createContainerWindow`. This is
        # THE bug location — if soffice is going to die, it dies here.
        real_iface = ui_element.getRealInterface()
        assert real_iface is not None, (
            "getRealInterface returned None — panel construction failed "
            "without raising. Check the talk2view.log for the last "
            "_create_panel_window log line before the silent exit."
        )

        # Liveness check: an unrelated UNO call must succeed,
        # proving the bridge survived panel construction.
        service_names = uno_context.ServiceManager.getAvailableServiceNames()
        assert service_names, "bridge alive but service manager empty"

        # The panel keeps a reference to its window; explicitly probe
        # it via XToolPanel.Window (read property) so we're sure the
        # window is a real remote object.
        panel_window = getattr(real_iface, "Window", None) or getattr(
            real_iface, "PanelWindow", None
        )
        assert panel_window is not None, (
            "XToolPanel exposes no Window/PanelWindow attribute — the "
            "dock framework will reject this panel."
        )

        # Tiny delay before doc-close so any pending dispose events
        # fire on the main loop, not during teardown.
        time.sleep(0.5)
    finally:
        # Force-close the visible doc so the next test isn't poisoned
        # by a lingering frame + sidebar.
        with contextlib.suppress(Exception):
            doc.close(True)


@pytest.mark.integration
def test_sidebar_deck_dispatch_on_visible_frame_does_not_crash(
    desktop: Any,
    oxt_installed: Any,
    uno_context: Any,
) -> None:
    """Dispatch ``.uno:SidebarDeck`` against a visible frame.

    Complements ``test_chat_panel_factory_constructs_panel_window``:
    that test bypasses the dispatch path and calls the factory
    directly; this test goes through the user-visible dispatch the
    same way the menu command does, on a visible frame so the dock
    actually triggers panel construction.
    """
    from com.sun.star.beans import PropertyValue  # type: ignore[import-not-found]

    doc = _make_visible_writer_doc(uno_context, desktop)
    try:
        controller = doc.getCurrentController()
        frame = controller.getFrame()
        dispatcher = uno_context.ServiceManager.createInstanceWithContext(
            "com.sun.star.frame.DispatchHelper", uno_context
        )
        prop = PropertyValue()
        prop.Name = "Sidebar"
        prop.Value = "com.talk2view.writer.Deck"
        dispatcher.executeDispatch(frame, ".uno:SidebarDeck", "_self", 0, (prop,))
        # Visible frame ⇒ dock actually constructs the panel on the
        # next main-loop tick. Sleep long enough for the construction
        # to finish (or crash).
        time.sleep(3)
        # Liveness probe.
        assert uno_context.ServiceManager.getAvailableServiceNames(), (
            "soffice died after .uno:SidebarDeck dispatch on a visible frame "
            "— panel construction crashed it. Check talk2view.log for the "
            "last _create_panel_window line."
        )
    finally:
        with contextlib.suppress(Exception):
            doc.close(True)
