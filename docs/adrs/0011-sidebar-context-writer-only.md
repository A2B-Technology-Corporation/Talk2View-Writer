# ADR-0011: Sidebar deck scoped to Writer + Writer Global docs

**Status:** Accepted
**Date:** 2026-05-17
**Phase:** A

## Context

LibreOffice's `Sidebar.xcu` declares the **`ContextList`** for each
deck and panel — a semicolon-separated list of `Application,
Context, Visibility` tuples. The application names are LibreOffice
internal:

- `WriterDocument` — `.odt` / `.docx`
- `WriterGlobalDocument` — Writer master documents (`.odm`)
- `WriterWebDocument` — Writer Web layout
- `WriterReportDocument` — Base report editor
- `CalcDocument` — `.ods`
- `ImpressDocument` — `.odp`
- `DrawDocument` — `.odg`
- `BaseDocument` — `.odb`

The tools we're porting (insert paragraph, manage styles, headers /
footers, comments, etc.) are Writer-specific UNO calls. They are
either irrelevant or actively broken in Calc / Impress / Draw / Base.

## Decision

`Sidebar.xcu` lists exactly two contexts:

```
WriterDocument, any, visible ;
WriterGlobalDocument, any, visible ;
```

Both the **deck** and the **panel** carry this filter. Users opening
Calc or Impress will not see the Talk2View deck tab.

## Alternatives considered

- **Universal (`any, any, visible`)** — shows the deck everywhere
  including Calc, where every tool would error out. Bad UX.
- **Writer + Web (`WriterDocument, WriterWebDocument`)** — Web
  layout is rarely used and most of our tools (page setup, headers,
  comments) make less sense there. Skipping for now; add if a user
  asks.
- **Per-tool context filters** — would require splitting the panel
  into Writer-only vs. cross-app variants. Not justifiable for a
  single chat surface.

## Consequences

**Pros**
- The deck appears only where it makes sense; no confused user opens
  it in Calc and types "format my numbers" hoping it works.
- One less class of error to handle in tool implementations.

**Cons**
- Users with a Writer Web doc don't get the panel. If demand emerges,
  add `WriterWebDocument` and audit tools for compatibility.
- We don't know yet whether the cloud agent's behaviour changes when
  the document type isn't Writer. If we ever broaden contexts, the
  system prompt may need a "you are operating in <doc_type>" note.

**Follow-up**
- Add a `_REGISTERED_CONTEXTS` constant in `config.py` if multiple
  files end up needing this list.
- Revisit if a customer asks for Writer Web or `WriterReportDocument`
  support.

## References

- Code: `extension/Sidebar.xcu` — both `<prop oor:name="ContextList">`
  entries
- LibreOffice source: `sfx2/source/sidebar/Context.cxx`
  (list of recognised application names)
- Related ADRs: ADR-0003 (sidebar deck)
