# ADR-0012: Token storage deferred to Phase B

**Status:** Deferred
**Date:** 2026-05-17
**Phase:** A (placeholder), Phase B (implementation)

## Context

The Python SDK exposes a pluggable `TokenStorage` interface for
persisting the user JWT + refresh token between sessions. Two
implementations ship in-box:

- **`MemoryStorage`** — tokens live only in process memory. Every
  LibreOffice restart forces a re-login.
- **`FileStorage`** *(if available)* — JSON file on disk; survives
  restarts but stores tokens in plaintext.

There is no SDK-shipped OS-keychain backend. Each platform integrator
(Word's React app uses browser `localStorage`, Slicer uses Qt
settings) picks an appropriate store.

## Decision (interim)

Phase A: **do not pick a storage yet.** The SDK is not actually
instantiated in Phase A code. Phase B's first commit will:

1. Implement a `FileTokenStorage` writing to
   `~/.config/talk2view-writer/tokens.json` (or platform-appropriate
   `xdg-config` / `%APPDATA%` / `~/Library/Application Support`).
2. Set the file permissions to user-read-write only (0600 on Unix).
3. Document the security boundary in this ADR and update Status to
   **Accepted**.

If the user wants stronger guarantees (OS keychain), file a
follow-up ADR after Phase B.

## Alternatives considered

- **`MemoryStorage` only.** Easier (zero code), but forcing re-login
  every LibreOffice launch is bad UX for a daily-driver assistant.
- **OS keychain (libsecret / Keychain / Windows Credential Manager).**
  Best security, but requires per-platform native bindings or a
  cross-platform shim like `keyring`. Pulls in C deps and complicates
  the `.oxt` bundling story (see ADR-0006).
- **Encrypted file with a key derived from a passphrase the user
  enters every launch.** Re-introduces the re-prompt problem.

## Consequences

**Pros (of deferring)**
- Phase A ships without committing to a security model that may need
  to change.

**Cons (of deferring)**
- Phase B can't ship a working login until this is resolved. So
  it's a hard blocker for Phase B's first commit, not an optional
  follow-up.

**Follow-up**
- Phase B picks the plain-`FileTokenStorage` path unless a
  conflicting decision arrives first.
- Long-term: if enterprise customers ask, add `keyring`-backed
  storage behind a feature flag.

## References

- SDK: `Talk2View-Platform/packages/sdk-python/src/talk2view/storage.py`
- Related ADRs: ADR-0002 (cloud SDK), ADR-0010 (partner key)
- Investigations: `docs/investigations.md` #7
