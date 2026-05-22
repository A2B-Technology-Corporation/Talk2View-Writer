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
import socket
import tempfile
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from com.sun.star.uno import XComponentContext

logger = logging.getLogger(__name__)

# Tools we expose to the web UI for the MVP slice. Everything in
# ``tools/`` is technically callable, but the chat UI's writerTools.ts
# only declares schemas for these five; gating the dispatcher to the
# same set means the engine cannot trick the bridge into calling
# something the user hasn't authorised. Future slices broaden this
# to all 20 tools.
_MVP_TOOL_NAMES: tuple[str, ...] = (
    "get_document",
    "get_selection",
    "insert_content",
    "format_text",
    "search_document",
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
        """Parse one JSON-RPC request, dispatch, return one JSON response."""
        req_id: Any = None
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
            if method == "proxy_fetch":
                result = self._proxy_fetch(
                    str(params.get("url", "")),
                    str(params.get("method", "GET")).upper(),
                    params.get("headers") or {},
                    params.get("body"),
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
        except Exception as exc:
            logger.exception(
                "BridgeServer.dispatch: id=%s line=%r raised", req_id, line
            )
            return json.dumps(
                {
                    "id": req_id,
                    "error": {
                        "type": type(exc).__name__,
                        "message": str(exc),
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
        try:
            with httpx.Client(timeout=30.0, follow_redirects=True) as client:
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
