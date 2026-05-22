# ADR-0030: Web chat UI via pywebview subprocess + Unix-socket bridge

**Status:** Accepted
**Date:** 2026-05-22
**Phase:** F
**Supersedes:** ADR-0029 (the floating non-modal *XDialog* chat window)
**Superseded by:** —

## Context

ADR-0029 replaced the broken LibreOffice sidebar with a non-modal
`XDialog` constructed from `chat_panel.xdl`. The 2026-05-22 repro on
LO 26.2.3.2 Debian showed two symptoms with that approach:

1. **Slow** — every text-stream chunk from the engine requires a
   round-trip through `UIThreadDispatcher.run_sync` → UNO
   `setPropertyValue("Text", current + chunk)`. The serial UNO
   marshalling is fundamentally incompatible with chat-level
   throughput.
2. **Doesn't send messages** — the chat worker thread initialised
   the `AsyncCallback` service and stopped. The Python UI-thread
   bridge can deadlock or silently drop callbacks when the
   sidebar/dialog VCL window isn't a peer the framework expects.

User direction (verbatim): *"can we use the same web style
interface as the word integration which works really well so we can
reuse code and keep it as close to the platform as possible?"*

Talk2View-Word ships a React + `@talk2view/sdk/ui` taskpane. The
`Talk2View` SDK component is host-neutral — it accepts
`partnerKey`, `baseUrl`, and an array of tools whose `execute`
callbacks can do whatever the host environment supports. For Word,
tools call `Word.run(...)` (Office.js). For LibreOffice Writer we
need an equivalent host bridge that reaches the UNO
`XComponentContext`.

## Decision

Three-layer architecture:

