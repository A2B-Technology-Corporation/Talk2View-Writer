"""Minimal settings dialog (Phase F).

Currently a read-only status panel showing:

  * partner key (masked) + base URL,
  * current auth state (email if logged in),
  * a path to where the bundled SYSTEM_PROMPT.md was loaded from,
  * version + license link.

Future iterations will add a model picker and user-overridable
preferences (font size, history limit). The dialog is intentionally
read-only for now so we don't have to design persistent settings
storage in Phase F.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from talk2view_writer import config
from talk2view_writer.system_prompt import load_system_prompt

if TYPE_CHECKING:
    from com.sun.star.awt import XWindow
    from com.sun.star.uno import XComponentContext

    from talk2view_writer.sdk_client import Talk2ViewSDKClient

logger = logging.getLogger(__name__)


def _mask_partner_key(key: str) -> str:
    """Show only the prefix + last 4 chars of the partner key."""
    if len(key) < 12:
        return "***"
    return f"{key[:8]}…{key[-4:]}"


def _build_status_text(sdk: Talk2ViewSDKClient) -> str:
    prompt = load_system_prompt()
    prompt_status = (
        f"loaded ({len(prompt)} chars)" if prompt is not None else "engine default"
    )
    user = sdk.current_user if sdk.is_authenticated() else None
    auth_line = (
        f"Logged in as: {user.email}"
        if user is not None
        else "Logged out — use Talk2View → Login"
    )
    # Surface the log path so users can copy-paste it into a bug
    # report or open it in a text editor without hunting through
    # XDG / AppData / Library directories.
    from talk2view_writer._logging import log_file_path

    log_path = log_file_path()
    return (
        "Talk2View-Writer\n"
        f"\n"
        f"Backend: {config.BASE_URL}\n"
        f"Partner key: {_mask_partner_key(config.PARTNER_KEY)}\n"
        f"\n"
        f"{auth_line}\n"
        f"\n"
        f"System prompt: {prompt_status}\n"
        f"\n"
        f"Log file: {log_path}\n"
        f"(Attach this file to bug reports. Set T2V_WRITER_DEBUG=1 in\n"
        f"the environment before launching LibreOffice for verbose logs.)\n"
        f"\n"
        f"Settings UI is read-only in this build."
    )


def show_settings_dialog(
    ctx: XComponentContext,
    sdk: Talk2ViewSDKClient,
    parent_window: XWindow | None = None,
) -> None:
    """Pop a read-only status box.

    Uses the toolkit's ``MessageBox`` factory so we don't need to
    construct a full ``UnoControlDialog`` for what is effectively a
    text dump. Replace with a proper dialog when the settings list
    grows.
    """
    toolkit = ctx.ServiceManager.createInstanceWithContext(
        "com.sun.star.awt.Toolkit", ctx
    )
    if parent_window is None:
        desktop = ctx.ServiceManager.createInstanceWithContext(
            "com.sun.star.frame.Desktop", ctx
        )
        frame = desktop.getCurrentFrame()
        if frame is not None:
            parent_window = frame.getContainerWindow()

    text = _build_status_text(sdk)
    box = toolkit.createMessageBox(
        parent_window,
        # MessageBoxType.INFOBOX
        0,
        # MessageBoxButtons.BUTTONS_OK
        1,
        "Talk2View Settings",
        text,
    )
    box.execute()
    logger.info("Settings dialog dismissed")
