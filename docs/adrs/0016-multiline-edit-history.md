# ADR-0016: Chat history rendered as a multiline `UnoControlEdit`

**Status:** Accepted
**Date:** 2026-05-17
**Phase:** B

## Context

The chat panel needs a scrolling, append-only widget that shows the
history of user turns + assistant responses + tool annotations.
Candidates in UNO:

1. **`UnoControlEdit` with `MultiLine=True, ReadOnly=True, VScroll=True`** —
   plain-text scroll buffer. We append by concatenating the new chunk
   to the existing `Text` property and re-setting it.
2. **Custom-drawn widget on a `Window`** — bespoke render loop, full
   control over per-message styling (bubbles, avatars, code blocks).
3. **WebView (HTML)** — render the history as HTML in a Qt WebEngine
   widget. Highest fidelity (Markdown, syntax highlighting), heaviest
   runtime dependency.

## Decision

Use option **1** for Phase B. The widget is created in
`Talk2ViewPanel._add_edit(...)` with `MultiLine=True, ReadOnly=True,
VScroll=True`.

Messages are appended with the prefix `"You: "` for user turns and
`"Talk2View: "` for assistant responses. Tool annotations use
`"[tool: name]"`. Errors use `"[error] ..."`.

## Alternatives considered

- **Custom-drawn widget.** Lots of effort for chat-bubble styling
  that no user will care about in v0.1. Defer until we have feedback
  asking for it.
- **WebView.** Reuses Talk2View-Word's React chat components but
  imports Qt WebEngine (≥ 50 MB) and adds JS↔Python bridging — not
  worth it for a chat history we render once-per-frame.
- **Two `FixedText` widgets per message** stacked in a scrolling
  `VerticalBox`. Closer to how a real chat UI looks but requires
  managing widget lifecycle per message and a scroll viewport we'd
  have to implement.

## Consequences

**Pros**
- Trivially shippable in Phase B.
- Streaming-friendly — appending a single token is one
  `setPropertyValue("Text", current + chunk)` call.
- Free copy-paste support, search support (Ctrl+F inside the field),
  and platform-native text rendering.

**Cons**
- **O(n) append cost** as the history grows — every appended chunk
  triggers a full re-set of the `Text` property. Fine for sub-100KB
  histories (a long chat); pathological for multi-hour sessions.
- **No styling.** No bold for `**markdown**`, no syntax highlighting,
  no distinguishing user vs. assistant by colour. Plain text only.
- **No per-message hit-testing.** "Copy this message" / "Regenerate
  from here" UX is impossible without rebuilding as discrete widgets.

**Follow-up**
- Add a "Clear chat" button in Phase F to bound the buffer size.
- If users ask for styling, revisit with WebView or per-message
  widgets. New ADR at that time.
- Consider a `MaximumTextLength` cap (e.g. 200 KB) and auto-trim from
  the top once exceeded.

## References

- Code:
  `src/talk2view_writer/ui/sidebar_panel.py::Talk2ViewPanel._add_edit`
- Code:
  `src/talk2view_writer/ui/sidebar_panel.py::Talk2ViewPanel._append_history`
- UNO docs: `com.sun.star.awt.UnoControlEditModel` properties
  (`MultiLine`, `ReadOnly`, `VScroll`, `HScroll`, `MaximumTextLength`)
- Related ADRs: ADR-0007 (manual layout), ADR-0017 (cross-thread
  updates)
