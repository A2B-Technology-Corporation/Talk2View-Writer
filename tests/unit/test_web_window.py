"""Unit tests for ``talk2view_writer.ui.web_window.WebWindow``.

Covers the refocus-on-re-click path (SIGUSR1 sent to the existing
subprocess) and the spawn-on-first-call path (no SIGUSR1 fired).
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
        """When the subprocess is alive, ``show()`` sends SIGUSR1 to it.

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

        assert sent == [(12345, signal.SIGUSR1)], (
            f"expected one SIGUSR1 to pid 12345, got {sent!r}"
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
        """SIGUSR1 race recovery.

        If SIGUSR1 raises ``ProcessLookupError`` (race vs subprocess
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
            "ProcessLookupError on SIGUSR1 should trigger a respawn"
        )
