"""About + License Information dialogs for the Talk2View-Writer menu.

Two menu commands (``Addons.xcu`` → ``vnd.com.talk2view.writer:about`` /
``:license``) land in the ProtocolHandler's ``dispatch`` and call
:func:`show_about` / :func:`show_license` here.

Content is sourced from the Talk2View website (footer + legal pages): the
medical-device disclaimer and copyright line are reproduced verbatim. The
text/URL builders are pure (no UNO) so they unit-test without LibreOffice;
``uno`` is imported lazily inside the dialog renderers only.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from talk2view_writer import __version__
from talk2view_writer._paths import file_url_to_path
from talk2view_writer.config import EXTENSION_ID

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Content (verbatim from the Talk2View website where legal — see footer +
# /legal/privacy-policy + /legal/terms-of-use).
# ---------------------------------------------------------------------------

PRODUCT_NAME = "Talk2View for Writer"

WEBSITE_URL = "https://talk2view.com"
GITHUB_URL = "https://github.com/A2B-Technology-Corporation/Talk2View-Writer"
PRIVACY_URL = "https://talk2view.com/legal/privacy-policy"
TERMS_URL = "https://talk2view.com/legal/terms-of-use"
CONTACT_URL = "https://talk2view.com/resources/contact"

# Ordered (label, url) pairs rendered as clickable links in the About dialog.
LINKS: tuple[tuple[str, str], ...] = (
    ("Website", WEBSITE_URL),
    ("Source code (GitHub)", GITHUB_URL),
    ("Privacy, Security & Responsible AI Policy", PRIVACY_URL),
    ("Terms of Use", TERMS_URL),
    ("Contact & support", CONTACT_URL),
)

DESCRIPTION = (
    "An AI document assistant for LibreOffice Writer — chat with your "
    "document to draft, edit, format, review, and more."
)

# Footer copyright line, verbatim from the website.
COPYRIGHT = (
    "© 2026 Talk2View, Inc. / Talk2View Pty Ltd. All rights reserved. "
    "A subsidiary of A2B Technology Corporation Pty Ltd."
)

LICENSE_SUMMARY = (
    "Talk2View for Writer is free software, licensed under the "
    "Mozilla Public License 2.0 (MPL-2.0)."
)

# Medical-device disclaimer, verbatim from the website footer.
DISCLAIMER = (
    "Disclaimer: Talk2View is not cleared or approved by the FDA (U.S.), "
    "TGA (Australia), or notified bodies under the EU MDR as a medical "
    "device. It is intended for educational and research purposes only and "
    "must not be used for clinical diagnosis, treatment, or medical "
    "decision-making."
)


def build_about_text(version: str = __version__) -> str:
    """Return the About dialog body (links are rendered separately)."""
    return "\n".join(
        [
            f"Version {version}",
            "",
            DESCRIPTION,
            "",
            COPYRIGHT,
            "",
            LICENSE_SUMMARY,
            'See the "License Information" menu item for the full licence text.',
            "",
            DISCLAIMER,
        ]
    )


def _extension_root(ctx: Any) -> Path:
    """Filesystem path of the installed extension via the PIP singleton."""
    pip = ctx.getValueByName(
        "/singletons/com.sun.star.deployment.PackageInformationProvider"
    )
    url = pip.getPackageLocation(EXTENSION_ID)
    parsed = urlparse(url)
    if parsed.scheme != "file":
        raise RuntimeError(f"extension root must be a file:// URL, got {url!r}")
    return file_url_to_path(url)


def read_license_text(ctx: Any) -> str:
    """Read the bundled MPL-2.0 LICENSE; fall back to a pointer if missing."""
    path = _extension_root(ctx) / "registration" / "LICENSE"
    if not path.is_file():
        logger.warning("LICENSE not found at %s; showing fallback", path)
        return f"{LICENSE_SUMMARY}\n\nFull licence text: {GITHUB_URL}/blob/main/LICENSE\n"
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# UNO dialog rendering (lazy `uno` import — keeps the builders above testable)
# ---------------------------------------------------------------------------

_FONT_WEIGHT_BOLD = 150.0  # com.sun.star.awt.FontWeight.BOLD
_PUSHBUTTON_OK = 1  # com.sun.star.awt.PushButtonType.OK — ends execute()


def _add_control(model: Any, kind: str, name: str, **props: Any) -> Any:
    control = model.createInstance(f"com.sun.star.awt.{kind}")
    for key, value in props.items():
        setattr(control, key, value)
    model.insertByName(name, control)
    return control


def _run_dialog(ctx: Any, smgr: Any, model: Any) -> None:
    dialog = smgr.createInstanceWithContext(
        "com.sun.star.awt.UnoControlDialog", ctx
    )
    dialog.setModel(model)
    toolkit = smgr.createInstanceWithContext("com.sun.star.awt.Toolkit", ctx)
    # Parent the modal over the current document window when there is one.
    parent_peer = None
    desktop = smgr.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
    frame = desktop.getCurrentFrame()
    if frame is not None:
        parent_peer = frame.getContainerWindow()
    dialog.createPeer(toolkit, parent_peer)
    try:
        dialog.execute()
    finally:
        dialog.dispose()


def _new_dialog_model(ctx: Any, title: str, width: int, height: int) -> tuple[Any, Any]:
    smgr = ctx.ServiceManager
    model = smgr.createInstanceWithContext(
        "com.sun.star.awt.UnoControlDialogModel", ctx
    )
    model.Title = title
    model.Width = width
    model.Height = height
    return smgr, model


def _build_about_model(ctx: Any) -> tuple[Any, Any]:
    """Construct the About dialog model + controls (no peer, no execute)."""
    width, height = 250, 250
    smgr, model = _new_dialog_model(ctx, "About Talk2View", width, height)

    _add_control(
        model,
        "UnoControlFixedTextModel",
        "title",
        PositionX=10,
        PositionY=8,
        Width=width - 20,
        Height=14,
        Label=PRODUCT_NAME,
        FontHeight=13.0,
        FontWeight=_FONT_WEIGHT_BOLD,
    )
    _add_control(
        model,
        "UnoControlFixedTextModel",
        "body",
        PositionX=10,
        PositionY=26,
        Width=width - 20,
        Height=120,
        Label=build_about_text(),
        MultiLine=True,
    )
    _add_control(
        model,
        "UnoControlFixedTextModel",
        "more",
        PositionX=10,
        PositionY=150,
        Width=width - 20,
        Height=10,
        Label="More information:",
        FontWeight=_FONT_WEIGHT_BOLD,
    )
    link_y = 162
    for index, (label, url) in enumerate(LINKS):
        _add_control(
            model,
            "UnoControlFixedHyperlinkModel",
            f"link{index}",
            PositionX=10,
            PositionY=link_y,
            Width=width - 20,
            Height=10,
            Label=label,
            URL=url,
        )
        link_y += 11
    _add_control(
        model,
        "UnoControlButtonModel",
        "ok",
        PositionX=width - 60,
        PositionY=height - 20,
        Width=50,
        Height=14,
        Label="Close",
        PushButtonType=_PUSHBUTTON_OK,
        DefaultButton=True,
    )
    return smgr, model


def show_about(ctx: Any) -> None:
    """Show the modal "About Talk2View" dialog."""
    logger.info("show_about: opening About dialog (version %s)", __version__)
    smgr, model = _build_about_model(ctx)
    _run_dialog(ctx, smgr, model)


def _build_license_model(ctx: Any, text: str) -> tuple[Any, Any]:
    """Construct the License dialog model + controls (no peer, no execute)."""
    width, height = 340, 360
    smgr, model = _new_dialog_model(
        ctx, "Talk2View — License Information", width, height
    )
    _add_control(
        model,
        "UnoControlFixedTextModel",
        "summary",
        PositionX=8,
        PositionY=6,
        Width=width - 16,
        Height=20,
        Label=f"{LICENSE_SUMMARY} The full licence text follows.",
        MultiLine=True,
    )
    _add_control(
        model,
        "UnoControlEditModel",
        "license",
        PositionX=8,
        PositionY=30,
        Width=width - 16,
        Height=height - 56,
        Text=text,
        MultiLine=True,
        ReadOnly=True,
        VScroll=True,
        AutoVScroll=True,
    )
    _add_control(
        model,
        "UnoControlButtonModel",
        "ok",
        PositionX=width - 60,
        PositionY=height - 20,
        Width=50,
        Height=14,
        Label="Close",
        PushButtonType=_PUSHBUTTON_OK,
        DefaultButton=True,
    )
    return smgr, model


def show_license(ctx: Any) -> None:
    """Show the modal "License Information" dialog with the full MPL-2.0 text."""
    logger.info("show_license: opening License dialog")
    text = read_license_text(ctx)
    smgr, model = _build_license_model(ctx, text)
    _run_dialog(ctx, smgr, model)
