"""Unit tests for the Windows / WebView2 microphone patch (ADR-0041).

Runs in the venv with a fake ``webview.platforms.edgechromium`` backend +
a fake ``Microsoft.Web.WebView2.Core``, so the PermissionRequested wiring
is exercised on any OS. The real WebView2 capture path is verified
manually post-release (no headless Windows mic in CI); see
docs/investigations.md #58.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from talk2view_writer import web_runner as wr

pytestmark = pytest.mark.unit


def _install_fake_edge(monkeypatch: pytest.MonkeyPatch) -> Any:
    class _Kind:
        Microphone = "mic"
        Camera = "cam"
        Geolocation = "geo"

    class _State:
        Default = "default"
        Allow = "allow"
        Deny = "deny"

    core = types.ModuleType("Microsoft.Web.WebView2.Core")
    core.CoreWebView2PermissionKind = _Kind  # type: ignore[attr-defined]
    core.CoreWebView2PermissionState = _State  # type: ignore[attr-defined]
    for name in ("Microsoft", "Microsoft.Web", "Microsoft.Web.WebView2"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "Microsoft.Web.WebView2.Core", core)

    class _PermissionRequested:
        def __init__(self) -> None:
            self.handlers: list[Any] = []

        def __iadd__(self, handler: Any) -> Any:
            self.handlers.append(handler)
            return self

    class _Core:
        def __init__(self) -> None:
            self.PermissionRequested = _PermissionRequested()

    class _WebView2:
        def __init__(self) -> None:
            self.CoreWebView2 = _Core()

    class EdgeChrome:
        def __init__(self) -> None:
            self.webview = _WebView2()
            self.ready_calls = 0

        def on_webview_ready(self, sender: Any, args: Any) -> None:
            self.ready_calls += 1

    class _Event:
        def __init__(self, kind: str) -> None:
            self.PermissionKind = kind
            self.State = _State.Default

    mod = types.ModuleType("webview.platforms.edgechromium")
    mod.EdgeChrome = EdgeChrome  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "webview.platforms.edgechromium", mod)
    import webview.platforms as wp

    monkeypatch.setattr(wp, "edgechromium", mod, raising=False)
    return mod, _Event, _Kind, _State


def test_edge_patch_grants_microphone_and_camera(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod, event_cls, kind, state = _install_fake_edge(monkeypatch)
    wr._patch_edgechromium_media_permission()

    ec = mod.EdgeChrome()
    ec.on_webview_ready(sender=None, args=None)  # patched wrapper
    assert ec.ready_calls == 1  # original still runs

    handlers = ec.webview.CoreWebView2.PermissionRequested.handlers
    assert len(handlers) == 1
    handler = handlers[0]

    mic = event_cls(kind.Microphone)
    handler(None, mic)
    assert mic.State == state.Allow

    cam = event_cls(kind.Camera)
    handler(None, cam)
    assert cam.State == state.Allow

    geo = event_cls(kind.Geolocation)
    handler(None, geo)
    assert geo.State == state.Default  # non-media requests left untouched

    assert getattr(mod.EdgeChrome, "_t2v_media_patched", False) is True


def test_edge_patch_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    mod, _event, _kind, _state = _install_fake_edge(monkeypatch)
    wr._patch_edgechromium_media_permission()
    wr._patch_edgechromium_media_permission()  # sentinel -> no double wrap
    ec = mod.EdgeChrome()
    ec.on_webview_ready(sender=None, args=None)
    # Wrapped once: one subscription, original ran once.
    assert len(ec.webview.CoreWebView2.PermissionRequested.handlers) == 1
    assert ec.ready_calls == 1


def test_edge_patch_noop_when_backend_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "webview.platforms.edgechromium", None)
    wr._patch_edgechromium_media_permission()  # must not raise
