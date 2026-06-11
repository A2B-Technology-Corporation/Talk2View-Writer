"""UI-thread dispatcher for cross-thread UNO calls.

Tools registered via the Talk2View SDK execute on the SDK's worker
thread when the agent issues an ``interrupt``. Those tools mutate the
LibreOffice document, which requires UI-thread execution — UNO is
single-threaded and document mutation from a background thread will
corrupt state or crash LibreOffice.

This module provides :class:`UIThreadDispatcher`, which marshals a
callable from any thread onto LibreOffice's main event loop via the
``com.sun.star.awt.AsyncCallback`` service. The dispatcher exposes a
synchronous helper (``run_sync``) that blocks the caller until the
callable completes on the UI thread and returns its result (or
re-raises its exception).

See ``docs/adrs/0018-ui-thread-marshalling-queue.md`` for the design
discussion and trade-offs vs the Phase B direct-write pattern
(ADR-0017, now superseded).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar

import unohelper  # type: ignore[import-not-found]
from com.sun.star.awt import XCallback  # type: ignore[import-not-found]

from talk2view_writer.perf import log_timing, monotonic_ms

if TYPE_CHECKING:
    from com.sun.star.uno import XComponentContext

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

DEFAULT_TIMEOUT_S = 30.0


class UIThreadTimeoutError(TimeoutError):
    """Raised when a UI-thread call does not complete within the timeout."""


class UIThreadDispatcher:
    """Marshal callables onto LibreOffice's main (UI) thread.

    Instantiated once per extension session (owned by
    :class:`talk2view_writer.extension.Talk2ViewWriterExtension`). The
    underlying ``AsyncCallback`` UNO service is created lazily on first
    use; constructor only stashes the context.

    Thread-safety: ``run_sync`` may be called concurrently from any
    number of worker threads. Each call uses its own ``threading.Event``
    + result slot, so concurrent calls do not interfere with each other.

    Resource lifetime: keep a strong reference to ``UIThreadDispatcher``
    for the whole extension lifetime. The internal ``_callbacks`` list
    keeps pending ``XCallback`` instances alive across the marshalling
    hop — PyUNO does not retain them otherwise.
    """

    def __init__(self, ctx: XComponentContext) -> None:
        self.ctx = ctx
        self._async_service: object | None = None
        self._lock = threading.Lock()
        # Strong refs to in-flight callbacks — see "Resource lifetime" above.
        self._callbacks: list[_RunOnUIThreadCallback] = []

    def _ensure_service(self) -> object:
        with self._lock:
            if self._async_service is None:
                self._async_service = self.ctx.ServiceManager.createInstanceWithContext(
                    "com.sun.star.awt.AsyncCallback", self.ctx
                )
                if self._async_service is None:
                    raise RuntimeError(
                        "Could not create com.sun.star.awt.AsyncCallback service "
                        "— UI-thread marshalling is unavailable. See "
                        "docs/investigations.md #12."
                    )
                logger.info("AsyncCallback service created")
            return self._async_service

    def run_sync(
        self,
        fn: Callable[..., _T],
        *args: Any,
        timeout: float = DEFAULT_TIMEOUT_S,
        **kwargs: Any,
    ) -> _T:
        """Run ``fn(*args, **kwargs)`` on the UI thread and return its result.

        Blocks the calling thread until the callable has executed.

        Args:
            fn: The callable to invoke on the UI thread.
            *args: Positional arguments forwarded to ``fn``.
            timeout: Seconds to wait. Raises :class:`UIThreadTimeoutError`
                if exceeded. Default 30 seconds — long enough for any
                reasonable UNO call, short enough that a deadlock surfaces.
            **kwargs: Keyword arguments forwarded to ``fn``.

        Returns:
            Whatever ``fn`` returns.

        Raises:
            UIThreadTimeoutError: ``timeout`` exceeded.
            Exception: Re-raises any exception ``fn`` raised, with the
                original traceback preserved via ``raise ... from``.
        """
        service = self._ensure_service()
        done = threading.Event()
        slot: list[tuple[bool, Any]] = []  # [(success, value_or_exc)]

        callback = _RunOnUIThreadCallback(self, fn, args, kwargs, slot, done)
        with self._lock:
            self._callbacks.append(callback)
        # Timing (task #12): ``total`` spans submit -> completion;
        # ``exec`` is the UNO call itself on the UI thread (recorded by
        # the callback); ``marshal = total - exec`` is the queue latency
        # — how long the call waited for LO's event loop. A fat
        # ``marshal_ms`` with a thin ``exec_ms`` means the UI thread was
        # busy, not the document operation.
        submit = time.monotonic()
        # Default so the ``finally`` cleanup runs even if ``addCallback``
        # raises before ``done.wait`` is reached: treat "never submitted"
        # as fired=True so the strong ref is removed rather than leaked
        # (no callback can fire if submission failed).
        fired = True
        already_running = False
        try:
            service.addCallback(callback, None)  # type: ignore[attr-defined]
            fired = done.wait(timeout)
            if not fired:
                # Timed out. Decide the callback's fate ATOMICALLY before it
                # can fire, under the same lock ``notify`` takes to mark
                # itself started — so the two never interleave:
                #   * not started yet -> mark cancelled. ``self._callbacks``
                #     holds the only strong ref keeping the queued
                #     ``XCallback`` alive; we leave it in place and the
                #     callback self-cleans when LO eventually fires it
                #     (``notify``'s cancelled branch). Removing it here could
                #     let it be GC'd while LO holds a weak handle.
                #   * already started -> an uncancellable UNO call is running
                #     past the deadline. We can't stop it; it self-cleans in
                #     its own ``finally``. Record that the document MAY have
                #     been mutated after the caller gave up.
                with self._lock:
                    if callback._started:
                        already_running = True
                    else:
                        callback._cancelled = True
            total_ms = monotonic_ms(submit)
            exec_ms = callback.exec_ms
            log_timing(
                logger,
                "ui_thread.run_sync",
                total_ms,
                fn=getattr(fn, "__name__", repr(fn)),
                exec_ms=None if exec_ms is None else round(exec_ms, 1),
                marshal_ms=(
                    None if exec_ms is None else round(total_ms - exec_ms, 1)
                ),
                timed_out=not fired,
            )
            if not fired:
                if already_running:
                    logger.warning(
                        "UI-thread call to %r exceeded %ss but was already "
                        "executing — the document MAY have been mutated after "
                        "the caller gave up; verify before retrying.",
                        getattr(fn, "__name__", fn),
                        timeout,
                    )
                raise UIThreadTimeoutError(
                    f"UI-thread call to {getattr(fn, '__name__', fn)!r} "
                    f"did not complete within {timeout}s"
                )
            success, value = slot[0]
            if success:
                return value  # type: ignore[no-any-return]
            # value is an Exception; preserve traceback chain
            raise value
        finally:
            # Only remove on the non-timeout path. On a timeout the callback
            # is still pending; it removes itself when it eventually fires
            # (the cancelled branch in ``notify``), so the strong ref must
            # survive here.
            if fired:
                with self._lock:
                    if callback in self._callbacks:
                        self._callbacks.remove(callback)


class _RunOnUIThreadCallback(unohelper.Base, XCallback):
    """UNO ``XCallback`` that runs a Python callable + signals completion.

    Kept as a separate class (not a closure) so that PyUNO holds a stable
    reference to a real Python object across the marshalling hop.
    """

    def __init__(
        self,
        parent: UIThreadDispatcher,
        fn: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict,
        slot: list[tuple[bool, Any]],
        done: threading.Event,
    ) -> None:
        self._parent = parent
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._slot = slot
        self._done = done
        # Set by ``run_sync`` on its timeout path, under the parent lock. A
        # cancelled callback that later fires must NOT run the wrapped
        # function (the caller has already given up — running it would be a
        # phantom UNO write); it only self-cleans from the parent's
        # ``_callbacks`` and returns.
        self._cancelled = False
        # Set by ``notify`` under the parent lock the instant it commits to
        # running ``fn``. ``run_sync``'s timeout path reads it to decide
        # whether the callback can still be cancelled (not started) or is
        # already executing an uncancellable UNO call (started) — the
        # check-and-mark is mutually exclusive via the lock.
        self._started = False
        # Set on the UI thread once ``notify`` runs ``fn``; stays None
        # if the callback never fires (timeout). Read by ``run_sync``
        # for the ``exec_ms`` timing field.
        self.exec_ms: float | None = None

    def notify(self, data: Any) -> None:
        # Catch is *required* — this is cross-thread exception marshalling.
        # The exception is shipped back to the caller via _slot and
        # re-raised there, so the traceback surfaces at the worker
        # thread (where the call originated) rather than the UI thread
        # (where it's useless). Don't add logger.exception here: it
        # duplicates noise; the worker-thread raise gives the real
        # location.
        with self._parent._lock:
            if self._cancelled:
                # ``run_sync`` timed out and gave up before LO fired us. Do
                # NOT run ``fn`` (phantom write) and do NOT touch
                # ``slot``/``done`` — the caller has moved on. Self-clean the
                # strong ref that ``run_sync``'s timeout path deliberately
                # left in place so ``_callbacks`` cannot grow unbounded.
                if self in self._parent._callbacks:
                    self._parent._callbacks.remove(self)
                return
            # Commit to running, under the lock, so ``run_sync``'s timeout
            # path sees a consistent view: once ``_started`` is set it must
            # treat us as an uncancellable in-flight call, not cancel us.
            self._started = True
        started = time.monotonic()
        try:
            result = self._fn(*self._args, **self._kwargs)
        except Exception as exc:
            self._slot.append((False, exc))
        else:
            self._slot.append((True, result))
        finally:
            self.exec_ms = monotonic_ms(started)
            # Always self-clean. On the normal path ``run_sync``'s finally
            # also removes us (idempotent membership check); on the
            # executed-after-timeout path this is the ONLY removal — without
            # it the callback (holding ``fn`` + args, potentially large
            # document text) would leak in ``_callbacks`` for the extension's
            # lifetime. Remove BEFORE signalling ``done`` so a waiter that
            # wakes and inspects ``_callbacks`` sees us already gone.
            with self._parent._lock:
                if self in self._parent._callbacks:
                    self._parent._callbacks.remove(self)
            self._done.set()
