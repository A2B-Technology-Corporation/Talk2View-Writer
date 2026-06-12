# ADR-0042: Floating (not status) window level on macOS so IME candidates stay visible

**Status:** Accepted
**Date:** 2026-06-12
**Phase:** G
**Supersedes:** —
**Superseded by:** —

## Context

The companion chat window opens with `on_top=True` (ADR-0039) so it
floats over the LibreOffice document like a docked deck. pywebview's
Cocoa backend implements `on_top` as
`NSWindow.setLevel_(NSStatusWindowLevel)` — level 25 — and exposes no
other level through its API (`webview/platforms/cocoa.py`, the
`if window.on_top:` branch of `BrowserView.__init__`).

macOS draws the input-method candidate window (Chinese/Japanese IME
candidate bar) below level 25. Result: typing Chinese in the chat box
left the composition underline visible in the input field, but the
candidate bar rendered *behind* the panel — only a sliver peeked out
past the window edge. Reported on macOS arm64, LibreOffice 26.x.

LibreOffice's own document windows sit at `NSNormalWindowLevel` (0), so
any level above 0 preserves the float-over-document behaviour; 25 was
far higher than the docking policy needs.

## Decision

After pywebview's `BrowserView.__init__` runs, re-lower the chat
window to `NSFloatingWindowLevel` (3): above LibreOffice's
normal-level windows, below the IME candidate window. Implemented as
`_patch_cocoa_window_level()` in `web_runner.py`, wrapping
`BrowserView.__init__` with a sentinel guard — the same intentional
monkey-patch idiom as the WebKitGTK CORS patch and the Cocoa media
patch (ADR-0041). Nothing re-raises the level afterwards: pywebview
only touches it again via the `Window.on_top` setter, which we never
call.

## Alternatives considered

- **Drop `on_top` entirely** — the panel falls to normal level and no
  longer floats over the document when LibreOffice has focus; breaks
  the ADR-0039 docked-deck UX.
- **`window.on_top = False` after show** — same outcome as above via
  pywebview's `set_on_top` (status ↔ normal only); pywebview offers no
  intermediate level.
- **Upstream a `window_level` option to pywebview** — right long-term
  fix, but release-blocking on an upstream cycle; the wrap is three
  lines and removable if upstream lands it.

## Consequences

- **Pros** — IME candidate bar visible while typing in the chat box;
  docked-deck float over LibreOffice retained (3 > 0); pattern matches
  the repo's existing backend patches.
- **Cons** — one more coupling to pywebview's Cocoa internals
  (`BrowserView.__init__` storing the NSWindow as `self.window`); a
  pywebview upgrade that renames it degrades to a logged exception and
  the old status-level behaviour, not a crash.
- **Follow-up** — manual verification with a live IME on macOS arm64
  (no headless IME in CI); covered structurally by
  `tests/unit/test_web_runner_window_level.py`.

## References

- Code: `src/talk2view_writer/web_runner.py` (`_patch_cocoa_window_level`)
- Tests: `tests/unit/test_web_runner_window_level.py`
- Related ADRs: ADR-0030, ADR-0039, ADR-0041
- External: pywebview `webview/platforms/cocoa.py` (`on_top` →
  `NSStatusWindowLevel`); AppKit window levels: Normal=0, Floating=3,
  Status=25
