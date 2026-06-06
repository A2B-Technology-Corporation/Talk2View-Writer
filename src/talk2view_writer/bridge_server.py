"""Unix-socket JSON-RPC bridge between LibreOffice Python and the pywebview subprocess.

The pywebview subprocess (``talk2view_writer.web_runner``) runs the
chat UI on its own main thread. When a user-driven tool call fires
in JavaScript, it routes through this socket back to LO's Python so
the tool body can reach the real UNO ``XComponentContext`` and
manipulate the document.

Protocol: newline-delimited JSON, one request and one response per
line.

Request:

    {"id": <int>, "method": "invoke_tool",
     "params": {"name": <str>, "args": <object>}}

Response (success):

    {"id": <int>, "result": <any-JSON>}

Response (error):

    {"id": <int>, "error": {"message": <str>, "type": <str>}}

The bridge is single-connection by design — the only client is the
one pywebview subprocess we spawned. Multiple-connection support
would need locking around UI-thread marshalling and isn't useful
yet.
"""

from __future__ import annotations

import contextlib
import errno
import json
import logging
import os
import queue
import socket
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from talk2view_writer.perf import log_timing, monotonic_ms

if TYPE_CHECKING:
    from com.sun.star.uno import XComponentContext

logger = logging.getLogger(__name__)

# Tools we expose to the web UI. Everything in ``tools/`` is
# technically callable, but the chat UI's writerTools.ts only declares
# schemas for the names listed here; gating the dispatcher to the
# same set means the engine cannot trick the bridge into calling
# something the user hasn't authorised.
#
# Each addition is paired with a matching entry in
# src/web/src/tools.ts so the schema the engine sees and the function
# the bridge dispatches to stay in lockstep (Investigation #35).
_MVP_TOOL_NAMES: tuple[str, ...] = (
    # Reading
    "get_document",
    "get_selection",
    "select_text",
    # Writing
    "insert_content",
    "insert_table",
    "edit_table",
    "insert_image",
    "undo_redo",
    "delete_content",
    # Formatting
    "format_text",
    "format_paragraph",
    "manage_list",
    # Search
    "search_document",
    # Structure
    "insert_break",
    "set_header_footer",
    "insert_page_numbers",
    "set_page_setup",
    # Commenting
    "get_comments",
    "add_comment",
    "manage_comment",
    # Preferences
    "manage_preferences",
)


