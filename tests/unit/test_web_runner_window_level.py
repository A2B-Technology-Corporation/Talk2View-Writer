"""Unit tests for the macOS companion-window level patch (ADR-0042).

pywebview's Cocoa backend maps ``on_top=True`` to ``NSStatusWindowLevel``
(25), which stacks ABOVE the macOS input-method candidate window — typing
Chinese/Japanese in the chat box hid the candidate bar behind the panel.
The patch re-lowers the NSWindow to ``NSFloatingWindowLevel`` (3): still
above LibreOffice's normal-level (0) document windows, below the IME UI.

Runs in the venv with a fake ``webview.platforms.cocoa`` backend + fake
``AppKit``, so the init-wrapping is exercised on any OS. The real stacking
order against a live IME is verified manually (no headless macOS IME in CI).
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from talk2view_writer import web_runner as wr

pytestmark = pytest.mark.unit

_FAKE_STATUS_LEVEL = 25
_FAKE_FLOATING_LEVEL = 3


class _FakeNSWindow:
    """Records every ``setLevel_`` call like the real NSWindow would."""

    def __init__(self) -> None:
        self.levels: list[int] = []

    def setLevel_(self, level: int) -> None:  # noqa: N802 - ObjC selector name
        self.levels.append(level)


class _FakePywebviewWindow:
    def __init__(self, on_top: bool) -> None:
        self.on_top = on_top


def _install_fake_cocoa(monkeypatch: pytest.MonkeyPatch) -> Any:
    class BrowserView:
        """Mimic pywebview cocoa's on_top handling.

        ``__init__`` builds the NSWindow and applies NSStatusWindowLevel
        when the window is on_top, like the real backend does.
        """

        def __init__(self, window: Any) -> None:
            self.window = _FakeNSWindow()
            if window.on_top:
                self.window.setLevel_(_FAKE_STATUS_LEVEL)

    mod = types.ModuleType("webview.platforms.cocoa")
    mod.BrowserView = BrowserView  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "webview.platforms.cocoa", mod)
    import webview.platforms as wp

    monkeypatch.setattr(wp, "cocoa", mod, raising=False)

    appkit = types.ModuleType("AppKit")
    appkit.NSFloatingWindowLevel = _FAKE_FLOATING_LEVEL  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "AppKit", appkit)
    return mod


def test_on_top_window_is_lowered_to_floating_level(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = _install_fake_cocoa(monkeypatch)
    wr._patch_cocoa_window_level()

    view = mod.BrowserView(_FakePywebviewWindow(on_top=True))

    # pywebview set status level in init; the wrapper must re-lower it
    # so the window ends up at floating level (below the IME candidates).
    assert view.window.levels[-1] == _FAKE_FLOATING_LEVEL
    assert getattr(mod.BrowserView, "_t2v_level_patched", False) is True


def test_non_on_top_window_level_is_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = _install_fake_cocoa(monkeypatch)
    wr._patch_cocoa_window_level()

    view = mod.BrowserView(_FakePywebviewWindow(on_top=False))

    assert view.window.levels == []  # stays at the default (normal) level


def test_patch_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _install_fake_cocoa(monkeypatch)
    wr._patch_cocoa_window_level()
    once = mod.BrowserView.__init__
    wr._patch_cocoa_window_level()  # sentinel -> no double-wrap
    assert mod.BrowserView.__init__ is once


def test_patch_noop_when_backend_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "webview.platforms.cocoa", None)
    wr._patch_cocoa_window_level()  # must not raise
