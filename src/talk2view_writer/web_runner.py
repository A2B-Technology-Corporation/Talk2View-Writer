r"""Subprocess entry point for the pywebview chat window.

Per ADR-0030: pywebview enforces ``webview.start()`` must run on the
calling process's main thread. LibreOffice's main thread is owned
by LO's UI event loop, so we spawn this module as a separate
Python process — it has its own main thread, owns the pywebview
event loop, and never blocks LO.

This process opens a Unix-socket back to LO so JavaScript tool calls
in the chat UI can reach the real ``XComponentContext``. The
protocol is newline-delimited JSON; see
:mod:`talk2view_writer.bridge_server` for the wire shape.

Invocation:

    python3 -m talk2view_writer.web_runner <html_url> \\
        --bridge-socket <path>

The parent (LO extension) sets PYTHONPATH so this module + the
bundled ``webview`` package are importable.
"""

from __future__ import annotations

import argparse
import json
import logging
import socket
import sys
import threading
from typing import Any

logger = logging.getLogger("talk2view_writer.web_runner")


# ---------------------------------------------------------------------------
# Bridge client
# ---------------------------------------------------------------------------


class _BridgeClient:
    """Newline-delimited JSON-RPC client over a Unix socket.

    Single-connection; calls are serialised behind ``self._lock``
    because pywebview can dispatch multiple JS-originated calls
    concurrently from its worker pool.
    """

    def __init__(self, socket_path: str) -> None:
        self._socket_path = socket_path
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()
        self._next_id = 1
        self._read_buf = b""

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(self._socket_path)
        self._sock = sock
        logger.info("BridgeClient connected to %s", self._socket_path)

    def invoke_tool(self, name: str, args: dict[str, Any]) -> Any:
        return self._call("invoke_tool", {"name": name, "args": args})

    def list_tools(self) -> list[str]:
        return self._call("list_tools", {})

    def _call(self, method: str, params: dict[str, Any]) -> Any:
        if self._sock is None:
            raise RuntimeError("BridgeClient: not connected")
        with self._lock:
            req_id = self._next_id
            self._next_id += 1
            req = json.dumps(
                {"id": req_id, "method": method, "params": params}
            ).encode("utf-8")
            logger.info("BridgeClient -> %s", req.decode())
            self._sock.sendall(req + b"\n")
            line = self._read_line()
            logger.info("BridgeClient <- %s", line)
        resp = json.loads(line)
        if resp.get("id") != req_id:
            raise RuntimeError(
                f"BridgeClient: response id {resp.get('id')!r} "
                f"!= request id {req_id!r}"
            )
        if "error" in resp and resp["error"] is not None:
            err = resp["error"]
            raise RuntimeError(
                f"bridge {err.get('type', 'Error')}: {err.get('message', '')}"
            )
        return resp.get("result")

    def _read_line(self) -> str:
        """Read one newline-terminated line from the socket."""
        assert self._sock is not None
        while b"\n" not in self._read_buf:
            chunk = self._sock.recv(8192)
            if not chunk:
                raise RuntimeError("BridgeClient: socket closed by peer")
            self._read_buf += chunk
        line, self._read_buf = self._read_buf.split(b"\n", 1)
        return line.decode("utf-8")


# ---------------------------------------------------------------------------
# pywebview JS API
# ---------------------------------------------------------------------------


class _Api:
    """Exposed to JavaScript as ``window.pywebview.api``.

    Each method here is callable from JS as an async function:
    ``await window.pywebview.api.invoke_tool('get_selection', {})``.
    """

    def __init__(self, bridge: _BridgeClient | None) -> None:
        self._bridge = bridge

    def ping(self, message: str = "") -> dict[str, Any]:
        """No-op heartbeat. Useful for the JS smoke-test button."""
        logger.info("api.ping: %r", message)
        return {
            "echo": message,
            "from": "talk2view_writer.web_runner",
        }

    def list_tools(self) -> list[str]:
        """Return the list of MVP tool names the bridge will accept."""
        if self._bridge is None:
            return []
        return self._bridge.list_tools()

    def invoke_tool(self, name: str, args: dict[str, Any] | None = None) -> Any:
        """Run a tool by name with JSON-serialisable args.

        Routes to LO's Python via the bridge socket; LO's bridge
        server invokes the matching tool (which marshals UNO calls
        to LO's UI thread). Returns the tool's JSON-encoded return
        value as a Python object.
        """
        if self._bridge is None:
            raise RuntimeError(
                "Bridge not configured; cannot invoke tool. Did you start "
                "the subprocess with --bridge-socket?"
            )
        return self._bridge.invoke_tool(name, args or {})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Open the chat window and block on the pywebview event loop."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [web_runner] %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        prog="talk2view_writer.web_runner",
        description="pywebview subprocess for the Talk2View-Writer chat window.",
    )
    parser.add_argument("html_url", help="file:// URL of the bundled HTML")
    parser.add_argument(
        "--bridge-socket",
        default=None,
        help="Path to LO's bridge Unix socket. Omit for offline tests.",
    )
    args = parser.parse_args()

    logger.info(
        "web_runner starting: python=%s url=%s bridge=%s",
        sys.version.split()[0],
        args.html_url,
        args.bridge_socket,
    )

    bridge: _BridgeClient | None = None
    if args.bridge_socket:
        bridge = _BridgeClient(args.bridge_socket)
        try:
            bridge.connect()
        except OSError:
            logger.exception(
                "web_runner: failed to connect to bridge at %s — continuing "
                "without tool access (UI will still render but tool calls "
                "will error)",
                args.bridge_socket,
            )
            bridge = None

    api = _Api(bridge)

    import webview

    logger.info("webview imported, calling create_window")
    webview.create_window(
        "Talk2View",
        url=args.html_url,
        js_api=api,
        width=400,
        height=600,
    )
    logger.info("webview.create_window returned; entering webview.start()")
    webview.start(debug=True)
    logger.info("webview.start() returned — window closed, exiting")


if __name__ == "__main__":
    main()