class BridgeServer:
    """Unix-socket JSON-RPC server for the pywebview subprocess."""

    def __init__(self, ctx: XComponentContext) -> None:
        self.ctx = ctx
        self.socket_path: str | None = None
        self._sock: socket.socket | None = None
        self._accept_thread: threading.Thread | None = None
        self._conn_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._tools_by_name: dict[str, Callable[..., Any]] | None = None
        # Active streams keyed by stream_id. Each entry is a Queue
        # that the worker thread feeds events into, drained by
        # proxy_stream_next calls. Cleaned up when the consumer
        # receives the "done" event. See ADR-0033 (streaming SSE).
        self._streams: dict[str, queue.Queue[dict[str, Any]]] = {}
        self._streams_lock = threading.Lock()

    # ----- Lifecycle ------------------------------------------------------

    def start(self) -> str:
        """Bind the socket and start accepting connections.

        Returns the socket path the subprocess should connect to.
        Safe to call only once per instance.
        """
        if self.socket_path is not None:
            raise RuntimeError("BridgeServer.start called twice")

        tmpdir = tempfile.mkdtemp(prefix="talk2view-bridge-")
        self.socket_path = os.path.join(tmpdir, "sock")
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(self.socket_path)
        os.chmod(self.socket_path, 0o600)
        self._sock.listen(1)
        self._sock.settimeout(1.0)  # so the accept loop can check _stop

        logger.info("BridgeServer.start: listening on %s", self.socket_path)

        self._accept_thread = threading.Thread(
            target=self._accept_loop,
            name="bridge-accept",
            daemon=True,
        )
        self._accept_thread.start()
        return self.socket_path

    def stop(self) -> None:
        """Shut down the server and remove the socket file."""
        logger.info("BridgeServer.stop: shutting down")
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                logger.exception("BridgeServer.stop: socket close failed")
        if self.socket_path is not None and os.path.exists(self.socket_path):
            try:
                os.unlink(self.socket_path)
            except OSError:
                logger.exception(
                    "BridgeServer.stop: unlink %s failed", self.socket_path
                )

    # ----- Internals ------------------------------------------------------

    def _accept_loop(self) -> None:
        """Accept exactly one connection at a time."""
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except TimeoutError:
                continue
            except OSError as exc:
                if exc.errno == errno.EBADF:
                    return  # socket closed by stop()
                logger.exception("BridgeServer accept failed")
                continue
            logger.info("BridgeServer: accepted connection")
            self._conn_thread = threading.Thread(
                target=self._handle_connection,
                args=(conn,),
                name="bridge-conn",
                daemon=True,
            )
            self._conn_thread.start()

    def _handle_connection(self, conn: socket.socket) -> None:
        """Read newline-delimited JSON requests; reply on the same connection."""
        try:
            buf = b""
            while not self._stop.is_set():
                chunk = conn.recv(8192)
                if not chunk:
                    logger.info("BridgeServer: peer closed")
                    return
                buf += chunk
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    if not raw.strip():
                        continue
                    response = self._dispatch_line(raw.decode("utf-8"))
                    conn.sendall(response.encode("utf-8") + b"\n")
        except Exception:
            logger.exception("BridgeServer connection handler failed")
        finally:
            with contextlib.suppress(OSError):
                conn.close()

    def _dispatch_line(self, line: str) -> str:
        """Parse one JSON-RPC request, dispatch, return one JSON response.

        Times the whole server-side handling per request and emits a
        ``timing op=bridge.dispatch`` line (task #12). The elapsed value
        is what the JS side perceives as the bridge round-trip minus the
        socket transit, so it bounds tool exec, UI-thread marshalling,
        and (for ``proxy_stream_next``) the engine's per-chunk latency.
        """
        t0 = time.monotonic()
        req_id: Any = None
        method = "?"
        try:
            req = json.loads(line)
            req_id = req.get("id")
            method = req.get("method")
            params = req.get("params") or {}
            logger.info(
                "BridgeServer.dispatch: id=%s method=%s params=%s",
                req_id,
                method,
                params,
            )
            response = self._handle_method(method, params, req_id)
        except Exception as exc:
            logger.exception(
                "BridgeServer.dispatch: id=%s line=%r raised", req_id, line
            )
            response = json.dumps(
                {
                    "id": req_id,
                    "error": {
                        "type": type(exc).__name__,
                        "message": str(exc),
                    },
                }
            )
        log_timing(
            logger,
            "bridge.dispatch",
            monotonic_ms(t0),
            id=req_id,
            method=method,
        )
        return response

    def _handle_method(
        self, method: str, params: dict[str, Any], req_id: Any
    ) -> str:
        """Route one parsed request to its handler, return the JSON reply."""
        if method == "invoke_tool":
            result = self._invoke_tool(
                params.get("name", ""), params.get("args") or {}
            )
            return json.dumps({"id": req_id, "result": result})
        if method == "list_tools":
            return json.dumps({"id": req_id, "result": list(_MVP_TOOL_NAMES)})
        if method == "log":
            self._log_from_web(
                str(params.get("level", "info")),
                str(params.get("message", "")),
                params.get("context"),
            )
            return json.dumps({"id": req_id, "result": None})
        if method == "get_host_window":
            return json.dumps({"id": req_id, "result": self._host_window()})
        if method == "proxy_fetch":
            result = self._proxy_fetch(
                str(params.get("url", "")),
                str(params.get("method", "GET")).upper(),
                params.get("headers") or {},
                params.get("body"),
            )
            return json.dumps({"id": req_id, "result": result})
        if method == "proxy_stream_open":
            result = self._proxy_stream_open(
                str(params.get("url", "")),
                str(params.get("method", "GET")).upper(),
                params.get("headers") or {},
                params.get("body"),
            )
            return json.dumps({"id": req_id, "result": result})
        if method == "proxy_stream_next":
            result = self._proxy_stream_next(
                str(params.get("stream_id", "")),
            )
            return json.dumps({"id": req_id, "result": result})
        return json.dumps(
            {
                "id": req_id,
                "error": {
                    "type": "UnknownMethod",
                    "message": f"unknown method {method!r}",
                },
            }
        )

    # ----- Logging from the web side --------------------------------------

    def _log_from_web(
        self, level: str, message: str, context: Any
    ) -> None:
        """Append a log line forwarded from the chat UI.

        The web layer routes console.*, window.error, unhandledrejection,
        and every chat message (user + assistant + tool_call) here so
        the rotating log captures everything the user can see in the
        chat window.
        """
        web_logger = logging.getLogger("talk2view_writer.web")
        level_lower = level.lower()
        log_method = getattr(web_logger, level_lower, web_logger.info)
        if context is not None:
            try:
                ctx_str = json.dumps(context, default=str)
            except (TypeError, ValueError):
                ctx_str = repr(context)
            log_method("%s | %s", message, ctx_str)
        else:
            log_method("%s", message)

    # ----- Host window (companion-window docking, ADR-0039) ---------------

    def _host_window(self) -> dict[str, Any]:
        """Describe LibreOffice's main window for the chat subprocess.

        The pywebview chat window queries this once, before it opens, so
        it can size / position / parent itself against the LibreOffice
        document window (ADR-0039). The UNO reads run on LO's UI thread
        via :class:`UIThreadDispatcher` — UNO is not thread-safe and this
        executes on a bridge worker thread.

        Returns a descriptor dict (see :meth:`_read_host_window`), or an
        empty dict if there is no current frame / window or the marshalled
        read fails. Never raises — a docking-metadata failure must not
        break opening the chat window.
        """
        try:
            from talk2view_writer.extension import get_extension

            ext = get_extension(self.ctx)
            return ext.ui_thread.run_sync(self._read_host_window, timeout=5.0)
        except Exception:
            logger.exception("get_host_window: failed — returning {}")
            return {}

    def _read_host_window(self) -> dict[str, Any]:
        """Read LO's container-window geometry + native handle (UI thread).

        Mirrors the proven ``frame.getContainerWindow()`` pattern in
        ``about.py`` — the frame's container window is a real VCL window
        with a working peer, unlike the LO 26.x sidebar parent stub that
        ADR-0029 documented as unusable.

        Returns ``{}`` when there is no current frame or container window
        (the synthetic test rig's ``FakeFrame.getContainerWindow()``
        returns ``None``). Otherwise returns::

            {"geometry": {"x", "y", "w", "h"} | None,
             "xid": int | None, "hwnd": int | None, "nswindow": int | None}

        ``getPosSize`` is documented to raise "not implemented" on some
        peers (ADR-0029); that field degrades to ``None`` rather than
        failing the whole read.
        """
        smgr = self.ctx.ServiceManager
        desktop = smgr.createInstanceWithContext(
            "com.sun.star.frame.Desktop", self.ctx
        )
        frame = desktop.getCurrentFrame()
        if frame is None:
            return {}
        window = frame.getContainerWindow()
        if window is None:
            return {}
        result: dict[str, Any] = {
            "geometry": None,
            "xid": None,
            "hwnd": None,
            "nswindow": None,
        }
        try:
            rect = window.getPosSize()
            result["geometry"] = {
                "x": rect.X,
                "y": rect.Y,
                "w": rect.Width,
                "h": rect.Height,
            }
        except Exception:
            logger.exception("get_host_window: getPosSize failed — geometry=None")
        try:
            result.update(self._native_handle(window))
        except Exception:
            logger.exception(
                "get_host_window: native handle extraction failed — handle=None"
            )
        logger.info("get_host_window: %s", result)
        return result

    def _native_handle(self, window: Any) -> dict[str, Any]:
        """Best-effort native window handle for the current platform.

        Used by the subprocess to set a transient-for / owner relationship
        so the chat window stacks with LibreOffice. Returns ``{}`` when the
        peer doesn't expose ``XSystemDependentWindowPeer`` — which is the
        case on strict-PyUNO builds (ADR-0026), so callers must treat the
        handle as optional and fall back to geometry-only positioning.
        """
        import sys

        import uno

        xsd_type = uno.getTypeByName(
            "com.sun.star.awt.XSystemDependentWindowPeer"
        )
        peer = window.queryInterface(xsd_type)
        if peer is None:
            return {}
        const_name = {
            "linux": "com.sun.star.lang.SystemDependent.SYSTEM_XWINDOW",
            "win32": "com.sun.star.lang.SystemDependent.SYSTEM_WIN32",
            "darwin": "com.sun.star.lang.SystemDependent.SYSTEM_MAC",
        }.get(sys.platform)
        if const_name is None:
            return {}
        sys_type = uno.getConstantByName(const_name)
        # ProcessId is an empty byte sequence → "this process".
        handle = peer.getWindowHandle((), sys_type)
        # A handle of 0 means "no usable native handle" on every platform —
        # e.g. LO running as a native Wayland client returns XID 0. Normalise
        # it to None so the subprocess's `if not handle` checks read cleanly.
        if sys.platform == "linux":
            # X11 returns a SystemDependentXWindow struct; WindowHandle is
            # the XID. On Wayland the XID is an XWayland id that cannot be
            # used for cross-process parenting — the subprocess decides
            # whether to use it based on its own session type.
            return {"xid": int(handle.WindowHandle) or None}
        if sys.platform == "win32":
            return {"hwnd": int(handle) or None}
        if sys.platform == "darwin":
            return {"nswindow": int(handle) or None}
        return {}

    # ----- HTTPS proxy ----------------------------------------------------

    def _proxy_fetch(
        self,
        url: str,
        method: str,
        headers: dict[str, Any],
        body: Any,
    ) -> dict[str, Any]:
        """Proxy an HTTPS request from the webview through Python's httpx.

        The webview loads our HTML via ``file://``, so when the
        Talk2View SDK fetches ``https://engine.talk2view.com/...`` the
        engine treats the Origin as ``null`` (or ``file://`` on some
        WebKit builds) and rejects the response via CORS — but
        silently, without surfacing the rejection to the JS fetch
        promise. The 2026-05-22 19:50 repro showed requests firing
        and never resolving.

        Routing the call through this method instead lets Python's
        httpx make the actual HTTPS request. Python is not a browser
        and has no CORS rules; it just sends the headers we pass and
        returns the response verbatim.

        Returns a dict mirroring the parts of ``fetch``'s Response
        the SDK reads: status, statusText, headers, body. Non-streaming
        only — SSE chat streaming will need a separate path
        (next iteration).
        """
        # Lazy import — httpx is bundled but the rotating-log + unit
        # tests don't need to drag it in just to dispatch invoke_tool.
        import httpx

        # Coerce header values to str — they sometimes arrive as
        # ints / bools from JSON.
        clean_headers = {str(k): str(v) for k, v in headers.items()}

        if body is None:
            content: Any = None
        elif isinstance(body, str):
            content = body.encode("utf-8")
        elif isinstance(body, (bytes, bytearray)):
            content = bytes(body)
        else:
            # JSON-shaped body (dict/list) — re-serialise so the
            # exact bytes hit the wire.
            content = json.dumps(body).encode("utf-8")

        logger.info(
            "proxy_fetch: %s %s (header_count=%d body_len=%s)",
            method,
            url,
            len(clean_headers),
            len(content) if content is not None else None,
        )
        # /resume can take 60-120 s when the engine runs the next LLM
        # turn on a tool result (multi-step plans + slow models). Pre-2026-05-25
        # this was 30 s which surfaced as ``httpx.ReadTimeout`` mid-conversation
        # — the local "scope of work" smoke test caught it. Use httpx.Timeout
        # to keep connect+write tight (10 s — network-level should fail fast)
        # but allow long reads (300 s) for the SSE / engine-thinking endpoints.
        timeout = httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0)
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                resp = client.request(
                    method=method,
                    url=url,
                    headers=clean_headers,
                    content=content,
                )
        except httpx.RequestError as exc:
            logger.exception(
                "proxy_fetch: %s %s raised — synthesising 0 status",
                method,
                url,
            )
            return {
                "status": 0,
                "statusText": f"{type(exc).__name__}: {exc}",
                "headers": {},
                "body": "",
            }

        logger.info(
            "proxy_fetch: %s %s → %d %s (body_len=%d)",
            method,
            url,
            resp.status_code,
            resp.reason_phrase or "",
            len(resp.content),
        )
        return {
            "status": resp.status_code,
            "statusText": resp.reason_phrase or "",
            # Lower-case header names so JS-side matching is consistent;
            # the Fetch API also normalises to lowercase.
            "headers": {k.lower(): v for k, v in resp.headers.items()},
            # Use response.text for human-readable; binary responses
            # would need base64 but the engine's API is JSON throughout.
            "body": resp.text,
        }

    # ----- HTTPS streaming proxy -----------------------------------------

    def _proxy_stream_open(
        self,
        url: str,
        method: str,
        headers: dict[str, Any],
        body: Any,
    ) -> dict[str, Any]:
        """Begin streaming an HTTPS request via ``httpx.stream``.

        The actual HTTP work runs on a worker thread that pushes
        events into a per-stream Queue. The JS side drains the queue
        with ``proxy_stream_next(stream_id)`` calls. This polling
        model is forced by pywebview's request-response ``js_api`` —
        there is no way for Python to push to JS, so JS asks for the
        next event whenever it's ready.

        Event protocol on the queue:

          {"type": "headers", "status": N, "statusText": "...",
           "headers": {lowercase: value}}
          {"type": "chunk", "data": "..."}        # zero or more
          {"type": "error", "message": "..."}     # at most one
          {"type": "done"}                        # always last

        The consumer keeps calling ``proxy_stream_next`` until it
        receives a "done" event; the stream is removed from the
        registry at that point. A consumer that disappears mid-stream
        leaks the Queue + worker until LO exits — acceptable for now
        because there's exactly one consumer subprocess and it dies
        when LO does. See ADR-0033.
        """
        import httpx

        # Normalise inputs the same way ``_proxy_fetch`` does so the
        # streaming and non-streaming paths behave identically on
        # header / body coercion.
        clean_headers = {str(k): str(v) for k, v in headers.items()}
        content: bytes | None
        if body is None:
            content = None
        elif isinstance(body, str):
            content = body.encode("utf-8")
        elif isinstance(body, bytes | bytearray):
            content = bytes(body)
        else:
            content = json.dumps(body).encode("utf-8")

        stream_id = uuid.uuid4().hex
        q: queue.Queue[dict[str, Any]] = queue.Queue()
        with self._streams_lock:
            self._streams[stream_id] = q

        def worker() -> None:
            # Producer-side engine timing (task #12): TTFB measures how
            # long the engine took to start responding; total + chunk
            # count + byte count summarise the whole stream. This is the
            # ground-truth engine latency, independent of how promptly
            # the JS consumer re-polls.
            req_start = time.monotonic()
            ttfb_ms: float | None = None
            chunk_count = 0
            byte_count = 0
            try:
                with httpx.stream(
                    method,
                    url,
                    headers=clean_headers,
                    content=content,
                    timeout=httpx.Timeout(30.0, read=None),
                    follow_redirects=True,
                ) as resp:
                    ttfb_ms = monotonic_ms(req_start)
                    log_timing(
                        logger,
                        "stream.ttfb",
                        ttfb_ms,
                        stream_id=stream_id[:8],
                        status=resp.status_code,
                    )
                    q.put(
                        {
                            "type": "headers",
                            "status": resp.status_code,
                            "statusText": resp.reason_phrase or "",
                            "headers": {
                                k.lower(): v for k, v in resp.headers.items()
                            },
                        }
                    )
                    for chunk in resp.iter_text():
                        if chunk:
                            chunk_count += 1
                            byte_count += len(chunk)
                            q.put({"type": "chunk", "data": chunk})
            except Exception as exc:
                logger.exception(
                    "proxy_stream_open worker raised for %s %s", method, url
                )
                q.put({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
            finally:
                log_timing(
                    logger,
                    "stream.total",
                    monotonic_ms(req_start),
                    stream_id=stream_id[:8],
                    chunks=chunk_count,
                    bytes=byte_count,
                    ttfb_ms=None if ttfb_ms is None else round(ttfb_ms, 1),
                )
                q.put({"type": "done"})

        thread = threading.Thread(
            target=worker, name=f"proxy-stream-{stream_id[:8]}", daemon=True
        )
        thread.start()

        logger.info(
            "proxy_stream_open: %s %s stream_id=%s", method, url, stream_id
        )
        return {"stream_id": stream_id}

    def _proxy_stream_next(self, stream_id: str) -> dict[str, Any]:
        """Pop the next event from ``stream_id``'s queue.

        Blocks the dispatch thread (i.e. one bridge-server connection
        handler) until an event is available. JS-side dispatch is
        single-threaded per stream, so the only thing blocked is one
        chunk's read latency.

        Removes the stream from the registry when it returns a
        ``done`` event. Unknown stream IDs return a ``type=error``
        result (rather than raising) so the JS side gets a defined
        event it can route to the stream's error path.
        """
        with self._streams_lock:
            q = self._streams.get(stream_id)
        if q is None:
            return {
                "type": "error",
                "message": f"unknown stream_id {stream_id!r}",
            }
        # 60s upper bound — keeps the bridge thread from blocking on
        # a worker that has wedged (e.g. the engine started streaming
        # and then went unresponsive). The JS side can retry.
        #
        # The wait here is the consumer-perceived per-chunk latency:
        # with the JS side re-polling immediately, it equals the engine's
        # inter-chunk gap. ``stream.chunk_wait`` is the single most
        # useful line for diagnosing "the chat feels slow" — sum it over
        # a turn to see total engine streaming time (task #12).
        wait_start = time.monotonic()
        try:
            event = q.get(timeout=60.0)
        except queue.Empty:
            logger.warning(
                "proxy_stream_next: 60s wait elapsed for %s — returning timeout",
                stream_id,
            )
            log_timing(
                logger,
                "stream.chunk_wait",
                monotonic_ms(wait_start),
                stream_id=stream_id[:8],
                event="timeout",
            )
            return {"type": "timeout"}
        log_timing(
            logger,
            "stream.chunk_wait",
            monotonic_ms(wait_start),
            stream_id=stream_id[:8],
            event=event.get("type"),
        )
        if event.get("type") == "done":
            with self._streams_lock:
                self._streams.pop(stream_id, None)
        return event

    # ----- Tool dispatch --------------------------------------------------

    def _invoke_tool(self, name: str, args: dict[str, Any]) -> Any:
        """Look up the named tool and call it with ``**args``.

        Tools are decorated with ``@ui_thread_tool``, which marshals
        UNO calls to LO's UI thread via :class:`UIThreadDispatcher`.
        That means we can invoke from this bridge's worker thread
        safely — the marshalling happens inside the tool wrapper.
        """
        if name not in _MVP_TOOL_NAMES:
            raise ValueError(
                f"tool {name!r} not in MVP allowlist {_MVP_TOOL_NAMES}"
            )
        tool = self._lookup_tool(name)
        logger.info("BridgeServer._invoke_tool: %s(**%s)", name, args)
        result = tool(**args)
        logger.info(
            "BridgeServer._invoke_tool: %s returned %s",
            name,
            _truncate(result),
        )
        return result

    def _lookup_tool(self, name: str) -> Callable[..., Any]:
        """Resolve a tool function by name. Registry is built once + cached."""
        if self._tools_by_name is None:
            from talk2view_writer.tools import all_tools

            self._tools_by_name = {fn.__name__: fn for fn in all_tools()}
            logger.info(
                "BridgeServer: tool registry built with %d tools",
                len(self._tools_by_name),
            )
        tool = self._tools_by_name.get(name)
        if tool is None:
            raise KeyError(f"tool {name!r} not registered")
        return tool


def _truncate(value: Any, limit: int = 240) -> str:
    """Render a tool result for logging without flooding the log file."""
    s = repr(value)
    return s if len(s) <= limit else s[: limit - 3] + "..."
