"""Real-WebKitGTK microphone-permission gui_smoke test (Linux only).

Proves the production fix end-to-end: drives a live ``WebKit2.WebView``
through ``webkit_media_permission_check.py`` (run under the system Python
that has PyGObject + WebKit2, the same interpreter the pywebview subprocess
uses) and asserts that connecting the production
``_grant_media_permission`` handler flips ``getUserMedia`` from
``NotAllowedError`` (the bug) to allowed (the fix).

Linux only — WebKitGTK is the only backend we can drive headlessly under
Xvfb. macOS (WKWebView/TCC) and Windows (WebView2) mic paths are verified
manually post-release (see docs/investigations.md #58). The unit tests in
``tests/unit/test_web_runner_media.py`` cover the per-OS patch wiring on all
platforms.

Skips (never fails) when the environment can't support the check: no system
Python with WebKit2, or no audio-input device to discriminate the permission
gate (without a device getUserMedia fails with NotFoundError regardless of
permission). CI provisions a PulseAudio virtual source so it actually runs.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.gui_smoke,
    pytest.mark.skipif(
        sys.platform != "linux",
        reason="WebKitGTK headless mic check is Linux-only; macOS/Windows verified manually",
    ),
]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PROBE = _REPO_ROOT / "tests" / "integration" / "webkit_media_permission_check.py"


def _system_python_with_webkit() -> str | None:
    """Find a Python interpreter that can import Gtk + WebKit2.

    The uv venv (sys.executable) has no PyGObject; the production webview
    runs under the system interpreter instead. Try the usual candidates.
    """
    candidates = [
        shutil.which("python3"),
        "/usr/bin/python3",
        "/usr/bin/python3.13",
        "/usr/bin/python3.12",
    ]
    probe = (
        "import gi; gi.require_version('Gtk','3.0');\n"
        "try:\n"
        "    gi.require_version('WebKit2','4.1')\n"
        "except ValueError:\n"
        "    gi.require_version('WebKit2','4.0')\n"
        "from gi.repository import Gtk, WebKit2\n"
    )
    seen: set[str] = set()
    for cand in candidates:
        if not cand or cand in seen:
            continue
        seen.add(cand)
        # Skip the venv interpreter — it has no gi.
        if Path(cand).resolve() == Path(sys.executable).resolve():
            continue
        try:
            res = subprocess.run(
                [cand, "-c", probe],
                capture_output=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if res.returncode == 0:
            return cand
    return None


def _run_probe(python: str, *, grant: bool) -> dict[str, object]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_REPO_ROOT / "src")
    # CI runs Xvfb on X11; force the X11 GDK backend (WebKitGTK getUserMedia
    # has a known Wayland GBM/EGL capture quirk — see investigations #58).
    env.setdefault("GDK_BACKEND", "x11")
    cmd = [python, str(_PROBE), "--timeout", "30"]
    if grant:
        cmd.append("--grant")
    res = subprocess.run(
        cmd, capture_output=True, text=True, timeout=90, check=False, env=env
    )
    for line in res.stdout.splitlines():
        if line.startswith("RESULT="):
            return json.loads(line[len("RESULT=") :])  # type: ignore[no-any-return]
    raise AssertionError(
        f"probe emitted no RESULT line (rc={res.returncode})\n"
        f"--- stdout ---\n{res.stdout}\n--- stderr ---\n{res.stderr}"
    )


def test_permission_handler_flips_getusermedia() -> None:
    python = _system_python_with_webkit()
    if python is None:
        pytest.skip("no system Python with Gtk + WebKit2 typelibs available")
    assert _PROBE.is_file(), _PROBE

    without = _run_probe(python, grant=False)
    with_handler = _run_probe(python, grant=True)

    # An insecure context would make navigator.mediaDevices undefined and
    # report NoMediaDevices instead — confirm file:// is treated as secure.
    assert without.get("secure") is True, without

    # No audio-input device → getUserMedia fails with NotFoundError no
    # matter the permission, so the gate can't be discriminated. Skip
    # rather than fail (CI provisions a virtual mic; dev boxes may not).
    if without.get("error") == "NotFoundError":
        pytest.skip(f"no audio input device to exercise the permission gate: {without}")

    # The bug: without the handler, WebKit default-denies -> NotAllowedError.
    assert without.get("error") == "NotAllowedError", without
    # The fix: with the production handler connected, the request is granted
    # and getUserMedia no longer hits the permission wall (it resolves, or
    # at worst fails for a non-permission reason).
    assert with_handler.get("error") != "NotAllowedError", with_handler
