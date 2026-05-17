# ADR-0007: Manual widget positioning rather than vcl.builder XML

**Status:** Accepted
**Date:** 2026-05-17
**Phase:** A

## Context

The sidebar panel must lay out widgets (label, composer, history list,
buttons). LibreOffice / UNO offers two layout approaches:

1. **Manual positioning** — create widgets via
   `Toolkit.createWindow(WindowDescriptor)`, call `setPosSize(x, y, w,
   h, …)` on each, listen for parent-resize events and reflow.
2. **`vcl.builder` XML** — author a `.ui` GTK Builder XML file (the
   format used by LibreOffice's own dialogs), load via
   `com.sun.star.awt.ContainerWindowProvider`. Provides a real layout
   engine (boxes, grids, expand/fill rules).

## Decision

Use **manual positioning** for the sidebar panel in
`src/talk2view_writer/ui/sidebar_panel.py`. A `_PanelResizeListener`
watches the parent window and re-runs `_layout_children()` on every
resize.

This applies to **the sidebar panel only**. Future dialogs (login,
settings) may use `vcl.builder` XML if their layouts grow complex —
that decision is local to each dialog.

## Alternatives considered

- **`vcl.builder` XML for the panel.** The format is well-defined and
  gives us real layout. But:
  - LibreOffice expects builder files inside specific resource
    locations; extensions can ship them but the loading is documented
    poorly outside the LibreOffice source.
  - Editing UI in the GTK XML format with no Glade-equivalent for
    LibreOffice's widget set is friction-heavy.
  - The panel layout is simple (vertical stack: history + composer
    + button row) — overkill for one column of widgets.
- **Build a thin Python layout library** (a stack/box helper that
  positions children automatically). Reasonable, but a Phase 6+
  refactor once we have 2-3 panels to share it across. Not Phase A.

## Consequences

**Pros**
- Zero new file formats. All UI code is Python and live-debuggable.
- Works in any LibreOffice 7.0+ without dependency on `vcl.builder`
  internals.
- The panel's layout is deterministic and easy to reason about.

**Cons**
- Verbose — every widget requires create, set model, add, position,
  reflow on resize.
- No automatic baseline alignment, no proper padding model — pixel
  values are baked in (`_PADDING`, `_LABEL_HEIGHT`, `_BUTTON_HEIGHT`).
  Will not respect users' platform-level font scaling perfectly.
- HiDPI/DPI scaling is the parent toolkit's responsibility — we use
  device-independent pixels but boundary cases (very small widths,
  RTL) need testing.

**Follow-up**
- Phase B adds the chat history widget (likely a multiline edit field
  or rich text). If the layout becomes more than 4 widgets we revisit
  this ADR.
- Long-term: extract a `BoxLayout(children, padding=…)` helper if
  reused across panels.

## References

- Code: `src/talk2view_writer/ui/sidebar_panel.py::Talk2ViewPanel._layout_children`
- Code: `src/talk2view_writer/ui/sidebar_panel.py::_PanelResizeListener`
- Related ADRs: ADR-0003 (sidebar deck)
