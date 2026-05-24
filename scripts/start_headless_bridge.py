"""Trigger Talk2View-Writer's bridge in an already-running soffice; print the socket path.

The live E2E Playwright suite (Architecture C — see ADR-0036) needs
the real Python BridgeServer running so the bundle-driven tool calls
mutate a real Writer document. This script is the orchestrator's
hook: assumes soffice is already up on a UNO socket and the extension
is installed, then dispatches the chat-open menu command via UNO so
the extension instantiates the bridge.

Pre-conditions (caller responsibility):
  - soffice running with ``--accept=socket,host=...,port=...;urp;``.
  - Extension installed (``unopkg add`` or equivalent).
  - ``T2V_WRITER_HEADLESS_BRIDGE=1`` set in soffice's environment so
    ``WebWindow.show()`` starts the bridge but skips the pywebview
    spawn (see ``src/talk2view_writer/ui/web_window.py``). Without
    this, pywebview takes the single bridge connection itself and
    the live test can't talk to the bridge.

Output:
  Prints the bridge's Unix-socket path to stdout (one line, no
  trailing newline).

Exit codes:
  0 — bridge started, socket path printed.
  1 — couldn't reach soffice, dispatch failed, or socket path didn't
      appear in the log within ``--timeout`` seconds.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path


def _resolve_uno_context(host: str, port: int):  # type: ignore[no-untyped-def]
    """Return a remote UNO XComponentContext over a UNO bridge.

    Uses python3-uno's standard ``Bootstrap.bootstrap()`` pattern via
    a UNO URL resolver. ``soffice --accept`` exposes the
    ``StarOffice.ServiceManager``; we resolve that and walk to the
    remote ``XComponentContext``.
    """
    import uno  # python3-uno; provided by the LibreOffice package
    from com.sun.star.connection import NoConnectException

    local_ctx = uno.getComponentContext()
    resolver = local_ctx.ServiceManager.createInstanceWithContext(
        "com.sun.star.bridge.UnoUrlResolver", local_ctx
    )
    url = (
        f"uno:socket,host={host},port={port};urp;"
        f"StarOffice.ComponentContext"
    )
    try:
        return resolver.resolve(url)
    except NoConnectException as exc:
        raise SystemExit(
            f"could not connect to soffice at {host}:{port}: {exc}. "
            "Is soffice running with the right --accept arg?"
        ) from exc


def _dispatch_show_panel(ctx) -> None:  # type: ignore[no-untyped-def]
    """Dispatch the ``vnd.com.talk2view.writer:showPanel`` menu URL.

    Same path the user takes when clicking the menu item — drives
    ``Talk2ViewProtocolHandler.dispatch`` → ``extension.show_chat_window``
    → ``WebWindow.show()`` which (with ``T2V_WRITER_HEADLESS_BRIDGE=1``)
    starts the bridge without spawning pywebview.
    """
    from com.sun.star.util import URL

    desktop = ctx.ServiceManager.createInstanceWithContext(
        "com.sun.star.frame.Desktop", ctx
    )
    # ``loadComponentFromURL`` against private:factory/swriter returns
    # a Writer doc + frame we dispatch the menu URL against. We do NOT
    # pass Hidden=True because tools later call ``get_writer_document``
    # → ``desktop.getCurrentComponent()`` which returns None for hidden
    # docs (the active component is the focused one). Under Xvfb in
    # CI this is invisible; locally a Writer window briefly appears.
    doc = desktop.loadComponentFromURL(
        "private:factory/swriter", "_blank", 0, ()
    )
    if doc is None:
        raise SystemExit("loadComponentFromURL returned None")
    frame = doc.getCurrentController().getFrame()
    dispatch_provider = frame.queryDispatch
    transformer = ctx.ServiceManager.createInstanceWithContext(
        "com.sun.star.util.URLTransformer", ctx
    )
    url = URL()
    url.Complete = "vnd.com.talk2view.writer:showPanel"
    _, parsed_url = transformer.parseStrict(url)
    dispatcher = dispatch_provider(parsed_url, "_self", 0)
    if dispatcher is None:
        raise SystemExit(
            "extension did not register a dispatch for "
            "vnd.com.talk2view.writer:showPanel — is the .oxt installed?"
        )
    dispatcher.dispatch(parsed_url, ())


_SOCKET_LINE_RE = re.compile(
    r"BridgeServer\.start: listening on (?P<path>\S+)"
)


def _scrape_socket_path(log_path: Path, timeout: float) -> str:
    """Poll ``log_path`` for the BridgeServer-listening line; return the socket path.

    The bridge_server logs ``BridgeServer.start: listening on /tmp/...``
    when ``start()`` returns. We tail the file and return the path from
    the most recent matching line.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if log_path.exists():
            text = log_path.read_text(encoding="utf-8", errors="replace")
            # Multiple chat-open clicks would emit multiple "listening"
            # lines (one per BridgeServer instance). Take the last —
            # that's the one currently bound.
            matches = list(_SOCKET_LINE_RE.finditer(text))
            if matches:
                return matches[-1].group("path")
        time.sleep(0.25)
    raise SystemExit(
        f"timed out after {timeout}s scraping {log_path} for "
        f"'BridgeServer.start: listening on …'. The dispatch may have "
        "failed silently; check the log file."
    )


def _default_log_path() -> Path:
    """Where ``_logging.log_file_path()`` writes by default on Linux.

    Mirrors ``src/talk2view_writer/_logging.py``'s XDG_CACHE_HOME
    resolution — Linux is the only platform live-E2E targets (the
    Python bridge is AF_UNIX-only).
    """
    import os

    xdg = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(xdg) / "talk2view-writer" / "talk2view.log"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2002)
    parser.add_argument(
        "--log-path",
        type=Path,
        default=None,
        help="talk2view.log location (defaults to platform XDG path)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="max seconds to wait for the bridge socket line",
    )
    args = parser.parse_args()
    log_path = args.log_path or _default_log_path()

    ctx = _resolve_uno_context(args.host, args.port)
    _dispatch_show_panel(ctx)
    socket_path = _scrape_socket_path(log_path, args.timeout)
    sys.stdout.write(socket_path)
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
