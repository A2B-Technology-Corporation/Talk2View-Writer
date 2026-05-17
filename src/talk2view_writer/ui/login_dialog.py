"""Login dialog — programmatic UNO ``UnoControlDialog`` with email + password.

Phase B keeps the dialog deliberately minimal: two text fields, two
buttons, no signup link. Phase F may replace this with a `.xdl`
Dialog Editor file if we add password recovery / signup flows.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional, Tuple

import uno  # type: ignore[import-not-found]
import unohelper  # type: ignore[import-not-found]
from com.sun.star.awt import XActionListener  # type: ignore[import-not-found]

if TYPE_CHECKING:
    from com.sun.star.awt import ActionEvent, XWindow
    from com.sun.star.lang import EventObject
    from com.sun.star.uno import XComponentContext

logger = logging.getLogger(__name__)

_DIALOG_WIDTH = 200  # awt "map units" (~1/2 mm)
_DIALOG_HEIGHT = 110
_FIELD_HEIGHT = 12
_BUTTON_HEIGHT = 14
_LABEL_HEIGHT = 10
_MARGIN = 8


def show_login_dialog(
    ctx: "XComponentContext",
    parent_window: "Optional[XWindow]" = None,
    *,
    initial_email: str = "",
) -> Optional[Tuple[str, str]]:
    """Show the login dialog and return ``(email, password)`` or ``None``.

    Returns ``None`` if the user cancels. The caller is responsible for
    passing the credentials to ``Talk2ViewSDKClient.login`` and
    surfacing the resulting error (or success).
    """
    dialog_model = ctx.ServiceManager.createInstanceWithContext(
        "com.sun.star.awt.UnoControlDialogModel", ctx
    )
    dialog_model.setPropertyValues(
        ("Width", "Height", "Title"),
        (_DIALOG_WIDTH, _DIALOG_HEIGHT, "Talk2View — Log in"),
    )

    # Helper: add a child control to the dialog model.
    def add_control(
        kind: str,
        name: str,
        x: int,
        y: int,
        w: int,
        h: int,
        properties: dict,
    ) -> object:
        model = dialog_model.createInstance(
            f"com.sun.star.awt.UnoControl{kind}Model"
        )
        model.setPropertyValues(
            tuple(["Name", "PositionX", "PositionY", "Width", "Height"]
                  + list(properties.keys())),
            tuple([name, x, y, w, h] + list(properties.values())),
        )
        dialog_model.insertByName(name, model)
        return model

    # Email label + field.
    add_control(
        "FixedText", "lbl_email", _MARGIN, _MARGIN,
        _DIALOG_WIDTH - 2 * _MARGIN, _LABEL_HEIGHT,
        {"Label": "Email"},
    )
    add_control(
        "Edit", "txt_email",
        _MARGIN, _MARGIN + _LABEL_HEIGHT,
        _DIALOG_WIDTH - 2 * _MARGIN, _FIELD_HEIGHT,
        {"Text": initial_email},
    )

    # Password label + field.
    y_pass = _MARGIN + _LABEL_HEIGHT + _FIELD_HEIGHT + _MARGIN // 2
    add_control(
        "FixedText", "lbl_password", _MARGIN, y_pass,
        _DIALOG_WIDTH - 2 * _MARGIN, _LABEL_HEIGHT,
        {"Label": "Password"},
    )
    add_control(
        "Edit", "txt_password",
        _MARGIN, y_pass + _LABEL_HEIGHT,
        _DIALOG_WIDTH - 2 * _MARGIN, _FIELD_HEIGHT,
        # EchoChar 42 = "*" — UNO uses a 16-bit code-point integer here.
        {"EchoChar": 42, "Text": ""},
    )

    # Cancel + Log in buttons.
    y_btn = _DIALOG_HEIGHT - _BUTTON_HEIGHT - _MARGIN
    btn_w = (_DIALOG_WIDTH - 3 * _MARGIN) // 2
    cancel_model = add_control(
        "Button", "btn_cancel",
        _MARGIN, y_btn, btn_w, _BUTTON_HEIGHT,
        # PushButtonType.CANCEL = 2 — UNO returns 0 from execute() for cancel.
        {"Label": "Cancel", "PushButtonType": 2},
    )
    login_model = add_control(
        "Button", "btn_login",
        _MARGIN + btn_w + _MARGIN, y_btn, btn_w, _BUTTON_HEIGHT,
        # PushButtonType.OK = 1 — execute() returns 1 for OK.
        {"Label": "Log in", "PushButtonType": 1, "DefaultButton": True},
    )
    _ = (cancel_model, login_model)  # holding references is not required

    # Instantiate the dialog from the model and run it modally.
    dialog = ctx.ServiceManager.createInstanceWithContext(
        "com.sun.star.awt.UnoControlDialog", ctx
    )
    dialog.setModel(dialog_model)

    toolkit = ctx.ServiceManager.createInstanceWithContext(
        "com.sun.star.awt.Toolkit", ctx
    )
    if parent_window is not None:
        dialog.createPeer(toolkit, parent_window)
    else:
        dialog.createPeer(toolkit, None)

    # PushButtonType wiring above handles OK/Cancel return codes.
    result = dialog.execute()
    logger.info("Login dialog execute() returned %s", result)
    try:
        if result != 1:  # 1 = OK, 0 = cancelled
            return None
        email = dialog.getControl("txt_email").getModel().getPropertyValue("Text")
        password = dialog.getControl("txt_password").getModel().getPropertyValue(
            "Text"
        )
        if not email or not password:
            logger.info("Login dialog OK pressed with empty fields")
            return None
        return (str(email), str(password))
    finally:
        dialog.dispose()


# ---------------------------------------------------------------------------
# Action forwarder (currently unused — kept for Phase F when we add a
# "Forgot password" link or live validation. Programmatic OK/Cancel via
# PushButtonType is sufficient for Phase B.)
# ---------------------------------------------------------------------------


class _ActionForwarder(unohelper.Base, XActionListener):
    """Forward UNO action events to a Python callable."""

    def __init__(self, callback) -> None:
        self._callback = callback

    def actionPerformed(self, event: "ActionEvent") -> None:  # noqa: N802, ARG002
        self._callback()

    def disposing(self, event: "EventObject") -> None:  # noqa: ARG002
        pass


_ = uno  # silence "imported but unused" — needed for createUnoStruct callers later
