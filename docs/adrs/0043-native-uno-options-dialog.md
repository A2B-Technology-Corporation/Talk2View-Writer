# ADR-0043: Native UNO Options dialog for preference toggles

**Status:** Accepted
**Date:** 2026-06-16
**Phase:** G
**Supersedes:** —
**Superseded by:** —

## Context

ADR-0035 introduced user-facing behaviour toggles (today just
`ai_track_changes_enabled`), persisted by
`talk2view_writer.preferences.Preferences` and changeable only through the
chat via the `manage_preferences` tool. That surface has two gaps:

1. **Discoverability.** A user who wants to flip a setting has to know to
   ask the AI in prose ("turn off AI track changes"). There is no visible
   list of what settings exist or their current state.
2. **The caching gotcha.** `Preferences` caches the JSON file in memory on
   first read (`_load_locked`). Editing `preferences.json` by hand while
   soffice is running has no effect — the live process keeps the cached
   value until restart. A real user hit exactly this: track changes had
   been turned off, every `search_document` ran with `track_changes=False`,
   and there was no in-app way to see or fix it short of the chat command.

The extension already opens native UNO dialogs from the Talk2View menu for
About and License (`about.py`, dispatched by the `vnd.com.talk2view.writer:`
ProtocolHandler), so the pattern for a menu-driven modal is established and
proven.

## Decision

We will add an **Options** item to the Talk2View menu that opens a native
UNO modal dialog (`talk2view_writer.options.show_options`) listing every
boolean preference as a checkbox, labelled from a new `PREFERENCE_SPECS`
metadata table in `preferences.py`. On **OK**, changed checkboxes are
written back through the `Preferences` singleton's `set()`, which updates
the in-memory cache and the file — so a toggle takes effect on the *next*
AI edit without restarting LibreOffice. The dialog is data-driven over
`DEFAULTS`, so future preferences appear automatically once they have a
spec. The row builder (`build_options_rows`) is pure (no UNO) and
unit-tested; rendering is verified manually, as with About.

The legacy `settings` dispatch URL (pre-ADR-0030 profiles) is repointed
from "open the chat window" to this dialog; `login`/`logout` still funnel
to the chat window where auth lives.

## Alternatives considered

- **Add a Settings page to the pywebview chat app** — the chat UI (ADR-0030)
  is the other natural home. Rejected for now: it only works while the chat
  window is open and authenticated, and these are LibreOffice-side
  behaviour toggles (they gate the UNO track-changes envelope), not engine
  concerns. A native dialog is reachable from the menu regardless of chat
  state and writes the same cached store.
- **Use LibreOffice's Tools > Options extension page** (an `OptionsDialog`
  node in `.xcu` + an `XContainerWindowEventHandler`) — the "proper" home
  for extension settings. Rejected as heavier than warranted for one
  toggle: it needs an extra registered UNO service and a dialog-resource
  `.xdl`, versus reusing the already-proven `about.py` `UnoControlDialog`
  pattern. Revisit if the preference set grows large.
- **Do nothing (chat-only)** — leaves both gaps above, including the
  silent caching trap that already confused a user.

## Consequences

- **Pros** — settings are discoverable and visible; toggles apply live
  (no restart, no chat round-trip); the dialog grows automatically with
  `DEFAULTS`; reuses the established menu + dialog plumbing; the chat tool
  and the dialog share one store so they never disagree.
- **Cons** — a second write surface for the same preferences (kept
  consistent by both going through the singleton); checkbox-only, so a
  future non-boolean preference needs a new control type (the row builder
  skips non-bools with a warning and the unit tests assert every key has a
  spec, so this fails loudly rather than silently).
- **Follow-up** — if settings multiply or gain non-boolean types, migrate
  to the Tools > Options extension page.

## References

- Code: `src/talk2view_writer/options.py`,
  `src/talk2view_writer/preferences.py` (`PREFERENCE_SPECS`),
  `extension/talk2view_writer.py` (`dispatch`, `options`/`settings`),
  `extension/Addons.xcu` (menu item `m2`)
- Tests: `tests/unit/test_options.py`
- Related ADRs: ADR-0035 (track-changes preference), ADR-0030 (chat in
  pywebview), and the About/License dialog precedent
- External: `com.sun.star.awt.UnoControlDialogModel`,
  `com.sun.star.awt.UnoControlCheckBoxModel`
