"""Unit tests for the macOS / WKWebView microphone patch (ADR-0041).

Runs in the venv with a fake ``webview.platforms.cocoa`` backend + fake
``objc``, so the WKUIDelegate-subclassing wiring is exercised on any OS.
The real WKWebView capture path is verified manually post-release (no
headless macOS mic in CI); see docs/investigations.md #58.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from talk2view_writer import web_runner as wr

pytestmark = pytest.mark.unit

_MEDIA_SELECTOR = (
    "webView_requestMediaCapturePermissionForOrigin_initiatedByFrame_type_decisionHandler_"
)


def _install_fake_cocoa(monkeypatch: pytest.MonkeyPatch) -> Any:
    class _Delegate:  # stands in for the NSObject UI/nav delegate
        pass

    class BrowserView:
        BrowserDelegate = _Delegate

    mod = types.ModuleType("webview.platforms.cocoa")
    mod.BrowserView = BrowserView  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "webview.platforms.cocoa", mod)
    import webview.platforms as wp

    monkeypatch.setattr(wp, "cocoa", mod, raising=False)

    objc_mod = types.ModuleType("objc")
    objc_mod.selector = lambda fn, signature=None: fn  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "objc", objc_mod)
    return mod


def test_cocoa_patch_subclasses_delegate_with_grant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = _install_fake_cocoa(monkeypatch)
    original = mod.BrowserView.BrowserDelegate
    wr._patch_cocoa_media_permission()

    new = mod.BrowserView.BrowserDelegate
    assert new is not original
    assert issubclass(new, original)  # inherits all existing delegate methods
    assert hasattr(new, _MEDIA_SELECTOR)

    # The added method grants: decisionHandler(1) == WKPermissionDecisionGrant.
    captured: list[int] = []
    getattr(new(), _MEDIA_SELECTOR)(None, None, None, None, captured.append)
    assert captured == [1]
    assert getattr(mod.BrowserView, "_t2v_media_patched", False) is True


def test_cocoa_patch_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _install_fake_cocoa(monkeypatch)
    wr._patch_cocoa_media_permission()
    once = mod.BrowserView.BrowserDelegate
    wr._patch_cocoa_media_permission()  # sentinel -> no re-subclass
    assert mod.BrowserView.BrowserDelegate is once


def test_cocoa_patch_noop_when_backend_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "webview.platforms.cocoa", None)
    wr._patch_cocoa_media_permission()  # must not raise
