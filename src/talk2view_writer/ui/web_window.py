"""pywebview-backed chat window (ADR-0030 work-in-progress).

This is the **MVP sentinel** for the web-UI pivot. Its job in this
commit is narrow: prove ``import webview`` + ``webview.start()`` work
from LibreOffice's bundled Python so we can build the React +
Talk2View-SDK stack on top in subsequent commits.

What this commit does NOT do:

- spawn pywebview in a subprocess (TODO once we have a basic window
  opening — subprocess + Unix-socket IPC is the next step so LO's
  main thread doesn't block).
- bundle the React app.
- bridge any tool calls.

What this commit DOES do:

- bundle the ``pywebview`` + ``bottle`` + ``proxy_tools`` wheels via
  the Makefile.
- open a minimal "Talk2View MVP" HTML page via ``webview.start()``
  to verify the pipeline works end-to-end. Yes — blocking LO's main
  thread for the duration of the window. That's a stepping stone,
  not the final architecture; the next commit moves it to a
  subprocess.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlparse

if TYPE_CHECKING:
    from com.sun.star.uno import XComponentContext

logger = logging.getLogger(__name__)

_EXTENSION_ID = "com.talk2view.writer"
_HTML_PATH = "web/index.html"


class WebWindow:
    """Singleton pywebview-backed chat window.

    Per ADR-0030: replaces the UNO-based ``ChatWindow``. Constructed
    once per process. ``show()`` lazily resolves the bundled HTML,
    instantiates the pywebview window, and calls ``webview.start()``.
    """

    def __init__(self, ctx: XComponentContext) -> None:
        self.ctx = ctx
        self._started = False
        self._window: Any | None = None
        logger.info("WebWindow instantiated (ctx=%r)", ctx)

    def show(self) -> None:
        """Open the chat window. First call constructs + starts the webview."""
        logger.info("WebWindow.show: already_started=%s", self._started)
        if self._started:
            logger.info(
                "WebWindow.show: pywebview already running; nothing to do "
                "(re-focus support TBD)"
            )
            return

        html_path = self._resolve_html_path()
        logger.info("WebWindow.show: html_path=%s", html_path)

        import webview

        logger.info("WebWindow.show: webview imported (version=%s)", _webview_version(webview))

        self._window = webview.create_window(
            "Talk2View",
            url=html_path.as_uri(),
            width=400,
            height=600,
        )
        logger.info("WebWindow.show: create_window returned %r", self._window)
        self._started = True

        # webview.start() blocks until the window closes. Run it in a
        # daemon thread so LO's main thread isn't frozen for the
        # duration of the chat session. THIS IS A STEPPING STONE — on
        # GTK in particular, pywebview's mainloop may not behave fully
        # correctly off the main thread; the next commit moves the
        # webview into a subprocess.
        def _runloop() -> None:
            try:
                logger.info("WebWindow runloop: webview.start() entering")
                webview.start(debug=True)
                logger.info("WebWindow runloop: webview.start() returned")
            except Exception:
                logger.exception("WebWindow runloop: webview.start() raised")
                raise

        thread = threading.Thread(target=_runloop, daemon=True, name="pywebview")
        thread.start()
        logger.info("WebWindow.show: pywebview thread started (tid=%s)", thread.ident)

    def _resolve_html_path(self) -> Path:
        """Find the bundled web/index.html via the PIP singleton."""
        logger.info("WebWindow._resolve_html_path: resolving PIP")
        pip = self.ctx.getValueByName(
            "/singletons/com.sun.star.deployment.PackageInformationProvider"
        )
        extension_root_url = pip.getPackageLocation(_EXTENSION_ID)
        html_url = f"{extension_root_url}/{_HTML_PATH}"
        logger.info("WebWindow._resolve_html_path: html_url=%s", html_url)

        parsed = urlparse(html_url)
        if parsed.scheme != "file":
            raise RuntimeError(
                f"Talk2View web bundle URL must be file://, got {html_url!r}"
            )
        path = Path(unquote(parsed.path))
        if not path.is_file():
            raise FileNotFoundError(
                f"Talk2View web bundle missing: {path} "
                f"(resolved from {html_url})"
            )
        logger.info(
            "WebWindow._resolve_html_path: file exists size=%d bytes",
            path.stat().st_size,
        )
        return path


def _webview_version(webview_mod: Any) -> str:
    """Best-effort pywebview version string for diagnostic logs."""
    return getattr(webview_mod, "__version__", "(no __version__)")
