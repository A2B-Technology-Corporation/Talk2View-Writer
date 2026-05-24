# ADR-0036: Live E2E via Playwright + bridge-routed shim + real soffice + real engine

**Status:** Accepted
**Date:** 2026-05-24
**Phase:** G
**Supersedes:** —
**Superseded by:** —

## Context

ADR-0031 set up Playwright E2E specs that drive the chat-UI bundle in
Chromium against a per-test MockEngine. Fast, deterministic, no
secrets — but it doesn't exercise:

  - The real engine's tool-call/result/resume loop. Bugs like
    Platform #62 (model loops on identical `format_text` calls) and
    Platform #63 (model doesn't batch `format_text(queries=[...])`)
    cannot reproduce against a mock that doesn't run a real LLM.
  - The Python tool implementations. Synthetic-UNO unit tests
    cover their argument validation but not their UNO interaction
    against a real Writer document.
  - The bridge_server's Unix-socket JSON-RPC contract against the
    bundle's actual `window.pywebview.api` calls.

The user asked for *"the penguin short story test as part of the
Playwright-style E2E test in github workflows so that all the
features are tested exactly as close as possible to how a real
user would use it"*, with screenshots and logs as artifacts that
Claude can use to improve the app and fix bugs.

The product isn't a web app — it's a LibreOffice extension. Driving
the real pywebview window via Playwright doesn't work: pywebview
embeds WebKitGTK / WKWebView / WebView2, none of which expose CDP,
so Playwright can't attach. ADR-0031 rejected GUI-automation
approaches (xdotool / AppleScript / UI Automation) as brittle and
per-OS.

## Decision

Drive the chat-UI bundle in Playwright-Chromium with a custom
`pywebview` shim whose `window.pywebview.api.*` methods route to a
Node bridge-proxy. The proxy translates each HTTP call into the
Python `BridgeServer`'s newline-delimited JSON-RPC over its Unix
socket. The bridge_server runs inside a real soffice with the
Talk2View-Writer extension installed and `T2V_WRITER_HEADLESS_BRIDGE=1`
set so the extension starts the bridge but skips the pywebview
spawn — letting our Node bridge-proxy own the single bridge
connection. Tool calls go through to real Python tool functions
mutating a real Writer document. Engine calls go through
`proxy_fetch` / `proxy_stream_*` to the real `engine.talk2view.com`.

End-to-end coverage on the same code path the user sees, minus the
WebKitGTK rendering shell (which Chromium replaces). The bundle JS
itself is identical to what ships in the .oxt.

```
┌────────────────────────┐    HTTP    ┌──────────────┐  unix-socket  ┌──────────────────┐
│ Playwright + Chromium  │ ─────────▶ │ Bridge-proxy │ ───────────▶  │ BridgeServer     │
│  bundle JS             │            │  (Node, TS)  │   JSON-RPC    │  (Python in LO)  │
│  + live shim           │            │              │               │                  │
│  + pre-seeded tokens   │ ◀───────── │              │ ◀───────────  │  invoke_tool →   │
└────────────────────────┘   results  └──────────────┘   results     │   Python tools → │
        │                                                            │   real Writer doc│
        │ proxy_fetch /                                               └──────────────────┘
        │ proxy_stream_*                                                       │
        ▼                                                                     │
   engine.talk2view.com  ◀─────────── via the BridgeServer's httpx ───────────┘
```

### Concrete pieces

| File | Role |
|---|---|
| `src/talk2view_writer/ui/web_window.py` | Honours `T2V_WRITER_HEADLESS_BRIDGE=1` to start the bridge without spawning pywebview. |
| `scripts/start_headless_bridge.py` | UNO helper: dispatches `vnd.com.talk2view.writer:showPanel`, scrapes `talk2view.log` for the bridge socket path. |
| `tests/e2e/fixtures/bridge-proxy.ts` | Node HTTP server that translates browser requests into bridge JSON-RPC; SSE for `proxy_stream_*`. |
| `tests/e2e/fixtures/live-pywebview-shim.ts` | Browser-side `window.pywebview.api` that calls bridge-proxy endpoints. EventSource-driven streaming. |
| `tests/e2e/fixtures/live-test-fixtures.ts` | Playwright `test.extend` providing `liveBridgeProxy`, `liveBundleServer`, and `liveEngineLogin`. |
| `tests/e2e/scenarios/penguin_story.yaml` | Scripted user prompts + loose expected outcomes per step. |
| `tests/e2e/specs/live-bridge-smoke.spec.ts` | Round-trip sanity: list_tools, get_document, error path. |
| `tests/e2e/specs/live-penguin-story.spec.ts` | Drives the chat composer through the scenario; full artifact capture. |
| `.github/workflows/ci.yml` `e2e-live` job | Linux-only CI job that installs LO + python3-uno, spawns soffice headless under Xvfb, runs only `tests/e2e/specs/live-` specs, uploads all artifacts. |

