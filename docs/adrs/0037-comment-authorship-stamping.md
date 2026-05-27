# ADR-0037: Stamp author + date on AI-created comments

**Status:** Accepted
**Date:** 2026-05-27
**Phase:** G
**Supersedes:** —
**Superseded by:** —

## Context

When a human types a comment in Writer, LibreOffice auto-fills the
annotation's `Author` (from Tools › Options › User Data) and the
timestamp. The UNO `createInstance("com.sun.star.text.TextField.Annotation")`
path does **not** auto-fill either — a comment created through the API
comes out with a blank author and no date (Investigation #46).

`add_comment` (and the reply path in `manage_comment`) set only
`Content`, so every AI-created comment showed an empty author and no
date in the comment margin. A prior code comment claimed leaving them
unset "preserves Writer's normal behaviour" — that reasoning was
backwards: the API path is exactly where the auto-fill is missing.

Talk2View-Word doesn't hit this because Word's `insertComment` stamps
the signed-in Office user automatically; the gap is Writer-specific.

## Decision

We stamp every AI-created annotation (new comments **and** replies) with:

- **Author** = `"Talk2View on behalf of <LibreOffice user>"`, where the
  user name is read from `/org.openoffice.UserProfile/Data`
  (`givenname` + `sn`). When that name is unavailable we fall back to
  plain `"Talk2View"`.
- **Initials** = `"T2V"`.
- **DateTimeValue** = the current local time as a
  `com.sun.star.util.DateTime`.

Reading the user-profile name is best-effort: a stripped/headless build
that doesn't expose the configuration service logs a `warning` and falls
back to `"Talk2View"` rather than failing the comment insertion.

## Alternatives considered

- **Plain `"Talk2View"`** — clearly attributable to the assistant, but
  loses the connection to the human driving the session.
- **LibreOffice user name only** — matches a human-typed comment exactly,
  but AI comments would be indistinguishable from the user's own,
  defeating an audit trail.
- **Logged-in Talk2View account (SDK)** — the SDK auth identity lives in
  the web/bridge layer, not reachable from a UNO tool body; would require
  plumbing the identity across the bridge for marginal benefit over the
  LibreOffice profile name.
- **Leave unset (status quo)** — rejected: blank author/date is the bug
  being fixed.

## Consequences

- **Pros** — comments now carry an author and timestamp like normal
  Writer comments; AI authorship is explicit ("Talk2View on behalf
  of …") so reviewers can tell which comments came from the assistant.
- **Cons** — the author string is longer than a human name and may look
  unusual in the margin; the config read adds one UNO round-trip per
  comment (negligible).
- **Follow-up** — verify on a build where `add_comment` actually
  succeeds (Investigation #38 means the apt CI LO build can't attach
  comments at all, so the live E2E can't confirm the stamping there).
  Authorship logic is covered by unit tests against the helpers.

## References

- Code: `src/talk2view_writer/tools/commenting.py`
  (`_stamp_authorship`, `_comment_author`, `_lo_user_full_name`,
  `_now_uno_datetime`)
- Tests: `tests/synthetic/test_commenting_tools.py::TestCommentAuthorship`
- Investigations: `docs/investigations.md` #46, #38
- Related ADRs: ADR-0021 (JSON tool returns)
