# ADR-0010: Reuse Word partner key until a Writer key is issued

**Status:** Accepted — historical. Writer-specific key
`pk_live_474f6f8…40bc7` provisioned 2026-05-17 and now active in
`src/talk2view_writer/config.py::PARTNER_KEY`.
**Date:** 2026-05-17
**Phase:** A

## Context

Every Talk2View API call requires a partner key (header
`X-T2V-Partner-Key`). Talk2View-Word hardcodes
`pk_live_45c878caa500cdf6ea1a72f3e9a4ad324df061b7ec2c70d7` in
`src/taskpane/App.tsx` line 6.

A Writer-specific partner key would let us:
- track usage and rate-limit per-host-app,
- revoke Writer access without affecting Word,
- emit per-host metrics on the platform side.

But no Writer key exists yet, and the user explicitly chose to ship
Phase A with the same key Word uses, replacing it later.

## Decision

Talk2View-Writer hardcodes the same partner key as Talk2View-Word in
`src/talk2view_writer/config.py` (`PARTNER_KEY`). A `TODO` comment in
that file references this ADR.

When a Writer-specific key is provisioned, swap the constant and
update this ADR's Status to **Superseded by ADR-NNNN**.

## Alternatives considered

- **Read from environment variable** — works in dev but the `.oxt`
  runs inside LibreOffice's process, which the user does not launch
  from a custom-env shell. The variable would never be set in
  practice for end-users. Useful only for developer overrides; add as
  a fallback in a later ADR.
- **Settings dialog with secure storage** — right answer long-term
  for the partner key *and* the user JWT. Deferred to Phase F. The
  partner key is currently shared infrastructure (every install uses
  the same one), so storing it per-user is not actually needed.
- **Read from a config file shipped with the `.oxt`** — fine, but
  identical effect to hardcoding it in `config.py` since both ship
  inside the same `.oxt`.

## Consequences

**Pros**
- Single line of code; trivial to swap when a Writer key arrives.
- Matches Talk2View-Word's existing pattern verbatim.
- Phase A ships with a working backend connection.

**Cons**
- **The partner key is embedded in the distributed `.oxt`.** Anyone
  who unzips a `.oxt` can read it. This is true for Word too — see
  Investigation #6. The partner key isn't a secret per se (it
  identifies the partner, not the user), but treating it like one is
  prudent.
- Per-host analytics on the platform server can't distinguish Writer
  traffic from Word traffic until we swap keys.
- If the shared key is revoked for a Word-side incident, Writer
  breaks too.

**Follow-up**
- Track "issue Writer-specific partner key" as Investigation #6.
- Phase F settings dialog should expose a per-install **override** so
  enterprise customers can use their own partner keys.

## References

- Code: `src/talk2view_writer/config.py::PARTNER_KEY`
- Word source: `Talk2View-Word/src/taskpane/App.tsx` line 6
- Related ADRs: ADR-0002 (cloud SDK), ADR-0012 (token storage)
- Investigations: `docs/investigations.md` #6
