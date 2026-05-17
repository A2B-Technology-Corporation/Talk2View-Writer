# ADR-0009: Worker thread + UI-thread queue for SDK iteration

**Status:** Accepted (design) — *partially relaxed for Phase B by
[ADR-0017](0017-cross-thread-widget-updates-phase-b.md)*
**Date:** 2026-05-17
**Phase:** A (planning), Phase B (implementation)

## Context

Two threading constraints clash:

1. **UNO is not thread-safe.** Mutating widgets or the document model
   from a background thread can deadlock LibreOffice or corrupt
   state. UNO calls must run on the main (UI) thread, or under the
   `solar_mutex` if you really know what you're doing.
2. **The SDK's `chat(message)` returns a synchronous iterator** that
   blocks on SSE reads. Iterating it on the UI thread would freeze
   LibreOffice for the entire roundtrip.

Additionally, `@tool`-registered Python functions are invoked **by
the SDK** when the agent issues an `interrupt`. Those calls happen
inside the SDK's `chat()` iteration — i.e. wherever we iterate.

## Decision

For each chat message:

1. UI submit handler spawns a `threading.Thread` (or pulls from a
   single-slot worker pool).
2. The worker iterates `t2v.chat(message)`, converting each
   `ChatEvent` into a small Python record and pushing it into a
   `queue.Queue`.
3. A UI-thread timer (created via `XScheduler` or a periodic
   `XTopWindowListener` callback) drains the queue every ~50 ms and
   applies updates to the panel widgets.
4. **Tool callables registered via `@tool` must marshal any UNO call
   back to the UI thread** before mutating the document. They do this
   by pushing a "please run this UNO call" job onto a *second* queue
   and blocking on a result from a `threading.Event`-style handshake.

The exact UI-thread timer mechanism (LibreOffice has several:
`com.sun.star.util.URLTransformer`-based, top-window event polling,
or a custom `XCallback`) will be picked in Phase B after a small spike;
the boundary contract above stands regardless.

## Alternatives considered

- **Block the UI thread on `chat()` iteration.** Simpler code but
  freezes LibreOffice on every message. Unacceptable UX.
- **Use `asyncio` instead of threads.** UNO's main loop is GLib /
  Cocoa / Win32, not asyncio — bridging is awkward. Threads are the
  right primitive here.
- **Run tools directly on the worker thread.** Easier to write, but
  tools mutate the UNO document and that violates UNO's threading
  rules. Even read-only tools can race with the user's typing.

## Consequences

**Pros**
- LibreOffice stays responsive while the agent streams.
- Clear boundary: only the UI-thread drain touches widgets and only
  the tool marshalling helper touches UNO.
- Recoverable from network errors — the worker thread can raise; the
  drain shows the error in the panel.

**Cons**
- More moving parts than a synchronous design. The drain timer has to
  be carefully torn down on panel `dispose()`.
- Tool latency now includes the round-trip cost: worker → tool job
  queue → UI thread → execute → result back to worker. Should still
  be sub-millisecond for most tools but it's a real cost.
- Bugs in the tool marshalling are hard to debug — silent UNO
  exceptions on the wrong thread can manifest as random crashes.
  Plan to add an assertion helper `assert_ui_thread()` and a test
  helper `RunOnUiThread.run_sync(callable)` in Phase B.

**Follow-up**
- Phase B spike picks the UI-thread timer mechanism; document the
  choice in a follow-up ADR if the trade-offs are non-obvious.
- Add `assert_ui_thread()` helper in Phase B. Apply liberally in UNO
  helper functions.
- Investigate using `solar_mutex` (LibreOffice's global UI lock) as an
  alternative to the marshalling queue. SpeedWriter does not use it;
  unclear how stable it is from PyUNO — see `docs/investigations.md`
  #5.

## References

- SDK iteration: `Talk2View-Platform/packages/sdk-python/src/talk2view/__init__.py::Talk2View.chat`
- SDK tool dispatch: `Talk2View-Platform/packages/sdk-python/src/talk2view/sessions.py`
- Pattern reference: `SpeedWriter-LibreOffice/src/speedwriter/voice/manager.py`
  (TranscriptionPoller — closest precedent for periodic UI drain)
- Related ADRs: ADR-0002 (cloud SDK), ADR-0008 (tool decorator)
