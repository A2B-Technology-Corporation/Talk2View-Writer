# ADR-0014: File-backed token storage in user config directory

**Status:** Accepted
**Date:** 2026-05-17
**Phase:** B
**Supersedes:** ADR-0012 (Token storage deferred)

## Context

ADR-0012 deferred the token storage decision to Phase B. We now need
to pick the concrete backend so the SDK has somewhere to persist the
user JWT + refresh token between LibreOffice restarts.

The SDK protocol is simple: `TokenStorage.get/set/delete(key) -> str`.
It is satisfied by anything from in-memory to OS-keychain backends.
Available options for a Python LibreOffice extension:

- **`MemoryStorage`** (SDK default) — re-login every restart. Bad UX.
- **JSON file** — survives restarts, no external deps, easy to debug,
  trivially testable.
- **OS keychain via `keyring`** — best at-rest security, but pulls in
  per-platform native bindings and complicates the `.oxt` bundling
  story (ADR-0006).
- **GPG-encrypted file** — requires the user to enter a passphrase
  every launch, defeating the persistence benefit.

## Decision

Phase B ships **`FileTokenStorage`** (in
`src/talk2view_writer/storage.py`) — a JSON file at the OS-conventional
user config dir:

- Linux/BSD: `$XDG_CONFIG_HOME/talk2view-writer/tokens.json`
- macOS: `~/Library/Application Support/talk2view-writer/tokens.json`
- Windows: `%APPDATA%/talk2view-writer/tokens.json`

Implementation details:

- All operations serialise on an internal `threading.Lock`.
- Writes are atomic: write to `tokens.json.tmp`, `os.replace()` over.
- POSIX: file permissions are forced to `0o600` (user-only) after
  every write.
- Corrupt files (invalid JSON or non-object root) are silently reset
  to empty — we'd rather force a re-login than crash on every read.
- Public constructor accepts an optional `path` so tests / power
  users can override the location.

Files containing **user JWTs** are sensitive: anyone with read access
to the user's home directory can hijack the session. We accept this
risk for v0.1 — same posture as Talk2View-Module (Qt settings) and
Talk2View-Word (browser localStorage).

## Alternatives considered

- **`keyring`-backed storage.** Best security, but the `keyring`
  package has different backends per platform (`secretstorage` on
  Linux, `pyobjc` on macOS, `pywin32` on Windows). Bundling these
  into the `.oxt` cleanly is non-trivial. Defer until v1 / enterprise
  ask.
- **Per-user `keyring` with file fallback.** Reasonable middle
  ground but doubles the test surface in v0.1 for unclear benefit.
- **Don't persist; force re-login each launch.** Hostile UX for a
  daily-driver assistant.

## Consequences

**Pros**
- Survives LibreOffice restarts → user logs in once.
- Atomic writes prevent half-written files corrupting state.
- POSIX `0o600` keeps prying multi-user systems at bay.
- Pure stdlib (`json`, `os`, `pathlib`) — no new deps.
- Trivially testable (we shipped 13 tests in `tests/unit/test_storage.py`).

**Cons**
- Plaintext at rest. A malicious process running as the same user
  can read the file. (Same posture as essentially every desktop app
  that doesn't use an OS keychain.)
- The token file path is OS-conventional but not always *visible*
  to users — discovery is slightly harder if they want to nuke it.
- No encrypted-by-default story; cross-platform encryption without
  a key derivation step is a non-trivial project on its own.

**Follow-up**
- Upstream a `KeyringTokenStorage` into
  `Talk2View-Platform/packages/sdk-python` so every desktop
  integration can share it. Tracked in
  `docs/investigations.md` #7.
- Add a "Forget me" menu item (Phase F) that deletes the file.
- Document the storage location in user docs once they exist.

## References

- Code: `src/talk2view_writer/storage.py`
- Tests: `tests/unit/test_storage.py`
- SDK protocol:
  `Talk2View-Platform/packages/sdk-python/src/talk2view/storage.py`
- Related ADRs: ADR-0002 (cloud SDK), ADR-0012 (superseded),
  ADR-0010 (partner key)
- Investigations: `docs/investigations.md` #7
