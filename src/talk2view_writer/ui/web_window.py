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

    from talk2view_writer.bridge_server import BridgeServer

logger = logging.getLogger(__name__)


def _ru(obj: Any) -> str:
    """UNO-safe repr for log args (see chat_window history for why)."""
    try:
        return repr(obj)
    except Exception as exc:
        return f"<repr failed: {type(exc).__name__}>"


_EXTENSION_ID = "com.talk2view.writer"
_HTML_PATH = "web/index.html"

# Canonical install path for the LibreOffice-bundled Python wrapper on
# macOS. Used as a fallback when sys.executable isn't inside an .app
# bundle (e.g. when running unit tests under the user's own Python).
# Exposed as a module-level Path so tests can monkeypatch it.
STANDARD_LO_PYTHON_DARWIN = Path("/Applications/LibreOffice.app/Contents/Resources/python")

# URE_BOOTSTRAP encodes the LO install root. LO sets this in every
# process it owns (including embedded Python extensions like ours),
# so it's the official discovery mechanism — robust to portable
# installs and non-default install locations. The value is a
# ``vnd.sun.star.pathname:<path>`` URI pointing at ``fundamentalrc``
# inside ``Contents/Resources/`` on macOS — two levels up is the
# install root, and the Python wrapper sits at
# ``<install>/Contents/Resources/python`` on macOS.
_URE_BOOTSTRAP_PREFIX = "vnd.sun.star.pathname:"


