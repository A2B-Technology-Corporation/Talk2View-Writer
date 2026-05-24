"""Unit tests for ``talk2view_writer.ui.web_window.WebWindow``.

Covers the refocus-on-re-click path (SIGUSR2 sent to the existing
subprocess) and the spawn-on-first-call path (no SIGUSR2 fired).
The actual subprocess.Popen + signal delivery is mocked; we assert
on call shape, not behaviour of the real OS primitives.
"""

from __future__ import annotations

import signal
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.unit
class TestResolvePython:
    """Cross-platform discovery of the python interpreter."""

    def _win(self) -> Any:
        from talk2view_writer.ui.web_window import WebWindow

        return WebWindow(ctx=MagicMock(name="ctx"))

    def test_override_env_var_wins(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("T2V_PYTHON", "/custom/python")
        assert self._win()._resolve_python() == "/custom/python"

    def test_linux_uses_which_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("T2V_PYTHON", raising=False)
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(
            "shutil.which",
            lambda name: "/usr/local/bin/python3" if name == "python3" else None,
        )
        assert self._win()._resolve_python() == "/usr/local/bin/python3"

    def test_linux_falls_back_to_usr_bin_python3(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("T2V_PYTHON", raising=False)
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr("shutil.which", lambda name: None)
        assert self._win()._resolve_python() == "/usr/bin/python3"

    def test_darwin_uses_which_or_falls_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("T2V_PYTHON", raising=False)
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr("shutil.which", lambda name: None)
        assert self._win()._resolve_python() == "/usr/bin/python3"

    def test_windows_uses_which_python(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("T2V_PYTHON", raising=False)
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(
            "shutil.which",
            lambda name: "C:/Python/python.exe" if name == "python" else None,
        )
        assert self._win()._resolve_python() == "C:/Python/python.exe"

    def test_windows_raises_if_no_python(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("T2V_PYTHON", raising=False)
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr("shutil.which", lambda name: None)
        with pytest.raises(FileNotFoundError, match=r"python\.exe"):
            self._win()._resolve_python()

    def test_unsupported_platform_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("T2V_PYTHON", raising=False)
        monkeypatch.setattr(sys, "platform", "aix")
        with pytest.raises(RuntimeError, match="unsupported platform"):
            self._win()._resolve_python()


@pytest.mark.unit
class TestWebWindowRefocus:
    """Re-click on an open chat window refocuses, doesn't respawn."""

    def _make_window(self) -> Any:
        from talk2view_writer.ui.web_window import WebWindow

        return WebWindow(ctx=MagicMock(name="ctx"))

    @pytest.mark.skipif(
        sys.platform == "win32", reason="POSIX-only refocus path; Windows TBD"
    )
    def test_refocus_signals_existing_subprocess(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When the subprocess is alive, ``show()`` sends SIGUSR2 to it.

        We mock os.kill so the test doesn't depend on a real running
        subprocess. The assertion is on the call shape — pid + signum.
        """
        win = self._make_window()

        # Pretend a subprocess is alive with PID 12345.
        fake_proc = MagicMock()
        fake_proc.pid = 12345
        fake_proc.poll.return_value = None  # still running
        win._proc = fake_proc

        sent: list[tuple[int, int]] = []

        def fake_kill(pid: int, sig: int) -> None:
            sent.append((pid, sig))

        monkeypatch.setattr("os.kill", fake_kill)

        win.show()

        assert sent == [(12345, signal.SIGUSR2)], (
            f"expected one SIGUSR2 to pid 12345, got {sent!r}"
        )

    @pytest.mark.skipif(
        sys.platform == "win32", reason="POSIX-only refocus path; Windows TBD"
    )
    def test_refocus_does_not_respawn_subprocess(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        win = self._make_window()

        fake_proc = MagicMock()
        fake_proc.pid = 12345
        fake_proc.poll.return_value = None
        win._proc = fake_proc

        monkeypatch.setattr("os.kill", lambda pid, sig: None)
        popen_calls: list[Any] = []
        monkeypatch.setattr(
            "subprocess.Popen",
            lambda *args, **kwargs: popen_calls.append((args, kwargs))
            or MagicMock(),
        )

        win.show()
        assert popen_calls == [], (
            "show() should NOT spawn a new subprocess when one is alive"
        )

    @pytest.mark.skipif(
        sys.platform == "win32", reason="POSIX-only refocus path; Windows TBD"
    )
    def test_signal_failure_falls_back_to_respawn(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SIGUSR2 race recovery.

        If SIGUSR2 raises ``ProcessLookupError`` (race vs subprocess
        exit), ``show()`` should still recover by spawning a fresh
        subprocess.
        """
        win = self._make_window()

        fake_proc = MagicMock()
        fake_proc.pid = 12345
        fake_proc.poll.return_value = None  # initially alive
        win._proc = fake_proc

        def fake_kill(pid: int, sig: int) -> None:
            raise ProcessLookupError("no such process")

        monkeypatch.setattr("os.kill", fake_kill)
        # Stub out the spawn path so this test doesn't try to fork.
        spawn_calls: list[Any] = []
        with patch.object(win, "_spawn_subprocess") as spawn_mock:
            spawn_mock.side_effect = lambda: spawn_calls.append(True)
            win.show()

        assert spawn_calls == [True], (
            "ProcessLookupError on SIGUSR2 should trigger a respawn"
        )


@pytest.mark.unit
class TestHeadlessBridgeMode:
    """``T2V_WRITER_HEADLESS_BRIDGE=1`` starts the bridge without pywebview.

    The live E2E test suite (Playwright + Node bridge-proxy + real
    soffice + real engine, per the live-spec architecture) needs to
    own the bridge_server's single Unix-socket connection. Spawning
    pywebview would consume that connection itself. The env var lets
    the test set up soffice + the extension, trigger the chat menu
    command (which initialises the bridge), and skip the subprocess
    spawn so the Node bridge-proxy can be the sole connection.
    """

    def _make_window(self) -> Any:
        from talk2view_writer.ui.web_window import WebWindow

        return WebWindow(ctx=MagicMock(name="ctx"))

    def test_env_var_unset_runs_normal_spawn_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Default behaviour: env var absent → ``_spawn_subprocess`` fires."""
        monkeypatch.delenv("T2V_WRITER_HEADLESS_BRIDGE", raising=False)
        win = self._make_window()

        with patch.object(win, "_spawn_subprocess") as spawn_mock:
            win.show()

        spawn_mock.assert_called_once()

    def test_env_var_set_skips_spawn_starts_bridge(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``T2V_WRITER_HEADLESS_BRIDGE=1`` → bridge starts, no Popen."""
        monkeypatch.setenv("T2V_WRITER_HEADLESS_BRIDGE", "1")
        win = self._make_window()

        with (
            patch.object(win, "_spawn_subprocess") as spawn_mock,
            patch.object(win, "_ensure_bridge") as bridge_mock,
        ):
            bridge_mock.return_value = "/tmp/talk2view-bridge-test/sock"
            win.show()

        spawn_mock.assert_not_called()
        bridge_mock.assert_called_once()

    def test_env_var_set_to_zero_does_not_skip_spawn(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Empty-string value treated as unset; spawn proceeds.

        Uses the standard 'any truthy non-empty string' rule the rest
        of the codebase follows for debug toggles. A genuinely-off
        path uses ``delenv``.
        """
        monkeypatch.setenv("T2V_WRITER_HEADLESS_BRIDGE", "")
        win = self._make_window()

        with patch.object(win, "_spawn_subprocess") as spawn_mock:
            win.show()

        spawn_mock.assert_called_once()
