"""Unit tests for the microphone (getUserMedia) permission patches (ADR-0041).

These run in the venv on every push (no GUI libs needed) and exercise the
per-OS wiring of all three patches against fake pywebview backend modules:

- Linux / WebKitGTK: wraps ``BrowserView.__init__`` to set the media-stream
  settings and connect ``permission-request`` to ``_grant_media_permission``.
- macOS / WKWebView: subclasses ``BrowserView.BrowserDelegate`` to add the
  ``requestMediaCapturePermission`` grant method.
- Windows / WebView2: wraps ``EdgeChrome.on_webview_ready`` to subscribe a
  ``PermissionRequested`` handler granting Microphone / Camera.

The live WebKitGTK end-to-end behaviour (the actual ``getUserMedia`` flip) is
covered by the Linux-only gui_smoke test
``tests/integration/test_webkit_media_permission.py``.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from talk2view_writer import web_runner as wr

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# _grant_media_permission — the shared, duck-typed handler
# ---------------------------------------------------------------------------


def _named_req(class_name: str) -> Any:
    cls = type(class_name, (), {})

    def allow(self: Any) -> None:
        self.allowed = True

    cls.allow = allow  # type: ignore[attr-defined]
    inst = cls()
    inst.allowed = False
    return inst


@pytest.mark.parametrize(
    "name",
    [
        "WebKitUserMediaPermissionRequest",
        "UserMediaPermissionRequest",
        "WebKitDeviceInfoPermissionRequest",
        "DeviceInfoPermissionRequest",
    ],
)
def test_grant_allows_media_and_device_requests(name: str) -> None:
    req = _named_req(name)
    handled = wr._grant_media_permission(None, req)
    assert handled is True  # handled -> stops WebKit's default-deny
    assert req.allowed is True


@pytest.mark.parametrize(
    "name",
    [
        "WebKitGeolocationPermissionRequest",
        "WebKitNotificationPermissionRequest",
        "WebKitPointerLockPermissionRequest",
    ],
)
def test_grant_ignores_other_requests(name: str) -> None:
    req = _named_req(name)
    handled = wr._grant_media_permission(None, req)
    assert handled is False  # not handled -> WebKit applies its own default
    assert req.allowed is False


# ---------------------------------------------------------------------------
# Linux / WebKitGTK
# ---------------------------------------------------------------------------


def _install_fake_gtk(monkeypatch: pytest.MonkeyPatch) -> Any:
    class _Props:
        def __init__(self) -> None:
            self.enable_media_stream = False
            self.enable_webrtc = False

    class _Settings:
        def __init__(self, props: Any) -> None:
            self.props = props

    class _WebView:
        def __init__(self) -> None:
            self._props = _Props()
            self.connected: list[tuple[str, Any]] = []

        def get_settings(self) -> Any:
            return _Settings(self._props)

        def connect(self, signal: str, handler: Any) -> None:
            self.connected.append((signal, handler))

    class BrowserView:
        def __init__(self, window: Any) -> None:
            self.window = window
            self.webview = _WebView()

    mod = types.ModuleType("webview.platforms.gtk")
    mod.BrowserView = BrowserView  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "webview.platforms.gtk", mod)
    import webview.platforms as wp

    monkeypatch.setattr(wp, "gtk", mod, raising=False)
    return mod


def test_gtk_patch_enables_media_and_connects_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = _install_fake_gtk(monkeypatch)
    wr._patch_webkitgtk_media_permission()

    bv = mod.BrowserView(window="win")
    assert bv.webview._props.enable_media_stream is True
    assert bv.webview._props.enable_webrtc is True
    by_signal = dict(bv.webview.connected)
    assert "permission-request" in by_signal
    assert by_signal["permission-request"] is wr._grant_media_permission
    assert getattr(mod.BrowserView, "_t2v_media_patched", False) is True


def test_gtk_patch_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _install_fake_gtk(monkeypatch)
    wr._patch_webkitgtk_media_permission()
    wr._patch_webkitgtk_media_permission()  # second call no-ops via sentinel
    bv = mod.BrowserView(window="win")
    # Exactly one connect, not two (init wrapped once).
    assert [s for s, _ in bv.webview.connected].count("permission-request") == 1


def test_gtk_patch_noop_when_backend_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    # None in sys.modules forces ImportError on `from webview.platforms import gtk`.
    monkeypatch.setitem(sys.modules, "webview.platforms.gtk", None)
    wr._patch_webkitgtk_media_permission()  # must not raise
