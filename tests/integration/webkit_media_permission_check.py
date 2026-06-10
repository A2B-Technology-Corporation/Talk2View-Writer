#!/usr/bin/env python3
"""Real-WebKitGTK probe for the microphone (getUserMedia) permission fix.

Runs under the SYSTEM Python (the one with PyGObject + WebKit2 typelibs —
the same interpreter the production pywebview subprocess uses), NOT the uv
venv. Builds a live ``WebKit2.WebView``, loads a ``file://`` page that calls
``navigator.mediaDevices.getUserMedia({audio: true})``, and prints a single
``RESULT=<json>`` line describing the outcome.

It imports the *production* handler
``talk2view_writer.web_runner._grant_media_permission`` (duck-typed, so it
imports without ``gi``) and connects it exactly as
``_patch_webkitgtk_media_permission`` does — so a green run exercises the
real code path, not a replica.

Usage::

    PYTHONPATH=src python3 tests/integration/webkit_media_permission_check.py [--grant]

``--grant`` connects the permission handler (the fix); omitting it reproduces
the unpatched default-deny. Exit code is always 0 — the pass/fail logic lives
in the pytest wrapper (``test_webkit_media_permission.py``), which runs this
twice and asserts the handler flips ``NotAllowedError`` to allowed. Output
line: ``RESULT={"error": "NotAllowedError", "secure": true}`` or
``RESULT={"ok": true, "tracks": 1, "secure": true}``.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import gi  # type: ignore[import-not-found]

gi.require_version("Gtk", "3.0")
try:
    gi.require_version("WebKit2", "4.1")
except ValueError:
    gi.require_version("WebKit2", "4.0")
from gi.repository import GLib, Gtk, WebKit2  # type: ignore[import-not-found]  # noqa: E402

# Import the real production handler. The repo root is two levels up
# (tests/integration/ -> repo). src/ holds the package.
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "src"))
from talk2view_writer.web_runner import _grant_media_permission  # noqa: E402

_PAGE = """<!doctype html><meta charset="utf-8"><title>mic</title>
<script>
function report(o){ window.webkit.messageHandlers.t2v.postMessage(JSON.stringify(o)); }
window.addEventListener('load', async () => {
  const secure = window.isSecureContext;
  try {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      report({error: 'NoMediaDevices', secure}); return;
    }
    const stream = await navigator.mediaDevices.getUserMedia({audio: true});
    report({ok: true, tracks: stream.getAudioTracks().length, secure});
  } catch (e) {
    report({error: (e && e.name) ? e.name : String(e), secure});
  }
});
</script>"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--grant",
        action="store_true",
        help="connect the production permission handler (the fix)",
    )
    parser.add_argument("--timeout", type=int, default=25)
    args = parser.parse_args()

    manager = WebKit2.UserContentManager()
    manager.register_script_message_handler("t2v")
    webview = WebKit2.WebView.new_with_user_content_manager(manager)

    props = webview.get_settings().props
    props.enable_media_stream = True

    if args.grant:
        # Exactly what _patch_webkitgtk_media_permission connects.
        webview.connect("permission-request", _grant_media_permission)

    result: dict[str, object] = {"error": "timeout", "secure": None}

    def on_message(_mgr: object, js_result: object) -> None:
        nonlocal result
        try:
            value = js_result.get_js_value().to_string()  # type: ignore[attr-defined]
            result = json.loads(value)
        except Exception as exc:
            result = {"error": f"probe-parse-failure: {exc}", "secure": None}
        Gtk.main_quit()

    manager.connect("script-message-received::t2v", on_message)

    def on_timeout() -> bool:
        Gtk.main_quit()
        return False

    GLib.timeout_add_seconds(args.timeout, on_timeout)

    win = Gtk.Window()
    win.set_default_size(480, 320)
    win.add(webview)
    win.show_all()

    # Load from a real file:// origin so isSecureContext is true (file://
    # is a secure context for getUserMedia; an insecure origin would make
    # navigator.mediaDevices undefined instead).
    with tempfile.NamedTemporaryFile(
        "w", suffix=".html", delete=False, encoding="utf-8"
    ) as fh:
        fh.write(_PAGE)
        html_path = fh.name
    webview.load_uri(f"file://{html_path}")

    Gtk.main()
    Path(html_path).unlink(missing_ok=True)

    print(f"RESULT={json.dumps(result)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
