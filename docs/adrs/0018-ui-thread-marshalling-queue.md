# ADR-0018: UI-thread marshalling via `AsyncCallback` + `XCallback`

**Status:** Accepted
**Date:** 2026-05-17
**Phase:** C
**Supersedes:** [ADR-0017](0017-cross-thread-widget-updates-phase-b.md)
(direct cross-thread widget writes)

## Context

ADR-0009 set the rule: only the UI thread mutates UNO. ADR-0017
relaxed that rule for Phase B because we had no tool execution and
the only cross-thread calls were widget property writes — empirically
safe but not principled.

Phase C introduces tools. The very first proof tool, ``insert_content``,
mutates the document's ``XText`` — that's the canonical "must be on
the UI thread" UNO call. We can no longer paper over the threading
boundary. We need a real marshalling primitive.

LibreOffice provides exactly one documented Python-accessible way to
schedule a callable on the main event loop: ``com.sun.star.awt.AsyncCallback``,
a service that takes an ``XCallback`` and invokes its ``notify(Any)``
method on the UI thread. We have not previously verified it works
from PyUNO, but it is the documented primitive and SpeedWriter's
absence of an equivalent helper is more about scope (voice doesn't
need fine-grained UI marshalling) than feasibility.

## Decision

Introduce :class:`talk2view_writer.ui_thread.UIThreadDispatcher`,
owned by the extension singleton. It exposes:

```python
dispatcher.run_sync(fn, *args, timeout=30.0, **kwargs) -> T
```

Semantics:

- Wraps ``fn(*args, **kwargs)`` in a private ``_RunOnUIThreadCallback``
  (an XCallback implementation).
- Posts via the ``com.sun.star.awt.AsyncCallback`` service.
- Blocks the calling thread on a ``threading.Event`` until the UI
  thread fires ``notify()`` and the wrapped callable completes.
- Returns the callable's return value.
- Re-raises any exception raised by the callable, preserving the
  traceback.
- Raises :class:`UIThreadCallTimeout` if the callback hasn't fired
  in ``timeout`` seconds (default 30) — long enough for normal UNO
  calls, short enough that a deadlock surfaces instead of hanging
  LibreOffice forever.

A thin decorator :func:`talk2view_writer.tools._base.ui_thread_tool`
wraps tool function bodies with ``run_sync``. Decorator stack:

```python
@tool                # outermost — SDK schema introspection
@ui_thread_tool      # innermost — UI-thread marshalling
def insert_content(content: str) -> str:
    ...
```

The signature is preserved via ``functools.wraps`` (which sets
``__wrapped__``), and the SDK's introspection follows ``__wrapped__``
so the schema sees the original parameters.

The sidebar panel also migrates: every cross-thread widget write
(``_append_history``, ``_set_status``, ``_set_busy``) gets a `_*_ui`
sibling that does the actual setPropertyValue calls, with the
outer method dispatching via the queue.

## Alternatives considered

- **Keep the direct-write pattern from ADR-0017.** Untenable as soon
  as we add document mutation — the technical-unsafety stops being
  empirical and starts being load-bearing.
- **`solar_mutex` acquired on the worker thread.** Still
  uninvestigated (Investigation #12). If the spike succeeds we could
  simplify by acquiring the global UI lock instead of marshalling
  back. But the dispatcher pattern is more portable across UNO
  bindings, more testable, and gives us a single place to add
  timing / tracing later.
- **Background polling timer that drains a queue.** Works but adds
  per-tick latency (50 ms × N drains). ``AsyncCallback`` is event-
  driven, not polled.
- **Block tool functions on the main UI thread.** Would force the
  SDK iteration onto the UI thread, freezing LibreOffice during
  every chat message. The whole reason we have a worker thread is
  to keep LibreOffice responsive.

## Consequences

**Pros**
- Tools can mutate UNO safely. Phase D can port the remaining 24
  tools using ``@ui_thread_tool`` without re-thinking threading per
  tool.
- The sidebar panel's chat-event handling is now principled — every
  cross-thread call goes through the same chokepoint.
- The dispatcher is a single, mockable test seam (see
  ``tests/unit/test_ui_thread.py``).
- Exceptions inside tools propagate correctly with full tracebacks
  back to the SDK worker thread, where the SDK turns them into
  agent-visible error messages.

**Cons**
- **Per-call latency.** Every UNO call from a tool now incurs the
  thread-hop cost. For text manipulation tools this is sub-millisecond
  but it's real. If a tool makes hundreds of small UNO calls in a
  loop, consider batching them inside a single ``run_sync``
  invocation rather than dispatching each.
- **AsyncCallback service is undocumented in PyUNO specifically.**
  We rely on the cross-language UNO contract. If it turns out to
  behave differently than expected, we have a fallback plan
  (timer-based polling) but no implementation yet. Investigation
  #12 covers the verification spike.
- **30-second default timeout.** If a UNO call legitimately takes
  longer (massive document load, slow file system), we raise
  :class:`UIThreadCallTimeout` and the agent sees an error.
  Override via ``run_sync(..., timeout=120)`` for known-slow ops.
- **Strong-ref management** of in-flight callbacks. PyUNO does not
  retain XCallback instances across the async boundary, so the
  dispatcher keeps a list. This is correct but easy to forget if
  someone later extracts a "fire and forget" path that doesn't
  block; the list could grow unboundedly there. Mitigated by today
  having only the sync path.

**Follow-up**
- Phase D: each tool ported with ``@ui_thread_tool``. Behavioural-
  delta notes for any tool whose Writer behaviour diverges from Word.
- Investigation #12: verify ``AsyncCallback`` actually marshals to
  the UI thread under PyUNO. If the verification fails, this ADR
  needs a follow-up with the chosen fallback.
- Consider adding an ``async`` variant (``run_async(fn, callback)``)
  for fire-and-forget UI updates that don't need a return value;
  would avoid the worker-thread block for purely additive UI updates.

## References

- Code:
  `src/talk2view_writer/ui_thread.py::UIThreadDispatcher`
- Code:
  `src/talk2view_writer/tools/_base.py::ui_thread_tool`
- Code:
  `src/talk2view_writer/ui/sidebar_panel.py` — `_dispatch_ui` +
  `_*_ui` sibling methods
- Tests: `tests/unit/test_ui_thread.py` (6 tests)
- LibreOffice service: ``com.sun.star.awt.AsyncCallback`` (defined
  in `offapi/com/sun/star/awt/AsyncCallback.idl`)
- Related ADRs: ADR-0009 (threading rules), ADR-0017 (superseded),
  ADR-0019 (tool registry)
- Investigations: `docs/investigations.md` #12
