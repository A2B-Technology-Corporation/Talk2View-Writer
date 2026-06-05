"""Unit tests for the JS↔Python bridge server (ADR-0030 step 2).

Covers the protocol surface — line-delimited JSON-RPC, the MVP-tool
allowlist, the success/error response shapes — without spinning up a
real pywebview subprocess.

We exercise ``_dispatch_line`` directly with crafted JSON strings,
and use a real Unix socket end-to-end test for the accept loop.
"""

from __future__ import annotations

import json
import socket
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from talk2view_writer.bridge_server import _MVP_TOOL_NAMES, BridgeServer


@pytest.fixture
def stub_tool(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    """Replace the tool registry with deterministic stubs.

    Each MVP tool becomes a function that records its name + args
    and returns a fixed string. Returns the recorded call list so
    tests can assert what the dispatcher saw.
    """
    calls: list[tuple[str, dict[str, Any]]] = []

    def _make(name: str):
        def _stub(**kwargs: Any) -> str:
            calls.append((name, kwargs))
            return f"<{name} ok args={sorted(kwargs)}>"

        _stub.__name__ = name
        return _stub

    fake_tools = [_make(n) for n in _MVP_TOOL_NAMES]
    # Plus one synthetic non-MVP tool so we can test the allowlist gate.
    # All 21 real Writer tools are now exposed, so this is a fake name
    # purely for asserting the allowlist still blocks unknown tools.
    extra = _make("hypothetical_future_tool")
    fake_tools.append(extra)

    def fake_all_tools() -> list[Any]:
        return list(fake_tools)

    monkeypatch.setattr(
        "talk2view_writer.tools.all_tools", fake_all_tools
    )
    return calls


@pytest.mark.unit
class TestDispatchLine:
    """``_dispatch_line`` produces correctly-shaped JSON-RPC responses."""

    def _server(self) -> BridgeServer:
        return BridgeServer(ctx=MagicMock(name="ctx"))

    def test_invoke_tool_round_trip(
        self, stub_tool: list[tuple[str, dict[str, Any]]]
    ) -> None:
        srv = self._server()
        req = json.dumps(
            {
                "id": 7,
                "method": "invoke_tool",
                "params": {"name": "get_selection", "args": {}},
            }
        )
        resp = json.loads(srv._dispatch_line(req))
        assert resp == {"id": 7, "result": "<get_selection ok args=[]>"}
        assert stub_tool == [("get_selection", {})]

    def test_invoke_tool_passes_args_as_kwargs(
        self, stub_tool: list[tuple[str, dict[str, Any]]]
    ) -> None:
        srv = self._server()
        req = json.dumps(
            {
                "id": 1,
                "method": "invoke_tool",
                "params": {
                    "name": "get_document",
                    "args": {"start_index": 0, "count": 5},
                },
            }
        )
        srv._dispatch_line(req)
        assert stub_tool == [
            ("get_document", {"start_index": 0, "count": 5})
        ]

    def test_invoke_tool_rejects_non_mvp_tool(
        self, stub_tool: list[tuple[str, dict[str, Any]]]
    ) -> None:
        """Allowlist blocks tools not in _MVP_TOOL_NAMES."""
        srv = self._server()
        req = json.dumps(
            {
                "id": 2,
                "method": "invoke_tool",
                "params": {"name": "hypothetical_future_tool", "args": {}},
            }
        )
        resp = json.loads(srv._dispatch_line(req))
        assert resp["id"] == 2
        assert "error" in resp
        assert "hypothetical_future_tool" in resp["error"]["message"]
        assert resp["error"]["type"] == "ValueError"
        # The tool was NOT invoked.
        assert stub_tool == []

    def test_unknown_method_returns_error(
        self, stub_tool: list[tuple[str, dict[str, Any]]]
    ) -> None:
        srv = self._server()
        req = json.dumps({"id": 3, "method": "do_the_thing", "params": {}})
        resp = json.loads(srv._dispatch_line(req))
        assert resp["id"] == 3
        assert resp["error"]["type"] == "UnknownMethod"

    def test_list_tools_returns_mvp_allowlist(
        self, stub_tool: list[tuple[str, dict[str, Any]]]
    ) -> None:
        srv = self._server()
        req = json.dumps({"id": 4, "method": "list_tools", "params": {}})
        resp = json.loads(srv._dispatch_line(req))
        assert resp["id"] == 4
        assert resp["result"] == list(_MVP_TOOL_NAMES)

    def test_malformed_json_returns_error(self) -> None:
        srv = self._server()
        resp = json.loads(srv._dispatch_line("not json"))
        # id is None for unparseable requests
        assert resp["id"] is None
        assert resp["error"]["type"] == "JSONDecodeError"

    def test_tool_raising_returns_error_with_type(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A tool that raises propagates as a JSON-RPC error response."""

        def boom(**_kwargs: Any) -> str:
            raise RuntimeError("kaboom")

        boom.__name__ = "get_selection"

        def fake_all_tools() -> list[Any]:
            return [boom]

        monkeypatch.setattr(
            "talk2view_writer.tools.all_tools", fake_all_tools
        )
        srv = self._server()
        req = json.dumps(
            {
                "id": 5,
                "method": "invoke_tool",
                "params": {"name": "get_selection", "args": {}},
            }
        )
        resp = json.loads(srv._dispatch_line(req))
        assert resp["id"] == 5
        assert resp["error"]["type"] == "RuntimeError"
        assert "kaboom" in resp["error"]["message"]


@pytest.mark.unit
class TestSocketLifecycle:
    """End-to-end smoke test via a real Unix socket."""

    def test_start_returns_writable_socket_path(
        self,
        stub_tool: list[tuple[str, dict[str, Any]]],
    ) -> None:
        srv = BridgeServer(ctx=MagicMock(name="ctx"))
        try:
            path = srv.start()
            assert path == srv.socket_path

            # Connect like the subprocess would.
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(5.0)
            client.connect(path)
            try:
                req = (
                    json.dumps(
                        {
                            "id": 1,
                            "method": "invoke_tool",
                            "params": {
                                "name": "get_selection",
                                "args": {},
                            },
                        }
                    )
                    + "\n"
                ).encode()
                client.sendall(req)

                # Read one line back.
                buf = b""
                deadline = time.time() + 5.0
                while b"\n" not in buf and time.time() < deadline:
                    chunk = client.recv(8192)
                    if not chunk:
                        break
                    buf += chunk
                assert b"\n" in buf, f"no response within timeout (buf={buf!r})"
                line, _ = buf.split(b"\n", 1)
                resp = json.loads(line.decode())
                assert resp == {"id": 1, "result": "<get_selection ok args=[]>"}
                assert stub_tool == [("get_selection", {})]
            finally:
                client.close()
        finally:
            srv.stop()
            # Give the accept thread a tick to wind down.
            if srv._accept_thread is not None:
                srv._accept_thread.join(timeout=2.0)

    def test_stop_removes_socket_file(
        self, stub_tool: list[tuple[str, dict[str, Any]]]
    ) -> None:
        import os

        srv = BridgeServer(ctx=MagicMock(name="ctx"))
        path = srv.start()
        assert os.path.exists(path)
        srv.stop()
        assert not os.path.exists(path), (
            f"socket file should be unlinked after stop(), still at {path}"
        )

    def test_start_twice_raises(
        self, stub_tool: list[tuple[str, dict[str, Any]]]
    ) -> None:
        srv = BridgeServer(ctx=MagicMock(name="ctx"))
        try:
            srv.start()
            with pytest.raises(RuntimeError, match="start called twice"):
                srv.start()
        finally:
            srv.stop()


@pytest.mark.unit
class TestProxyStream:
    """Streaming proxy: open + drain chunk-by-chunk.

    Mocks httpx.stream so we can script the SSE chunks deterministically
    without spinning a real server. The bridge worker thread runs to
    completion before the test reads chunks; ordering is enforced by
    the FIFO queue.
    """

    def _server(self) -> BridgeServer:
        return BridgeServer(ctx=MagicMock(name="ctx"))

    def _patch_httpx_stream(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        status: int = 200,
        reason: str = "OK",
        headers: dict[str, str] | None = None,
        chunks: list[str] | None = None,
        raise_exc: Exception | None = None,
    ) -> list[dict[str, Any]]:
        """Patch ``httpx.stream`` to yield a scripted response.

        Returns a list that records every call to ``stream(...)`` so
        tests can assert on the request shape.
        """
        from contextlib import contextmanager

        calls: list[dict[str, Any]] = []

        class _Resp:
            status_code = status
            reason_phrase = reason

            def __init__(self) -> None:
                self.headers = headers or {}

            def iter_text(self):
                yield from chunks or []

        @contextmanager
        def fake_stream(method, url, **kwargs):
            calls.append({"method": method, "url": url, **kwargs})
            if raise_exc is not None:
                raise raise_exc
            yield _Resp()

        import httpx

        monkeypatch.setattr(httpx, "stream", fake_stream)
        return calls

    def test_stream_open_returns_stream_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._patch_httpx_stream(monkeypatch, chunks=["data: hello\n\n"])
        srv = self._server()
        line = json.dumps(
            {
                "id": 1,
                "method": "proxy_stream_open",
                "params": {
                    "url": "https://example.test/x",
                    "method": "POST",
                    "headers": {"accept": "text/event-stream"},
                    "body": "{}",
                },
            }
        )
        resp = json.loads(srv._dispatch_line(line))
        assert resp["id"] == 1
        assert "stream_id" in resp["result"]
        assert isinstance(resp["result"]["stream_id"], str)
        assert len(resp["result"]["stream_id"]) > 0

    def test_stream_delivers_headers_then_chunks_then_done(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._patch_httpx_stream(
            monkeypatch,
            status=200,
            reason="OK",
            headers={"content-type": "text/event-stream"},
            chunks=["data: a\n\n", "data: b\n\n", "data: [DONE]\n\n"],
        )
        srv = self._server()
        opened = json.loads(
            srv._dispatch_line(
                json.dumps(
                    {
                        "id": 1,
                        "method": "proxy_stream_open",
                        "params": {
                            "url": "https://example.test/x",
                            "method": "POST",
                            "headers": {},
                            "body": None,
                        },
                    }
                )
            )
        )
        stream_id = opened["result"]["stream_id"]

        events: list[dict[str, Any]] = []
        for next_id in range(2, 100):
            r = json.loads(
                srv._dispatch_line(
                    json.dumps(
                        {
                            "id": next_id,
                            "method": "proxy_stream_next",
                            "params": {"stream_id": stream_id},
                        }
                    )
                )
            )
            events.append(r["result"])
            if r["result"]["type"] == "done":
                break
        else:
            pytest.fail("stream never finished")

        # First event is headers
        assert events[0]["type"] == "headers"
        assert events[0]["status"] == 200
        assert events[0]["statusText"] == "OK"
        assert events[0]["headers"] == {"content-type": "text/event-stream"}
        # Then exactly three chunks in order
        chunk_events = [e for e in events if e["type"] == "chunk"]
        assert [e["data"] for e in chunk_events] == [
            "data: a\n\n",
            "data: b\n\n",
            "data: [DONE]\n\n",
        ]
        # Then a done event
        assert events[-1]["type"] == "done"

    def test_stream_next_unknown_stream_id_returns_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        srv = self._server()
        r = json.loads(
            srv._dispatch_line(
                json.dumps(
                    {
                        "id": 1,
                        "method": "proxy_stream_next",
                        "params": {"stream_id": "does-not-exist"},
                    }
                )
            )
        )
        # We model unknown stream as a result.type=error so the JS
        # side gets a defined event (vs an RPC error which would
        # reject the promise).
        assert r["result"]["type"] == "error"
        assert "unknown" in r["result"]["message"].lower()

    def test_stream_open_passes_method_url_and_body_to_httpx(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = self._patch_httpx_stream(monkeypatch, chunks=[])
        srv = self._server()
        body = '{"messages":[{"role":"user","content":"hi"}],"stream":true}'
        srv._dispatch_line(
            json.dumps(
                {
                    "id": 1,
                    "method": "proxy_stream_open",
                    "params": {
                        "url": "https://example.test/v1/sessions/abc/messages",
                        "method": "POST",
                        "headers": {"x-t2v-partner-key": "pk"},
                        "body": body,
                    },
                }
            )
        )
        # Wait briefly for the worker thread to make the httpx call.
        # The worker copies the call args at start.
        for _ in range(50):
            if calls:
                break
            time.sleep(0.01)
        assert len(calls) == 1
        assert calls[0]["method"] == "POST"
        assert calls[0]["url"] == "https://example.test/v1/sessions/abc/messages"
        # httpx receives the body as ``content=...`` bytes.
        assert calls[0]["content"] == body.encode("utf-8")

    def test_stream_done_event_cleans_up_registry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Re-reading a finished stream returns 'error: unknown stream'."""
        self._patch_httpx_stream(monkeypatch, chunks=["one"])
        srv = self._server()
        opened = json.loads(
            srv._dispatch_line(
                json.dumps(
                    {
                        "id": 1,
                        "method": "proxy_stream_open",
                        "params": {
                            "url": "https://example.test/x",
                            "method": "GET",
                            "headers": {},
                            "body": None,
                        },
                    }
                )
            )
        )
        stream_id = opened["result"]["stream_id"]
        # Drain to done
        for next_id in range(2, 20):
            r = json.loads(
                srv._dispatch_line(
                    json.dumps(
                        {
                            "id": next_id,
                            "method": "proxy_stream_next",
                            "params": {"stream_id": stream_id},
                        }
                    )
                )
            )
            if r["result"]["type"] == "done":
                break
        # Now stream_id should be unknown
        post = json.loads(
            srv._dispatch_line(
                json.dumps(
                    {
                        "id": 99,
                        "method": "proxy_stream_next",
                        "params": {"stream_id": stream_id},
                    }
                )
            )
        )
        assert post["result"]["type"] == "error"
        assert "unknown" in post["result"]["message"].lower()

    def test_stream_httpx_error_surfaces_as_error_event(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import httpx

        self._patch_httpx_stream(
            monkeypatch,
            raise_exc=httpx.ConnectError("connection refused"),
        )
        srv = self._server()
        opened = json.loads(
            srv._dispatch_line(
                json.dumps(
                    {
                        "id": 1,
                        "method": "proxy_stream_open",
                        "params": {
                            "url": "https://example.test/x",
                            "method": "GET",
                            "headers": {},
                            "body": None,
                        },
                    }
                )
            )
        )
        stream_id = opened["result"]["stream_id"]
        # First (and only) event should be an error, then a done.
        events: list[dict[str, Any]] = []
        for next_id in range(2, 10):
            r = json.loads(
                srv._dispatch_line(
                    json.dumps(
                        {
                            "id": next_id,
                            "method": "proxy_stream_next",
                            "params": {"stream_id": stream_id},
                        }
                    )
                )
            )
            events.append(r["result"])
            if r["result"]["type"] == "done":
                break
        types = [e["type"] for e in events]
        assert "error" in types
        assert types[-1] == "done"
        err = next(e for e in events if e["type"] == "error")
        assert "connection refused" in err["message"]


@pytest.mark.unit
class TestGetHostWindow:
    """``get_host_window`` reports LO's window for companion-window docking.

    The UI-thread marshalling (``_host_window``) needs a real LibreOffice,
    so these tests drive ``_read_host_window`` (the UNO-reading half)
    directly with fakes, and verify dispatch routing by stubbing
    ``_host_window``.
    """

    @staticmethod
    def _ctx_with_window(window: Any) -> MagicMock:
        """A ctx whose Desktop → frame → container window is ``window``."""
        ctx = MagicMock(name="ctx")
        desktop = MagicMock(name="desktop")
        frame = MagicMock(name="frame")
        frame.getContainerWindow.return_value = window
        desktop.getCurrentFrame.return_value = frame
        ctx.ServiceManager.createInstanceWithContext.return_value = desktop
        return ctx

    def test_get_host_window_not_in_tool_allowlist(self) -> None:
        # It is bridge infra, not a document tool the engine can invoke.
        assert "get_host_window" not in _MVP_TOOL_NAMES

    def test_dispatch_routes_to_host_window(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        srv = BridgeServer(ctx=MagicMock(name="ctx"))
        payload = {"geometry": {"x": 0, "y": 0, "w": 10, "h": 20}, "xid": None}
        monkeypatch.setattr(srv, "_host_window", lambda: payload)
        req = json.dumps({"id": 8, "method": "get_host_window", "params": {}})
        resp = json.loads(srv._dispatch_line(req))
        assert resp == {"id": 8, "result": payload}

    def test_read_host_window_no_frame_returns_empty(self) -> None:
        ctx = MagicMock(name="ctx")
        desktop = MagicMock(name="desktop")
        desktop.getCurrentFrame.return_value = None
        ctx.ServiceManager.createInstanceWithContext.return_value = desktop
        srv = BridgeServer(ctx=ctx)
        assert srv._read_host_window() == {}

    def test_read_host_window_no_container_window_returns_empty(self) -> None:
        srv = BridgeServer(ctx=self._ctx_with_window(None))
        assert srv._read_host_window() == {}

    def test_read_host_window_returns_geometry(self) -> None:
        window = MagicMock(name="window")
        window.getPosSize.return_value = SimpleNamespace(
            X=100, Y=50, Width=1200, Height=900
        )
        # No XSystemDependentWindowPeer → native handle skipped.
        window.queryInterface.return_value = None
        srv = BridgeServer(ctx=self._ctx_with_window(window))
        result = srv._read_host_window()
        assert result["geometry"] == {"x": 100, "y": 50, "w": 1200, "h": 900}
        assert result["xid"] is None
        assert result["hwnd"] is None

    def test_read_host_window_getpossize_failure_degrades_to_none(self) -> None:
        window = MagicMock(name="window")
        window.getPosSize.side_effect = RuntimeError("not implemented")
        window.queryInterface.return_value = None
        srv = BridgeServer(ctx=self._ctx_with_window(window))
        result = srv._read_host_window()
        assert result["geometry"] is None
        assert result["xid"] is None

    def test_read_host_window_native_handle_failure_keeps_geometry(self) -> None:
        window = MagicMock(name="window")
        window.getPosSize.return_value = SimpleNamespace(
            X=1, Y=2, Width=3, Height=4
        )
        window.queryInterface.side_effect = RuntimeError("boom")
        srv = BridgeServer(ctx=self._ctx_with_window(window))
        result = srv._read_host_window()
        assert result["geometry"] == {"x": 1, "y": 2, "w": 3, "h": 4}
        assert result["xid"] is None

    def test_native_handle_zero_xid_normalised_to_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # LO running as a native Wayland client yields XID 0, which is not a
        # usable handle — it must come back as None, not 0.
        import uno

        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.setattr(uno, "getTypeByName", lambda name: name, raising=False)
        monkeypatch.setattr(
            uno, "getConstantByName", lambda name: 4, raising=False
        )
        peer = MagicMock(name="peer")
        peer.getWindowHandle.return_value = SimpleNamespace(
            WindowHandle=0, DisplayPointer=0
        )
        window = MagicMock(name="window")
        window.queryInterface.return_value = peer
        srv = BridgeServer(ctx=MagicMock(name="ctx"))
        assert srv._native_handle(window) == {"xid": None}


@pytest.mark.unit
class TestToolRegistry:
    """The registry caches the tools list (built once per server)."""

    def test_lookup_caches_registry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        call_count = 0

        def t1(**_kwargs: Any) -> str:
            return "ok"

        t1.__name__ = "get_selection"

        def fake_all_tools() -> list[Any]:
            nonlocal call_count
            call_count += 1
            return [t1]

        monkeypatch.setattr(
            "talk2view_writer.tools.all_tools", fake_all_tools
        )
        srv = BridgeServer(ctx=MagicMock(name="ctx"))
        srv._lookup_tool("get_selection")
        srv._lookup_tool("get_selection")
        srv._lookup_tool("get_selection")
        assert call_count == 1, (
            "all_tools() should only be called once per server "
            "(registry cached)"
        )
