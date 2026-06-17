"""Options dialog for the Talk2View-Writer menu.

The menu command ``Addons.xcu`` -> ``vnd.com.talk2view.writer:options``
lands in the ProtocolHandler's ``dispatch`` and calls :func:`show_options`
here. The dialog renders one checkbox per boolean preference declared in
:data:`talk2view_writer.preferences.DEFAULTS` (labelled from
:data:`talk2view_writer.preferences.PREFERENCE_SPECS`) and persists any
changes through the :class:`~talk2view_writer.preferences.Preferences`
singleton on OK.

Persisting through the singleton matters: ``Preferences`` caches the
file's contents in memory on first read, so a setting changed here takes
effect for the *next* AI edit without restarting LibreOffice — unlike
hand-editing ``preferences.json`` while soffice is running. This is the
same store the ``manage_preferences`` chat tool writes to (ADR-0035), so
the menu dialog and the chat stay in sync.

The row builder (:func:`build_options_rows`) is pure (no UNO) so it
unit-tests without LibreOffice; ``uno`` is imported lazily inside the
dialog renderer only, mirroring :mod:`talk2view_writer.about`.

See ADR-0043.
"""

from __future__ import annotations

import logging
from typing import Any, NamedTuple

from talk2view_writer.preferences import (
    DEFAULTS,
    PREFERENCE_SPECS,
    Preferences,
    get_preferences,
)

logger = logging.getLogger(__name__)


class OptionRow(NamedTuple):
    """One boolean preference, resolved for display in the Options dialog."""

    key: str
    label: str
    description: str
    value: bool
    default: bool


def build_options_rows(prefs: Preferences | None = None) -> list[OptionRow]:
    """Resolve every boolean preference into a display row.

    Pure apart from reading the preferences store, so it is unit-testable
    without UNO. Iterates :data:`DEFAULTS` (insertion-ordered) so the
    dialog's control order is stable and intentional. Non-boolean
    preferences are skipped with a warning — the checkbox dialog only
    knows how to render booleans, and adding a non-bool preference should
    consciously add a matching control type rather than silently appear
    broken.

    Args:
        prefs: Preferences store to read from. Defaults to the process
            singleton; tests pass a tmp-path-backed instance.

    Returns:
        One :class:`OptionRow` per boolean preference, in ``DEFAULTS`` order.
    """
    store = prefs or get_preferences()
    rows: list[OptionRow] = []
    for key, default in DEFAULTS.items():
        if not isinstance(default, bool):
            logger.warning(
                "Options dialog skips non-boolean preference %r "
                "(no control type for %s)",
                key,
                type(default).__name__,
            )
            continue
        spec = PREFERENCE_SPECS.get(key)
        if spec is None:
            # Defensive: a preference with no display metadata. The unit
            # tests assert this never happens, but render a sane fallback
            # rather than crash the menu if it slips through.
            logger.warning("Preference %r has no PreferenceSpec; using key as label", key)
            label, description = key, ""
        else:
            label, description = spec.label, spec.description
        rows.append(
            OptionRow(
                key=key,
                label=label,
                description=description,
                value=bool(store.get(key)),
                default=bool(default),
            )
        )
    return rows


# ---------------------------------------------------------------------------
# UNO dialog rendering (lazy `uno` import — keeps build_options_rows testable)
# ---------------------------------------------------------------------------

_FONT_WEIGHT_BOLD = 150.0  # com.sun.star.awt.FontWeight.BOLD
_PUSHBUTTON_OK = 1  # com.sun.star.awt.PushButtonType.OK
_PUSHBUTTON_CANCEL = 2  # com.sun.star.awt.PushButtonType.CANCEL
_STATE_CHECKED = 1  # com.sun.star.awt.checkbox state: 1 = checked
_EXECUTE_OK = 1  # UnoControlDialog.execute() returns 1 when OK ends it

# Layout constants (1/100 mm-ish dialog map units, matching about.py).
_WIDTH = 320
_MARGIN = 10
_CHECKBOX_HEIGHT = 12
_DESC_LINE_HEIGHT = 9
_DESC_LINES = 3
_ROW_GAP = 8


def _add_control(model: Any, kind: str, name: str, **props: Any) -> Any:
    control = model.createInstance(f"com.sun.star.awt.{kind}")
    for key, value in props.items():
        setattr(control, key, value)
    model.insertByName(name, control)
    return control


def _row_control_name(index: int) -> str:
    """Checkbox control name for the row at ``index`` (stable, queryable)."""
    return f"pref_{index}"


