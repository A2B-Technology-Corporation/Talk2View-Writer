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
