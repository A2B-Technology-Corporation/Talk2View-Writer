"""Subprocess-backed pywebview chat window (ADR-0030 work-in-progress).

The 2026-05-22 repro confirmed pywebview's hard requirement that
``webview.start()`` runs on the calling process's main thread:

    WebViewException: pywebview must be run on a main thread.

LO's main thread is owned by LO's UI event loop — we can't take it
over without freezing LibreOffice. So this module spawns
``python3 -m talk2view_writer.web_runner <html_url>`` as a separate
process. The subprocess owns its own main thread, drives the
pywebview event loop, and dies when the user closes the window.

This commit only spawns the subprocess + opens the window. The
JS↔Python bridge (Unix socket back to LO for tool calls) is the
next slice.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlparse

if TYPE_CHECKING:
    from com.sun.star.uno import XComponentContext

logger = logging.getLogger(__name__)


def _ru(obj: Any) -> str:
    """UNO-safe repr for log args (see chat_window history for why)."""
    try:
        return repr(obj)
    except Exception as exc:
        return f"<repr failed: {type(exc).__name__}>"


_EXTENSION_ID = "com.talk2view.writer"
_HTML_PATH = "web/index.html"


class WebWindow:
    """Singleton subprocess-backed chat window.

    ``show()`` spawns the pywebview subprocess if it's not already
    running. Reinvocation while the subprocess is alive is a no-op
    (TODO: refocus the existing window via the IPC bridge once that
    lands).
    """

    def __init__(self, ctx: XComponentContext) -> None:
        self.ctx = ctx
        self._proc: subprocess.Popen | None = None
        self._stderr_pump: threading.Thread | None = None
        logger.info("WebWindow instantiated (ctx=%s)", _ru(ctx))

    def show(self) -> None:
        """Spawn the pywebview subprocess pointed at the bundled HTML."""
        logger.info(
            "WebWindow.show: subprocess_alive=%s", self._is_alive()
        )
        if self._is_alive():
            logger.info(
                "WebWindow.show: subprocess already running (pid=%s); "
                "no-op (TODO: refocus via IPC)",
                self._proc.pid if self._proc else None,
            )
            return

        html_path = self._resolve_html_path()
        pythonpath_dir = self._resolve_pythonpath()
        python_bin = self._resolve_python()
        logger.info(
            "WebWindow.show: html_path=%s pythonpath_dir=%s python=%s",
            html_path,
            pythonpath_dir,
            python_bin,
        )

        env = os.environ.copy()
        # Prepend our pythonpath so the subprocess can import the
        # bundled ``webview`` + ``talk2view_writer.web_runner``.
        existing_pp = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            f"{pythonpath_dir}{os.pathsep}{existing_pp}"
            if existing_pp
            else pythonpath_dir
        )
        # Help the user diagnose failures in the subprocess.
        env["PYTHONUNBUFFERED"] = "1"

        args = [
            python_bin,
            "-m",
            "talk2view_writer.web_runner",
            html_path.as_uri(),
        ]
        logger.info("WebWindow.show: spawning subprocess %s", args)
        try:
            self._proc = subprocess.Popen(
                args,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                # Detach so closing LO doesn't take the window with
                # it on signal — explicit cleanup is preferred but
                # the subprocess is meant to outlive a single LO
                # event-loop tick at minimum.
                start_new_session=True,
            )
        except Exception:
            logger.exception(
                "WebWindow.show: subprocess.Popen raised — re-raising"
            )
            raise
        logger.info(
            "WebWindow.show: subprocess started pid=%s", self._proc.pid
        )

        # Pump subprocess stderr into our log so the user sees
        # webview/GTK/whatever diagnostic output without having to
        # tail a separate file.
        self._stderr_pump = threading.Thread(
            target=self._pump_stderr,
            name="web_runner-stderr",
            daemon=True,
        )
        self._stderr_pump.start()

    # ----- helpers --------------------------------------------------------

    def _is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _pump_stderr(self) -> None:
        """Forward subprocess stderr lines into our logger."""
        if self._proc is None or self._proc.stderr is None:
            return
        try:
            for raw in self._proc.stderr:
                line = raw.decode("utf-8", errors="replace").rstrip()
                if not line:
                    continue
                logger.info("web_runner[stderr]: %s", line)
        except Exception:
            logger.exception(
                "web_runner stderr pump exited unexpectedly"
            )
        finally:
            rc = self._proc.poll() if self._proc else None
            logger.info("web_runner subprocess exited rc=%s", rc)

    def _resolve_html_path(self) -> Path:
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

    def _resolve_pythonpath(self) -> str:
        """Resolve the extension's bundled pythonpath/ directory."""
        pip = self.ctx.getValueByName(
            "/singletons/com.sun.star.deployment.PackageInformationProvider"
        )
        extension_root_url = pip.getPackageLocation(_EXTENSION_ID)
        parsed = urlparse(extension_root_url)
        if parsed.scheme != "file":
            raise RuntimeError(
                f"Extension root URL must be file://, got "
                f"{extension_root_url!r}"
            )
        path = Path(unquote(parsed.path)) / "pythonpath"
        if not path.is_dir():
            raise FileNotFoundError(
                f"pythonpath/ missing under extension root: {path}"
            )
        return str(path)

    def _resolve_python(self) -> str:
        """Find a Python interpreter to spawn the subprocess with.

        LO embeds Python rather than launching it, so ``sys.executable``
        points at the LO binary (soffice). We need an actual Python
        interpreter to ``python -m`` against.

        On Linux LO 26.x typically uses the system Python (the .so
        loaded into soffice is the system libpython3), so
        ``/usr/bin/python3`` matches the bundled Python's ABI. On
        macOS/Windows LO ships its own Python and we'll need a
        platform-specific lookup — TODO for the macOS/Windows port.
        """
        # Honour an override for testing / macOS / Windows.
        override = os.environ.get("T2V_PYTHON")
        if override:
            logger.info("WebWindow._resolve_python: T2V_PYTHON=%s", override)
            return override

        if sys.platform == "linux":
            candidate = shutil.which("python3") or "/usr/bin/python3"
            logger.info(
                "WebWindow._resolve_python: linux candidate=%s", candidate
            )
            return candidate
        if sys.platform == "darwin":
            # LO on macOS bundles its own Python; the path is fairly
            # standard but a future commit should resolve it from the
            # LO install location reported by the PIP. Fall back to a
            # system python3 if that exists.
            candidate = shutil.which("python3") or "/usr/bin/python3"
            logger.info(
                "WebWindow._resolve_python: darwin candidate=%s "
                "(TODO: resolve LO-bundled python)",
                candidate,
            )
            return candidate
        if sys.platform == "win32":
            candidate = shutil.which("python") or shutil.which("python3")
            if not candidate:
                raise FileNotFoundError(
                    "Talk2View needs python.exe on PATH (TODO: resolve "
                    "LO-bundled python)"
                )
            return candidate
        raise RuntimeError(f"unsupported platform: {sys.platform}")
