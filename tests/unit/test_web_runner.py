"""Unit tests for web_runner's js_api (_Api) + bridge-client timing."""

from __future__ import annotations

import json
import logging
import webbrowser

import pytest

from talk2view_writer.web_runner import _Api, _BridgeClient


@pytest.mark.unit
class TestOpenExternal:
    def test_opens_https_in_browser(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[str] = []
        monkeypatch.setattr(
            webbrowser, "open", lambda url, new=0: calls.append(url) or True
        )
        result = _Api(None).open_external(
            "https://github.com/A2B-Technology-Corporation/Talk2View-Writer/releases/latest"
        )
        assert result == {"opened": True}
        assert calls == [
            "https://github.com/A2B-Technology-Corporation/Talk2View-Writer/releases/latest"
        ]

    @pytest.mark.parametrize(
        "url", ["file:///etc/passwd", "javascript:alert(1)", "vnd.evil:x", ""]
    )
    def test_rejects_non_http(self, monkeypatch: pytest.MonkeyPatch, url: str) -> None:
        called: list[str] = []
        monkeypatch.setattr(webbrowser, "open", lambda *a, **k: called.append(a) or True)
        assert _Api(None).open_external(url) == {"opened": False}
        assert called == [], "webbrowser.open must not run for non-http(s) URLs"

    def test_open_failure_is_swallowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(*_a: object, **_k: object) -> bool:
            raise OSError("no browser")

        monkeypatch.setattr(webbrowser, "open", boom)
        assert _Api(None).open_external("https://example.com") == {"opened": False}


class _FakeSock:
    """A socket stub that replies with one canned JSON-RPC line."""

    def __init__(self, response_line: bytes) -> None:
        self._resp = response_line
        self.sent = b""

    def sendall(self, data: bytes) -> None:
        self.sent += data

    def recv(self, _n: int) -> bytes:
        resp, self._resp = self._resp, b""
        return resp


@pytest.mark.unit
class TestBridgeClientTiming:
    """``_BridgeClient._call`` times the round-trip + lock contention.

    The single bridge socket serialises every call behind one lock, so
    a debug ``log`` can queue behind an in-flight ``proxy_stream_next``.
    ``lock_wait_ms`` is the field that exposes that contention (task
    #12); ``ms`` is the server round-trip itself.
    """

    _LOG = "talk2view_writer.web_runner"

    def _client(self, response: dict) -> _BridgeClient:
        client = _BridgeClient("/tmp/unused.sock")
        client._sock = _FakeSock((json.dumps(response) + "\n").encode())  # type: ignore[assignment]
        return client

    def test_call_emits_timing_line(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = self._client({"id": 1, "result": "pong"})
        log = logging.getLogger(self._LOG)
        log.addHandler(caplog.handler)
        log.setLevel(logging.INFO)
        result = client._call("list_tools", {})
        assert result == "pong"
        assert "timing op=bridge.client_call" in caplog.text
        assert "method=list_tools" in caplog.text
        assert "lock_wait_ms=" in caplog.text
        assert "id=1" in caplog.text

    def test_wire_dump_is_debug_not_info(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The per-call request/response dump must not spam INFO.

        It fires on every bridge call — hundreds of ``proxy_stream_next``
        round-trips per turn, each carrying the chunk payload. At INFO
        that's real formatting + IO overhead and it buries the timing
        lines, so the raw dump is DEBUG-only (task #13).
        """
        client = self._client({"id": 1, "result": "pong"})
        log = logging.getLogger(self._LOG)
        log.addHandler(caplog.handler)
        log.setLevel(logging.INFO)  # production level
        client._call("list_tools", {})
        assert "BridgeClient ->" not in caplog.text
        assert "BridgeClient <-" not in caplog.text
        # The compact timing summary still lands at INFO.
        assert "timing op=bridge.client_call" in caplog.text


class _NoReadSock:
    """A socket stub that records sends and FAILS if anyone reads.

    Used to prove fire-and-forget notifications never block on a reply.
    """

    def __init__(self) -> None:
        self.sent = b""

    def sendall(self, data: bytes) -> None:
        self.sent += data

    def recv(self, _n: int) -> bytes:  # pragma: no cover — must not run
        raise AssertionError("notify must not read a response")


@pytest.mark.unit
class TestBridgeClientNotify:
    """``log`` is a fire-and-forget notification (task #13)."""

    def _client(self) -> tuple[_BridgeClient, _NoReadSock]:
        sock = _NoReadSock()
        client = _BridgeClient("/tmp/unused.sock")
        client._sock = sock  # type: ignore[assignment]
        return client, sock

    def test_notify_sends_idless_frame_without_reading(self) -> None:
        client, sock = self._client()
        client.notify("log", {"level": "info", "message": "hi"})
        sent = json.loads(sock.sent.rstrip(b"\n").decode())
        assert sent["method"] == "log"
        assert sent["params"]["message"] == "hi"
        assert "id" not in sent, "notifications must omit the id field"

    def test_log_is_fire_and_forget(self) -> None:
        client, sock = self._client()
        # Returns immediately; _NoReadSock.recv would raise if read.
        assert client.log("warn", "careful", None) is None
        sent = json.loads(sock.sent.rstrip(b"\n").decode())
        assert sent["method"] == "log"
        assert "id" not in sent

    def test_notify_when_disconnected_is_silent(self) -> None:
        client = _BridgeClient("/tmp/unused.sock")
        # Not connected — a dropped log line must not raise.
        assert client.notify("log", {"message": "x"}) is None

    def test_next_call_id_unaffected_by_notify(self) -> None:
        """Notifications don't consume request ids — replies still line up."""
        client = _BridgeClient("/tmp/unused.sock")
        client._sock = _FakeSock((json.dumps({"id": 1, "result": "ok"}) + "\n").encode())  # type: ignore[assignment]
        client.notify("log", {"message": "noise"})
        # The first real call still uses id=1 (the canned reply's id).
        assert client._call("list_tools", {}) == "ok"
