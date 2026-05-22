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
    # Plus one non-MVP tool so we can test the allowlist gate.
    extra = _make("undo_redo")
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
                "params": {"name": "undo_redo", "args": {}},
            }
        )
        resp = json.loads(srv._dispatch_line(req))
        assert resp["id"] == 2
        assert "error" in resp
        assert "undo_redo" in resp["error"]["message"]
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
