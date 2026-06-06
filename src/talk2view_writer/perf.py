"""Lightweight timing instrumentation for the chat path.

Every cross-process / cross-thread hop in a chat turn — the JS<->Python
bridge round-trip, LO's UI-thread marshalling hop, and the engine SSE
chunk waits — emits a single, greppable ``timing`` log line so a slow
run can be diagnosed after the fact. Grep the LibreOffice log for
``timing op=`` and bucket by ``op`` to see where wall-clock went.

Line format (STABLE — log-analysis scripts depend on it; do not
reorder ``op`` / ``ms`` or change the prefix)::

    timing op=<name> ms=<float, 1dp> [<k>=<v> ...]

A ``None`` field value renders as ``na`` so the column stays present
(e.g. ``ttfb_ms=na`` when a stream errored before its first byte).

The clock is injectable (``clock=...``) purely so tests can pin the
arithmetic deterministically; production callers use the default
``time.monotonic``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

__all__ = ["format_timing", "log_timing", "monotonic_ms", "timed"]


def _render_value(value: Any) -> str:
    return "na" if value is None else str(value)


def format_timing(op: str, ms: float, **fields: Any) -> str:
    """Render one timing line. See module docstring for the format."""
    parts = [f"timing op={op}", f"ms={ms:.1f}"]
    parts.extend(f"{key}={_render_value(val)}" for key, val in fields.items())
    return " ".join(parts)


def log_timing(
    logger: logging.Logger, op: str, ms: float, **fields: Any
) -> None:
    """Emit one timing line at INFO level."""
    logger.info("%s", format_timing(op, ms, **fields))


def monotonic_ms(start: float, *, clock: Callable[[], float] = time.monotonic) -> float:
    """Milliseconds elapsed since ``start`` (a prior ``clock()`` reading)."""
    return (clock() - start) * 1000.0


@contextmanager
def timed(
    logger: logging.Logger,
    op: str,
    *,
    clock: Callable[[], float] = time.monotonic,
    **fields: Any,
) -> Iterator[dict[str, Any]]:
    """Time the ``with`` body and log one ``timing`` line on exit.

    Yields a mutable ``dict`` seeded with ``fields``; mutate it inside
    the body to attach values only known at exit (e.g. the SSE event
    type a chunk-wait resolved to). The line is logged even if the body
    raises — the exception then propagates unchanged.
    """
    start = clock()
    extra: dict[str, Any] = dict(fields)
    try:
        yield extra
    finally:
        log_timing(logger, op, monotonic_ms(start, clock=clock), **extra)
