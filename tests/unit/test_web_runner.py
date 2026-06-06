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
