# ADR-0035: Track changes by default for AI edits

## Status

Accepted — 2026-05-23.

## Context

Users have a global "Track Changes" setting in LibreOffice Writer
(``Edit → Track Changes → Record``). When ON, every edit — by anyone —
is recorded as a redline the user can later accept or reject. When
OFF, edits land directly in the document.

Talk2View-Writer's AI can mutate the document via 13+ tools
(insert_content, format_text, search_document, …). Treating an AI's
edits as if they were just another set of keystrokes — silently
applied with no audit trail — surprised users in early testing: they
wanted a way to see what the AI changed before committing to it,
without being forced to leave global track-changes ON for their own
typing.

The naive fix — "flip the global Track Changes toggle when the
extension starts up" — fails several ways:

1. Pollutes the user's own typing with redlines for the entire
   session.
2. Leaks state across documents (the property is per-document but
   we'd persist it across saves).
3. If the extension crashes or is unloaded mid-session, the user's
   document is left in track-changes=True even though their preference
   was off.

We need AI edits to be tracked **without** changing the document's
overall track-changes mode.

## Decision

Wrap every mutating AI tool call in a save → enable → restore envelope
on the document's ``RecordChanges`` property:

```
prior = doc.getPropertyValue("RecordChanges")
doc.setPropertyValue("RecordChanges", True)
try:
    run_tool()
finally:
    doc.setPropertyValue("RecordChanges", bool(prior))
```

The envelope lives in
``talk2view_writer/tools/_base.py``'s ``ui_thread_tool`` decorator,
inside the same UI-thread marshal call as the tool body. A
``_MUTATING_TOOL_NAMES`` frozenset enumerates which tools get the
envelope; read-only tools (``get_document``, ``get_selection``) and
state-restoring tools (``undo_redo``) skip it.

Gated by a new user preference, ``ai_track_changes_enabled``, default
**True**. Stored in
``$XDG_CONFIG_HOME/talk2view-writer/preferences.json`` (Linux);
parallel paths on macOS / Windows. The chat-surface
``manage_preferences`` tool lets the user toggle it via natural-
language requests ("turn off AI track changes").

The preference is a separate file from ``tokens.json`` so wiping one
doesn't affect the other.

## Alternatives considered

### A. Global Track Changes flip

Flip ``RecordChanges`` on at session start, restore at session end.
**Rejected** because user's own typing gets tracked too, and a
crashed session leaves the doc in the wrong state.

### B. Per-tool ``@track_changes`` decorator

Each mutating tool declares ``@track_changes`` explicitly. **Rejected**
because every new tool needs to remember to opt in — too easy to ship
a mutating tool that silently bypasses redlining. The
``_MUTATING_TOOL_NAMES`` set is centrally reviewable.

### C. Wrap inside ``bridge_server._invoke_tool``

Move the envelope up into the bridge dispatch layer rather than the
tool decorator. **Rejected** because the dispatch layer doesn't run on
the UI thread, and toggling ``RecordChanges`` is a UNO call. Doing it
inside ``ui_thread_tool`` keeps the marshal close to the toggle.

### D. Make the user opt in (default False)

Conservative — only flip if the user has explicitly enabled it.
**Rejected** based on the user goal of "gold-standard
word-processor copilot": a copilot whose edits sneak in unannounced
is a worse default than one whose edits are visible and reviewable.

## Consequences

### Positive

- AI edits are visible, reviewable, rejectable through the standard
  LibreOffice Track Changes UI.
- The user's own typing retains whatever track-changes mode they had
  set globally.
- A crashed tool call still restores the prior value via the
  ``finally`` block.
- Users who don't want redlines can ask the AI: "turn off AI track
  changes" → ``manage_preferences(action="set",
  key="ai_track_changes_enabled", value=false)``. Persists across
  sessions.

### Negative

- A tool's edit is "framed" by track-changes regardless of whether
  the tool actually mutates anything. ``search_document`` in
  count-only mode (no ``replace_with``) is currently wrapped
  unnecessarily — harmless but slightly wasteful (two extra UNO
  property writes per call). If this matters we can refine the
  decision per-call.
- The preference is process-wide, not per-document. A user who wants
  redlines for some docs and not others has to toggle every time.
  Likely acceptable for v1; revisit if real users ask for
  per-document.
- If a user opens a doc that already has ``RecordChanges=True`` and
  the AI then runs a tool, the restore is to True — no behaviour
  change. But the AI's edits will appear with the AI's name
  (whatever soffice has as the editing identity) rather than the
  user's. Document this in the system prompt eventually.

### Operational

- New preference file: ``preferences.json`` alongside ``tokens.json``.
- New tool: ``manage_preferences`` — added to the MVP allowlist.
- The set of tools the chat surfaces grew from 5 to 7
  (manage_preferences + format_paragraph). format_paragraph was
  already implemented; only its schema in tools.ts is new.

## Links

- ``src/talk2view_writer/preferences.py`` — the storage module.
- ``src/talk2view_writer/tools/preferences_tool.py`` — the
  ``manage_preferences`` tool body.
- ``src/talk2view_writer/tools/_base.py:_run_with_track_changes`` —
  the envelope.
- Investigation #35 — the precedent for schema-vs-signature contract
  tests (we added one for format_text in this work).
