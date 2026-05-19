"""Integration test — actually constructs the Talk2View panel window.

Earlier revisions of this test asserted only that
``getRealInterface()`` returned a non-None XToolPanel proxy and the
UNO bridge stayed alive. Both pass when the underlying VCL widget
tree is empty — that's exactly what happened on the user's
LibreOffice 26.2.3.2 install on 2026-05-19: the sidebar opened to
an empty grey rectangle, but CI was green.

This rewrite asserts on the **rendered widget tree**:

  1. ``ChatPanelFactory.createUIElement(...)`` returns an XUIElement.
  2. ``getRealInterface()`` returns a non-None XToolPanel.
  3. The panel window exposes every control id from
     ``panels/chat_panel.xdl`` via ``getControl(...)`` — empty
     containers from a failed ``createContainerWindow`` would
     return ``None`` here.
  4. Each control has a positive ``PosSize`` (width AND height
     > 0). A 0x0 control is the same failure mode as a missing
     one: drawn but invisible.
  5. The composite panel window itself has positive size.
  6. A screenshot of the panel region (and a full-window
     screenshot, for context) is saved to ``_diag/`` so CI uploads
     it as an artifact. A blank rectangle is the visual signature
     of the bug.

The widget-existence assertion is what we should have had since
day one — see investigation #29 (added in this commit).
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

# Where the test rig drops talk2view.log copies + panel/window
# screenshots. The CI workflow's "Upload diagnostic logs" step uploads
# this directory under ``integration-logs-<os>-<lo_apt_source>-<sha>``,
# so failures are inspectable post-hoc by downloading the artifact.
_DIAG_DIR = Path(
    os.environ.get("T2V_INTEGRATION_DIAG", "_diag")
).resolve()


def _xdotool_or_import_available() -> tuple[str, ...] | None:
    """Return the screenshot command + args template, or None if unavailable.

    ImageMagick's ``import`` is the most portable headless-X grabber and
    is already installed on the Linux CI runners alongside soffice's
    apt deps. Fall back to ``scrot`` if ``import`` is absent. If
    neither is present (macOS / Windows runners) we skip screenshot
    capture and leave the widget-existence assertions to carry the
    test — the failing test artefact is still the talk2view.log copy.
    """
    if shutil.which("import") is not None:
        return ("import", "-window", "root")
    if shutil.which("scrot") is not None:
        return ("scrot",)
    return None


def _capture_screenshot(out_path: Path, rect: tuple[int, int, int, int] | None) -> None:
    """Capture the X11 root window (or a crop) to ``out_path``.

    ``rect`` is ``(x, y, width, height)`` in screen pixels. When None
    the full screen is captured. Best-effort: a screenshot tool absence
    or grab failure is logged but does not raise — we never want a
    diagnostic failure to mask a real test failure.
    """
    tool = _xdotool_or_import_available()
    if tool is None:
        return
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = list(tool)
    if rect is not None and tool[0] == "import":
        x, y, w, h = rect
        cmd += ["-crop", f"{w}x{h}+{x}+{y}"]
    cmd += [str(out_path)]
    with contextlib.suppress(
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        FileNotFoundError,
    ):
        subprocess.run(cmd, check=True, timeout=10)


# Every named control in ``panels/chat_panel.xdl`` that the production
# code's ``_bind_controls`` looks up. If any of these is missing or
# zero-sized, the panel is broken in a way the user can SEE
# (an empty grey rectangle) and the test must fail.
_EXPECTED_CONTROL_IDS = (
    "status_label",
    "login_button",
    "history_field",
    "composer_field",
    "send_button",
)


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

        # Give the dock a moment to lay out the panel widgets on the
        # main thread. ``getRealInterface`` returns synchronously but
        # the actual VCL placement happens on the next main-loop tick.
        time.sleep(1.5)

        # ----- THE assertions this whole test exists for ----------
        # ``getControl`` is the panel's API for fetching named XDL
        # widgets. A broken panel (empty grey rectangle reported by
        # the user on 2026-05-19) returns None for every id even
        # though ``getRealInterface()`` returned a non-None XToolPanel
        # — that's the failure mode this assertion catches.
        missing: list[str] = []
        zero_sized: list[str] = []
        controls: dict[str, Any] = {}
        for control_id in _EXPECTED_CONTROL_IDS:
            ctrl = panel_window.getControl(control_id)
            if ctrl is None:
                missing.append(control_id)
                continue
            controls[control_id] = ctrl
            # Each control must have positive on-screen size. A 0x0
            # control is rendered but invisible — same UX as missing.
            ctrl_window = getattr(ctrl, "Peer", None) or ctrl
            try:
                pos = ctrl_window.PosSize
                w, h = pos.Width, pos.Height
            except Exception:
                w = h = 0
            if w <= 0 or h <= 0:
                zero_sized.append(f"{control_id}({w}x{h})")

        # The panel container itself must have positive size.
        try:
            pp = panel_window.PosSize
            panel_w, panel_h = pp.Width, pp.Height
        except Exception:
            panel_w = panel_h = 0

        # Capture screenshots BEFORE the assertion so they're always
        # present in the diag artifact, even when the test fails.
        _DIAG_DIR.mkdir(parents=True, exist_ok=True)
        try:
            frame_pos = parent_window.PosSize
            full_rect = (
                frame_pos.X,
                frame_pos.Y,
                frame_pos.Width,
                frame_pos.Height,
            )
        except Exception:
            full_rect = None
        try:
            panel_pos_abs = panel_window.PosSize
            panel_rect = (
                panel_pos_abs.X,
                panel_pos_abs.Y,
                panel_pos_abs.Width,
                panel_pos_abs.Height,
            )
        except Exception:
            panel_rect = None
        _capture_screenshot(_DIAG_DIR / "panel.png", panel_rect)
        _capture_screenshot(_DIAG_DIR / "writer_window.png", full_rect)
        _capture_screenshot(_DIAG_DIR / "root.png", None)

        assert not missing, (
            f"Panel is missing widgets {missing!r} — "
            f"getControl(id) returned None. The XDL container loaded "
            f"but the children weren't instantiated. Check the "
            f"screenshots in _diag/panel.png and _diag/writer_window.png "
            f"and the createContainerWindow line in talk2view.log."
        )
        assert not zero_sized, (
            f"Panel widgets present but zero-sized: {zero_sized!r}. "
            f"They exist in the widget tree but won't render — same "
            f"UX as missing. See _diag/panel.png."
        )
        assert panel_w > 0 and panel_h > 0, (
            f"Panel container itself is {panel_w}x{panel_h} — the "
            f"dock allocated no space for our panel. Likely a Sidebar.xcu "
            f"layout-property issue. See _diag/writer_window.png."
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
