"""Subprocess entry point for the pywebview chat window.

Per ADR-0030: pywebview enforces ``webview.start()`` must run on the
calling process's main thread (it raises ``WebViewException`` if not).
LibreOffice's main thread is owned by LO's UI event loop, so we
spawn this module as a separate Python process — it has its own main
thread, owns the pywebview event loop, and never blocks LO.

Invocation:

    python3 -m talk2view_writer.web_runner <html_url>

The parent (LO extension) sets PYTHONPATH so this module + the
bundled ``webview`` package are importable.

This MVP commit only opens the window. The JS↔Python bridge (Unix
socket to the parent LO process for tool calls) comes in the next
commit.
"""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger("talk2view_writer.web_runner")


def main() -> None:
    """Open the chat window and block on the pywebview event loop."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [web_runner] %(levelname)s %(name)s: %(message)s",
    )

    if len(sys.argv) < 2:
        logger.error(
            "Usage: python -m talk2view_writer.web_runner <html_url>"
        )
        sys.exit(2)

    url = sys.argv[1]
    logger.info(
        "web_runner starting: pid=%s python=%s url=%s",
        sys.executable,
        sys.version.split()[0],
        url,
    )

    import webview

    logger.info("webview imported, calling create_window")
    webview.create_window(
        "Talk2View",
        url=url,
        width=400,
        height=600,
    )
    logger.info("webview.create_window returned; entering webview.start()")
    webview.start(debug=True)
    logger.info("webview.start() returned — window closed, exiting")


if __name__ == "__main__":
    main()
