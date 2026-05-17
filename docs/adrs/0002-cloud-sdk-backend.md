# ADR-0002: Cloud `talk2view` SDK rather than local RPyC Core

**Status:** Accepted
**Date:** 2026-05-17
**Phase:** A

## Context

Talk2View clients reach the AI agent through one of two paths:

1. **Cloud SDK** — HTTPS to `engine.talk2view.com`, two-tier auth
   (partner key + user JWT), SSE streaming, tool calls round-tripped via
   `/v1/sessions/{id}/resume`. Used by Talk2View-Word and any other
   browser/Office add-in. Python equivalent exists at
   `Talk2View-Platform/packages/sdk-python/`.
2. **Local RPyC** — `talk2view_core` binary launched as a subprocess,
   RPyC on `localhost:18812`, `process_message` + `get_stream_updates`
   polling. Used by Talk2View-Module (Slicer) and SpeedWriter
   (LibreOffice voice).

Either could power Talk2View-Writer. We picked **cloud SDK**.

## Decision

Talk2View-Writer depends on the Python `talk2view` SDK and talks to
`https://engine.talk2view.com`. No local Core binary is shipped or
required.

The SDK handles SSE streaming, session lifecycle, and the tool-execution
`interrupt → resume` loop internally. Our code only:

- instantiates `Talk2View(partner_key, base_url, storage=…)`,
- calls `auth.login()` / `auth.logout()` for credentials,
- registers tool functions via `tools.register_from_functions([...])`,
- iterates `chat(message)` for streamed events.

## Alternatives considered

- **Local RPyC like Slicer/SpeedWriter** — would force users to install
  a separate `talk2view_core` server binary (with its own ~200 MB of
  ML deps). Wrong trade for a general-purpose document assistant whose
  inference happens in the cloud anyway. Also leaves us re-inventing
  session management when the SDK already does it.
- **Both backends behind an adapter** — premature abstraction. The
  cloud + local Cores share an API shape but the lifecycle and config
  story are very different. Add the second backend if a customer needs
  it.
- **Direct HTTP calls (skip SDK)** — would reimplement SSE parsing,
  the tool round-trip protocol, and token refresh in our own code.
  The Python SDK is ~500 LOC and already production-tested by
  `Talk2View-Platform`'s own integration tests; building a parallel
  implementation buys nothing.

## Consequences

**Pros**
- No second runtime to ship/install/version-match.
- Same backend as Talk2View-Word — bug fixes, model upgrades, and
  policy changes apply to both with no code change here.
- The SDK encapsulates auth refresh and the tool round-trip; our
  surface area shrinks.

**Cons**
- Hard online requirement. No offline mode. (SpeedWriter's local-only
  privacy claim does not apply to us.)
- We depend on a vendor SDK whose internal contracts (event shapes,
  resume protocol) we have to track when the SDK ships breaking
  changes.
- Cloud key handling becomes a security-relevant concern; see ADR-0010
  and the partner-key entry in `docs/investigations.md`.

**Follow-up**
- Phase B implements `sdk_client.py` and selects a `TokenStorage`
  backend (see ADR-0012).
- Investigate whether the `talk2view` Python SDK can be vendored as a
  PyPI release rather than an editable path dep (ADR-0005).

## References

- Code: `src/talk2view_writer/config.py` (`BASE_URL`)
- SDK: `Talk2View-Platform/packages/sdk-python/src/talk2view/__init__.py`
- Related ADRs: ADR-0005 (path dep), ADR-0009 (threading), ADR-0010
  (partner key), ADR-0012 (token storage)
