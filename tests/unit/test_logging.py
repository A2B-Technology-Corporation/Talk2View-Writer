"""Tests for the persistent rotating log setup."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import pytest

from talk2view_writer import _logging


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    """Tear down the package-level logging configuration between tests."""
    _logging.reset_for_tests()
    yield
    _logging.reset_for_tests()


@pytest.mark.unit
class TestLogFilePath:
    def test_linux_uses_xdg_cache(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        path = _logging.log_file_path()
        assert path == tmp_path / "talk2view-writer" / "talk2view.log"
        assert path.parent.is_dir()

    def test_linux_falls_back_to_dot_cache(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        path = _logging.log_file_path()
        assert path == tmp_path / ".cache" / "talk2view-writer" / "talk2view.log"

    def test_macos_uses_library_logs(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setenv("HOME", str(tmp_path))
        path = _logging.log_file_path()
        assert path == tmp_path / "Library" / "Logs" / "Talk2View-Writer" / "talk2view.log"

    def test_windows_uses_localappdata(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        path = _logging.log_file_path()
        assert path == tmp_path / "Talk2View-Writer" / "talk2view.log"


@pytest.mark.unit
class TestSetupLogging:
    def test_creates_file_and_returns_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        path = _logging.setup_logging()
        logging.getLogger("talk2view_writer.test").info("hello")
        # flush all handlers so the message is on disk
        for h in logging.getLogger("talk2view_writer").handlers:
            h.flush()
        assert path.is_file()
        content = path.read_text(encoding="utf-8")
        assert "hello" in content
        assert "talk2view_writer.test" in content

    def test_idempotent_no_handler_duplication(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        _logging.setup_logging()
        first = len(logging.getLogger("talk2view_writer").handlers)
        _logging.setup_logging()
        second = len(logging.getLogger("talk2view_writer").handlers)
        assert first == second, "handler count grew on second setup_logging()"

    def test_debug_env_var_sets_debug_level(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        monkeypatch.setenv("T2V_WRITER_DEBUG", "1")
        _logging.setup_logging()
        assert logging.getLogger("talk2view_writer").level == logging.DEBUG

    def test_no_debug_env_var_defaults_to_info(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        monkeypatch.delenv("T2V_WRITER_DEBUG", raising=False)
        _logging.setup_logging()
        assert logging.getLogger("talk2view_writer").level == logging.INFO

    def test_returns_same_path_when_called_twice(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        first = _logging.setup_logging()
        second = _logging.setup_logging()
        assert first == second

    def test_writes_initialisation_banner(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """First log line must include log file + pid + python + platform.

        Bug-report triage relies on these. Without them every report
        starts with 'tell me what version you're on'.
        """
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        path = _logging.setup_logging()
        for h in logging.getLogger("talk2view_writer").handlers:
            h.flush()
        content = path.read_text(encoding="utf-8")
        assert "logging initialised" in content
        assert str(os.getpid()) in content
        assert sys.platform in content


@pytest.mark.unit
class TestExceptionHooks:
    def test_excepthook_routes_to_logger(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        path = _logging.setup_logging()
        try:
            raise RuntimeError("boom in main thread")
        except RuntimeError:
            import traceback

            exc_type, exc_value, exc_tb = sys.exc_info()
            sys.excepthook(exc_type, exc_value, exc_tb)
            del traceback
        for h in logging.getLogger("talk2view_writer").handlers:
            h.flush()
        content = path.read_text(encoding="utf-8")
        assert "boom in main thread" in content
        assert "Unhandled exception" in content
