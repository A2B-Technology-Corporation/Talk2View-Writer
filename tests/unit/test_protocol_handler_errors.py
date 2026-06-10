"""Regression tests for the ProtocolHandler's UI error boundary.

``Talk2ViewProtocolHandler.dispatch`` funnels every exception into
``_show_error`` to surface it via an ERRORBOX (the project's single UI
error boundary). The earlier implementation returned silently when
``desktop.getCurrentFrame()`` was ``None`` (menu invoked with no focused
document frame) and dereferenced ``getContainerWindow()`` /
``getToolkit()`` without None-guards, so a failure raised while no frame
was focused was double-swallowed and the user saw nothing.

These tests pin the fixed behaviour: a ``None`` frame still surfaces an
error via a FRAMELESS message box (parent peer ``None``), and
``_show_error`` never raises — even when toolkit creation itself fails —
because it runs inside ``dispatch``'s ``except`` block.

The entry module lives at ``extension/talk2view_writer.py`` (outside the
installed package), so it is loaded by file path. The UNO modules it
imports are stubbed by ``tests/conftest.py``; this test builds its own
``MagicMock`` instances for the desktop/window/toolkit, as the other
unit tests do.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_UNO_ENTRY = _REPO_ROOT / "extension" / "talk2view_writer.py"


def _load_entry_module() -> ModuleType:
    """Load ``extension/talk2view_writer.py`` by file path.

    The module is not part of the installed ``talk2view_writer`` package
    (it is the bare UNO entry shim), so a normal import won't reach it.
    The ``uno`` / ``unohelper`` / ``com.sun.star.*`` imports it performs
    at module load resolve to the conftest stubs already in
    ``sys.modules``.
    """
    if "t2v_entry_under_test" in sys.modules:
        return sys.modules["t2v_entry_under_test"]
    spec = importlib.util.spec_from_file_location(
        "t2v_entry_under_test", _UNO_ENTRY
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["t2v_entry_under_test"] = module
    spec.loader.exec_module(module)
    return module


def _make_handler(desktop: Any) -> Any:
    """Build a Talk2ViewProtocolHandler whose Desktop service is ``desktop``."""
    entry = _load_entry_module()
    ctx = MagicMock(name="ctx")
    smgr = ctx.ServiceManager

    def _create(service_name: str, _ctx: Any) -> Any:
        if service_name == "com.sun.star.frame.Desktop":
            return desktop
        if service_name == "com.sun.star.awt.Toolkit":
            return desktop._frameless_toolkit
        raise AssertionError(f"unexpected service {service_name!r}")

    smgr.createInstanceWithContext.side_effect = _create
    return entry.Talk2ViewProtocolHandler(ctx)


@pytest.mark.unit
class TestShowErrorNoFrame:
    def test_no_frame_falls_back_to_frameless_box(self) -> None:
        """A ``None`` current frame still surfaces an ERRORBOX.

        Without the fix ``_show_error`` returned early and the user saw
        nothing. The frameless fallback must build a message box from the
        ``com.sun.star.awt.Toolkit`` service with a ``None`` parent peer.
        """
        desktop = MagicMock(name="desktop")
        desktop.getCurrentFrame.return_value = None
        frameless_toolkit = MagicMock(name="frameless_toolkit")
        desktop._frameless_toolkit = frameless_toolkit

        handler = _make_handler(desktop)
        handler._show_error("Talk2View", "boom")

        # The box must have been created (the user sees the error) ...
        frameless_toolkit.createMessageBox.assert_called_once()
        # ... with a None parent peer (frameless), per about.py's pattern.
        call = frameless_toolkit.createMessageBox.call_args
        assert call.args[0] is None
        frameless_toolkit.createMessageBox.return_value.execute.assert_called_once()

    def test_none_container_window_falls_back_to_frameless_box(self) -> None:
        """A frame whose container window is ``None`` also falls back.

        The frame is non-None but ``getContainerWindow()`` returns None,
        so the prior unguarded ``window.getToolkit()`` would have raised
        inside dispatch's except block. The guard routes to the frameless
        path with a ``None`` parent peer instead.
        """
        frame = MagicMock(name="frame")
        frame.getContainerWindow.return_value = None
        desktop = MagicMock(name="desktop")
        desktop.getCurrentFrame.return_value = frame
        frameless_toolkit = MagicMock(name="frameless_toolkit")
        desktop._frameless_toolkit = frameless_toolkit

        handler = _make_handler(desktop)
        handler._show_error("Talk2View", "boom")

        frameless_toolkit.createMessageBox.assert_called_once()
        assert frameless_toolkit.createMessageBox.call_args.args[0] is None


@pytest.mark.unit
class TestShowErrorNeverRaises:
    def test_does_not_raise_when_toolkit_creation_fails(self) -> None:
        """``_show_error`` swallows a toolkit failure rather than raising.

        It runs inside ``dispatch``'s ``except`` block; if it raised, the
        new exception would re-enter the dispatcher and LibreOffice would
        swallow it silently, hiding the original error.
        """
        desktop = MagicMock(name="desktop")
        desktop.getCurrentFrame.return_value = None
        frameless_toolkit = MagicMock(name="frameless_toolkit")
        frameless_toolkit.createMessageBox.side_effect = RuntimeError("no display")
        desktop._frameless_toolkit = frameless_toolkit

        handler = _make_handler(desktop)

        # Must not propagate — the assertion is the absence of a raise.
        handler._show_error("Talk2View", "boom")

    def test_does_not_raise_when_window_toolkit_fails(self) -> None:
        """A failure on the framed path is also caught, not propagated."""
        window = MagicMock(name="window")
        window.getToolkit.return_value.createMessageBox.side_effect = RuntimeError(
            "toolkit gone"
        )
        frame = MagicMock(name="frame")
        frame.getContainerWindow.return_value = window
        desktop = MagicMock(name="desktop")
        desktop.getCurrentFrame.return_value = frame
        desktop._frameless_toolkit = MagicMock(name="frameless_toolkit")

        handler = _make_handler(desktop)

        handler._show_error("Talk2View", "boom")
