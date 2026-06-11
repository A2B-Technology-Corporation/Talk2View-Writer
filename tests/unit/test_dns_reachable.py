"""Unit tests for the DNS pre-check that bounds proxied requests.

httpx's connect timeout doesn't cover ``getaddrinfo`` on the sync transport,
so on a dead connection the lookup retries for ~25 s before the request even
fails. ``_dns_reachable`` resolves the host on a deadline-bounded thread so the
bridge can fail fast with a friendly message (investigations #63 part 3).
"""

from __future__ import annotations

import json
import socket
import threading
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

import talk2view_writer.bridge_server as bs
from talk2view_writer.bridge_server import BridgeServer, _dns_reachable

pytestmark = pytest.mark.unit


class TestDnsReachable:
    def test_resolvable_host_returns_true(self) -> None:
        # localhost always resolves without touching the network.
        assert _dns_reachable("https://localhost/v1/config") is True

    def test_unresolvable_host_returns_false(self) -> None:
        # ``.invalid`` is reserved (RFC 6761) and never resolves, so this
        # exercises the OSError branch quickly and deterministically.
        assert _dns_reachable("https://does-not-exist.invalid/v1/config") is False

    def test_url_without_host_degrades_to_true(self) -> None:
        # No host to resolve -> don't block; let httpx handle the odd URL.
        assert _dns_reachable("not-a-url") is True

    def test_timeout_treated_as_unreachable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A lookup that outlasts the deadline is abandoned and reported down.

        Simulate a wedged resolver: ``getaddrinfo`` blocks well past the
        deadline. ``_dns_reachable`` must return False at the deadline rather
        than wait for the OS resolver to give up (~25 s in the wild).
        """
        started = threading.Event()

        def _slow_getaddrinfo(*_a: Any, **_k: Any) -> list[Any]:
            started.set()
            time.sleep(5.0)  # far longer than the 0.2 s deadline below
            return []

        monkeypatch.setattr(socket, "getaddrinfo", _slow_getaddrinfo)
        t0 = time.monotonic()
        result = _dns_reachable("https://slow.example/v1/config", timeout_s=0.2)
        elapsed = time.monotonic() - t0
        assert result is False
        assert started.is_set(), "the resolver thread should have started"
        # Returned at the deadline, not after the 5 s sleep.
        assert elapsed < 2.0, f"returned in {elapsed:.2f}s — should be ~0.2s"


class _RecordingSock:
    """Minimal socket stub; the DNS-unreachable path never reaches it."""

    def __init__(self) -> None:
        self.sent = b""

    def sendall(self, data: bytes) -> None:  # pragma: no cover - not exercised
        self.sent += data


class TestProxyFetchDnsUnreachable:
    def test_proxy_fetch_returns_friendly_envelope_without_httpx(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When DNS is down, fail fast — never construct an httpx client."""
        monkeypatch.setattr(bs, "_dns_reachable", lambda *_a, **_k: False)

        import httpx

        def _boom(*_a: Any, **_k: Any) -> Any:  # pragma: no cover - must not run
            raise AssertionError("httpx.Client must not be built when DNS is down")

        monkeypatch.setattr(httpx, "Client", _boom)

        srv = BridgeServer(ctx=MagicMock(name="ctx"))
        result = srv._proxy_fetch(
            "https://engine.talk2view.com/v1/config", "GET", {}, None
        )
        assert result["status"] == 503
        assert result["headers"].get("content-type") == "application/json"
        body = json.loads(result["body"])
        assert body["error"]["type"] == "network"
        assert "internet connection" in body["error"]["message"].lower()
        # Raw resolver text never reaches the user.
        assert "getaddrinfo" not in result["body"].lower()


class TestProxyStreamDnsUnreachable:
    def test_stream_emits_friendly_error_without_httpx(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A stream opened on a dead connection fails with a friendly error.

        The worker must not call ``httpx.stream``. Its first (and terminal)
        event is the friendly network error so the JS consumer's poll loop
        ends instead of hanging until the read timeout. ``_proxy_stream_next``
        drops the registry entry on the ``error`` event (the consumer stops
        draining after it), so a follow-up poll reports an unknown stream —
        which confirms the stream was cleaned up, not leaked.
        """
        monkeypatch.setattr(bs, "_dns_reachable", lambda *_a, **_k: False)

        import httpx

        def _boom(*_a: Any, **_k: Any) -> Any:  # pragma: no cover - must not run
            raise AssertionError("httpx.stream must not run when DNS is down")

        monkeypatch.setattr(httpx, "stream", _boom)

        srv = BridgeServer(ctx=MagicMock(name="ctx"))
        opened = srv._proxy_stream_open(
            "https://engine.talk2view.com/v1/chat", "POST", {}, '{"messages": []}'
        )
        stream_id = opened["stream_id"]

        first = srv._proxy_stream_next(stream_id)
        assert first["type"] == "error"
        assert "internet connection" in first["message"].lower()

        # The stream was popped on the terminal error — no leak.
        after = srv._proxy_stream_next(stream_id)
        assert after["type"] == "error"
        assert "unknown stream" in after["message"].lower()
