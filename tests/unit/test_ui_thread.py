"""Tests for ``talk2view_writer.ui_thread.UIThreadDispatcher``.

The real UNO ``AsyncCallback`` service is mocked. Each test stubs the
context's ServiceManager to return a fake service whose ``addCallback``
invokes the registered ``XCallback`` immediately (synchronously) — this
lets us exercise the dispatcher's plumbing without LibreOffice running.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any
from unittest.mock import MagicMock

import pytest


def _attach_caplog(caplog: pytest.LogCaptureFixture) -> logging.Handler:
    """Route ``talk2view_writer.ui_thread`` records into ``caplog``.

    The package logger sets ``propagate=False`` (``_logging.py``), so
    ``caplog`` (rooted at the root logger) needs the handler attached to
    the ui_thread logger directly. Caller is responsible for nothing —
    the handler stays for the rest of the test, which is fine.
    """
    log = logging.getLogger("talk2view_writer.ui_thread")
    log.addHandler(caplog.handler)
    log.setLevel(logging.INFO)
    return caplog.handler


def _make_ctx(addCallback_impl) -> MagicMock:  # noqa: N803 — UNO interface naming
    """Build a fake XComponentContext whose AsyncCallback uses ``addCallback_impl``."""
    fake_async = MagicMock()
    fake_async.addCallback.side_effect = addCallback_impl

    ctx = MagicMock()
    ctx.ServiceManager.createInstanceWithContext.return_value = fake_async
    return ctx


@pytest.mark.unit
def test_run_sync_returns_callable_result() -> None:
    from talk2view_writer.ui_thread import UIThreadDispatcher

    def synchronous_add_callback(callback: Any, data: Any) -> None:
        callback.notify(data)

    ctx = _make_ctx(synchronous_add_callback)
    dispatcher = UIThreadDispatcher(ctx)
    result = dispatcher.run_sync(lambda x, y: x + y, 3, 4)
    assert result == 7


@pytest.mark.unit
def test_run_sync_propagates_exception() -> None:
    from talk2view_writer.ui_thread import UIThreadDispatcher

    def synchronous_add_callback(callback: Any, data: Any) -> None:
        callback.notify(data)

    ctx = _make_ctx(synchronous_add_callback)
    dispatcher = UIThreadDispatcher(ctx)

    def boom() -> None:
        raise ValueError("kaboom")

    with pytest.raises(ValueError, match="kaboom"):
        dispatcher.run_sync(boom)


@pytest.mark.unit
def test_run_sync_times_out_when_callback_never_fires() -> None:
    from talk2view_writer.ui_thread import UIThreadDispatcher, UIThreadTimeoutError

    # AsyncCallback service that does nothing — simulates a stuck UI thread.
    ctx = _make_ctx(lambda callback, data: None)
    dispatcher = UIThreadDispatcher(ctx)

    with pytest.raises(UIThreadTimeoutError):
        dispatcher.run_sync(lambda: 42, timeout=0.05)


@pytest.mark.unit
def test_run_sync_works_when_callback_fires_on_other_thread() -> None:
    """AsyncCallback fires on its own thread — confirm we still get the result."""
    from talk2view_writer.ui_thread import UIThreadDispatcher

    threads_used: list = []

    def deferred_add_callback(callback: Any, data: Any) -> None:
        # Fire the callback from a background thread, like the real
        # AsyncCallback service would do from the UI event loop.
        def run() -> None:
            time.sleep(0.01)
            threads_used.append(threading.get_ident())
            callback.notify(data)

        threading.Thread(target=run, daemon=True).start()

    ctx = _make_ctx(deferred_add_callback)
    dispatcher = UIThreadDispatcher(ctx)
    assert dispatcher.run_sync(lambda: "result") == "result"
    # The notify must have run on a different thread than the caller.
    assert threads_used and threads_used[0] != threading.get_ident()


@pytest.mark.unit
def test_dispatcher_keeps_callback_alive_until_completion() -> None:
    """The dispatcher must hold a strong reference to in-flight callbacks."""
    from talk2view_writer.ui_thread import UIThreadDispatcher

    captured = []

    def synchronous_add_callback(callback: Any, data: Any) -> None:
        captured.append(callback)
        callback.notify(data)

    ctx = _make_ctx(synchronous_add_callback)
    dispatcher = UIThreadDispatcher(ctx)
    dispatcher.run_sync(lambda: "ok")

    # After completion the dispatcher must have cleaned up its strong ref.
    assert dispatcher._callbacks == []
    # But during the call, our captured ref proves the callback existed.
    assert len(captured) == 1


@pytest.mark.unit
def test_concurrent_run_sync_calls_isolated() -> None:
    """Concurrent callers must not interfere with each other's results."""
    from talk2view_writer.ui_thread import UIThreadDispatcher

    def synchronous_add_callback(callback: Any, data: Any) -> None:
        # Simulate per-call latency on a background thread so calls interleave.
        def run() -> None:
            callback.notify(data)

        threading.Thread(target=run, daemon=True).start()

    ctx = _make_ctx(synchronous_add_callback)
    dispatcher = UIThreadDispatcher(ctx)

    results: dict = {}
    errors: list = []

    def caller(value: int) -> None:
        try:
            results[value] = dispatcher.run_sync(lambda v: v * 2, value)
        except Exception as exc:  # pragma: no cover — surfaces in assertion below
            errors.append(exc)

    threads = [threading.Thread(target=caller, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert not errors
    assert results == {i: i * 2 for i in range(10)}


@pytest.mark.unit
def test_run_sync_emits_timing_line(caplog: pytest.LogCaptureFixture) -> None:
    """Each marshalling hop is timed (task #12).

    ``ui_thread.run_sync`` is the UNO marshal boundary every mutating
    tool crosses. The timing line separates ``exec_ms`` (the UNO call
    itself, on the UI thread) from ``marshal_ms`` (time the call spent
    queued before the UI thread picked it up) — the latter is what grows
    when LO's event loop is busy.
    """
    from talk2view_writer.ui_thread import UIThreadDispatcher

    def synchronous_add_callback(callback: Any, data: Any) -> None:
        callback.notify(data)

    ctx = _make_ctx(synchronous_add_callback)
    dispatcher = UIThreadDispatcher(ctx)
    _attach_caplog(caplog)
    dispatcher.run_sync(lambda: "ok")

    assert "timing op=ui_thread.run_sync" in caplog.text
    assert "exec_ms=" in caplog.text
    assert "marshal_ms=" in caplog.text


@pytest.mark.unit
def test_run_sync_times_out_path_still_logs_timing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from talk2view_writer.ui_thread import UIThreadDispatcher, UIThreadTimeoutError

    ctx = _make_ctx(lambda callback, data: None)  # never fires
    dispatcher = UIThreadDispatcher(ctx)
    _attach_caplog(caplog)
    with pytest.raises(UIThreadTimeoutError):
        dispatcher.run_sync(lambda: 42, timeout=0.05)
    assert "timing op=ui_thread.run_sync" in caplog.text
    # No UI-thread exec happened, so exec is unknown -> 'na'.
    assert "exec_ms=na" in caplog.text
    assert "timed_out=True" in caplog.text


@pytest.mark.unit
def test_late_fire_after_timeout_does_not_run_wrapped_fn() -> None:
    """A timed-out callback that fires LATE must not run the wrapped fn.

    Regression: ``run_sync``'s timeout path used to unconditionally remove
    the callback from ``_callbacks`` — the sole strong ref keeping the
    still-queued ``XCallback`` alive. A late fire would then run the wrapped
    UNO work after the caller had already given up (phantom write) or crash.

    The cancellation-flag fix keeps the strong ref on timeout, marks the
    callback cancelled, and has ``notify`` no-op + self-clean when it fires.
    Here we capture the pending callback (without firing it), let ``run_sync``
    time out, then fire it manually — the way LO's stuck event loop would
    eventually do.
    """
    from talk2view_writer.ui_thread import UIThreadDispatcher, UIThreadTimeoutError

    captured: list = []
    ran = []

    def capture_without_firing(callback: Any, data: Any) -> None:
        # Mimic a stuck UI thread: the callback is queued but not run.
        captured.append((callback, data))

    ctx = _make_ctx(capture_without_firing)
    dispatcher = UIThreadDispatcher(ctx)

    def wrapped() -> str:
        ran.append(True)
        return "phantom"

    # (a) run_sync raises the timeout error.
    with pytest.raises(UIThreadTimeoutError):
        dispatcher.run_sync(wrapped, timeout=0.05)

    # The strong ref must survive the timeout — the callback is still pending.
    assert len(dispatcher._callbacks) == 1
    assert len(captured) == 1

    # LO finally fires the long-queued callback.
    callback, data = captured[0]
    callback.notify(data)  # (d) must not raise

    # (b) the late fire did NOT execute the wrapped function.
    assert ran == []
    # (c) the callback self-cleaned from _callbacks (no unbounded growth).
    assert dispatcher._callbacks == []


@pytest.mark.unit
def test_executed_after_timeout_self_cleans_and_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A UNO call still executing when the timeout fires must not leak.

    Regression for the disputed run_sync finding: if ``fn`` had already
    started on the UI thread when ``run_sync`` timed out, the callback
    used to leak in ``_callbacks`` for the extension's lifetime — only the
    cancelled branch self-cleaned, and run_sync's finally skips removal on
    timeout. It must now self-clean from its own ``finally``, and run_sync
    must warn that the document MAY have been mutated post-timeout (the
    uncancellable-call case is irreducible; visibility is the mitigation).
    """
    from talk2view_writer.ui_thread import (
        UIThreadDispatcher,
        UIThreadTimeoutError,
    )

    _attach_caplog(caplog)
    ran: list[bool] = []
    release_fn = threading.Event()

    def slow_fn() -> str:
        # Block until the test releases us — guarantees fn is still
        # executing when run_sync's deadline passes (so _started is set
        # and the callback is uncancellable).
        release_fn.wait(2.0)
        ran.append(True)
        return "completed-after-timeout"

    def deferred_add_callback(callback: Any, data: Any) -> None:
        # Fire on a background thread (like the real AsyncCallback). notify
        # sets _started under the lock, then enters slow_fn and blocks.
        threading.Thread(target=lambda: callback.notify(data), daemon=True).start()

    ctx = _make_ctx(deferred_add_callback)
    dispatcher = UIThreadDispatcher(ctx)

    with pytest.raises(UIThreadTimeoutError):
        dispatcher.run_sync(slow_fn, timeout=0.2)

    # fn was already running (not cancelled) — let it finish.
    release_fn.set()
    deadline = time.monotonic() + 2.0
    while dispatcher._callbacks and time.monotonic() < deadline:
        time.sleep(0.01)

    assert ran == [True], "an already-started call must run to completion"
    assert dispatcher._callbacks == [], "callback must self-clean — no leak"
    assert "MAY have been mutated" in caplog.text
