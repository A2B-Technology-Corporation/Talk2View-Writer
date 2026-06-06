"""Unit tests for the timing-instrumentation helpers (``perf``).

The chat path crosses three process/thread boundaries — the JS<->Python
bridge socket, LO's UI-thread marshalling hop, and the engine SSE
stream. Each hop emits one greppable ``timing op=... ms=...`` line via
these helpers so a slow run can be diagnosed by grepping the log. These
tests pin the line format (log-analysis depends on it) and the clock
arithmetic, with an injected clock so they never flake on real time.
"""

from __future__ import annotations

import logging

import pytest

from talk2view_writer import perf


@pytest.mark.unit
class TestFormatTiming:
    def test_basic_shape(self) -> None:
        assert perf.format_timing("bridge.dispatch", 12.34) == (
            "timing op=bridge.dispatch ms=12.3"
        )

    def test_rounds_to_one_decimal(self) -> None:
        assert perf.format_timing("x", 0.04) == "timing op=x ms=0.0"
        assert perf.format_timing("x", 99.96) == "timing op=x ms=100.0"

    def test_extra_fields_appended_in_order(self) -> None:
        line = perf.format_timing("stream.chunk_wait", 5.0, id=7, event="chunk")
        assert line == "timing op=stream.chunk_wait ms=5.0 id=7 event=chunk"

    def test_none_field_renders_as_na(self) -> None:
        # ttfb is unknown when the stream errored before headers.
        line = perf.format_timing("stream.total", 1.0, ttfb_ms=None)
        assert line == "timing op=stream.total ms=1.0 ttfb_ms=na"


@pytest.mark.unit
class TestLogTiming:
    def test_emits_info_line(self, caplog: pytest.LogCaptureFixture) -> None:
        log = logging.getLogger("test.perf.logtiming")
        with caplog.at_level(logging.INFO, logger="test.perf.logtiming"):
            perf.log_timing(log, "bridge.dispatch", 3.0, method="log")
        assert "timing op=bridge.dispatch ms=3.0 method=log" in caplog.text


@pytest.mark.unit
class TestTimed:
    def test_logs_elapsed_from_injected_clock(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        log = logging.getLogger("test.perf.timed")
        ticks = iter([1.0, 1.5])  # start, end -> 500 ms
        with (
            caplog.at_level(logging.INFO, logger="test.perf.timed"),
            perf.timed(log, "ui_thread.run_sync", clock=lambda: next(ticks)),
        ):
            pass
        assert "timing op=ui_thread.run_sync ms=500.0" in caplog.text

    def test_extra_fields_are_mutable_at_exit(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        log = logging.getLogger("test.perf.timed2")
        ticks = iter([10.0, 10.1])
        with (
            caplog.at_level(logging.INFO, logger="test.perf.timed2"),
            perf.timed(
                log, "stream.chunk_wait", clock=lambda: next(ticks), id=3
            ) as fields,
        ):
            fields["event"] = "done"
        assert (
            "timing op=stream.chunk_wait ms=100.0 id=3 event=done" in caplog.text
        )

    def test_logs_even_when_body_raises(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        log = logging.getLogger("test.perf.timed3")
        ticks = iter([0.0, 0.2])
        with (
            caplog.at_level(logging.INFO, logger="test.perf.timed3"),
            pytest.raises(ValueError),
            perf.timed(log, "boom", clock=lambda: next(ticks)),
        ):
            raise ValueError("nope")
        assert "timing op=boom ms=200.0" in caplog.text