def _build_options_model(ctx: Any, rows: list[OptionRow]) -> tuple[Any, Any, dict[str, str]]:
    """Construct the Options dialog model + controls (no peer, no execute).

    Returns the service manager, the dialog model, and a mapping of
    checkbox control name -> preference key so the caller can read each
    box's state back after ``execute()`` and persist it.
    """
    smgr = ctx.ServiceManager
    model = smgr.createInstanceWithContext(
        "com.sun.star.awt.UnoControlDialogModel", ctx
    )
    model.Title = "Talk2View Options"
    model.Width = _WIDTH

    _add_control(
        model,
        "UnoControlFixedTextModel",
        "heading",
        PositionX=_MARGIN,
        PositionY=8,
        Width=_WIDTH - 2 * _MARGIN,
        Height=14,
        Label="Talk2View Settings",
        FontHeight=12.0,
        FontWeight=_FONT_WEIGHT_BOLD,
    )

    name_to_key: dict[str, str] = {}
    y = 28
    row_height = _CHECKBOX_HEIGHT + _DESC_LINES * _DESC_LINE_HEIGHT + _ROW_GAP
    for index, row in enumerate(rows):
        cb_name = _row_control_name(index)
        name_to_key[cb_name] = row.key
        _add_control(
            model,
            "UnoControlCheckBoxModel",
            cb_name,
            PositionX=_MARGIN,
            PositionY=y,
            Width=_WIDTH - 2 * _MARGIN,
            Height=_CHECKBOX_HEIGHT,
            Label=row.label,
            State=_STATE_CHECKED if row.value else 0,
        )
        _add_control(
            model,
            "UnoControlFixedTextModel",
            f"desc_{index}",
            PositionX=_MARGIN + 8,
            PositionY=y + _CHECKBOX_HEIGHT,
            Width=_WIDTH - 2 * _MARGIN - 8,
            Height=_DESC_LINES * _DESC_LINE_HEIGHT,
            Label=row.description,
            MultiLine=True,
        )
        y += row_height

    footer_y = y + 2
    _add_control(
        model,
        "UnoControlFixedTextModel",
        "footer",
        PositionX=_MARGIN,
        PositionY=footer_y,
        Width=_WIDTH - 2 * _MARGIN,
        Height=10,
        Label="Changes take effect immediately.",
    )

    buttons_y = footer_y + 16
    _add_control(
        model,
        "UnoControlButtonModel",
        "cancel",
        PositionX=_WIDTH - 2 * _MARGIN - 110,
        PositionY=buttons_y,
        Width=55,
        Height=14,
        Label="Cancel",
        PushButtonType=_PUSHBUTTON_CANCEL,
    )
    _add_control(
        model,
        "UnoControlButtonModel",
        "ok",
        PositionX=_WIDTH - _MARGIN - 55,
        PositionY=buttons_y,
        Width=55,
        Height=14,
        Label="OK",
        PushButtonType=_PUSHBUTTON_OK,
        DefaultButton=True,
    )
    model.Height = buttons_y + 24
    return smgr, model, name_to_key


def _persist_changes(
    model: Any, name_to_key: dict[str, str], rows: list[OptionRow], prefs: Preferences
) -> list[str]:
    """Write back any checkbox whose state differs from the stored value.

    Reads each checkbox's ``State`` from the live dialog model after
    ``execute()`` returns OK. Only changed preferences are written, so an
    unchanged dialog touches neither the cache nor the file. Returns the
    list of preference keys that were changed (for logging).

    Raises:
        OSError: From :meth:`Preferences.set` if the file cannot be written
            — surfaced rather than hidden (memory: never hide errors).
    """
    prior = {row.key: row.value for row in rows}
    changed: list[str] = []
    for cb_name, key in name_to_key.items():
        new_value = bool(model.getByName(cb_name).State == _STATE_CHECKED)
        if new_value != prior[key]:
            prefs.set(key, new_value)
            changed.append(key)
    return changed


def show_options(ctx: Any, prefs: Preferences | None = None) -> None:
    """Show the modal "Talk2View Options" dialog and persist changes on OK.

    Must run on the LibreOffice UI thread — it is called directly from the
    ProtocolHandler's ``dispatch`` (a menu command already on the UI
    thread), exactly as :func:`talk2view_writer.about.show_about` is.

    Args:
        ctx: The UNO component context.
        prefs: Preferences store to read/write. Defaults to the process
            singleton.

    Raises:
        OSError: If a changed preference cannot be persisted on OK.
    """
    store = prefs or get_preferences()
    rows = build_options_rows(store)
    logger.info("show_options: opening Options dialog (%d preference(s))", len(rows))

    smgr, model, name_to_key = _build_options_model(ctx, rows)
    dialog = smgr.createInstanceWithContext(
        "com.sun.star.awt.UnoControlDialog", ctx
    )
    dialog.setModel(model)
    toolkit = smgr.createInstanceWithContext("com.sun.star.awt.Toolkit", ctx)
    parent_peer = None
    desktop = smgr.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
    frame = desktop.getCurrentFrame()
    if frame is not None:
        parent_peer = frame.getContainerWindow()
    dialog.createPeer(toolkit, parent_peer)
    try:
        result = dialog.execute()
        if result == _EXECUTE_OK:
            # Read state from the dialog model BEFORE dispose() tears it down.
            changed = _persist_changes(model, name_to_key, rows, store)
            logger.info(
                "show_options: OK — %d preference(s) changed: %s",
                len(changed),
                changed,
            )
        else:
            logger.info("show_options: cancelled — no changes persisted")
    finally:
        dialog.dispose()
