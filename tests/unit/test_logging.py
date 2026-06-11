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
class TestDebugEnabled:
    """``debug_enabled`` gates DEBUG logging *and* the pywebview inspector."""

    @pytest.mark.parametrize("value", ["1", "true", "True", "YES", "on", "On"])
    def test_truthy_values_enable(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setenv("T2V_WRITER_DEBUG", value)
        assert _logging.debug_enabled() is True

    @pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "anything"])
    def test_falsey_values_disable(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setenv("T2V_WRITER_DEBUG", value)
        assert _logging.debug_enabled() is False

    def test_unset_defaults_to_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("T2V_WRITER_DEBUG", raising=False)
        assert _logging.debug_enabled() is False


@pytest.mark.unit
class TestRedactSecrets:
    """Credentials must never reach the persistent log (shared in bug reports)."""

    _JWT = (
        "eyJhbGciOiJIUzI1NiJ9.eyJlbWFpbCI6ImJlbkBleGFtcGxlLmNvbSJ9.abc-DEF_123"
    )

    def test_redacts_bearer_token(self) -> None:
        out = _logging.redact_secrets(f"Authorization: Bearer {self._JWT}")
        assert self._JWT not in out
        assert "Bearer <redacted>" in out

    def test_redacts_partner_key(self) -> None:
        out = _logging.redact_secrets("key=pk_live_474f6f895dfec144a70b841db0")
        assert "474f6f895dfec144" not in out
        assert "pk_live_<redacted>" in out

    def test_redacts_bare_jwt(self) -> None:
        out = _logging.redact_secrets(f"token in body {self._JWT} here")
        assert self._JWT not in out
        assert "<redacted-jwt>" in out

    def test_redacts_json_password_field(self) -> None:
        out = _logging.redact_secrets(
            'body={"email": "c@hospital.org", "password": "S3cr3t-PW!"}'
        )
        assert "S3cr3t-PW!" not in out
        assert '"password": "<redacted>"' in out
        # The email is not a credential and stays for triage.
        assert "c@hospital.org" in out

    def test_redacts_python_repr_password_field(self) -> None:
        out = _logging.redact_secrets("params={'password': 'hunter2'}")
        assert "hunter2" not in out
        assert "'password':" in out

    def test_redacts_refresh_token_field(self) -> None:
        out = _logging.redact_secrets(
            'body={"refresh_token": "v1.MR0pa9ueOPAQUE-not-a-jwt"}'
        )
        assert "MR0pa9ueOPAQUE" not in out
        assert '"refresh_token": "<redacted>"' in out

    def test_password_redaction_is_idempotent(self) -> None:
        once = _logging.redact_secrets('{"password": "abc"}')
        assert _logging.redact_secrets(once) == once

    def test_leaves_ordinary_text_untouched(self) -> None:
        msg = "BridgeServer.dispatch: id=42 method=invoke_tool name=get_document"
        assert _logging.redact_secrets(msg) == msg

    def test_is_idempotent(self) -> None:
        once = _logging.redact_secrets(f"Bearer {self._JWT}")
        assert _logging.redact_secrets(once) == once

    def test_formatter_redacts_rendered_line(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """End-to-end: a token logged through the package logger lands redacted."""
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        path = _logging.setup_logging()
        logging.getLogger("talk2view_writer.test").info(
            "proxy params={'Authorization': 'Bearer %s'}", self._JWT
        )
        for h in logging.getLogger("talk2view_writer").handlers:
            h.flush()
        content = path.read_text(encoding="utf-8")
        assert self._JWT not in content
        assert "Bearer <redacted>" in content


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


@pytest.mark.unit
class TestFlushLogs:
    """``flush_logs`` is the safety net before risky native UNO calls.

    The file handler buffers writes; if soffice segfaults inside
    ``createContainerWindow`` the most recent log lines are lost.
    Calling ``flush_logs`` immediately before such calls ensures the
    diagnostics survive the crash.
    """

    def test_flushes_every_package_handler(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        _logging.setup_logging()
        package_logger = logging.getLogger("talk2view_writer")

        flushed: list[int] = []
        for h in package_logger.handlers:
            orig = h.flush

            def make_wrapper(h=h, orig=orig):
                def wrapped() -> None:
                    flushed.append(id(h))
                    orig()

                return wrapped

            h.flush = make_wrapper()  # type: ignore[method-assign]

        _logging.flush_logs()

        # Every handler attached to the package logger was flushed.
        for h in package_logger.handlers:
            assert id(h) in flushed

    def test_re_raises_when_a_handler_flush_fails(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A flush failure must surface as a RuntimeError with a chained cause."""
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        _logging.setup_logging()
        package_logger = logging.getLogger("talk2view_writer")

        assert package_logger.handlers, "setup_logging must attach handlers"
        boom_handler = package_logger.handlers[0]
        original_flush = boom_handler.flush

        def raise_flush() -> None:
            raise OSError("disk full")

        boom_handler.flush = raise_flush  # type: ignore[method-assign]
        try:
            with pytest.raises(RuntimeError) as info:
                _logging.flush_logs()
            assert "flush" in str(info.value)
            assert isinstance(info.value.__cause__, OSError)
            assert "disk full" in str(info.value.__cause__)
        finally:
            boom_handler.flush = original_flush  # type: ignore[method-assign]

    def test_continues_flushing_other_handlers_after_failure(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A broken handler must not block the others mid-flush."""
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        _logging.setup_logging()
        package_logger = logging.getLogger("talk2view_writer")

        # Need at least two handlers to test this. setup_logging
        # attaches stderr + file, so this should hold.
        assert (
            len(package_logger.handlers) >= 2
        ), "this test needs >=2 handlers"

        boom_handler = package_logger.handlers[0]
        other_handlers = package_logger.handlers[1:]
        original_boom = boom_handler.flush
        original_others = [h.flush for h in other_handlers]

        boom_handler.flush = lambda: (_ for _ in ()).throw(  # type: ignore[method-assign]
            OSError("boom")
        )
        flushed_others: list[int] = []
        for h in other_handlers:
            def make_w(h=h):
                def w() -> None:
                    flushed_others.append(id(h))

                return w

            h.flush = make_w()  # type: ignore[method-assign]

        try:
            with pytest.raises(RuntimeError):
                _logging.flush_logs()
            # Every other handler still got flushed despite the failure.
            for h in other_handlers:
                assert id(h) in flushed_others
        finally:
            boom_handler.flush = original_boom  # type: ignore[method-assign]
            for h, orig in zip(other_handlers, original_others, strict=False):
                h.flush = orig  # type: ignore[method-assign]


@pytest.mark.unit
class TestRuntimeInfoBanner:
    """The runtime-info second banner line is critical for triage.

    Bug reports need to be readable without the user having to dump
    their kernel version + arch — so we always log it.
    """

    def test_writes_runtime_info_line(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        monkeypatch.setenv("T2V_WRITER_DEBUG", "1")
        path = _logging.setup_logging()
        for h in logging.getLogger("talk2view_writer").handlers:
            h.flush()
        content = path.read_text(encoding="utf-8")
        assert "runtime info" in content
        assert "platform=" in content
        assert "machine=" in content
        # env_debug echoed so the support engineer can confirm whether
        # the user actually reproduced with DEBUG.
        assert "env_debug='1'" in content
