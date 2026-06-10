# ADR-0041: Grant microphone (getUserMedia) across all three webview backends

**Status:** Accepted
**Date:** 2026-06-10
**Phase:** G
**Supersedes:** —
**Superseded by:** —

## Context

The Talk2View SDK's voice / speech-to-text button calls
`navigator.mediaDevices.getUserMedia({audio: true})` from the chat UI,
which runs inside the pywebview subprocess (ADR-0030). On Linux this
fails with `NotAllowedError: The request is not allowed by the user
agent`, and the SDK logs "Microphone access denied".

Diagnosis (live-reproduced on WebKitGTK 2.52.3 and grounded in the
WebKitGTK / WKWebView / WebView2 source + docs — see
docs/investigations.md #58):

- **It is the webview engine default-denying, not the OS, not our code,
  and not the origin.** Every embedded webview refuses media capture
  unless the *host application* explicitly grants it; pywebview's
  backends grant it on none. On WebKitGTK, `getUserMedia` raises a
  `WebKitWebView::permission-request` carrying a
  `WebKitUserMediaPermissionRequest`, and an *unhandled* request is
  denied by default. pywebview connects no such handler.
- **`file://` is a secure context** for getUserMedia (confirmed: the
  page reports `isSecureContext === true`, and the failure is
  `NotAllowedError`, not the `TypeError` an insecure origin produces).
  So serving the UI over `http://` would not help.
- **The mic code lives in the compiled `@talk2view/sdk` bundle**, not in
  our own `src/web/`, and calls the standard API correctly. The only
  lever we own is the host process (`web_runner.py`), which already
  monkey-patches pywebview's GTK backend for CORS / window identity
  (ADR-0030 / ADR-0039).

## Decision

Add three per-OS permission grants in `web_runner.main()`, siblings to
`_patch_webkitgtk_cors_settings`, each self-guarded by importing its own
pywebview backend module (so exactly one applies per OS; the others
no-op). The grant logic is shared where possible via a duck-typed,
`gi`-free module-level `_grant_media_permission`.

- **Linux / WebKitGTK** (`_patch_webkitgtk_media_permission`): wrap
  `BrowserView.__init__` to set `enable_media_stream` (+ `enable_webrtc`
  where present) and `connect("permission-request",
  _grant_media_permission)`, which `allow()`s the UserMedia /
  DeviceInfo requests. **Live-verified end-to-end**: with the handler,
  `getUserMedia` resolves with an audio track; without it, the exact
  `NotAllowedError` reproduces.
- **macOS / WKWebView** (`_patch_cocoa_media_permission`): subclass
  pywebview's `BrowserView.BrowserDelegate` (the WKUIDelegate) to add
  `webView:requestMediaCapturePermissionForOrigin:initiatedByFrame:type:decisionHandler:`
  granting capture, and point the backend's nested-class attribute at
  the subclass (inheriting every existing delegate method).
- **Windows / WebView2** (`_patch_edgechromium_media_permission`): wrap
  `EdgeChrome.on_webview_ready` to subscribe
  `CoreWebView2.PermissionRequested`, granting Microphone / Camera.

Linux is the one platform we can drive headlessly, so it is verified
both live and in CI. macOS and Windows are correct-by-construction
against pywebview's actual backend source but **verified manually after
release** — and on macOS the grant is necessary-but-not-sufficient:
WKWebView also requires LibreOffice's *own* `NSMicrophoneUsageDescription`
+ a one-time TCC consent, which an `.oxt` cannot inject.

## Alternatives considered

- **Serve the UI over `http://127.0.0.1`** — rejected. `file://` is
  already a secure context, so this would not change a permission
  `NotAllowedError`; it adds a local HTTP server and would re-break the
  `file://`-origin CORS relaxation the existing patch depends on.
- **Patch pywebview upstream / fork it** — rejected (for now). Slower
  feedback loop; we already monkey-patch its backends for CORS and
  window identity, so this is idiomatic for our integration. An upstream
  PR is a reasonable follow-up.
- **Drop the SDK voice feature** — rejected. Speech-to-text is a wanted
  capability; the call is correct, only the host grant was missing.
- **Hard-set only the `enable_media_stream` setting** — rejected as
  insufficient: pywebview already sets it, yet capture still denies
  because the permission-request handler is what is missing.

## Consequences

- The SDK voice button works on Linux immediately. macOS / Windows are
  wired and unit-tested but pending manual verification + (macOS) the LO
  host entitlement.
- The grant is **unconditional** for media/device requests. This is
  acceptable because our webview only ever loads our own bundled
  `file://` chat UI — there is no untrusted third-party content that
  could abuse the grant.
- Testing: cross-platform unit tests drive each patch against fake
  backend modules (`tests/unit/test_web_runner_media.py`); a Linux-only
  `gui_smoke` test drives a live `WebKit2.WebView` through the real
  `getUserMedia` flip (`tests/integration/test_webkit_media_permission.py`),
  with CI best-effort provisioning WebKit2 + a PulseAudio virtual mic
  (skips cleanly if unavailable).
- New `web_runner.main()` now wraps `BrowserView.__init__` three times
  on GTK (transient / CORS / media); each captures the then-current
  `original_init` and is sentinel-guarded, so they chain safely.

See docs/investigations.md #58.
