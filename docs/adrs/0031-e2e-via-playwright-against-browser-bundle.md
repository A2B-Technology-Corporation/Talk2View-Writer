# ADR-0031: E2E via Playwright against the browser-bundle in Chromium, not pywebview

**Status:** Accepted
**Date:** 2026-05-22
**Phase:** G
**Supersedes:** —
**Superseded by:** —

## Context

Talk2View-Writer's chat UI is a webpack bundle (React 19 + `@talk2view/sdk/ui`)
loaded into a pywebview subprocess on the user's machine — see ADR-0030.
The bundle runs over `file://` and reaches `engine.talk2view.com` via a
Unix-socket bridge to LibreOffice's Python, which proxies the HTTPS
calls through `httpx` (so the engine never sees a `file://` Origin and
the response side-steps CORS).

For E2E coverage the user has asked for **Playwright-style workflows
on every supported OS with screenshots saved as artifacts** so failures
can be reviewed by reading the screenshot, not by guessing from logs.

Two ways we could drive the chat UI from Playwright:

1. **Drive the real pywebview window.** Pywebview wraps WebKitGTK
   (Linux), WKWebView (macOS), and Edge WebView2 (Windows). Playwright
   does **not** target any of these — only Chromium, Firefox, and
   WebKit (Apple's). The closest match is `playwright.chromium`, which
   can attach to a Chrome DevTools Protocol endpoint. WebKitGTK, WKWebView,
   and WebView2 do **not** expose CDP. Driving the real pywebview
   would require platform-specific UI automation (AT-SPI on Linux,
   accessibility APIs on macOS, UI Automation on Windows) — what the
   existing `tests/integration/test_dogtail.py` already attempts at a
   coarse level.
2. **Drive a Chromium tab against the same bundle.** Serve the
   bundle over `http://localhost:N`, point a fresh Chromium page at it,
   pre-inject a stub `window.pywebview.api` object so the bridge calls
   resolve locally, and run Playwright assertions against the React tree.
   The bundle code is identical to what ships in the `.oxt`; we are
   testing the **app behaviour** rather than the **embedding shell**.

The boundary between "app behaviour" and "embedding shell" matters here:
WebKitGTK-specific bugs (the WebKitGTK CORS quirk that needed
`allow_universal_access_from_file_urls`, the subprocess spawn lifecycle,
the Unix-socket bridge) are platform behaviours testable from Python.
Everything else — auth UI, chat composer behaviour, message rendering,
tool-call approval, error states, streaming UX, every one of the 20
writer tools — is React + SDK logic that runs identically inside a
Chromium tab.

## Decision

We will run Playwright E2E specs against the **bundled JS app served from
a local HTTP server**, with a **mock engine fixture** providing the
`engine.talk2view.com` surface deterministically. A small page-init
shim injects `window.pywebview.api` so the bridge calls (`invoke_tool`,
`proxy_fetch`, `log`) resolve against the same in-process mock that
serves the engine. The `.oxt`'s actual pywebview/UNO embedding is
covered separately by the existing integration suite (`tests/integration/`)
which already exercises the real soffice + the real bridge.

Concretely:

- `tests/e2e/` houses Playwright TypeScript specs (`*.spec.ts`).
- `tests/e2e/fixtures/mock-engine.ts` is a small Node HTTP server that
  responds to `/v1/config`, `/v1/tools/register`, `/v1/sessions`,
  `/v1/sessions/{id}/messages` (SSE stream), and the auth endpoints.
  Scenarios are pre-seeded; the SSE stream is scriptable per-test.
- `tests/e2e/fixtures/pywebview-shim.ts` is the JS injected into the
  page before navigation, exposing `window.pywebview.api` and mirroring
  every method the bundle calls.
- `playwright.config.ts` at the repo root defines the projects:
  Chromium for cross-OS smoke, WebKit for macOS-flavoured parity
  (closest Playwright target to WKWebView).
- The existing GHA `integration` matrix gains a sibling
  `e2e` matrix that runs the Playwright specs on Linux + macOS + Windows
  with `--screenshot=only-on-failure --trace=retain-on-failure --video=retain-on-failure`
  and uploads all three as artifacts named for the run.

## Alternatives considered

- **Drive pywebview directly via dogtail / UI Automation.** Already covered
  for sidebar-deck parity via `tests/integration/test_dogtail.py`. Driving
  the chat composer character-by-character across three GUI-automation
  libraries (one per OS) is brittle, slow, and gives us no introspection
  into the React tree. The pywebview window is a single visual surface
  to those libraries — we'd be screenshot-diffing rendered pixels, not
  asserting on app state.
- **Drive WebKitGTK via Playwright's WebKit target.** Playwright's
  `webkit` is Apple WebKit (the Safari engine), not WebKitGTK (the GTK
  port). Even though the rendering engines share lineage they are
  separate distributions with different bug surfaces; pointing at
  Apple WebKit doesn't validate WebKitGTK behaviour.
- **Embed Chromium in pywebview for tests.** Pywebview's Chromium
  backend (CEF) needs ~200 MB of native deps the production build
  doesn't ship. We'd be testing a configuration users never run.
- **Skip Playwright; rely on the existing pytest + dogtail rig.** Doesn't
  meet the user's "Playwright-style with screenshot artifacts" bar, and
  the dogtail layer has no way to read React state — tests would assert
  on pixel diffs or AT-SPI accessibility-tree shape, both fragile.

## Consequences

- **Pros**
  - Single deterministic test framework that runs across all three CI
    OSes via Playwright's official setup script (no per-OS AT-SPI
    pain).
  - Page-object model gives us hookable assertions on React state.
  - Screenshot, video, and trace artifacts on every failure — exactly
    what the user asked for, available for review post-run.
  - Mock engine eliminates flakiness from real-engine quota / outage /
    streaming latency.
  - Same bundle.js the production .oxt ships; UI bugs that survive
    `make build` are caught here.

- **Cons**
  - We are explicitly **not** testing the WebKitGTK/WKWebView/WebView2
    rendering shells in this matrix. Bugs that only manifest under
    those engines (e.g. ADR-0030's CORS workaround) still need to land
    in the Python integration suite. We will write Python-level tests
    for every embedding-shell behaviour we identify.
  - Mock engine drifts from the real engine over time. Mitigation:
    schema-based contract test that compares the mock's response shapes
    against the SDK's TypeScript types (and ideally against a snapshot
    of the real engine's OpenAPI / FastAPI schema once exposed).

- **Follow-up**
  - Add a contract test that the mock engine's response shapes match
    `@talk2view/sdk` types.
  - Add a `tests/e2e/specs/streaming.spec.ts` once ADR-0032 (streaming
    SSE proxy) lands.
  - Extend the GHA `integration` matrix's `_diag/` artifact pattern to
    cover Playwright artifacts uniformly.

## References

- Code: `playwright.config.ts`, `tests/e2e/`
- Related ADRs: ADR-0024 (existing mock-chat rig), ADR-0030 (pywebview
  subprocess architecture), ADR-0029 (chat window decision).
- External: <https://playwright.dev/docs/test-fixtures>
