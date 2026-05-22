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
import os
import socket
import sys
import threading
from pathlib import Path
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

    def log(self, level: str, message: str, context: Any | None) -> None:
        # No need to round-trip the response; fire-and-forget the
        # log call but keep the request-id machinery so the server
        # can correlate later.
        self._call("log", {"level": level, "message": message, "context": context})

    def proxy_fetch(
        self,
        url: str,
        method: str,
        headers: dict[str, str],
        body: str | None,
    ) -> dict[str, Any]:
        return self._call(
            "proxy_fetch",
            {
                "url": url,
                "method": method,
                "headers": headers,
                "body": body,
            },
        )

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

    def proxy_fetch(
        self,
        url: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        body: str | None = None,
    ) -> dict[str, Any]:
        """Proxy an HTTPS request through Python's httpx.

        Called from JS as ``window.pywebview.api.proxy_fetch(url,
        method, headers, body)``. The webview's file:// origin
        can't talk to engine.talk2view.com directly (browser CORS
        silently drops the response); Python has no such rules.
        See ``BridgeServer._proxy_fetch`` for the Python side.
        """
        if self._bridge is None:
            raise RuntimeError(
                "Bridge not configured; cannot proxy fetch. Did you "
                "start the subprocess with --bridge-socket?"
            )
        return self._bridge.proxy_fetch(url, method, headers or {}, body)

    def log(
        self,
        level: str = "info",
        message: str = "",
        context: Any | None = None,
    ) -> None:
        """Forward a log line from the chat UI into LO's rotating log.

        Called from JS as ``window.pywebview.api.log(level, message,
        context)``. Best-effort: a bridge outage shouldn't crash the
        UI, so we swallow the connection error here. The local
        ``[talk2view-web]`` print keeps a trail in the subprocess
        stderr (which LO's web_runner-stderr pump captures too).
        """
        print(f"[talk2view-web] {level}: {message}", file=sys.stderr)
        if self._bridge is None:
            return
        try:
            self._bridge.log(level, message, context)
        except Exception:
            logger.exception(
                "_Api.log: bridge log forwarding failed — continuing"
            )


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

    _patch_webkitgtk_cors_settings()
    # Belt + braces: also raise the SSL tolerance just in case the
    # WebKitGTK shipped with this user's distro doesn't bundle the
    # same CA roots as system Python's httpx.
    webview.settings['IGNORE_SSL_ERRORS'] = True

    storage_path = _webview_storage_path()
    logger.info("webview imported, calling create_window")
    webview.create_window(
        "Talk2View",
        url=args.html_url,
        js_api=api,
        width=400,
        height=600,
    )
    logger.info(
        "webview.create_window returned; entering webview.start "
        "(private_mode=False storage_path=%s)",
        storage_path,
    )
    # private_mode=False persists cookies + localStorage across
    # sessions. The Talk2View SDK stores its auth token in
    # localStorage, so flipping this is the difference between
    # "user signs in every time they open the app" and "session
    # restored automatically". storage_path keeps the data in a
    # stable XDG-friendly location instead of pywebview's default
    # (~/.cache/<sys.argv[0]>) which depends on how soffice was
    # launched.
    webview.start(
        debug=True,
        private_mode=False,
        storage_path=str(storage_path),
    )
    logger.info("webview.start() returned — window closed, exiting")


def _webview_storage_path() -> Path:
    """Return a stable per-OS directory for webview cookies + localStorage.

    Mirrors ``talk2view_writer._logging.log_file_path`` so all of our
    persistent state lives under the same parent. Created if missing.
    """
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "Talk2View-Writer" / "webview"
    elif sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        base = Path(local) / "Talk2View-Writer" / "webview"
    else:
        xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        base = Path(xdg) / "talk2view-writer" / "webview"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _patch_webkitgtk_cors_settings() -> None:
    """Flip ``allow_universal_access_from_file_urls`` on WebKitGTK.

    The 2026-05-22 repro showed the Talk2View SDK firing
    ``POST https://engine.talk2view.com/v1/tools/register`` and the
    fetch never resolving — WebKitGTK silently drops cross-origin
    requests from ``file://`` pages unless universal access is
    explicitly granted. pywebview's GTK backend (gtk.py:217-227)
    sets several WebKit settings but not this one; we splice it in.

    No-op on macOS / Windows (those backends import their own
    platform module). Safe to call before ``webview.create_window``.
    """
    try:
        from webview.platforms import gtk as gtk_backend
    except ImportError:
        logger.info(
            "WebKitGTK patch: pywebview.platforms.gtk not importable on "
            "this platform — assuming non-Linux backend; skipping"
        )
        return

    if getattr(gtk_backend.BrowserView, "_t2v_cors_patched", False):
        return

    original_init = gtk_backend.BrowserView.__init__

    def patched_init(self: Any, window: Any) -> None:
        original_init(self, window)
        try:
            props = self.webview.get_settings().props
            props.allow_universal_access_from_file_urls = True
            # Pywebview only sets this from settings['ALLOW_FILE_URLS']
            # (default True) — make doubly sure file→file works too,
            # since SDK code-split chunks load via additional file://
            # requests off our entry HTML.
            props.allow_file_access_from_file_urls = True
            logger.info(
                "WebKitGTK patch applied: "
                "allow_universal_access_from_file_urls=True, "
                "allow_file_access_from_file_urls=True"
            )
        except Exception:
            logger.exception(
                "WebKitGTK patch: setting CORS-relaxing props raised — "
                "the webview will still open but cross-origin fetches to "
                "engine.talk2view.com will be silently dropped"
            )

    gtk_backend.BrowserView.__init__ = patched_init
    gtk_backend.BrowserView._t2v_cors_patched = True
    logger.info("WebKitGTK patch: BrowserView.__init__ wrapped")


if __name__ == "__main__":
    main()
