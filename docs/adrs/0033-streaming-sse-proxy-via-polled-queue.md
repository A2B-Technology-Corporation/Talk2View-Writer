# ADR-0033: Streaming SSE proxy via a polled per-stream queue

**Status:** Accepted
**Date:** 2026-05-22
**Phase:** G
**Supersedes:** —
**Superseded by:** —

## Context

ADR-0030 routes engine calls through `BridgeServer._proxy_fetch` to
side-step WebKit's `file://`-origin CORS rejection. That implementation
uses `httpx.Client(...).request(...)`, which **buffers the entire
response body** before returning. For non-streaming endpoints
(`/v1/config`, `/v1/tools/register`, `/v1/sessions`) this is fine —
they return tiny JSON objects in well under a second.

The chat-completion endpoint (`POST /v1/sessions/{id}/messages`) is
different. It is **Server-Sent Events** — the engine emits one
`data: { ... delta ... }` line per token (or small token batch) and
flushes after each. A user who types "summarise this 5-page document"
should see assistant text materialise progressively as the model
generates it, not after a 20-second wait while the entire response
buffers in `_proxy_fetch`.

Pywebview's JS-to-Python bridge (`js_api`) is strictly
**request-response**. There is no Python → JS push channel. So we
need a polling protocol where JS asks the bridge for chunks as they
become available, and the bridge waits on a queue fed by an httpx
streaming worker.

## Decision

Add two JSON-RPC methods to `BridgeServer`:

- **`proxy_stream_open(url, method, headers, body)`** — Spawns a worker
  thread running `httpx.stream(...)`. The worker pushes events into a
  per-stream `queue.Queue`. Returns `{stream_id}`.
- **`proxy_stream_next(stream_id)`** — Blocks until the next event is
  available on the queue. Returns one of:
  - `{"type": "headers", "status", "statusText", "headers": {lower:val}}`
  - `{"type": "chunk", "data": "..."}`
  - `{"type": "error", "message": "..."}`
  - `{"type": "done"}`
  - `{"type": "timeout"}` (after 60s with no event — JS retries)

JS-side `bridge.ts` detects streaming endpoints — either `Accept:
text/event-stream` in the request, or `/v1/sessions/{id}/messages` in
the URL — and uses these two methods to drive a `ReadableStream`-bodied
`Response`. The SDK reads `response.body.getReader()` exactly as it
would with a native fetch.

The stream is cleaned up from the bridge's registry as soon as the
consumer reads `done`. A consumer that disappears mid-stream leaks
the queue + worker until LO exits — acceptable because there is
exactly one consumer subprocess per LO session and the subprocess
dies with LO.

## Alternatives considered

- **`evaluate_js` push from Python to JS.** Pywebview supports
  calling `window.evaluate_js(...)` from any thread, which would let
  the Python worker push chunks via something like
  `window.__t2vStreamChunk('id', 'data')`. Lower latency than polling
  (one JS execution instead of an RPC round-trip per chunk) but
  requires JS-string escaping of arbitrary engine bytes, opens up
  XSS-from-engine risks if the chunks aren't paranoidly escaped, and
  the per-chunk JSON serialisation is no cheaper than the RPC.
  Polling is simpler, testable, and the per-chunk latency
  (~1-2 ms socket RTT) is invisible at LLM token rates (~10-50/s).
  Revisit if profiling shows the RPC overhead matters.

- **Two persistent socket connections.** A second socket from web_runner
  for streaming, with the bridge sending chunks unsolicited. Solves
  the latency concern but doubles the bridge surface area (two
  connection lifecycles, two error modes) for marginal benefit.

- **Long-polling with batching.** `proxy_stream_next` waits for up to N
  chunks or T ms then returns a batch. Reduces RPC count but
  complicates the JS-side ReadableStream protocol (no longer one
  chunk per pull). Not worth it at current chunk volumes.

- **Switch the SDK off SSE.** Refactor the engine to return chat
  completions as a single JSON blob. Wrong direction — users on slow
  connections + slow models would see worse UX, and the engine's
  existing SDK clients (Word task pane) depend on SSE.

## Consequences

- **Pros**
  - The chat composer feels live: tokens appear as they are
    generated, matching the production Word/web experience.
  - Streaming + non-streaming paths share the same proxy boundary
    (one URL/headers/body interface), so future endpoints get either
    treatment by accidentally selecting `Accept: text/event-stream`
    or matching the URL pattern.
  - The queue model keeps the bridge's dispatch thread free —
    `proxy_stream_next` blocks on `queue.get` for one chunk at a
    time, never holding the bridge across the whole stream.
  - Unit-testable: `httpx.stream` is monkey-patched in
    `test_bridge_server.py::TestProxyStream` to script the event
    sequence; no real network.

- **Cons**
  - Per-chunk RPC overhead (~1-2 ms) is real. Acceptable now;
    measure if engine token rates approach 1 kHz.
  - The polling protocol has a stream-registry leak surface — a
    consumer that crashes mid-stream leaves the queue + worker
    dangling. Mitigated by per-process scope (LO restart cleans up).
  - 60-second `proxy_stream_next` timeout matches a worker that has
    gone unresponsive but doesn't propagate the engine's own
    timeouts; if the engine takes >60s for a single chunk, we will
    spin `timeout` events until either chunk arrives or done.

- **Follow-up**
  - Add a contract test that the streaming + non-streaming proxies
    produce identical Response semantics for small non-SSE bodies.
  - Consider `evaluate_js` push if profiling shows the RPC overhead
    dominates first-token latency.

## References

- Code: `src/talk2view_writer/bridge_server.py` (`_proxy_stream_open`,
  `_proxy_stream_next`); `src/talk2view_writer/web_runner.py`
  (`_Api.proxy_stream_open`, `_Api.proxy_stream_next`);
  `src/web/src/bridge.ts` (`_proxyStream`); `tests/e2e/specs/streaming-chat.spec.ts`.
- Tests: `tests/unit/test_bridge_server.py::TestProxyStream`,
  `tests/e2e/specs/streaming-chat.spec.ts`.
- Related ADRs: ADR-0030 (proxy_fetch + the bridge), ADR-0031 (Playwright E2E).
