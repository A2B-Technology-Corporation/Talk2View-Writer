"""Tests for ``talk2view_writer.ui_thread.UIThreadDispatcher``.

The real UNO ``AsyncCallback`` service is mocked. Each test stubs the
context's ServiceManager to return a fake service whose ``addCallback``
invokes the registered ``XCallback`` immediately (synchronously) — this
lets us exercise the dispatcher's plumbing without LibreOffice running.
"""

from __future__ import annotations

import threading
import time
from typing import Any
from unittest.mock import MagicMock

import pytest


def _make_ctx(addCallback_impl) -> MagicMock:  # noqa: N803 — UNO interface naming
    """Build a fake XComponentContext whose AsyncCallback service uses ``addCallback_impl``."""
    fake_async = MagicMock()
    fake_async.addCallback.side_effect = addCallback_impl

    ctx = MagicMock()
    ctx.ServiceManager.createInstanceWithContext.return_value = fake_async
    return ctx


@pytest.mark.unit
def test_run_sync_returns_callable_result() -> None:
    from talk2view_writer.ui_thread import UIThreadDispatcher

    def synchronous_addCallback(callback: Any, data: Any) -> None:  # noqa: N803, ARG001
        callback.notify(data)

    ctx = _make_ctx(synchronous_addCallback)
    dispatcher = UIThreadDispatcher(ctx)
    result = dispatcher.run_sync(lambda x, y: x + y, 3, 4)
    assert result == 7


@pytest.mark.unit
def test_run_sync_propagates_exception() -> None:
    from talk2view_writer.ui_thread import UIThreadDispatcher

    def synchronous_addCallback(callback: Any, data: Any) -> None:  # noqa: N803, ARG001
        callback.notify(data)

    ctx = _make_ctx(synchronous_addCallback)
    dispatcher = UIThreadDispatcher(ctx)

    def boom() -> None:
        raise ValueError("kaboom")

    with pytest.raises(ValueError, match="kaboom"):
        dispatcher.run_sync(boom)


@pytest.mark.unit
def test_run_sync_times_out_when_callback_never_fires() -> None:
    from talk2view_writer.ui_thread import UIThreadCallTimeout, UIThreadDispatcher

    # AsyncCallback service that does nothing — simulates a stuck UI thread.
    ctx = _make_ctx(lambda callback, data: None)
    dispatcher = UIThreadDispatcher(ctx)

    with pytest.raises(UIThreadCallTimeout):
        dispatcher.run_sync(lambda: 42, timeout=0.05)


@pytest.mark.unit
def test_run_sync_works_when_callback_fires_on_other_thread() -> None:
    """Simulate AsyncCallback's real behaviour: invoke callback on a different thread."""
    from talk2view_writer.ui_thread import UIThreadDispatcher

    threads_used: list = []

    def deferred_addCallback(callback: Any, data: Any) -> None:  # noqa: N803
        # Fire the callback from a background thread, like the real
        # AsyncCallback service would do from the UI event loop.
        def run() -> None:
            time.sleep(0.01)
            threads_used.append(threading.get_ident())
            callback.notify(data)

        threading.Thread(target=run, daemon=True).start()

    ctx = _make_ctx(deferred_addCallback)
    dispatcher = UIThreadDispatcher(ctx)
    assert dispatcher.run_sync(lambda: "result") == "result"
    # The notify must have run on a different thread than the caller.
    assert threads_used and threads_used[0] != threading.get_ident()


@pytest.mark.unit
def test_dispatcher_keeps_callback_alive_until_completion() -> None:
    """The dispatcher must hold a strong reference to in-flight callbacks."""
    from talk2view_writer.ui_thread import UIThreadDispatcher

    captured = []

    def synchronous_addCallback(callback: Any, data: Any) -> None:  # noqa: N803, ARG001
        captured.append(callback)
        callback.notify(data)

    ctx = _make_ctx(synchronous_addCallback)
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

    def synchronous_addCallback(callback: Any, data: Any) -> None:  # noqa: N803, ARG001
        # Simulate per-call latency on a background thread so calls interleave.
        def run() -> None:
            callback.notify(data)

        threading.Thread(target=run, daemon=True).start()

    ctx = _make_ctx(synchronous_addCallback)
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
