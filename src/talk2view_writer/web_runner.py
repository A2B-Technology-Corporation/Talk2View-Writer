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
import contextlib
import json
import logging
import os
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any, Literal, cast

from ._logging import debug_enabled
from .perf import log_timing, monotonic_ms

logger = logging.getLogger("talk2view_writer.web_runner")


# ---------------------------------------------------------------------------
# Bridge client
# ---------------------------------------------------------------------------


class _PendingCall:
    """One in-flight request awaiting its response, routed to by id."""

    __slots__ = ("error", "event", "result")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.result: Any = None
        self.error: dict[str, Any] | None = None


class _BridgeClient:
    """Multiplexed newline-delimited JSON-RPC client over a Unix socket.

    pywebview dispatches JS-originated calls concurrently from its worker
    pool, so requests are MULTIPLEXED over the single socket rather than
    serialised: each :meth:`_call` registers a pending entry keyed by
    request id, sends its frame, and waits; a single background reader
    thread reads responses and routes each to the waiting caller by id.

    This means a slow call (a proxy_fetch on a bad connection, or a
    proxy_stream_next blocking up to 60 s on the engine) never blocks
    another call — the bug that made a chat send queue ~40 s behind the
    startup calls (investigation #63).

    ``_write_lock`` guards only a single ``sendall`` so two senders' frame
    bytes never interleave. The read buffer is owned solely by the reader
    thread, so it needs no lock.
    """

    def __init__(self, socket_path: str) -> None:
        self._socket_path = socket_path
        self._sock: socket.socket | None = None
        self._write_lock = threading.Lock()  # guards sendall (frame integrity)
        self._pending_lock = threading.Lock()  # guards _pending + _next_id + reader
        self._pending: dict[int, _PendingCall] = {}
        self._next_id = 1
        self._read_buf = b""  # reader-thread-only
        self._reader: threading.Thread | None = None
        # Set when the socket dies; new + waiting calls fail with this.
        self._closed_error: str | None = None

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(self._socket_path)
        self._sock = sock
        self._start_reader()
        logger.info("BridgeClient connected to %s", self._socket_path)

    def _start_reader(self) -> None:
        """Start the response-reader thread once (idempotent, thread-safe)."""
        with self._pending_lock:
            if self._reader is not None:
                return
            reader = threading.Thread(
                target=self._read_loop, name="bridge-reader", daemon=True
            )
            self._reader = reader
        reader.start()

    def invoke_tool(self, name: str, args: dict[str, Any]) -> Any:
        return self._call("invoke_tool", {"name": name, "args": args})

    def list_tools(self) -> list[str]:
        return cast(list[str], self._call("list_tools", {}))

    def get_host_window(self) -> dict[str, Any]:
        """Ask LO for its main-window geometry + native handle (ADR-0039).

        Returns the descriptor dict from ``BridgeServer._host_window`` (or
        ``{}`` if LO has no current frame / the read failed). Used once,
        before the chat window opens, to size / position / parent it
        against the LibreOffice document window.
        """
        return cast(dict[str, Any], self._call("get_host_window", {}))

    def log(self, level: str, message: str, context: Any | None) -> None:
        # Fire-and-forget notification: no id, no reply read. Debug logs
        # fire hundreds of times per turn; routing them through the
        # blocking ``_call`` round-trip made each one wait for (and hold)
        # the single bridge lock, serialising them behind in-flight
        # ``proxy_stream_next`` reads. As a notification, a log costs
        # only a ``sendall`` (task #13).
        self.notify("log", {"level": level, "message": message, "context": context})

    def proxy_fetch(
        self,
        url: str,
        method: str,
        headers: dict[str, str],
        body: str | None,
    ) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            self._call(
                "proxy_fetch",
                {
                    "url": url,
                    "method": method,
                    "headers": headers,
                    "body": body,
                },
            ),
        )

    def proxy_stream_open(
        self,
        url: str,
        method: str,
        headers: dict[str, str],
        body: str | None,
    ) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            self._call(
                "proxy_stream_open",
                {
                    "url": url,
                    "method": method,
                    "headers": headers,
                    "body": body,
                },
            ),
        )

    def proxy_stream_next(self, stream_id: str) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            self._call("proxy_stream_next", {"stream_id": stream_id}),
        )

    def _call(self, method: str, params: dict[str, Any]) -> Any:
        if self._sock is None:
            raise RuntimeError("BridgeClient: not connected")

        pending = _PendingCall()
        # Register the pending entry BEFORE sending, so a response can never
        # arrive (and be dropped) before we're ready to receive it.
        with self._pending_lock:
            if self._closed_error is not None:
                raise RuntimeError(f"BridgeClient: {self._closed_error}")
            req_id = self._next_id
            self._next_id += 1
            self._pending[req_id] = pending
        req = json.dumps(
            {"id": req_id, "method": method, "params": params}
        ).encode("utf-8")
        # DEBUG, not INFO: this dumps the full request (including stream chunk
        # payloads on the way back) on every one of the hundreds of round-trips
        # per turn. The compact ``bridge.client_call`` timing line is the INFO
        # summary; the raw frames are debug-only (task #13).
        logger.debug("BridgeClient -> %s", req.decode())
        # Timing (task #12): ``lock_wait_ms`` is now just the brief wait for
        # the send lock (multiplexed — no longer the whole round-trip). ``ms``
        # is send -> response, the true per-call latency.
        t_before = time.monotonic()
        with self._write_lock:
            lock_wait_ms = monotonic_ms(t_before)
            t_sent = time.monotonic()
            self._sock.sendall(req + b"\n")
        # Start the reader only AFTER this request is registered + sent, so it
        # can never read a response before its pending entry exists. Normally
        # connect() already started it (this is then a no-op); this covers
        # callers that set _sock directly without connect() (e.g. unit tests).
        self._start_reader()
        pending.event.wait()
        rt_ms = monotonic_ms(t_sent)
        # The pending entry was already removed from the map by whoever set
        # the event (the reader on a response, or _fail_all_pending on EOF),
        # so there's nothing to pop here.
        log_timing(
            logger,
            "bridge.client_call",
            rt_ms,
            method=method,
            id=req_id,
            lock_wait_ms=round(lock_wait_ms, 1),
        )
        if pending.error is not None:
            err = pending.error
            raise RuntimeError(
                f"bridge {err.get('type', 'Error')}: {err.get('message', '')}"
            )
        return pending.result

    def _read_loop(self) -> None:
        """Read response frames and route each to its waiting caller by id.

        Runs on the single reader thread for the connection's lifetime. On
        EOF / socket error it fails every still-pending call so no caller
        waits forever.
        """
        try:
            while True:
                line = self._read_line()
                logger.debug("BridgeClient <- %s", line)
                try:
                    resp = json.loads(line)
                except ValueError:
                    logger.warning("BridgeClient: dropping unparseable response")
                    continue
                rid = resp.get("id")
                with self._pending_lock:
                    # Pop on fulfilment: once removed, a later EOF in
                    # _fail_all_pending can't clobber this resolved call.
                    pending = self._pending.pop(rid, None) if rid is not None else None
                if pending is None:
                    logger.warning(
                        "BridgeClient: response for unknown id %r dropped", rid
                    )
                    continue
                err = resp.get("error")
                if err is not None:
                    pending.error = err
                else:
                    pending.result = resp.get("result")
                pending.event.set()
        except Exception as exc:
            self._fail_all_pending(f"socket closed by peer ({exc})")

    def _fail_all_pending(self, reason: str) -> None:
        """Wake every still-waiting call with an error (socket died)."""
        with self._pending_lock:
            self._closed_error = reason
            waiters = list(self._pending.values())
            self._pending.clear()
        for pending in waiters:
            pending.error = {"type": "ConnectionError", "message": reason}
            pending.event.set()

    def notify(self, method: str, params: dict[str, Any]) -> None:
        """Send a fire-and-forget notification (no ``id``, no reply read).

        The server stays silent for id-less requests, so this returns as
        soon as the bytes are written. It takes ONLY ``_write_lock`` — held
        just for the single ``sendall`` — and registers no pending entry, so
        a log line never waits on (or behind) any in-flight ``_call``; the
        worst it waits is one other sender's ``sendall``.

        Notifications deliberately do NOT consume a request id: ids only
        matter for matching a reply, and there is no reply. Keeping
        ``_next_id`` untouched means real calls' reply-id matching is
        unaffected.

        Silently no-ops if not connected — a dropped log line must never
        crash the UI.
        """
        if self._sock is None:
            return
        req = json.dumps({"method": method, "params": params}).encode("utf-8")
        with self._write_lock:
            self._sock.sendall(req + b"\n")

    def _read_line(self) -> str:
        """Read one newline-terminated line from the socket (reader thread only)."""
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

    def open_external(self, url: str) -> dict[str, Any]:
        """Open an http(s) URL in the user's default browser.

        The chat UI runs in a pywebview WebKitGTK/Cocoa/EdgeChromium
        webview where JS ``window.open`` is a no-op (no browser chrome to
        host a new tab), so external links — e.g. the update banner's
        "Releases" link — must go through the host process. Handled in
        this subprocess; no LO round-trip needed.

        Only http/https is allowed so a compromised page can't trigger a
        ``file:`` / arbitrary-scheme handler.
        """
        if not isinstance(url, str) or not url.lower().startswith(
            ("http://", "https://")
        ):
            logger.warning("open_external: refusing non-http(s) URL %r", url)
            return {"opened": False}
        logger.info("open_external: opening %s", url)
        try:
            import webbrowser

            opened = webbrowser.open(url, new=2)
        except Exception:
            logger.exception("open_external: failed to open %s", url)
            return {"opened": False}
        return {"opened": bool(opened)}

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

    def proxy_stream_open(
        self,
        url: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        body: str | None = None,
    ) -> dict[str, Any]:
        """Open a streaming proxy. Returns ``{stream_id}``.

        JS then calls :meth:`proxy_stream_next` repeatedly with the
        returned id to drain events. See ADR-0033.
        """
        if self._bridge is None:
            raise RuntimeError(
                "Bridge not configured; cannot open proxy stream."
            )
        return self._bridge.proxy_stream_open(url, method, headers or {}, body)

    def proxy_stream_next(self, stream_id: str) -> dict[str, Any]:
        """Pop the next streaming event for ``stream_id``."""
        if self._bridge is None:
            raise RuntimeError(
                "Bridge not configured; cannot read proxy stream."
            )
        return self._bridge.proxy_stream_next(stream_id)

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
    # DEBUG when T2V_WRITER_DEBUG is set, else INFO — so the verbose
    # per-call wire dumps surface only when a developer is diagnosing
    # (the same gate as the web inspector). See task #13.
    logging.basicConfig(
        level=logging.DEBUG if debug_enabled() else logging.INFO,
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
    parser.add_argument(
        "--icon",
        default=None,
        help="Path to the window icon (PNG). Brands the chat window.",
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

    # Companion-window docking (ADR-0039): ask LO for its main-window
    # geometry + native handle, combine with the user's last-saved
    # geometry, and compute where/how to place the chat window. Best-effort
    # — any failure degrades to a centred default-size window.
    global _HOST_PARENT, _LATEST_GEOMETRY
    host_window: dict[str, Any] = {}
    if bridge is not None:
        try:
            host_window = bridge.get_host_window() or {}
        except Exception:
            logger.exception(
                "web_runner: get_host_window failed — using default geometry"
            )
    session = _session_type()
    geometry = _window_geometry(
        host_window, _load_geometry(), sys.platform, session
    )
    _HOST_PARENT = host_window
    _LATEST_GEOMETRY = {
        "width": geometry["width"],
        "height": geometry["height"],
    }
    logger.info(
        "web_runner: host_window=%s session=%s geometry=%s",
        host_window,
        session,
        geometry,
    )

    # On macOS pywebview's Cocoa backend imports AppKit / Foundation /
    # WebKit / objc — none of which ship with LibreOffice's bundled
    # Python framework. We bundle pyobjc as universal2 wheels under
    # ``_vendored_wheels/<runtime-tag>/`` (see ADR-0038) and the
    # loader prepends that path to sys.path so pywebview's
    # ``initialize(gui='cocoa')`` resolves cleanly. No-op on
    # Linux/Windows.
    from ._wheel_loader import ensure_vendored_pyobjc

    ensure_vendored_pyobjc()

    import webview

    _patch_webkitgtk_cors_settings()
    # Grant microphone (getUserMedia) for the SDK voice / speech-to-text
    # button. Each patch self-guards by importing its own backend module,
    # so exactly one applies per OS and the others are no-ops (ADR-0041).
    _patch_webkitgtk_media_permission()  # Linux / WebKitGTK
    _patch_cocoa_media_permission()  # macOS / WKWebView
    _patch_edgechromium_media_permission()  # Windows / WebView2
    # Companion-window integration (ADR-0039): brand the GTK process as
    # "Talk2View" and (X11 only) make the window transient-for LO. Both
    # no-op on non-GTK platforms / Wayland.
    if sys.platform.startswith("linux"):
        _apply_window_identity()
        _patch_gtk_window_transient()
    # NOTE: we deliberately do NOT set webview.settings['IGNORE_SSL_ERRORS'].
    # Disabling TLS certificate validation for the whole webview is an
    # unacceptable posture for a credential-handling, FDA-bound component:
    # any direct request the webview makes (an asset URL in engine output,
    # a redirect target, a future non-proxied endpoint) would accept a
    # forged certificate and be MITM-able. Engine traffic is proxied
    # through the bridge's httpx, which validates certificates (verify=True
    # by default). If a distro's WebKitGTK genuinely lacks CA roots, point
    # it at a bundled/system CA bundle rather than turning validation off.
    storage_path = _webview_storage_path()
    logger.info("webview imported, calling create_window")
    window = webview.create_window(
        "Talk2View",
        url=args.html_url,
        js_api=api,
        width=geometry["width"],
        height=geometry["height"],
        x=geometry["x"],
        y=geometry["y"],
        frameless=geometry["frameless"],
        easy_drag=False,
        on_top=geometry["on_top"],
        resizable=True,
        min_size=_MIN_SIZE,
    )
    _track_window_geometry(window)
    _install_focus_signal_handler(webview)
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
    # gui='gtk' on Linux skips pywebview's QT import-probe path, which
    # otherwise prints a misleading "QT cannot be loaded" / qtpy
    # ModuleNotFoundError error on every launch even though GTK is
    # what we want and it works fine. On macOS pywebview's only
    # backend is Cocoa, on Windows it's EdgeChromium — passing
    # gui=None there lets pywebview pick the right one.
    gui_backend: Literal["gtk"] | None = (
        "gtk" if sys.platform.startswith("linux") else None
    )
    webview.start(
        debug=debug_enabled(),
        private_mode=False,
        storage_path=str(storage_path),
        gui=gui_backend,
        icon=args.icon,
    )
    logger.info("webview.start() returned — window closed, exiting")
    # Persist the final geometry so the panel reopens where the user left
    # it (size everywhere; position where the platform honours it).
    _save_geometry(_LATEST_GEOMETRY)


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


# ---------------------------------------------------------------------------
# Companion-window docking (ADR-0039)
# ---------------------------------------------------------------------------

# Default side-panel proportions: tall and narrow, like a docked deck.
_DEFAULT_WIDTH = 400
_DEFAULT_HEIGHT = 800
_MIN_SIZE = (320, 400)

# Last-known geometry, updated by the GTK move/resize events and persisted
# when the window closes so the panel reopens where the user left it.
_LATEST_GEOMETRY: dict[str, Any] = {}

# LO's native window handle for the transient-for patch (X11 only). Set in
# main() from the bridge's get_host_window reply before webview.start().
_HOST_PARENT: dict[str, Any] | None = None


def _session_type() -> str | None:
    """Return ``'wayland'`` / ``'x11'`` on Linux, else ``None``.

    Selects which docking capabilities the compositor allows (ADR-0039):
    on Wayland a client cannot position its own toplevel or parent into
    another process, so positioning + transient-for are skipped there.
    """
    if sys.platform != "linux":
        return None
    session = os.environ.get("XDG_SESSION_TYPE", "").lower()
    if session in ("wayland", "x11"):
        return session
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return None


def _coerce_int(value: Any) -> int | None:
    """Return ``int(value)`` or ``None`` if it isn't a finite number."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _window_geometry(
    host: dict[str, Any],
    persisted: dict[str, Any],
    platform: str,
    session: str | None,
) -> dict[str, Any]:
    """Compute the chat window's size / position / chrome (ADR-0039).

    Pure function — no GTK, no I/O — so the per-platform docking policy is
    unit-tested in isolation.

    Policy:
      * Size: last persisted size wins; otherwise a tall narrow side-panel,
        matching LO's height when first docking.
      * Position: only platforms that let a client place its own toplevel
        (everything except Linux/Wayland) auto-dock onto LO's right edge.
        A persisted user position always wins. Wayland gets no coords — the
        compositor places it and the user drags-to-snap once.
      * frameless: kept ``False`` in v1 so the compositor's title-bar drag +
        edge-snap work everywhere; a frameless panel + client-side drag
        strip is the v2 follow-up.
      * on_top: ``True`` so it floats over the document like a docked deck.
    """
    geom = host.get("geometry") or None
    width = _coerce_int(persisted.get("width")) or _DEFAULT_WIDTH
    height = _coerce_int(persisted.get("height"))

    positionable = not (platform == "linux" and session == "wayland")
    x = _coerce_int(persisted.get("x")) if positionable else None
    y = _coerce_int(persisted.get("y")) if positionable else None

    if positionable and geom and x is None and y is None:
        # Dock onto LO's right edge, matching its height on first open.
        x = int(geom["x"]) + int(geom["w"]) - width
        y = int(geom["y"])
        if height is None:
            height = int(geom["h"])

    if height is None:
        height = _DEFAULT_HEIGHT

    return {
        "width": width,
        "height": height,
        "x": x,
        "y": y,
        "frameless": False,
        "on_top": True,
    }


def _geometry_path() -> Path:
    """Path to the persisted-geometry file (alongside the webview state)."""
    return _webview_storage_path() / "geometry.json"


def _load_geometry() -> dict[str, Any]:
    """Read the persisted geometry dict, or ``{}`` if absent / unreadable."""
    path = _geometry_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        logger.exception("geometry: failed to read %s — ignoring", path)
        return {}
    return data if isinstance(data, dict) else {}


def _save_geometry(geo: dict[str, Any]) -> None:
    """Persist the geometry dict (no-op for an empty dict)."""
    if not geo:
        return
    path = _geometry_path()
    try:
        path.write_text(json.dumps(geo))
        logger.info("geometry: persisted %s to %s", geo, path)
    except OSError:
        logger.exception("geometry: failed to write %s", path)


def _track_window_geometry(window: Any) -> None:
    """Subscribe to GTK move/resize events so geometry persists.

    The handlers mutate the module-level :data:`_LATEST_GEOMETRY`, which is
    written to disk when the window closes. On Wayland ``moved`` reports
    inert coordinates, but ``resized`` (size) is always meaningful — and
    the restore path ignores Wayland positions anyway (see
    :func:`_window_geometry`).
    """

    def _on_resized(width: int, height: int) -> None:
        _LATEST_GEOMETRY["width"] = int(width)
        _LATEST_GEOMETRY["height"] = int(height)

    def _on_moved(x: int, y: int) -> None:
        _LATEST_GEOMETRY["x"] = int(x)
        _LATEST_GEOMETRY["y"] = int(y)

    try:
        window.events.resized += _on_resized
        window.events.moved += _on_moved
    except Exception:
        logger.exception("geometry: failed to subscribe to move/resize events")


def _apply_window_identity(app_name: str = "Talk2View") -> None:
    """Brand the GTK process so the chat window reads as ``app_name``.

    Without this the window inherits a generic ``python3`` identity and
    won't carry the Talk2View name/icon or group consistently in the
    taskbar/overview. Sets the program name GTK derives the Wayland
    ``app_id`` / X11 ``WM_CLASS`` from (ADR-0039). Linux/GTK only; no-op
    elsewhere (the icon is set separately via ``webview.start(icon=...)``).
    """
    try:
        from gi.repository import GLib
    except Exception:
        logger.info("window identity: gi.repository.GLib unavailable — skipping")
        return
    for fn_name, value in (
        ("set_prgname", app_name),
        ("set_application_name", app_name),
    ):
        try:
            getattr(GLib, fn_name)(value)
        except Exception:
            logger.exception("window identity: GLib.%s(%r) failed", fn_name, value)


def _patch_gtk_window_transient() -> None:
    """Wrap the GTK BrowserView to make the chat window transient-for LO.

    A transient-for relationship makes the window manager stack the chat
    window with the LibreOffice document window (child-of-LO behaviour).
    Only engages on X11, where ``_HOST_PARENT`` carries LO's XID; on
    Wayland there is no usable cross-process parent handle (LO does not
    expose an xdg-foreign token via UNO — investigation #49), so it
    no-ops. Sibling to ``_patch_webkitgtk_cors_settings``; guarded by a
    sentinel so it applies once.
    """
    try:
        from webview.platforms import gtk as gtk_backend
    except ImportError:
        logger.info(
            "transient patch: pywebview.platforms.gtk not importable — skipping"
        )
        return

    if getattr(gtk_backend.BrowserView, "_t2v_transient_patched", False):
        return

    original_init = gtk_backend.BrowserView.__init__

    def patched_init(self: Any, window: Any) -> None:
        original_init(self, window)
        _try_set_transient(self.window)

    gtk_backend.BrowserView.__init__ = patched_init  # type: ignore[method-assign]
    gtk_backend.BrowserView._t2v_transient_patched = True  # type: ignore[attr-defined]
    logger.info("transient patch: BrowserView.__init__ wrapped")


def _try_set_transient(gtk_window: Any) -> None:
    """Set ``gtk_window`` transient-for LO's window via its XID (X11 only)."""
    parent = _HOST_PARENT
    xid = parent.get("xid") if parent else None
    if not xid:
        return
    try:
        from gi.repository import Gdk, GdkX11

        display = Gdk.Display.get_default()
        foreign = GdkX11.X11Window.foreign_new_for_display(display, int(xid))
        if foreign is None:
            logger.info("transient: foreign_new_for_display(%s) -> None", xid)
            return
        gtk_window.realize()
        gtk_window.get_window().set_transient_for(foreign)
        logger.info("transient: chat window set transient-for LO xid=%s", xid)
    except Exception:
        logger.exception("transient: set_transient_for(xid=%s) failed", xid)


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

    # Intentional monkey-patch — pywebview's gtk backend doesn't expose
    # the CORS-relaxing settings any other way. The sentinel attribute
    # guards re-application.
    gtk_backend.BrowserView.__init__ = patched_init  # type: ignore[method-assign]
    gtk_backend.BrowserView._t2v_cors_patched = True  # type: ignore[attr-defined]
    logger.info("WebKitGTK patch: BrowserView.__init__ wrapped")


# ---------------------------------------------------------------------------
# Microphone / getUserMedia permission (ADR-0041)
# ---------------------------------------------------------------------------
#
# The Talk2View SDK's voice button calls
# ``navigator.mediaDevices.getUserMedia({audio: true})``. Every embedded
# webview engine refuses that by default unless the host app explicitly
# grants the capture permission; pywebview's backends don't, so the SDK
# fails with "NotAllowedError" until we splice a grant in. The three
# patches below cover WebKitGTK / WKWebView / WebView2; each is a no-op on
# the wrong OS because its backend module isn't importable there.

# Class names (no ``Webkit`` prefix under PyGObject) of the WebKitGTK
# permission-request subclasses we grant: getUserMedia raises a
# UserMedia request; enumerateDevices a DeviceInfo request. Matched by
# name so this stays importable without ``gi`` (the venv has no gi — the
# webview runs under system Python).
_GRANTED_PERMISSION_REQUESTS = frozenset(
    {
        "WebKitUserMediaPermissionRequest",
        "UserMediaPermissionRequest",
        "WebKitDeviceInfoPermissionRequest",
        "DeviceInfoPermissionRequest",
    }
)


def _grant_media_permission(_webview: Any, request: Any) -> bool:
    """WebKitGTK ``permission-request`` handler — allow mic / device-info.

    Returns ``True`` (request handled, stop WebKit's default-deny
    fall-through) for the media-capture / device-enumeration requests,
    ``False`` for anything else so WebKit applies its own per-class
    default. Duck-typed by class name so this module imports cleanly
    without PyGObject. Shared with the CI check in
    ``tests/integration/webkit_media_permission_check.py``.
    """
    name = type(request).__name__
    if name in _GRANTED_PERMISSION_REQUESTS:
        request.allow()
        logger.info("media patch: allowed %s", name)
        return True
    return False


def _patch_webkitgtk_media_permission() -> None:
    """Grant getUserMedia on WebKitGTK by handling ``permission-request``.

    WebKitGTK fires ``WebKitWebView::permission-request`` with a
    ``WebKitUserMediaPermissionRequest``; per its docs an *unhandled*
    request is denied by default, so getUserMedia rejects with
    ``NotAllowedError``. pywebview's GTK backend connects no such handler,
    so we splice one in (same monkey-patch style as the CORS patch).
    Pure WebKit — no UNO, so no UIThreadDispatcher needed.

    No-op on macOS / Windows (``webview.platforms.gtk`` not importable).
    """
    try:
        from webview.platforms import gtk as gtk_backend
    except ImportError:
        logger.info(
            "WebKitGTK media patch: pywebview.platforms.gtk not importable "
            "on this platform — assuming non-Linux backend; skipping"
        )
        return

    if getattr(gtk_backend.BrowserView, "_t2v_media_patched", False):
        return

    original_init = gtk_backend.BrowserView.__init__

    def patched_init(self: Any, window: Any) -> None:
        original_init(self, window)
        try:
            props = self.webview.get_settings().props
            # Master gate (pywebview already sets this, but its default is
            # version-dependent across WebKitGTK builds — set it ourselves).
            props.enable_media_stream = True
            with contextlib.suppress(AttributeError, TypeError):
                # WebKitGTK 2.38+; documented to imply enable_media_stream.
                # Absent on older builds — harmless to skip.
                props.enable_webrtc = True
            self.webview.connect("permission-request", _grant_media_permission)
            logger.info(
                "WebKitGTK media patch applied: enable_media_stream=True, "
                "permission-request handler connected"
            )
        except Exception:
            logger.exception(
                "WebKitGTK media patch: enabling media-stream / connecting "
                "permission-request raised — the webview still opens but the "
                "SDK voice button will fail with NotAllowedError"
            )

    gtk_backend.BrowserView.__init__ = patched_init  # type: ignore[method-assign]
    gtk_backend.BrowserView._t2v_media_patched = True  # type: ignore[attr-defined]
    logger.info("WebKitGTK media patch: BrowserView.__init__ wrapped")


def _patch_cocoa_media_permission() -> None:
    """Grant getUserMedia on macOS WKWebView via the WKUIDelegate callback.

    On macOS 12+, WKWebView asks its ``WKUIDelegate`` to decide capture
    permission through
    ``webView:requestMediaCapturePermissionForOrigin:initiatedByFrame:type:decisionHandler:``;
    if the delegate doesn't implement it, capture is denied. pywebview's
    Cocoa backend uses ``BrowserView.BrowserDelegate`` (an ``NSObject``)
    as the UI delegate but implements no media method. We subclass that
    delegate to add the method (granting), then point the backend's
    nested-class attribute at our subclass so pywebview instantiates it —
    inheriting every existing delegate method unchanged.

    NOTE: this is necessary but not always sufficient on macOS — WKWebView
    also requires the *host* process (LibreOffice) to carry an
    ``NSMicrophoneUsageDescription`` and the user's one-time TCC consent,
    neither of which an ``.oxt`` can inject. Untested pre-release; see
    docs/investigations.md #58. No-op off macOS (cocoa backend not
    importable).
    """
    try:
        from webview.platforms import cocoa as cocoa_backend
    except ImportError:
        logger.info(
            "Cocoa media patch: pywebview.platforms.cocoa not importable — "
            "assuming non-macOS backend; skipping"
        )
        return

    if getattr(cocoa_backend.BrowserView, "_t2v_media_patched", False):
        return

    try:
        import objc  # type: ignore[import-not-found]

        base_delegate = cocoa_backend.BrowserView.BrowserDelegate

        def _decide_media_capture(
            self: Any,
            _webview: Any,
            _origin: Any,
            _frame: Any,
            _type: Any,
            decision_handler: Any,
        ) -> None:
            # WKPermissionDecisionGrant == 1 (WKPermissionDecision is an
            # NSInteger enum: prompt=0, grant=1, deny=2). Passing the raw
            # int avoids importing the WebKit framework for one constant.
            decision_handler(1)
            logger.info("Cocoa media patch: granted WKWebView media capture")

        # Explicit Obj-C type signature (untestable here, so be defensive):
        # void return; self/_cmd; webView, origin, frame, type(q=NSInteger),
        # decisionHandler(@?=block).
        media_selector = objc.selector(_decide_media_capture, signature=b"v@:@@@q@?")

        # The macOS 12+ WKUIDelegate selector, split to stay under the line
        # limit: webView:requestMediaCapturePermissionForOrigin:
        # initiatedByFrame:type:decisionHandler:
        sel_name = (
            "webView_requestMediaCapturePermissionForOrigin"
            "_initiatedByFrame_type_decisionHandler_"
        )
        delegate_cls = type(
            "_T2VMediaBrowserDelegate", (base_delegate,), {sel_name: media_selector}
        )
        cocoa_backend.BrowserView.BrowserDelegate = delegate_cls  # type: ignore[assignment,misc]
        cocoa_backend.BrowserView._t2v_media_patched = True  # type: ignore[attr-defined]
        logger.info(
            "Cocoa media patch: BrowserDelegate subclassed with "
            "requestMediaCapturePermission grant"
        )
    except Exception:
        logger.exception(
            "Cocoa media patch: installing the media-capture delegate raised "
            "— the webview still opens but the SDK voice button may fail "
            "(also requires LibreOffice's own NSMicrophoneUsageDescription "
            "+ TCC consent)"
        )


def _patch_edgechromium_media_permission() -> None:
    """Grant getUserMedia on Windows WebView2 via ``PermissionRequested``.

    WebView2 raises ``CoreWebView2.PermissionRequested`` for mic / camera
    and, if unhandled, defaults to deny. pywebview's EdgeChromium backend
    subscribes no handler, so we wrap ``EdgeChrome.on_webview_ready``
    (which runs once ``CoreWebView2`` exists) to attach one that grants
    Microphone / Camera. Untested pre-release; see
    docs/investigations.md #58. No-op off Windows (edgechromium backend
    not importable).
    """
    try:
        from webview.platforms import edgechromium as edge_backend
    except ImportError:
        logger.info(
            "WebView2 media patch: pywebview.platforms.edgechromium not "
            "importable — assuming non-Windows backend; skipping"
        )
        return

    edge_chrome = edge_backend.EdgeChrome
    if getattr(edge_chrome, "_t2v_media_patched", False):
        return

    original_ready = edge_chrome.on_webview_ready

    def patched_ready(self: Any, sender: Any, args: Any) -> None:
        original_ready(self, sender, args)
        try:
            from Microsoft.Web.WebView2.Core import (  # type: ignore[import-not-found]
                CoreWebView2PermissionKind,
                CoreWebView2PermissionState,
            )

            def _on_permission(_s: Any, event: Any) -> None:
                if event.PermissionKind in (
                    CoreWebView2PermissionKind.Microphone,
                    CoreWebView2PermissionKind.Camera,
                ):
                    event.State = CoreWebView2PermissionState.Allow

            self.webview.CoreWebView2.PermissionRequested += _on_permission
            logger.info(
                "WebView2 media patch: PermissionRequested handler attached "
                "(grants Microphone / Camera)"
            )
        except Exception:
            logger.exception(
                "WebView2 media patch: attaching PermissionRequested handler "
                "raised — the webview still opens but the SDK voice button "
                "may fail with NotAllowedError"
            )

    edge_chrome.on_webview_ready = patched_ready  # type: ignore[method-assign]
    edge_chrome._t2v_media_patched = True  # type: ignore[attr-defined]
    logger.info("WebView2 media patch: EdgeChrome.on_webview_ready wrapped")


def _install_focus_signal_handler(webview_module: Any) -> None:
    """Register a SIGUSR2 handler that raises the chat window.

    When the user re-clicks the Talk2View menu while a chat window
    is already open, LO sends SIGUSR2 to us; we call ``window.show()``
    which on GTK invokes ``gtk_window_present()`` — raising +
    focusing the window. Idempotent: if no window exists yet (signal
    arrived between subprocess start and create_window), we no-op
    and the next show() call will succeed.

    SIGUSR2, not SIGUSR1: WebKitGTK's JavaScriptCore claims SIGUSR1
    for garbage collection during ``webview.start()`` and overrides
    any handler we installed earlier (it emits "Overriding existing
    handler for signal 10" to stderr). SIGUSR2 is not claimed by
    JSC or WebKit, so our handler survives the webview start.

    Windows ignores this path — POSIX-only signal — and falls back
    to the no-op-on-re-click behaviour documented in WebWindow.show.
    """
    import signal

    if not hasattr(signal, "SIGUSR2"):
        logger.info("Focus signal handler: SIGUSR2 not available on this OS")
        return

    def _handle_focus_signal(signum: int, frame: Any) -> None:
        try:
            windows = getattr(webview_module, "windows", []) or []
            if not windows:
                logger.warning(
                    "Focus signal: no webview windows registered yet — ignoring"
                )
                return
            win = windows[0]
            # ``show()`` on GTK calls gtk_window_present() which
            # both reveals + raises + focuses. On WKWebView/macOS
            # it raises the NSWindow.
            try:
                win.show()
            except Exception:
                logger.exception("Focus signal: window.show() raised")
            logger.info(
                "Focus signal: window raised (sig=%d windows=%d)",
                signum,
                len(windows),
            )
        except Exception:
            logger.exception("Focus signal handler raised")

    try:
        signal.signal(signal.SIGUSR2, _handle_focus_signal)
        logger.info("Focus signal handler: installed (SIGUSR2 → window.show)")
    except (OSError, ValueError):
        # ValueError if called from a non-main thread, OSError on
        # platforms that surface signal errors. Both unexpected here
        # (main thread, POSIX) but logging keeps the failure visible.
        logger.exception("Focus signal handler: signal.signal failed")


if __name__ == "__main__":
    main()