def _wrapper_from_ure_bootstrap() -> str | None:
    """Derive the LO macOS Python wrapper from ``URE_BOOTSTRAP``.

    Returns the wrapper path as a string if URE_BOOTSTRAP is set,
    parses cleanly, and the wrapper exists + is executable. Returns
    ``None`` otherwise (the caller will fall through to the next
    discovery method).

    macOS-specific: the LO install layout on Linux / Windows is
    different (``program/python`` vs ``Contents/Resources/python``)
    so this helper is only meaningful on macOS today.
    """
    raw = os.environ.get("URE_BOOTSTRAP", "")
    if not raw.startswith(_URE_BOOTSTRAP_PREFIX):
        return None
    fundamentalrc = Path(raw[len(_URE_BOOTSTRAP_PREFIX) :])
    # fundamentalrc lives at ``<install>/Contents/Resources/fundamentalrc``
    # on macOS; the Python wrapper is its sibling.
    wrapper = fundamentalrc.parent / "python"
    if wrapper.is_file() and os.access(wrapper, os.X_OK):
        return str(wrapper)
    return None


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
        self._bridge: BridgeServer | None = None  # lazy-imported
        logger.info("WebWindow instantiated (ctx=%s)", _ru(ctx))

    def show(self) -> None:
        """Open the chat window, or raise it to the front if already open.

        Re-clicking the menu while a chat window is already alive
        sends SIGUSR2 to the subprocess; ``web_runner`` catches the
        signal and calls ``window.show()`` which on GTK calls
        ``gtk_window_present()`` (raises + focuses the window). If
        the signal raises ``ProcessLookupError`` — race between
        ``poll()`` returning None and the subprocess actually
        exiting — we fall back to spawning a fresh window.

        SIGUSR2 rather than SIGUSR1 because WebKitGTK's
        JavaScriptCore uses SIGUSR1 internally for garbage collection
        (see WebKit's ``Set JSC_SIGNAL_FOR_GC if you want WebKit to
        use a different signal`` warning). Installing our handler on
        SIGUSR1 first appears to work, then WebKit overrides it
        during ``webview.start()`` — sending SIGUSR1 afterwards
        triggers JSC GC, not our focus handler.

        Windows: no POSIX signals; the second invocation still
        no-ops for now. Cross-platform refocus is TBD.
        """
        logger.info("WebWindow.show: subprocess_alive=%s", self._is_alive())
        # Headless-bridge mode: the live E2E test starts soffice + the
        # extension with this env var set, triggers the chat menu
        # command, and then drives the bundle from Playwright-Chromium
        # via a Node bridge-proxy that owns the bridge_server's single
        # Unix-socket connection. Spawning pywebview here would consume
        # that connection itself. The env var lets us start (or reuse)
        # the bridge and skip the subprocess spawn.
        if os.environ.get("T2V_WRITER_HEADLESS_BRIDGE"):
            socket_path = self._ensure_bridge()
            logger.info(
                "WebWindow.show: T2V_WRITER_HEADLESS_BRIDGE set — "
                "bridge ready at %s, pywebview spawn skipped",
                socket_path,
            )
            return
        if self._is_alive() and self._proc is not None:
            if sys.platform == "win32":
                logger.info(
                    "WebWindow.show: subprocess alive (pid=%s) on Windows — "
                    "no signal path; click is a no-op (refocus TBD)",
                    self._proc.pid,
                )
                return
            try:
                logger.info(
                    "WebWindow.show: refocus via SIGUSR2 to pid=%s",
                    self._proc.pid,
                )
                os.kill(self._proc.pid, signal.SIGUSR2)
                return
            except ProcessLookupError:
                # Race: poll() said alive but the process exited
                # between then and our kill. Clean up + respawn.
                logger.warning(
                    "WebWindow.show: SIGUSR2 to pid=%s raised "
                    "ProcessLookupError — subprocess gone, respawning",
                    self._proc.pid,
                )
                self._proc = None
            except OSError:
                # Other OS-level failures (permission, signal not
                # available, ...) — log + respawn so the user still
                # gets a working window.
                logger.exception(
                    "WebWindow.show: SIGUSR2 to pid=%s failed; respawning",
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
        icon_path = self._resolve_icon_path()
        logger.info(
            "WebWindow.show: html_path=%s pythonpath_dir=%s python=%s bridge_socket=%s",
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
            f"{pythonpath_dir}{os.pathsep}{existing_pp}" if existing_pp else pythonpath_dir
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
        if icon_path is not None:
            args += ["--icon", str(icon_path)]
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
            logger.exception("WebWindow.show: subprocess.Popen raised — re-raising")
            raise
        logger.info("WebWindow.show: subprocess started pid=%s", self._proc.pid)
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
                    "WebWindow.atexit: subprocess didn't exit on SIGTERM, sending SIGKILL"
                )
                self._proc.kill()
            except Exception:
                logger.exception("WebWindow.atexit: terminate raised — continuing")
        if self._bridge is not None:
            try:
                self._bridge.stop()
            except Exception:
                logger.exception("WebWindow.atexit: bridge.stop raised — continuing")

    def _ensure_bridge(self) -> str:
        """Lazily start the Unix-socket JSON-RPC bridge. Returns its path."""
        if self._bridge is not None:
            # BridgeServer.start() always sets socket_path before returning,
            # so by the time we have a non-None _bridge here socket_path
            # is guaranteed non-None. mypy can't infer that invariant.
            assert self._bridge.socket_path is not None
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
            logger.exception("web_runner stderr pump exited unexpectedly")
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
            raise RuntimeError(f"Talk2View web bundle URL must be file://, got {html_url!r}")
        path = Path(unquote(parsed.path))
        if not path.is_file():
            raise FileNotFoundError(
                f"Talk2View web bundle missing: {path} (resolved from {html_url})"
            )
        logger.info(
            "WebWindow._resolve_html_path: file exists size=%d bytes",
            path.stat().st_size,
        )
        return path

    def _resolve_icon_path(self) -> Path | None:
        """Resolve the bundled window icon, or ``None`` if absent.

        The icon brands the chat window (ADR-0039) but is not load-bearing
        — a missing icon must not stop the window opening, so this logs and
        returns ``None`` rather than raising.
        """
        pip = self.ctx.getValueByName(
            "/singletons/com.sun.star.deployment.PackageInformationProvider"
        )
        extension_root_url = pip.getPackageLocation(_EXTENSION_ID)
        parsed = urlparse(extension_root_url)
        if parsed.scheme != "file":
            logger.warning(
                "WebWindow._resolve_icon_path: non-file extension root %r",
                extension_root_url,
            )
            return None
        path = Path(unquote(parsed.path)) / "icons" / "talk2view.png"
        if not path.is_file():
            logger.warning(
                "WebWindow._resolve_icon_path: icon missing at %s", path
            )
            return None
        logger.info("WebWindow._resolve_icon_path: icon=%s", path)
        return path

    def _resolve_pythonpath(self) -> str:
        """Resolve the extension's bundled pythonpath/ directory."""
        pip = self.ctx.getValueByName(
            "/singletons/com.sun.star.deployment.PackageInformationProvider"
        )
        extension_root_url = pip.getPackageLocation(_EXTENSION_ID)
        parsed = urlparse(extension_root_url)
        if parsed.scheme != "file":
            raise RuntimeError(f"Extension root URL must be file://, got {extension_root_url!r}")
        path = Path(unquote(parsed.path)) / "pythonpath"
        if not path.is_dir():
            raise FileNotFoundError(f"pythonpath/ missing under extension root: {path}")
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
            logger.info("WebWindow._resolve_python: linux candidate=%s", candidate)
            return candidate
        if sys.platform == "darwin":
            # macOS LO embeds Python 3.12 in
            # `LibreOfficePython.framework` and sets PYTHONHOME in its
            # own process environment. Spawning the system
            # /usr/bin/python3 (Apple ships 3.9-class) makes the child
            # inherit that PYTHONHOME and try to load LO's bundled
            # stdlib — which references symbols only present in 3.10+
            # (e.g. `io.text_encoding`). The child dies before
            # pywebview can put a window on screen. The fix is to spawn
            # the LO-bundled interpreter itself via the canonical
            # `Contents/Resources/python` wrapper shipped inside the LO
            # app bundle (it sets PYTHONHOME correctly + execs the
            # matching framework interpreter).
            #
            # Resolution order, most-authoritative first:
            #
            #   1. ``URE_BOOTSTRAP`` env var. LO sets this on every
            #      process it owns (including ours) — its value
            #      encodes the install root unambiguously, so this
            #      handles portable + non-standard install paths
            #      without guessing.
            #   2. Walk every ``.app`` ancestor of ``sys.executable``
            #      checking for ``Contents/Resources/python``. Covers
            #      the case where URE_BOOTSTRAP is somehow missing
            #      (e.g. a non-LO host embedding our extension code
            #      in tests) — and works around the nested-bundle
            #      surprise that LO's Python lives inside
            #      ``Python.app`` *inside* ``LibreOffice.app``
            #      (the inner .app has no wrapper; the outer one
            #      does).
            #   3. Canonical ``/Applications/LibreOffice.app/...``
            #      path as last resort.
            candidate = self._find_lo_bundled_python_darwin()
            if candidate is not None:
                logger.info(
                    "WebWindow._resolve_python: darwin LO-bundled python=%s",
                    candidate,
                )
                return candidate
            raise FileNotFoundError(
                "Could not locate the LibreOffice-bundled Python "
                "interpreter on macOS. Set T2V_PYTHON to override "
                "(e.g. /Applications/LibreOffice.app/Contents/"
                "Resources/python)."
            )
        if sys.platform == "win32":
            candidate = shutil.which("python") or shutil.which("python3")
            if not candidate:
                raise FileNotFoundError(
                    "Talk2View needs python.exe on PATH (TODO: resolve LO-bundled python)"
                )
            return candidate
        raise RuntimeError(f"unsupported platform: {sys.platform}")

    def _find_lo_bundled_python_darwin(self) -> str | None:
        """Locate the LibreOffice-bundled Python wrapper on macOS.

        Three-stage lookup, most-authoritative first:

        1. Parse ``URE_BOOTSTRAP`` — the env var LO sets on every
           process it owns. Format::

               vnd.sun.star.pathname:/<install>/Contents/Resources/fundamentalrc

           Two levels up from ``fundamentalrc`` is the LO install
           root; ``Contents/Resources/python`` lives directly under
           it. This is the official LO install-discovery mechanism
           and handles portable / non-standard install paths
           without guessing.
        2. Walk every ``.app`` ancestor of ``sys.executable``
           checking for the wrapper. LO's bundled interpreter lives
           inside a nested ``Python.app`` *inside* the outer
           ``LibreOffice.app`` — the inner has no ``python``
           wrapper, the outer does — so iterate ALL ``.app``
           ancestors, don't stop at the first.
        3. Canonical ``/Applications/LibreOffice.app/...`` path as
           a last-resort hardcoded fallback (e.g. when this method
           is invoked from a non-LO host like unit tests).

        Returns the wrapper path as a string, or ``None`` if no
        executable wrapper can be found by any route.
        """
        from_bootstrap = _wrapper_from_ure_bootstrap()
        if from_bootstrap is not None:
            logger.info(
                "WebWindow._find_lo_bundled_python_darwin: URE_BOOTSTRAP → %s",
                from_bootstrap,
            )
            return from_bootstrap

        try:
            exe = Path(sys.executable).resolve()
        except OSError:
            exe = None
        if exe is not None:
            for parent in exe.parents:
                if parent.suffix != ".app":
                    continue
                wrapper = parent / "Contents" / "Resources" / "python"
                if wrapper.is_file() and os.access(wrapper, os.X_OK):
                    logger.info(
                        "WebWindow._find_lo_bundled_python_darwin: sys.executable .app walk → %s",
                        wrapper,
                    )
                    return str(wrapper)
                # else: keep walking — an outer .app ancestor (e.g.
                # LibreOffice.app wrapping a nested Python.app) may
                # still own the wrapper.

        if STANDARD_LO_PYTHON_DARWIN.is_file() and os.access(STANDARD_LO_PYTHON_DARWIN, os.X_OK):
            logger.info(
                "WebWindow._find_lo_bundled_python_darwin: hardcoded path → %s",
                STANDARD_LO_PYTHON_DARWIN,
            )
            return str(STANDARD_LO_PYTHON_DARWIN)
        return None
