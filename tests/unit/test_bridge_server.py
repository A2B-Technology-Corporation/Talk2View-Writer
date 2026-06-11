"""Unit tests for the JS↔Python bridge server (ADR-0030 step 2).

Covers the protocol surface — line-delimited JSON-RPC, the MVP-tool
allowlist, the success/error response shapes — without spinning up a
real pywebview subprocess.

We exercise ``_dispatch_line`` directly with crafted JSON strings,
and use a real Unix socket end-to-end test for the accept loop.
"""

from __future__ import annotations

import json
import logging
import socket
import time
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import MagicMock

import pytest

import talk2view_writer.bridge_server as bs
from talk2view_writer.bridge_server import _MVP_TOOL_NAMES, BridgeServer


@pytest.fixture(autouse=True)
def _stub_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin DNS resolution to success across this module.

    ``_proxy_fetch`` / ``proxy_stream_open`` now run a ``_dns_reachable``
    pre-check (investigations #63). These tests use non-resolving hosts
    (``example.test``, reserved per RFC 6761) with a mocked httpx, so without
    this stub the pre-check would short-circuit before httpx is reached. The
    DNS-bound behaviour has dedicated tests in ``test_dns_reachable.py``.
    """
    monkeypatch.setattr(bs, "_dns_reachable", lambda *_a, **_k: True)


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
class TestDispatchLineDoesNotLeakSecrets:
    """INFO-level dispatch logging must never persist credentials or PHI.

    Every engine request (auth login, token refresh) is routed through
    the bridge's proxy_fetch, and tool args carry document text. The
    persistent rotating log is shared in bug reports, so the default
    INFO path must record only the request envelope — never the body or
    tool args. See the credential-leak fix (bridge_server.py
    _dispatch_line / _invoke_tool).
    """

    def _server(self) -> BridgeServer:
        return BridgeServer(ctx=MagicMock(name="ctx"))

    @contextmanager
    def _capture_info(self) -> Iterator[list[logging.LogRecord]]:
        """Capture INFO+ records on the bridge logger regardless of propagate.

        The package logger sets ``propagate=False`` once ``setup_logging``
        has run, which makes pytest's ``caplog`` (attached at root) blind to
        these records. Attach our own handler to be robust.
        """
        records: list[logging.LogRecord] = []

        class _Collector(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record)

        logger = logging.getLogger("talk2view_writer.bridge_server")
        handler = _Collector(level=logging.INFO)
        prior_level = logger.level
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        try:
            yield records
        finally:
            logger.removeHandler(handler)
            logger.setLevel(prior_level)

    def test_proxy_fetch_body_not_logged_at_info(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Stub httpx so no real request fires; we only care about logging.
        sent: dict[str, Any] = {}

        class _Resp:
            status_code = 200
            reason_phrase = "OK"
            headers: ClassVar[dict[str, str]] = {}
            content = b""
            text = ""

        class _Client:
            def __init__(self, **_kw: Any) -> None: ...
            def __enter__(self) -> _Client:
                return self

            def __exit__(self, *_a: Any) -> None: ...
            def request(self, **kwargs: Any) -> _Resp:
                sent.update(kwargs)
                return _Resp()

        import httpx

        monkeypatch.setattr(httpx, "Client", _Client)
        srv = self._server()
        secret_body = json.dumps(
            {"email": "clinician@hospital.org", "password": "S3cr3t-PW!"}
        )
        req = json.dumps(
            {
                "id": 9,
                "method": "proxy_fetch",
                "params": {
                    "url": "https://engine.talk2view.com/v1/auth/login",
                    "method": "POST",
                    "headers": {"authorization": "Bearer secrettoken"},
                    "body": secret_body,
                },
            }
        )
        with self._capture_info() as records:
            srv._dispatch_line(req)
        info_text = "\n".join(r.getMessage() for r in records)
        assert "S3cr3t-PW!" not in info_text
        assert "clinician@hospital.org" not in info_text
        assert "secrettoken" not in info_text
        # The envelope (method) is still logged for debuggability.
        assert "proxy_fetch" in info_text

    def test_invoke_tool_args_not_logged_at_info(
        self,
        stub_tool: list[tuple[str, dict[str, Any]]],
    ) -> None:
        srv = self._server()
        phi = "Patient Jane Doe, MRN 123456, diagnosis confidential"
        req = json.dumps(
            {
                "id": 10,
                "method": "invoke_tool",
                "params": {"name": "insert_content", "args": {"text": phi}},
            }
        )
        with self._capture_info() as records:
            srv._dispatch_line(req)
        info_text = "\n".join(r.getMessage() for r in records)
        assert "Jane Doe" not in info_text
        assert "123456" not in info_text
        # Tool name + arg keys are still recorded.
        assert "insert_content" in info_text
        assert "text" in info_text


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

    def test_stop_removes_tempdir(
        self, stub_tool: list[tuple[str, dict[str, Any]]]
    ) -> None:
        """stop() removes the per-instance tempdir, not just the socket.

        start() creates a fresh tempfile.mkdtemp(prefix='talk2view-bridge-')
        and puts the socket inside it. Unlinking only the socket would
        leave an empty /tmp/talk2view-bridge-XXXX/ dir behind on every run.
        """
        import os

        srv = BridgeServer(ctx=MagicMock(name="ctx"))
        path = srv.start()
        tmpdir = os.path.dirname(path)
        assert os.path.isdir(tmpdir)
        srv.stop()
        assert not os.path.exists(tmpdir), (
            f"tempdir should be removed after stop(), still at {tmpdir}"
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
class TestConcurrentDispatch:
    """A slow request must not block other requests (investigation #63)."""

    def test_slow_request_does_not_block_a_fast_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A fast request answers while a slow one is still mid-flight.

        Before the concurrency fix the connection read loop dispatched
        requests serially, so a 25s proxy_fetch (or a 60s
        proxy_stream_next) blocked every following request — which made a
        chat send queue behind the startup calls. Now each request runs on
        its own pool worker, so the fast one returns immediately.
        """
        import threading

        release = threading.Event()
        slow_entered = threading.Event()

        def _slow(**_kw: Any) -> str:
            slow_entered.set()
            release.wait(5.0)
            return "<slow done>"

        _slow.__name__ = "get_document"

        def _fast(**_kw: Any) -> str:
            return "<fast done>"

        _fast.__name__ = "get_selection"

        monkeypatch.setattr(
            "talk2view_writer.tools.all_tools", lambda: [_slow, _fast]
        )

        srv = BridgeServer(ctx=MagicMock(name="ctx"))
        try:
            path = srv.start()
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(5.0)
            client.connect(path)
            try:
                buf = b""

                def read_one() -> dict[str, Any]:
                    nonlocal buf
                    deadline = time.time() + 5.0
                    while b"\n" not in buf and time.time() < deadline:
                        chunk = client.recv(8192)
                        if not chunk:
                            break
                        buf += chunk
                    assert b"\n" in buf, f"no response in time (buf={buf!r})"
                    line, buf = buf.split(b"\n", 1)
                    parsed: dict[str, Any] = json.loads(line.decode())
                    return parsed

                # Send the SLOW request (id=1) FIRST, then the FAST one (id=2).
                for rid, name in ((1, "get_document"), (2, "get_selection")):
                    client.sendall(
                        (
                            json.dumps(
                                {
                                    "id": rid,
                                    "method": "invoke_tool",
                                    "params": {"name": name, "args": {}},
                                }
                            )
                            + "\n"
                        ).encode()
                    )
                # Wait until the slow handler is actually running + blocked.
                assert slow_entered.wait(5.0)
                # The FAST response (id=2) must come back while id=1 is blocked.
                first = read_one()
                assert first["id"] == 2, (
                    f"fast request should answer first while the slow one "
                    f"blocks, got {first}"
                )
                assert first["result"] == "<fast done>"
                # Release the slow one; its response now arrives too.
                release.set()
                second = read_one()
                assert second["id"] == 1
                assert second["result"] == "<slow done>"
            finally:
                client.close()
        finally:
            release.set()
            srv.stop()
            if srv._accept_thread is not None:
                srv._accept_thread.join(timeout=2.0)

    def test_client_and_server_multiplex_end_to_end(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Full path: a fast _BridgeClient call answers while a slow one blocks.

        Exercises client multiplexing (reader thread + pending-by-id) and
        server concurrency (per-request worker) together over a real
        socket — the complete #63 fix.
        """
        import threading

        from talk2view_writer.web_runner import _BridgeClient

        release = threading.Event()
        slow_entered = threading.Event()

        def _slow(**_kw: Any) -> str:
            slow_entered.set()
            release.wait(5.0)
            return "<slow>"

        _slow.__name__ = "get_document"

        def _fast(**_kw: Any) -> str:
            return "<fast>"

        _fast.__name__ = "get_selection"

        monkeypatch.setattr(
            "talk2view_writer.tools.all_tools", lambda: [_slow, _fast]
        )

        srv = BridgeServer(ctx=MagicMock(name="ctx"))
        client: _BridgeClient | None = None
        try:
            path = srv.start()
            client = _BridgeClient(path)
            client.connect()

            results: dict[str, Any] = {}

            def call_slow() -> None:
                results["slow"] = client.invoke_tool("get_document", {})

            threading.Thread(target=call_slow, daemon=True).start()
            assert slow_entered.wait(5.0)

            # While the slow call is still blocked server-side, a fast call
            # from this thread completes and routes back by id.
            assert client.invoke_tool("get_selection", {}) == "<fast>"
            assert "slow" not in results  # the slow call is still in flight

            release.set()
            deadline = time.time() + 5.0
            while "slow" not in results and time.time() < deadline:
                time.sleep(0.01)
            assert results.get("slow") == "<slow>"
        finally:
            release.set()
            srv.stop()
            if srv._accept_thread is not None:
                srv._accept_thread.join(timeout=2.0)


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
        # The first event the consumer sees is the error; that is the
        # terminal event for the error path (the JS side stops pulling
        # after it), and the registry entry is dropped at that point so
        # the worker's trailing 'done' is no longer reachable — a further
        # poll returns an 'unknown stream' error rather than 'done'.
        first = json.loads(
            srv._dispatch_line(
                json.dumps(
                    {
                        "id": 2,
                        "method": "proxy_stream_next",
                        "params": {"stream_id": stream_id},
                    }
                )
            )
        )
        assert first["result"]["type"] == "error"
        # A ConnectError maps to the friendly, user-facing message rather than
        # the raw "connection refused" httpx text (see _friendly_network_error).
        assert "internet connection" in first["result"]["message"].lower()
        assert "connection refused" not in first["result"]["message"]
        # A consumer that keeps polling after the error gets 'unknown
        # stream', confirming the registry was cleaned up on 'error'.
        second = json.loads(
            srv._dispatch_line(
                json.dumps(
                    {
                        "id": 3,
                        "method": "proxy_stream_next",
                        "params": {"stream_id": stream_id},
                    }
                )
            )
        )
        assert second["result"]["type"] == "error"
        assert "unknown" in second["result"]["message"].lower()

    def test_stream_error_event_cleans_up_registry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An 'error' event drops the stream even if 'done' is never drained.

        The worker enqueues 'error' then 'done' on failure, but the JS
        consumer stops draining after 'error'. If we only popped the
        registry on 'done', the trailing 'done' would never be read and
        the entry would leak forever. Popping on 'error' too removes it
        immediately — verified by reading ``self._streams`` directly.
        """
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
        # Drain exactly one event — the error — and stop, mirroring the
        # JS consumer's error path (which does not pull the trailing
        # 'done').
        first = json.loads(
            srv._dispatch_line(
                json.dumps(
                    {
                        "id": 2,
                        "method": "proxy_stream_next",
                        "params": {"stream_id": stream_id},
                    }
                )
            )
        )
        assert first["result"]["type"] == "error"
        # The stream must be gone from the registry now, not lingering
        # until a 'done' that the consumer never reads.
        with srv._streams_lock:
            assert stream_id not in srv._streams

    def test_stream_request_has_finite_read_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The streaming httpx request uses a finite (non-None) read timeout.

        ``read=None`` would let a wedged engine block ``iter_text()``
        forever, never enqueueing 'error'/'done', so the JS side
        re-polls indefinitely. The read timeout must mirror the
        non-streaming path's finite bound.
        """
        import httpx

        calls = self._patch_httpx_stream(monkeypatch, chunks=[])
        srv = self._server()
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
        # Wait for the worker thread to issue the httpx.stream call.
        for _ in range(50):
            if calls:
                break
            time.sleep(0.01)
        assert len(calls) == 1
        timeout = calls[0]["timeout"]
        assert isinstance(timeout, httpx.Timeout)
        # The read timeout must be finite, not None.
        assert timeout.read is not None
        assert timeout.read == pytest.approx(300.0)


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
class TestTimingInstrumentation:
    """Every dispatched request + every stream-chunk wait is timed.

    The timing lines (``timing op=...``) are how a slow chat turn gets
    diagnosed from the LibreOffice log after the fact (task #12). These
    assert the instrumentation fires with the right ``op`` and the
    correlating fields — not the wall-clock value, which would flake.
    """

    _LOG = "talk2view_writer.bridge_server"

    @contextmanager
    def _capture(self, caplog: pytest.LogCaptureFixture) -> Iterator[None]:
        """Capture the bridge logger's records despite ``propagate=False``.

        The ``talk2view_writer`` package logger disables propagation
        (``_logging.py``), so ``caplog`` — which listens on the root
        logger — never sees the child ``bridge_server`` records. Attach
        caplog's handler straight to the bridge logger for the duration
        of the test instead of fighting the propagation chain.
        """
        log = logging.getLogger(self._LOG)
        log.addHandler(caplog.handler)
        prev_level = log.level
        log.setLevel(logging.INFO)
        try:
            yield
        finally:
            log.removeHandler(caplog.handler)
            log.setLevel(prev_level)

    def test_dispatch_emits_timing_line(
        self,
        stub_tool: list[tuple[str, dict[str, Any]]],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        srv = BridgeServer(ctx=MagicMock(name="ctx"))
        req = json.dumps({"id": 4, "method": "list_tools", "params": {}})
        with self._capture(caplog):
            srv._dispatch_line(req)
        assert "timing op=bridge.dispatch" in caplog.text
        assert "method=list_tools" in caplog.text
        assert "id=4" in caplog.text

    def test_dispatch_times_even_on_error(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        srv = BridgeServer(ctx=MagicMock(name="ctx"))
        with self._capture(caplog):
            srv._dispatch_line("not json")
        # Malformed line still gets a timing line (method unknown -> '?').
        assert "timing op=bridge.dispatch" in caplog.text

    def test_stream_chunk_wait_is_timed_with_event_type(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        import queue

        srv = BridgeServer(ctx=MagicMock(name="ctx"))
        stream_id = "deadbeefcafebabe"
        q: queue.Queue[dict[str, Any]] = queue.Queue()
        q.put({"type": "chunk", "data": "x"})
        with srv._streams_lock:
            srv._streams[stream_id] = q
        with self._capture(caplog):
            event = srv._proxy_stream_next(stream_id)
        assert event["type"] == "chunk"
        assert "timing op=stream.chunk_wait" in caplog.text
        assert "event=chunk" in caplog.text

    def test_stream_worker_logs_ttfb_and_total(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        class _Resp:
            status_code = 200
            reason_phrase = "OK"
            headers: ClassVar[dict[str, str]] = {}

            def iter_text(self):
                yield "data: a\n\n"
                yield "data: b\n\n"

        @contextmanager
        def fake_stream(method, url, **kwargs):
            yield _Resp()

        import httpx

        monkeypatch.setattr(httpx, "stream", fake_stream)
        srv = BridgeServer(ctx=MagicMock(name="ctx"))
        with self._capture(caplog):
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
        assert "timing op=stream.ttfb" in caplog.text
        assert "timing op=stream.total" in caplog.text
        assert "chunks=2" in caplog.text


@pytest.mark.unit
class TestNotifications:
    """Fire-and-forget notifications (id-less requests) get no reply.

    Debug ``log`` lines are sent as notifications so they never block on
    a response round-trip nor desync the framing of real RPC replies
    (task #13). JSON-RPC convention: a request without an ``id`` is a
    notification and the server stays silent.
    """

    def _server(self) -> BridgeServer:
        return BridgeServer(ctx=MagicMock(name="ctx"))

    def test_notification_returns_no_response(self) -> None:
        srv = self._server()
        line = json.dumps(
            {"method": "log", "params": {"level": "info", "message": "hi"}}
        )
        assert srv._dispatch_line(line) is None

    def test_request_with_id_still_responds(self) -> None:
        srv = self._server()
        line = json.dumps(
            {"id": 9, "method": "log", "params": {"message": "hi"}}
        )
        resp = json.loads(srv._dispatch_line(line))
        assert resp == {"id": 9, "result": None}

    def test_notification_handler_error_stays_silent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Even if the handler raises, a notification gets no reply (the
        # client isn't reading, so a stray error frame would desync the
        # next real reply).
        srv = self._server()

        def boom(*_a: Any, **_k: Any) -> None:
            raise RuntimeError("log sink down")

        monkeypatch.setattr(srv, "_log_from_web", boom)
        line = json.dumps({"method": "log", "params": {"message": "x"}})
        assert srv._dispatch_line(line) is None

    def test_socket_notification_does_not_desync_following_reply(
        self, stub_tool: list[tuple[str, dict[str, Any]]]
    ) -> None:
        """A notification followed by a real call yields exactly one reply.

        This is the framing-safety guarantee: if the server wrongly
        replied to the notification, the client's next ``recv`` would
        read the notification's reply as the call's reply (id mismatch).
        """
        srv = BridgeServer(ctx=MagicMock(name="ctx"))
        try:
            path = srv.start()
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(5.0)
            client.connect(path)
            try:
                notify = (
                    json.dumps(
                        {"method": "log", "params": {"message": "fire"}}
                    )
                    + "\n"
                ).encode()
                call = (
                    json.dumps(
                        {
                            "id": 42,
                            "method": "list_tools",
                            "params": {},
                        }
                    )
                    + "\n"
                ).encode()
                client.sendall(notify + call)

                buf = b""
                deadline = time.time() + 5.0
                while b"\n" not in buf and time.time() < deadline:
                    chunk = client.recv(8192)
                    if not chunk:
                        break
                    buf += chunk
                assert b"\n" in buf, f"no response (buf={buf!r})"
                first, rest = buf.split(b"\n", 1)
                resp = json.loads(first.decode())
                # The one reply we get is the list_tools reply, NOT a log
                # reply — proving the notification produced no frame.
                assert resp["id"] == 42
                assert resp["result"] == list(_MVP_TOOL_NAMES)
                # And there is no second frame queued behind it.
                assert rest.strip() == b""
            finally:
                client.close()
        finally:
            srv.stop()
            if srv._accept_thread is not None:
                srv._accept_thread.join(timeout=2.0)


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
