"""Integration test — opening the Talk2View deck must not crash soffice.

This is the test that would have caught every panel-rendering regression
in 2026-05-18's debugging session. The bugs we saw — missing
ProtocolHandler dispatch, missing UIElementFactories registration,
peer creation in the wrong window hierarchy, and the missing
XSidebarPanel interface — all manifested the same way: soffice exits
shortly after the panel's createUIElement() returns, with no Python
exception to catch.

The test trips them because the UNO connection itself dies when
soffice segfaults — a follow-up call after dispatching the deck-open
command raises a UNO RuntimeException instead of returning.
"""

from __future__ import annotations

import time
from typing import Any

import pytest


@pytest.mark.integration
def test_sidebar_deck_opens_without_crashing_soffice(
    blank_document: Any,
    oxt_installed: Any,
    uno_context: Any,
) -> None:
    """Open the Talk2View sidebar deck and verify soffice survives.

    Steps:
      1. Resolve the current frame from the freshly-opened doc.
      2. Dispatch ``.uno:SidebarDeck`` with ``Sidebar`` =
         ``com.talk2view.writer.Deck`` — same call our menu handler
         makes when the user clicks "Show Talk2View Panel".
      3. Wait a beat so the framework actually constructs the panel
         via ChatPanelFactory.createUIElement and asks it for its
         layout sizes via XSidebarPanel.getHeightForWidth.
      4. Make an unrelated UNO call. If soffice segfaulted during
         step 2/3, this call raises with a BridgeRuntimeError or
         the connection just hangs (pytest's per-test timeout kills
         the run). Either way → test fails.
    """
    from com.sun.star.beans import PropertyValue  # type: ignore[import-not-found]

    # `blank_document` already opened a Writer doc hidden;
    # `getCurrentController().getFrame()` gives us its frame.
    controller = blank_document.getCurrentController()
    assert controller is not None, "fresh doc has no current controller"
    frame = controller.getFrame()
    assert frame is not None, "fresh doc has no current frame"

    dispatcher = uno_context.ServiceManager.createInstanceWithContext(
        "com.sun.star.frame.DispatchHelper", uno_context
    )

    prop = PropertyValue()
    prop.Name = "Sidebar"
    prop.Value = "com.talk2view.writer.Deck"

    dispatcher.executeDispatch(frame, ".uno:SidebarDeck", "_self", 0, (prop,))

    # The dispatcher is fire-and-forget; the sidebar framework
    # constructs + asks XSidebarPanel.getHeightForWidth on a
    # subsequent main-loop tick. Give it real wall time before we
    # probe for liveness — anything under a second has caught
    # intermittent races in past runs.
    time.sleep(3)

    # Liveness probe. If soffice died, this raises immediately
    # because the UNO bridge sees the socket close. If soffice is
    # alive but unresponsive (panel construction looping), pytest's
    # per-test timeout kills the suite — also a failure signal.
    service_names = uno_context.ServiceManager.getAvailableServiceNames()
    assert service_names, "service manager returned empty service list"

    # Bonus liveness: the dispatcher we just called must still be
    # usable. Re-running the same dispatch shouldn't crash either —
    # this catches the regression where the *second* click crashed
    # because dispose() left dangling state from the first.
    dispatcher.executeDispatch(frame, ".uno:SidebarDeck", "_self", 0, (prop,))
    time.sleep(1)
    assert uno_context.ServiceManager.getAvailableServiceNames(), (
        "soffice died on the second sidebar-deck open dispatch"
    )