```
┌──────────────────────────────────────────────────────────────────┐
│ pywebview subprocess  (python3 -m talk2view_writer.web_runner)   │
│ ┌─────────────────────────────────────────────────────────────┐  │
│ │ WebKitGTK window → React + @talk2view/sdk/ui + writerTools  │  │
│ │                                                             │  │
│ │   tool.execute(args) → window.pywebview.api.invoke_tool     │  │
│ └──────────────────┬──────────────────────────────────────────┘  │
│                    │ pywebview JS-API bridge                     │
│                    ▼                                             │
│   BridgeClient (Unix-socket JSON-RPC, newline-delimited)         │
└────────────────────┬─────────────────────────────────────────────┘
                     │ AF_UNIX, chmod 0600
                     ▼
┌──────────────────────────────────────────────────────────────────┐
│ LibreOffice process  (Python extension)                          │
│ ┌─────────────────────────────────────────────────────────────┐  │
│ │ BridgeServer ── _dispatch ── @ui_thread_tool ── UNO calls   │  │
│ └─────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

Concretely:

- The chat UI is **the same React + Talk2View SDK code Word runs**,
  bundled by webpack from `src/web/`. Only difference: tool
  `execute` bodies call `invokeTool(name, args)` (Unix socket back
  to LO) instead of `Word.run(...)`.
- pywebview runs in a **subprocess** (`talk2view_writer.web_runner`)
  spawned by the LO extension when the user clicks
  **Talk2View → Open Chat**. pywebview's `webview.start()` enforces
  main-thread execution; LO's main thread is owned by LO's UI loop;
  the subprocess gets its own main thread.
- The subprocess connects to a **Unix-socket JSON-RPC server**
  (`talk2view_writer.bridge_server.BridgeServer`) the LO extension
  started before spawning. Each tool call is one line of JSON
  (`{"id","method","params"}`); each response one line back.
- LO-side tool dispatch reuses the existing
  `talk2view_writer.tools.*` functions — they're already
  `@ui_thread_tool` decorated so the UNO calls marshal to LO's UI
  thread inside the tool body.

The chat itself (auth, message stream, tool orchestration) lives
entirely in the browser-side `@talk2view/sdk/ui`. The Python side
sees only one event type from the SDK: "execute this tool with
these args, give me back JSON." That cleanly separates the
host-bridge from the chat-protocol concerns.

## Alternatives considered

- **Keep ADR-0029 UNO dialog and fix the slowness / send hang** —
  rejected. The fundamental cost is UNO marshalling per chat
  chunk; even with the best `setPropertyValue` batching this is
  orders of magnitude slower than DOM updates. And the
  send-message hang in the 2026-05-22 repro is the same family of
  bug as the LO 26.x sidebar parent-window issues — VCL/Python
  bridge fragility. Web-based UI sidesteps both.

- **System browser + WebSocket** — rejected on user preference for
  a native-window feel ("close to the platform"). The
  architecture is identical otherwise (same React, same SDK, same
  bridge) and is a documented fallback if pywebview is found
  infeasible on a future LO build.

- **pywebview in-process (no subprocess)** — rejected by hard
  empirical block: `webview.start()` raises
  `WebViewException('pywebview must be run on a main thread.')` if
  invoked off the calling process's main thread. LO's main thread
  is unavailable.

- **PyQt6 + QtWebEngine bundled** — rejected. ~80 MB of Chromium
  per platform vs ~40 KB pywebview + the OS's existing webview
  (WebKitGTK on Linux, native WKWebView on macOS, Edge WebView2 on
  Windows). pywebview's bundle profile is right for an enterprise
  desktop extension; QtWebEngine is right for products where
  rendering parity matters more than .oxt size.

- **Custom toolbar via XLayoutManager** — investigated during the
  ADR-0029 deliberations; rejected because the docked-tool API
  hits the same XSidebarPanel/XToolPanel pipeline that's broken
  on LO 26.x.

## Consequences

- **Pros:**
  - Single chat UI source-of-truth shared with Talk2View-Word.
    Changes to the SDK's chat components propagate to both
    integrations.
  - DOM-rate text streaming. No UNO bottleneck on render.
  - The Python side only sees tool invocations — small, testable
    surface (~250 lines across `bridge_server.py` +
    `web_runner.py` vs ~700 LOC for the prior UNO chat panel).
  - Auth, message history, tool orchestration, and approval flow
    all live in the SDK component — we get them for free, and
    they evolve outside this repo.
  - The host bridge is the only thing that needs to change for
    macOS / Windows ports.

- **Cons:**
  - Build pipeline adds Node + webpack alongside Python + uv. The
    Makefile `build-web` target installs npm deps on first build
    (one-off).
  - The .oxt grows by ~5.7 MB (Shiki syntax-highlighting accounts
    for ~4.5 MB of that; bundling without Shiki is a future
    optimisation if size matters).
  - pywebview has per-platform native deps: pygobject + WebKit2
    on Linux (Debian: `apt install python3-gi gir1.2-webkit2-4.1`,
    which LO 26.2 already pulls in transitively); pyobjc on
    macOS (pre-installed with system Python); pythonnet + Edge
    WebView2 on Windows (WebView2 pre-installed since Windows 10
    build 16299).
  - The subprocess + socket adds two failure modes the in-process
    dialog didn't have (subprocess spawn failure, socket bind
    failure). Both surface as actionable errors via the existing
    `logger.exception` envelope.

- **Follow-up:**
  - `_resolve_python` ports for macOS + Windows (currently
    Linux-only / Debian-tested).
  - Subprocess lifecycle: kill the webview window when LO exits;
    refocus the existing window when the user re-clicks Open
    Chat (currently rebinds the socket and spawns afresh).
  - Drop the legacy Python `Talk2ViewSDKClient` /
    `show_login_dialog` once the web auth flow is verified
    end-to-end — the SDK-in-browser already does login.
  - Trim the bundle: lazy-load Shiki (it ships with the SDK but
    we don't need code blocks for the MVP chat flow).

## References

- Code: `src/talk2view_writer/bridge_server.py`,
  `src/talk2view_writer/web_runner.py`,
  `src/talk2view_writer/ui/web_window.py`, `src/web/` (Node
  project + React app), `extension/web/` (build output).
- Tests: `tests/unit/test_bridge_server.py` (11 tests covering
  protocol shape, allowlist, lifecycle).
- ADR superseded: ADR-0029.
- Investigation: `docs/investigations.md` #29 (final closure
  note pointing here).
