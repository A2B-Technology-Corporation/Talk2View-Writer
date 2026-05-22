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

import atexit
import logging
import os
import shutil
import signal
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
        self._bridge: Any = None  # BridgeServer, lazy-imported
        logger.info("WebWindow instantiated (ctx=%s)", _ru(ctx))

    def show(self) -> None:
        """Open the chat window, or raise it to the front if already open.

        Re-clicking the menu while a chat window is already alive
        sends SIGUSR1 to the subprocess; ``web_runner`` catches the
        signal and calls ``window.show()`` which on GTK calls
        ``gtk_window_present()`` (raises + focuses the window). If
        the signal raises ``ProcessLookupError`` — race between
        ``poll()`` returning None and the subprocess actually
        exiting — we fall back to spawning a fresh window.

        Windows: no SIGUSR1; the second invocation still no-ops for
        now. Cross-platform refocus is task TBD.
        """
        logger.info("WebWindow.show: subprocess_alive=%s", self._is_alive())
        if self._is_alive() and self._proc is not None:
            if sys.platform == "win32":
                logger.info(
                    "WebWindow.show: subprocess alive (pid=%s) on Windows — "
                    "no SIGUSR1 path; click is a no-op (refocus TBD)",
                    self._proc.pid,
                )
                return
            try:
                logger.info(
                    "WebWindow.show: refocus via SIGUSR1 to pid=%s",
                    self._proc.pid,
                )
                os.kill(self._proc.pid, signal.SIGUSR1)
                return
            except ProcessLookupError:
                # Race: poll() said alive but the process exited
                # between then and our kill. Clean up + respawn.
                logger.warning(
                    "WebWindow.show: SIGUSR1 to pid=%s raised "
                    "ProcessLookupError — subprocess gone, respawning",
                    self._proc.pid,
                )
                self._proc = None
            except OSError:
                # Other OS-level failures (permission, signal not
                # available, ...) — log + respawn so the user still
                # gets a working window.
                logger.exception(
                    "WebWindow.show: SIGUSR1 to pid=%s failed; respawning",
                    self._proc.pid,
                )
                self._proc = None

        self._spawn_subprocess()

    def _spawn_subprocess(self) -> None:
        """Start a fresh pywebview subprocess.

        Extracted from :meth:`show` so the refocus branch can be
        unit-tested without driving the full subprocess.Popen path.
        """
        html_path = self._resolve_html_path()
        pythonpath_dir = self._resolve_pythonpath()
        python_bin = self._resolve_python()
        socket_path = self._ensure_bridge()
        logger.info(
            "WebWindow.show: html_path=%s pythonpath_dir=%s python=%s "
            "bridge_socket=%s",
            html_path,
            pythonpath_dir,
            python_bin,
            socket_path,
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
            "--bridge-socket",
            socket_path,
        ]
        logger.info("WebWindow.show: spawning subprocess %s", args)
        try:
            # No ``start_new_session`` — the subprocess shares LO's
            # process group, so when LO exits (clean or signal) the
            # webview process gets the same signal and dies with it.
            # Avoids orphaned chat windows after LO closes. Belt+
            # braces: ``atexit`` below explicitly terminates the
            # process on normal Python shutdown.
            self._proc = subprocess.Popen(
                args,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except Exception:
            logger.exception(
                "WebWindow.show: subprocess.Popen raised — re-raising"
            )
            raise
        logger.info(
            "WebWindow.show: subprocess started pid=%s", self._proc.pid
        )
        atexit.register(self._terminate_on_exit)

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

    def _terminate_on_exit(self) -> None:
        """Tear down the subprocess + bridge on LO shutdown (atexit hook)."""
        if self._is_alive() and self._proc is not None:
            logger.info(
                "WebWindow.atexit: terminating subprocess pid=%s",
                self._proc.pid,
            )
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                logger.warning(
                    "WebWindow.atexit: subprocess didn't exit on SIGTERM, "
                    "sending SIGKILL"
                )
                self._proc.kill()
            except Exception:
                logger.exception(
                    "WebWindow.atexit: terminate raised — continuing"
                )
        if self._bridge is not None:
            try:
                self._bridge.stop()
            except Exception:
                logger.exception(
                    "WebWindow.atexit: bridge.stop raised — continuing"
                )

    def _ensure_bridge(self) -> str:
        """Lazily start the Unix-socket JSON-RPC bridge. Returns its path."""
        if self._bridge is not None:
            return self._bridge.socket_path
        from talk2view_writer.bridge_server import BridgeServer

        self._bridge = BridgeServer(self.ctx)
        path = self._bridge.start()
        logger.info("WebWindow: bridge listening on %s", path)
        return path

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
