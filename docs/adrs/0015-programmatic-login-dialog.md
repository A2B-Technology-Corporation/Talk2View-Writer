# ADR-0015: Login dialog built programmatically (not `.xdl`)

**Status:** Accepted
**Date:** 2026-05-17
**Phase:** B

## Context

The login dialog needs two text fields (email, password), two buttons
(Cancel, Log in), and modal behaviour. LibreOffice supports two ways
to build dialogs:

1. **Programmatic** — construct an `UnoControlDialogModel`, insert
   child models for each control, set positions explicitly, call
   `dialog.execute()`.
2. **XDL file** — author an XML dialog file (`Dialog.xdl`) and load
   via `com.sun.star.awt.DialogProvider2.createDialog(URL)`. Designed
   in LibreOffice's built-in Basic IDE Dialog Editor.

## Decision

Build the login dialog **programmatically** in
`src/talk2view_writer/ui/login_dialog.py::show_login_dialog`.

Specifically: use `PushButtonType` (1 = OK, 2 = CANCEL) on the
buttons. This lets LibreOffice handle the dialog's OK/Cancel return
codes natively — `dialog.execute()` returns `1` for OK, `0` for
Cancel — without writing action listener glue.

## Alternatives considered

- **XDL file.** Cleaner for complex dialogs but heavier for a 4-widget
  form. Also requires shipping a `.xdl` resource inside the `.oxt`
  and either loading via `vnd.sun.star.script:` URLs (fragile) or
  loading from a file path relative to the extension (works but adds
  packaging boilerplate).
- **Programmatic with manual ActionListener wiring** instead of
  PushButtonType. Wordier; we'd have to track an OK/Cancel state
  variable. PushButtonType is the documented pattern for OK/Cancel
  semantics.
- **`MessageBox` with two text fields** — no, `MessageBox` doesn't
  support arbitrary inputs.

## Consequences

**Pros**
- All UI code in one Python file; no extra resource to ship in `.oxt`.
- Live-editable without restarting LibreOffice (just `make build`).
- Easy to add validation later without context-switching to an XML
  editor.

**Cons**
- Layout values are pixel constants — coordinate-based positioning
  doesn't reflow with font size or RTL.
- Limited styling — no rich text, no inline error states. If we want
  "✗ Incorrect password" inline, we'd extend with a hidden label
  that we toggle.
- Larger dialogs (Settings, Preferences) get awkward fast. We will
  revisit this ADR for Settings in Phase F.

**Follow-up**
- Phase F adds a Settings dialog. If that dialog has more than 6-8
  widgets or any custom controls, consider XDL with a follow-up
  ADR.
- Add inline error labels under each field for validation feedback.
- Add a "Show password" eye-toggle button.

## References

- Code: `src/talk2view_writer/ui/login_dialog.py`
- Constants: `PushButtonType` is defined in
  `com.sun.star.awt.PushButtonType` (1 = OK, 2 = CANCEL, 3 = HELP).
- Related ADRs: ADR-0007 (manual layout), ADR-0014 (token storage)
