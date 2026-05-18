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
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar

import unohelper  # type: ignore[import-not-found]
from com.sun.star.awt import XCallback  # type: ignore[import-not-found]

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

        callback = _RunOnUIThreadCallback(fn, args, kwargs, slot, done)
        with self._lock:
            self._callbacks.append(callback)
        try:
            service.addCallback(callback, None)  # type: ignore[attr-defined]
            if not done.wait(timeout):
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
        fn: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict,
        slot: list[tuple[bool, Any]],
        done: threading.Event,
    ) -> None:
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._slot = slot
        self._done = done

    def notify(self, data: Any) -> None:
        # Catch is *required* — this is cross-thread exception marshalling.
        # The exception is shipped back to the caller via _slot and
        # re-raised there, so the traceback surfaces at the worker
        # thread (where the call originated) rather than the UI thread
        # (where it's useless). Don't add logger.exception here: it
        # duplicates noise; the worker-thread raise gives the real
        # location.
        try:
            result = self._fn(*self._args, **self._kwargs)
        except Exception as exc:
            self._slot.append((False, exc))
        else:
            self._slot.append((True, result))
        finally:
            self._done.set()
