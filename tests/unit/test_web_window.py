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

    def test_override_env_var_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("T2V_PYTHON", "/custom/python")
        assert self._win()._resolve_python() == "/custom/python"

    def test_linux_uses_which_result(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("T2V_PYTHON", raising=False)
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(
            "shutil.which",
            lambda name: "/usr/local/bin/python3" if name == "python3" else None,
        )
        assert self._win()._resolve_python() == "/usr/local/bin/python3"

    def test_linux_falls_back_to_usr_bin_python3(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("T2V_PYTHON", raising=False)
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr("shutil.which", lambda name: None)
        assert self._win()._resolve_python() == "/usr/bin/python3"

    def test_darwin_prefers_ure_bootstrap_over_sys_executable_walk(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Any,
    ) -> None:
        """URE_BOOTSTRAP wins over every other discovery method.

        LO sets URE_BOOTSTRAP on every process it owns; trusting it
        avoids guessing from sys.executable. If URE_BOOTSTRAP points
        at a valid wrapper, that path must be used even when
        sys.executable's .app walk would find a *different* wrapper.
        """
        # Build two distinct install trees: one referenced by
        # URE_BOOTSTRAP, one reachable via sys.executable's .app walk.
        ure_install = tmp_path / "ure-lo.app"
        ure_resources = ure_install / "Contents" / "Resources"
        ure_resources.mkdir(parents=True)
        ure_fundamentalrc = ure_resources / "fundamentalrc"
        ure_fundamentalrc.write_text("# fake\n")
        ure_wrapper = ure_resources / "python"
        ure_wrapper.write_text("#!/bin/sh\n")
        ure_wrapper.chmod(0o755)

        walk_install = tmp_path / "walk-lo.app"
        walk_resources = walk_install / "Contents" / "Resources"
        walk_macos = walk_install / "Contents" / "MacOS"
        walk_resources.mkdir(parents=True)
        walk_macos.mkdir(parents=True)
        walk_wrapper = walk_resources / "python"
        walk_wrapper.write_text("#!/bin/sh\n")
        walk_wrapper.chmod(0o755)
        walk_soffice = walk_macos / "soffice"
        walk_soffice.write_text("#!/bin/sh\n")
        walk_soffice.chmod(0o755)

        monkeypatch.delenv("T2V_PYTHON", raising=False)
        monkeypatch.setenv("URE_BOOTSTRAP", f"vnd.sun.star.pathname:{ure_fundamentalrc}")
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(sys, "executable", str(walk_soffice))
        assert self._win()._resolve_python() == str(ure_wrapper)

    def test_darwin_ure_bootstrap_with_missing_wrapper_falls_through(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Any,
    ) -> None:
        """URE_BOOTSTRAP pointing at non-existent wrapper falls through.

        If LO ever exposes URE_BOOTSTRAP without the canonical layout
        beneath it (e.g. a malformed dev build), discovery must keep
        looking instead of erroring — the walk-up will succeed in
        the standard case.
        """
        ure_install = tmp_path / "bad-lo.app" / "Contents" / "Resources"
        ure_install.mkdir(parents=True)
        ure_fundamentalrc = ure_install / "fundamentalrc"
        ure_fundamentalrc.write_text("# fake\n")
        # Intentionally NO python wrapper alongside fundamentalrc.

        walk_install = tmp_path / "good-lo.app"
        walk_resources = walk_install / "Contents" / "Resources"
        walk_macos = walk_install / "Contents" / "MacOS"
        walk_resources.mkdir(parents=True)
        walk_macos.mkdir(parents=True)
        walk_wrapper = walk_resources / "python"
        walk_wrapper.write_text("#!/bin/sh\n")
        walk_wrapper.chmod(0o755)
        walk_soffice = walk_macos / "soffice"
        walk_soffice.write_text("#!/bin/sh\n")
        walk_soffice.chmod(0o755)

        monkeypatch.delenv("T2V_PYTHON", raising=False)
        monkeypatch.setenv("URE_BOOTSTRAP", f"vnd.sun.star.pathname:{ure_fundamentalrc}")
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(sys, "executable", str(walk_soffice))
        assert self._win()._resolve_python() == str(walk_wrapper)

    def test_darwin_walks_past_nested_app_to_outer_lo_app(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Any,
    ) -> None:
        """Nested .app bundles must not stop the walk early.

        LO's bundled Python lives inside ``Python.app`` *inside*
        ``LibreOffice.app``. The inner .app has no python wrapper;
        the outer one does. The walk must keep going past the inner
        .app rather than aborting at the first ancestor.
        """
        # Outer .app with the wrapper (the LO bundle).
        outer = tmp_path / "LibreOffice.app"
        outer_contents = outer / "Contents"
        outer_resources = outer_contents / "Resources"
        outer_resources.mkdir(parents=True)
        outer_wrapper = outer_resources / "python"
        outer_wrapper.write_text("#!/bin/sh\n")
        outer_wrapper.chmod(0o755)

        # Inner Python.app with NO wrapper (mirrors LO's nesting).
        inner = (
            outer_contents
            / "Frameworks"
            / "LibreOfficePython.framework"
            / "Versions"
            / "3.12"
            / "Resources"
            / "Python.app"
        )
        inner_macos = inner / "Contents" / "MacOS"
        inner_macos.mkdir(parents=True)
        inner_python = inner_macos / "Python"
        inner_python.write_text("#!/bin/sh\n")
        inner_python.chmod(0o755)

        monkeypatch.delenv("T2V_PYTHON", raising=False)
        monkeypatch.delenv("URE_BOOTSTRAP", raising=False)
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(sys, "executable", str(inner_python))
        assert self._win()._resolve_python() == str(outer_wrapper)

    def test_darwin_walks_up_from_sys_executable_to_find_app_wrapper(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Any,
    ) -> None:
        """Prefer the .app bundle wrapper when sys.executable is inside one.

        Handles portable / non-standard LibreOffice installs by walking
        up from ``sys.executable`` to the nearest ``*.app`` ancestor and
        checking its ``Contents/Resources/python``.
        """
        # Build a fake .app bundle layout under tmp_path:
        # tmp/LibreOffice.app/Contents/MacOS/soffice (sys.executable)
        # tmp/LibreOffice.app/Contents/Resources/python (the wrapper)
        app = tmp_path / "LibreOffice.app"
        contents = app / "Contents"
        macos = contents / "MacOS"
        resources = contents / "Resources"
        macos.mkdir(parents=True)
        resources.mkdir(parents=True)
        soffice = macos / "soffice"
        soffice.write_text("#!/bin/sh\n")
        soffice.chmod(0o755)
        wrapper = resources / "python"
        wrapper.write_text("#!/bin/sh\n")
        wrapper.chmod(0o755)

        monkeypatch.delenv("T2V_PYTHON", raising=False)
        monkeypatch.delenv("URE_BOOTSTRAP", raising=False)
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(sys, "executable", str(soffice))
        assert self._win()._resolve_python() == str(wrapper)

    def test_darwin_falls_back_to_standard_install(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Any,
    ) -> None:
        """Fall back to the canonical /Applications path when outside any .app.

        Covers the case where ``sys.executable`` points outside any LO
        bundle — e.g. unit tests running under the user's own Python.
        """
        # Standard install layout: pretend it lives under tmp_path so
        # the test isn't dependent on a real LO install. We monkeypatch
        # the module-level STANDARD_LO_PYTHON_DARWIN constant.
        standard = (
            tmp_path / "Applications" / "LibreOffice.app" / "Contents" / "Resources" / "python"
        )
        standard.parent.mkdir(parents=True)
        standard.write_text("#!/bin/sh\n")
        standard.chmod(0o755)

        # sys.executable is the user's venv python, not inside any .app
        venv_python = tmp_path / "venv" / "bin" / "python"
        venv_python.parent.mkdir(parents=True)
        venv_python.write_text("")

        monkeypatch.delenv("T2V_PYTHON", raising=False)
        monkeypatch.delenv("URE_BOOTSTRAP", raising=False)
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(sys, "executable", str(venv_python))
        monkeypatch.setattr(
            "talk2view_writer.ui.web_window.STANDARD_LO_PYTHON_DARWIN",
            standard,
        )
        assert self._win()._resolve_python() == str(standard)

    def test_darwin_raises_when_no_lo_python_found(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Any,
    ) -> None:
        """Raise a clear FileNotFoundError when neither lookup succeeds.

        The error must mention ``T2V_PYTHON`` so the user knows the
        override exists.
        """
        venv_python = tmp_path / "venv" / "bin" / "python"
        venv_python.parent.mkdir(parents=True)
        venv_python.write_text("")

        missing = tmp_path / "does-not-exist" / "python"

        monkeypatch.delenv("T2V_PYTHON", raising=False)
        monkeypatch.delenv("URE_BOOTSTRAP", raising=False)
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(sys, "executable", str(venv_python))
        monkeypatch.setattr(
            "talk2view_writer.ui.web_window.STANDARD_LO_PYTHON_DARWIN",
            missing,
        )
        with pytest.raises(FileNotFoundError, match=r"T2V_PYTHON"):
            self._win()._resolve_python()

    def test_windows_uses_which_python(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("T2V_PYTHON", raising=False)
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(
            "shutil.which",
            lambda name: "C:/Python/python.exe" if name == "python" else None,
        )
        assert self._win()._resolve_python() == "C:/Python/python.exe"

    def test_windows_raises_if_no_python(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("T2V_PYTHON", raising=False)
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr("shutil.which", lambda name: None)
        with pytest.raises(FileNotFoundError, match=r"python\.exe"):
            self._win()._resolve_python()

    def test_unsupported_platform_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
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

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only refocus path; Windows TBD")
    def test_refocus_signals_existing_subprocess(self, monkeypatch: pytest.MonkeyPatch) -> None:
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

        assert sent == [(12345, signal.SIGUSR2)], f"expected one SIGUSR2 to pid 12345, got {sent!r}"

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only refocus path; Windows TBD")
    def test_refocus_does_not_respawn_subprocess(self, monkeypatch: pytest.MonkeyPatch) -> None:
        win = self._make_window()

        fake_proc = MagicMock()
        fake_proc.pid = 12345
        fake_proc.poll.return_value = None
        win._proc = fake_proc

        monkeypatch.setattr("os.kill", lambda pid, sig: None)
        popen_calls: list[Any] = []
        monkeypatch.setattr(
            "subprocess.Popen",
            lambda *args, **kwargs: popen_calls.append((args, kwargs)) or MagicMock(),
        )

        win.show()
        assert popen_calls == [], "show() should NOT spawn a new subprocess when one is alive"

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only refocus path; Windows TBD")
    def test_signal_failure_falls_back_to_respawn(self, monkeypatch: pytest.MonkeyPatch) -> None:
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

        assert spawn_calls == [True], "ProcessLookupError on SIGUSR2 should trigger a respawn"


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

    def test_env_var_unset_runs_normal_spawn_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default behaviour: env var absent → ``_spawn_subprocess`` fires."""
        monkeypatch.delenv("T2V_WRITER_HEADLESS_BRIDGE", raising=False)
        win = self._make_window()

        with patch.object(win, "_spawn_subprocess") as spawn_mock:
            win.show()

        spawn_mock.assert_called_once()

    def test_env_var_set_skips_spawn_starts_bridge(self, monkeypatch: pytest.MonkeyPatch) -> None:
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

    def test_env_var_set_to_zero_does_not_skip_spawn(self, monkeypatch: pytest.MonkeyPatch) -> None:
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
