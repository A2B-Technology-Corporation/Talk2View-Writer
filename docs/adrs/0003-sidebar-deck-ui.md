# ADR-0003: Sidebar deck as the primary UI surface

**Status:** Accepted
**Date:** 2026-05-17
**Phase:** A

## Context

Talk2View-Writer needs a persistent chat panel inside LibreOffice
Writer. Available UI surfaces:

1. **Sidebar deck** — the right-hand collapsible pane that hosts
   built-in panels (Properties, Styles, Gallery, …). Always visible
   when opened, dockable, native L&F.
2. **Modeless floating dialog** — standalone window the user toggles.
   Simpler implementation (well-trodden in SpeedWriter) but less
   integrated.
3. **Embedded webview** — Qt WebEngine inside LibreOffice, reusing
   Talk2View-Word's React UI. Highest fidelity, heaviest runtime,
   biggest deviation from native UNO patterns.
4. **Menu-driven only** — like SpeedWriter today: top-level menu with
   per-command transient dialogs. No persistent chat.

The user selected option 1 explicitly. This ADR records that decision
and the implementation approach.

## Decision

We register a new **Sidebar deck** "Talk2View" via
`extension/Sidebar.xcu` with one panel "Chat". The panel UI is built
in `src/talk2view_writer/ui/sidebar_panel.py` as a UNO `XUIElement`
returned from a `ChatPanelFactory` (an `XUIElementFactory`
implementation registered in `extension/talk2view_writer.py`).

The deck is contextualised to `WriterDocument` and
`WriterGlobalDocument` (see ADR-0011); it will not appear in Calc,
Impress, Draw, or Base.

## Alternatives considered

- **Modeless dialog** — would have been faster to ship (we have a
  blueprint in `SpeedWriter-LibreOffice/src/speedwriter/ui/dialog.py`)
  but a floating window for a continuously-used chat is poor UX —
  hides the document, doesn't dock, breaks on multi-monitor
  multi-window flows.
- **Webview** — would let us reuse Talk2View-Word's React components
  unchanged, but pulls in a Qt WebEngine dependency (~50 MB) and
  introduces JS↔Python bridging just to mutate UNO. Heavy for the
  benefit.
- **Menu-only** — incompatible with a chat-style interaction where
  the user is iterating ("rewrite that paragraph more formally").

## Consequences

**Pros**
- Native L&F — the panel docks, resizes, and themes with the rest of
  LibreOffice.
- Always-visible when opened, so the user doesn't lose context.
- No webview runtime; minimal install footprint.

**Cons**
- **New ground for this codebase.** SpeedWriter explicitly deferred
  the sidebar deck (their `ROADMAP.md` Phase 5). No working example
  to copy from. First-attempt registration may need iteration.
- Manual UI layout. UNO toolkit widgets are verbose to position; see
  ADR-0007.
- Sidebar panel widths are constrained (~250-320 px typically);
  rich-text history and large composer fields need careful sizing.

**Follow-up**
- Phase B replaces the Phase A placeholder layout with a real chat
  history + composer.
- Investigation: the Sidebar.xcu `IconURL` currently borrows an
  internal LibreOffice icon (`private:graphicrepository/sw/res/...`)
  for the deck tab. Replace with a branded Talk2View icon —
  see `docs/investigations.md` #4.

## References

- Code: `extension/Sidebar.xcu`,
  `extension/talk2view_writer.py::ChatPanelFactory`,
  `src/talk2view_writer/ui/sidebar_panel.py`
- LibreOffice docs: [Sidebar API](https://wiki.documentfoundation.org/Development/Sidebar)
  (sparse — most knowledge comes from reading
  `sfx2/source/sidebar/` in the LibreOffice source tree)
- Related ADRs: ADR-0004 (factory wiring), ADR-0007 (layout), ADR-0011
  (context filter)