### Artifacts captured per scenario step

Per step `_NN`:
  - `step_NN_pre.png` — Playwright fullPage screenshot before the prompt.
  - `step_NN_post.png` — fullPage after the streaming reply completes.
  - `step_NN_transcript.json` — prompt, assistant text, tool calls
    (just this step), full doc state via direct bridge call.

Per run:
  - `expected_vs_actual.json` — soft-failure digest across all steps.
  - `_diag/xvfb/screenshot_*.png` — periodic Xvfb desktop snapshots.
  - `_diag/talk2view.log` — full bridge + engine traffic.
  - `_diag/soffice.stdout.log`, `soffice.stderr.log` — soffice itself.
  - Playwright's standard `trace.zip`, `video.webm`, HTML report.

## Alternatives considered

- **A. Drive the real pywebview chat window via xdotool / AppleScript / UI Automation.**
  Closest to "real user" but brittle (pixel-based selectors), per-OS
  tooling, no introspection into the React tree. Existing dogtail
  attempt failed because WebKitGTK exposes little of its DOM via
  AT-SPI.

- **B. Pywebview's Chromium (CEF) backend in test mode + Playwright via CDP.**
  Real production code path, just CEF-rendered instead of WebKitGTK.
  Requires bundling the 200 MB CEF runtime in CI; same
  "not-WebKitGTK" caveat as the chosen design but with much heavier
  setup.

- **C. (chosen) Playwright + bundle + shim to real bridge to real soffice + real engine.**
  Same bundle JS, same Python tools, same engine, just the rendering
  engine swapped (Chromium ↔ WebKitGTK). One implementation; no
  per-OS GUI tooling; Playwright's screenshot / trace / video
  artifacts native.

- **D. C plus an A-style suite on Linux for shell parity.**
  Most comprehensive; deferred. The dogtail infrastructure is
  preserved in `tests/integration/test_dogtail.py` (currently
  skipped post-ADR-0029) ready for a rewrite against the floating
  chat window when WebKitGTK-specific bugs justify the effort.

## Consequences

### Pros

- Bugs like Platform #62 / #63 reproduce in CI — the scenario's
  `tool_calls.max_count` and `no_duplicate_with` constraints fail
  loudly when the model loops or fails to batch.
- The Python tool surface gets real-document coverage every PR.
  Tool-shape drift (Writer #3-style "phantom paragraph" regressions)
  shows up immediately in the per-step doc-state transcript.
- Screenshots + transcript dumps make failure modes inspectable
  post-run via `gh run download`, no SSH-to-runner needed.
- Live shim + bridge-proxy are general-purpose: any future spec
  that wants real-engine coverage just imports `test` from
  `live-test-fixtures.ts`.

### Cons

- WebKitGTK rendering shell is **not** exercised. Bugs that fire
  only on WebKitGTK (e.g. ADR-0030's CORS workaround) still need
  the existing Python integration suite (`tests/integration/`).
- Every PR + push hits the real engine via Word's partner key
  (ADR-0034). Single-test scenario ≈ 4-6 LLM calls. At list rate
  this adds up; acceptable for the coverage in exchange.
- Real LLM means non-deterministic output. Assertions are loose by
  design (substring match, paragraph-count ranges, tool-name set
  rather than exact sequence). Strict comparisons are encoded as
  soft-failures in `expected_vs_actual.json`, not test failures —
  tighten them as patterns stabilise.
- Linux-only. Python `BridgeServer` is `AF_UNIX`. Windows specs
  use `test.skip(process.platform === 'win32', …)` with the
  architectural reason in the comment.

### Follow-up

- Wire **Talk2View-Writer's own partner key** when Platform #61
  ships. Today the live tests authenticate via Word's profile per
  ADR-0034.
- **Tighten scenario expectations** as the assistant's behaviour
  stabilises. Each green run's `expected_vs_actual.json` is the
  source for what to tighten.
- **More scenarios.** Penguin story is the first; future scenarios:
  table editing, comment threads, multi-section page setup. Each
  is just another `.yaml` + a tiny spec that points at it.
- **Tighten the rendering-shell gap** with a small dogtail-driven
  smoke that opens the floating chat and asserts one round-trip,
  catching WebKitGTK-specific bugs the bundle-in-Chromium path
  misses.

## References

- Code: `tests/e2e/fixtures/live-*.ts`, `tests/e2e/scenarios/`, `tests/e2e/specs/live-*.spec.ts`, `scripts/start_headless_bridge.py`, `src/talk2view_writer/ui/web_window.py` (env-var hook).
- CI: `.github/workflows/ci.yml` `e2e-live` job.
- Related ADRs: ADR-0030 (pywebview subprocess architecture), ADR-0031 (existing mock-engine Playwright), ADR-0034 (Word partner key route-around).
- Platform issues caught by this design: #62 (tool loop), #63 (no batching).
