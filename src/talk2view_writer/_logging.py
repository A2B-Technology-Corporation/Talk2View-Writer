"""Persistent rotating log file + exception hooks for Talk2View-Writer.

LibreOffice extensions run under the LibreOffice process; their
``stderr`` only surfaces to a user who launched ``soffice`` from a
terminal — which most users don't. Without a persistent log file, a
bug report from a user means "send me your `journalctl`" (Linux) or
"watch what happens when you do X" (everyone else). That's not a
debuggable workflow.

This module sets up:

  * A rotating file handler writing to an OS-appropriate persistent
    location (see :func:`log_file_path`). 5 MB per file, 3 rotated
    backups kept — bounded disk use, plenty of history to diagnose
    intermittent issues.
  * A formatter with timestamp, thread name, logger name, level,
    message — enough to debug threading + module-boundary issues
    without rerunning.
  * A ``stderr`` handler at the same level — useful when running
    soffice from a terminal during development.
  * A ``sys.excepthook`` + ``threading.excepthook`` installation
    so unhandled exceptions land in the log instead of vanishing.

Idempotent: calling :func:`setup_logging` multiple times is safe;
the handlers are only attached once. Tests in
``tests/unit/test_logging.py`` exercise the idempotency + path
selection.

Verbosity:

  * Default: ``INFO``.
  * Set environment variable ``T2V_WRITER_DEBUG=1`` to flip
    everything to ``DEBUG`` — useful when reproducing an issue
    with a fresh log capture.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import platform as _platform
import sys
import threading
import traceback
from pathlib import Path
from types import TracebackType
from typing import Any

_LOG_FORMAT = (
    "%(asctime)s.%(msecs)03dZ %(levelname)-7s "
    "%(threadName)-18s %(name)-42s | %(message)s"
)
_LOG_DATEFMT = "%Y-%m-%dT%H:%M:%S"

_MAX_BYTES = 5 * 1024 * 1024  # 5 MB per file
_BACKUP_COUNT = 3

# Module-level logger named after the package so calls from anywhere
# in talk2view_writer flow through the configured handlers.
_PACKAGE_LOGGER = "talk2view_writer"

_setup_lock = threading.Lock()
_setup_done = False
_active_log_path: Path | None = None


def log_file_path() -> Path:
    """Return the persistent log file path for this OS.

    Honours XDG_CACHE_HOME on Linux + LOCALAPPDATA on Windows; falls
    back to ``~/.cache/talk2view-writer/`` and
    ``~/AppData/Local/Talk2View-Writer/`` respectively. macOS uses
    the conventional ``~/Library/Logs/Talk2View-Writer/``.

    Creates the parent directory if missing.
    """
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Logs" / "Talk2View-Writer"
    elif sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        base = Path(local) / "Talk2View-Writer"
    else:
        xdg = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
        base = Path(xdg) / "talk2view-writer"
    base.mkdir(parents=True, exist_ok=True)
    return base / "talk2view.log"


def _level_from_env() -> int:
    """Return ``DEBUG`` if T2V_WRITER_DEBUG is truthy, otherwise ``INFO``."""
    if os.environ.get("T2V_WRITER_DEBUG", "").lower() in ("1", "true", "yes", "on"):
        return logging.DEBUG
    return logging.INFO


def _install_excepthooks(logger: logging.Logger) -> None:
    """Route ``sys.excepthook`` + ``threading.excepthook`` into ``logger``.

    Without these, an unhandled exception in a worker thread vanishes
    silently — the chat panel just stops responding. With them, the
    full traceback lands in the log file with thread name attached.
    """

    def _excepthook(
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_tb: TracebackType | None,
    ) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            # Let Ctrl-C through to the default handler so soffice
            # can exit cleanly during development.
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logger.critical(
            "Unhandled exception (main thread):\n%s",
            "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
        )

    def _thread_excepthook(args: Any) -> None:
        # ``threading.ExceptHookArgs`` is a NamedTuple with
        # exc_type / exc_value / exc_traceback / thread.
        if issubclass(args.exc_type, SystemExit):
            return
        thread_name = args.thread.name if args.thread is not None else "<unknown>"
        logger.critical(
            "Unhandled exception in thread %s:\n%s",
            thread_name,
            "".join(
                traceback.format_exception(
                    args.exc_type, args.exc_value, args.exc_traceback
                )
            ),
        )

    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook


def setup_logging() -> Path:
    """Initialise the persistent log file + exception hooks.

    Idempotent and thread-safe. The first call configures everything;
    subsequent calls return the same active path without re-attaching
    handlers (which would otherwise produce duplicated log lines).

    Returns:
        The active log file path. Surface this to users in the
        Settings dialog + the bug report template so they can attach
        it to issues without having to hunt.
    """
    global _setup_done, _active_log_path
    with _setup_lock:
        if _setup_done and _active_log_path is not None:
            return _active_log_path

        path = log_file_path()
        level = _level_from_env()

        formatter = logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT)

        try:
            file_handler = logging.handlers.RotatingFileHandler(
                path,
                maxBytes=_MAX_BYTES,
                backupCount=_BACKUP_COUNT,
                encoding="utf-8",
            )
        except OSError as exc:
            # Disk full, permission denied, read-only home dir, etc.
            # Fall through to stderr-only logging — better than
            # silently swallowing all log output.
            print(
                f"Talk2View-Writer: cannot open log file {path}: {exc}. "
                "Falling back to stderr-only logging.",
                file=sys.stderr,
            )
            file_handler = None

        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setFormatter(formatter)
        stderr_handler.setLevel(level)

        package_logger = logging.getLogger(_PACKAGE_LOGGER)
        package_logger.setLevel(level)
        # Avoid handler duplication if setup_logging is somehow called
        # from inside a pytest run that already wired its own root.
        package_logger.handlers.clear()
        package_logger.addHandler(stderr_handler)
        if file_handler is not None:
            file_handler.setFormatter(formatter)
            file_handler.setLevel(level)
            package_logger.addHandler(file_handler)
        package_logger.propagate = False

        _install_excepthooks(package_logger)

        package_logger.info(
            "Talk2View-Writer logging initialised — file=%s level=%s pid=%s python=%s platform=%s",
            path,
            logging.getLevelName(level),
            os.getpid(),
            sys.version.split()[0],
            sys.platform,
        )
        # Second line: kernel / build / arch detail that matters for
        # diagnosing PyUNO-bridge differences across Linux distros and
        # macOS versions. Splitting from the banner keeps the original
        # line under terminal width and preserves backward-compat with
        # the bug-report triage regex.
        try:
            uname = " ".join(os.uname()) if hasattr(os, "uname") else "(no os.uname)"
        except OSError:
            # uname can raise on rare locked-down environments. Best-effort.
            uname = "(uname failed)"
        package_logger.info(
            "Talk2View-Writer runtime info — platform=%s machine=%s arch=%s uname=%s env_debug=%r",
            _platform.platform(),
            _platform.machine(),
            _platform.architecture()[0] if _platform.architecture() else "?",
            uname,
            os.environ.get("T2V_WRITER_DEBUG", ""),
        )

        _setup_done = True
        _active_log_path = path
        return path


def flush_logs() -> None:
    """Flush every handler on the package logger + root logger.

    Call this before any native UNO call that risks a segfault.
    Without a flush, the file-handler buffer may discard the most
    recent log lines when soffice dies — losing exactly the
    diagnostic info that would tell you where it crashed.

    If a handler's flush raises (out-of-disk, broken pipe, etc.) the
    remaining handlers are still flushed (we don't want one broken
    handler to block others mid-flush), then a ``RuntimeError`` is
    raised with the first failure chained as ``__cause__``. This
    honours the package rule "never hide errors — always re-raise so
    the full traceback lands in the log file" without abandoning
    other handlers.
    """
    errors: list[BaseException] = []
    seen: set[int] = set()
    for source in (logging.getLogger(_PACKAGE_LOGGER), logging.getLogger()):
        for handler in list(source.handlers):
            if id(handler) in seen:
                continue
            seen.add(id(handler))
            try:
                handler.flush()
            except Exception as exc:
                # Per-handler failure is intentionally collected (not
                # re-raised here) so the remaining handlers still get
                # flushed — then we raise a combined error at the end
                # with this failure chained as ``__cause__``.
                errors.append(exc)
    if errors:
        raise RuntimeError(
            f"flush_logs: {len(errors)} handler(s) failed to flush"
        ) from errors[0]


def reset_for_tests() -> None:
    """Tear down the configured logging state. Tests only."""
    global _setup_done, _active_log_path
    with _setup_lock:
        package_logger = logging.getLogger(_PACKAGE_LOGGER)
        for handler in list(package_logger.handlers):
            handler.close()
            package_logger.removeHandler(handler)
        _setup_done = False
        _active_log_path = None
