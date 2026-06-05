# Talk2View-Writer

[![CI](https://github.com/A2B-Technology-Corporation/Talk2View-Writer/actions/workflows/ci.yml/badge.svg)](https://github.com/A2B-Technology-Corporation/Talk2View-Writer/actions/workflows/ci.yml)
[![License: MPL 2.0](https://img.shields.io/badge/License-MPL_2.0-blue.svg)](LICENSE)

AI-powered document assistant for **LibreOffice Writer**. Sibling project to
[Talk2View-Word](../Talk2View-Word/) (Microsoft Word). Adds a Talk2View chat
companion window to LibreOffice Writer and executes Writer-native versions of
the Talk2View-Word tool catalog through the UNO API.

## Install (end users)

Download the latest `Talk2ViewWriter.oxt` from the
[Releases page](https://github.com/A2B-Technology-Corporation/Talk2View-Writer/releases/latest),
then either:

- **From the LibreOffice GUI**: `Tools → Extension Manager → Add...` and
  pick the `.oxt`. Accept the MPL 2.0 license when prompted.
- **From the command line**:

  ```bash
  unopkg add --force Talk2ViewWriter.oxt
  ```

Restart LibreOffice Writer; open the chat from **Talk2View → Open
Talk2View Chat** in the menu bar.

The `.oxt` is a single universal package — same file works on Linux
(x86_64 + aarch64), macOS (Intel + Apple Silicon), and Windows x86_64,
across Python 3.10–3.13. The cross-platform `pydantic_core` wheel
matrix is bundled in the package; the right one is selected
automatically at first launch (see [ADR-0023](docs/adrs/0023-vendor-pydantic-core-wheels.md)).

### Supported LibreOffice builds

The chat is a pywebview companion window (not a sidebar deck — the LO
26.x sidebar framework can't host a Python panel; see
[ADR-0029](docs/adrs/0029-floating-chat-window.md) /
[ADR-0030](docs/adrs/0030-web-chat-via-pywebview-subprocess.md)). It
works on any build that ships a stock PyUNO bridge: TDF `.deb`/`.dmg`/
`.msi` downloads (7.x–26.x), Flatpak, Snap, AppImage, macOS Homebrew
Cask, and Debian/Ubuntu apt packages.

The window integrates with LibreOffice as a docked side panel where the
platform allows — branded + grouped on every desktop, with true
edge-docking and child-of-LO stacking on X11, macOS, and Windows. On a
Wayland session it is branded/grouped/tall + drag-to-snap (the
compositor disallows client positioning/reparenting). See
[ADR-0039](docs/adrs/0039-companion-window-docking.md).

## Status

Phase F — packaging + comprehensive test rig complete. See
[`plan`](../../.claude/plans/i-want-to-make-rustling-eich.md) for the
full roadmap. The current branch covers:

- Chat runs in a pywebview companion window (ADR-0030) that integrates
  as a docked side panel where the platform allows (ADR-0039); the React
  UI handles every SDK event type (`text`, `status`, `todos`,
  `tool_call`, `error`, `done`).
- All 20 tools registered with the SDK; tool-call interrupts
  auto-execute on the worker thread and marshal UNO calls back to the
  UI thread (ADRs 0008, 0009, 0018, 0020).
- Cross-platform universal `.oxt` (Linux, macOS x86_64 + arm64,
  Windows) with a bundled `pydantic_core` wheel matrix (ADR-0023).
- Five test layers — 180 unit + 61 synthetic-UNO tool + 4 mock-engine
  SDK round-trip + 3 real-soffice integration + 1 live-engine chat —
  proving the panel ↔ SDK ↔ tool ↔ document loop without the engine or
  soffice when those are unavailable. See ADR-0024 for the split.

## Documentation

- [`docs/adrs/`](docs/adrs/README.md) — Architecture Decision Records (ADRs)
  for every substantive choice (sibling-project design, cloud SDK, sidebar
  deck, threading model, etc.). Start here if you want to know *why* the code
  looks the way it does.
- [`docs/investigations.md`](docs/investigations.md) — running log of
  surprising / wrong / deferred things noticed in Talk2View-Word,
  Talk2View-Platform, or LibreOffice itself. Things we *won't* fix while
  building Talk2View-Writer but don't want to lose.
- [`CLAUDE.md`](CLAUDE.md) — code standards inherited from
  `SpeedWriter-LibreOffice/CLAUDE.md` plus Talk2View-Writer-specific guidance
  (UNO threading rules, tool surface, etc.).

## Architecture

```
LibreOffice Writer
  └─ Talk2View menu → "Open Talk2View Chat"
       │
       └─ pywebview subprocess (WebKitGTK / WKWebView / WebView2)
             │  WebpackedReact+SDK bundle (src/web/)
             ├──────────────────────────────────────▶ engine.talk2view.com
             │                  (HTTPS via Python httpx proxy — bridge.py)
             │                  (auth, chat, tool registration all client-side)
             │
             ▼  invoke_tool / tool result
       Unix-socket JSON-RPC bridge ──▶ LibreOffice Python
                                            │
                                            ▼
                                     WriterTools (UNO XText / XTable / XStyle …)
```

- **Backend:** cloud (`engine.talk2view.com`) via partner key + user JWT.
- **Chat UI:** React + [`@talk2view/sdk/ui`](../Talk2View-Platform/packages/sdk-typescript/)
  bundled by webpack and loaded into a pywebview window. Auth + chat + settings
  all happen client-side in the SDK; the Python side is a thin shell that
  proxies HTTPS (CORS workaround for `file://`) and runs UNO tools.
- **Streaming:** SSE chat-completion responses are streamed chunk-by-chunk
  back to the SDK via a polled per-stream queue (ADR-0033).
- **Tools:** 20 Python functions, invoked via the bridge's `invoke_tool` RPC
  when the SDK emits a tool-call interrupt. Mirror the TypeScript tools in
  `Talk2View-Word/src/taskpane/tools/`.

## Development

```bash
make dev            # install with dev deps via uv
make lint test      # ruff + pytest
make build          # stage extension into build/Talk2ViewWriter/
make package        # produce dist/Talk2ViewWriter.oxt
make install-oxt    # install into user's LibreOffice profile
```

After `make install-oxt`, restart LibreOffice Writer and pick
**Talk2View → Open Talk2View Chat** from the menu bar. A Talk2View chat
companion window backed by pywebview opens with the bundled React UI,
docked beside LibreOffice where the platform allows (ADR-0039).

### Tests

Four layers, each runs by default in CI:

  - `tests/unit/` — pure-Python helpers + mocked-UNO unit tests.
    `make test-unit` runs these locally.
  - `tests/synthetic/` — tool bodies against an in-process synthetic
    UNO model. No soffice. Same `make test-unit` runs these too.
  - `tests/integration/` — pytest against a real soffice via the UNO
    bridge. `pytest -m integration` after starting soffice with
    `--accept=...`.
  - `tests/e2e/` — Playwright specs against the chat-UI bundle in
    Chromium + WebKit. `npx playwright test`. Two flavours:
      - **Mock-engine specs** (default) — bundle plus a per-test
        `MockEngine` fixture; no secrets, no soffice. Fast and
        deterministic. See ADR-0031.
      - **Live specs** (`tests/e2e/specs/live-*.spec.ts`) — drive
        the bundle in Chromium against a **real soffice + extension
        + bridge_server + engine.talk2view.com**. See ADR-0036.
        Captures per-step screenshots + transcripts as artifacts.

### Running the live E2E suite locally

```bash
make build install-oxt          # OXT must be installed in user profile
T2V_WRITER_HEADLESS_BRIDGE=1 \
  soffice --headless --norestore --nologo --nodefault \
  --accept="socket,host=127.0.0.1,port=2002;urp;" &

# Bridge-smoke spec (no engine creds needed):
T2V_E2E_LIVE_SOFFICE_PORT=2002 \
  npx playwright test tests/e2e/specs/live-bridge-smoke.spec.ts

# Penguin-story scenario (needs real T2V account):
T2V_E2E_LIVE_SOFFICE_PORT=2002 \
T2V_TEST_USER_EMAIL=you@example.com \
T2V_TEST_USER_PASSWORD=… \
  npx playwright test tests/e2e/specs/live-penguin-story.spec.ts
```

Artifacts land under `tests/e2e/test-results/` (Playwright traces +
screenshots) and `tests/e2e/test-results/live-penguin-story/`
(per-step pre/post screenshots, transcript JSON, expected-vs-actual
digest). In CI these are uploaded by the `e2e-live` job, downloadable
with `gh run download <run-id>`.

## Project Layout

```
Talk2View-Writer/
├── extension/                       # OXT packaging files
│   ├── talk2view_writer.py          # UNO entry (XJobExecutor + protocol handler)
│   ├── description.xml
│   ├── Addons.xcu                   # "Open Talk2View Chat" menu entry
│   └── META-INF/manifest.xml
├── src/talk2view_writer/
│   ├── extension.py                 # singleton (UI thread + chat window handle)
│   ├── bridge_server.py             # Unix-socket JSON-RPC bridge (ADR-0030)
│   ├── web_runner.py                # pywebview subprocess entry point
│   ├── ui/web_window.py             # WebWindow that spawns the subprocess
│   ├── tools/                       # 20 UNO tools across 6 modules
│   ├── uno_helpers/                 # cursor, tables, styles, comments
│   └── skills/                      # SKILL.md docs (copied from Talk2View-Word)
├── src/web/                         # React + SDK chat bundle (webpack)
│   └── src/{App.tsx,bridge.ts,...}
└── tests/{unit,synthetic,integration,e2e}/
```

## License

Mozilla Public License 2.0 — the same license LibreOffice itself
ships under. Full text in [LICENSE](LICENSE). Copyright A2B Technology
Corporation Pty Ltd and contributors.

The MPL is a weak / file-scoped copyleft: derivative works of MPL-
covered files must remain MPL, but the license can be combined freely
with code under most other licenses (including proprietary) as long as
modifications to MPL-covered files are made available under MPL.
