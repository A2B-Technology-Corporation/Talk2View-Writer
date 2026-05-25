# ADR-0034: Reuse Word's partner key, override system prompt for Writer

**Status:** Reverted 2026-05-25 — Writer partner key now provisioned upstream (Platform #61 resolved). The bundle, e2e fixtures, and integration test all use the Writer key again. See commit history for the switch-back.
**Date:** 2026-05-22
**Phase:** G
**Supersedes:** —
**Superseded by:** —

## Context

Every chat completion against `engine.talk2view.com` with the
Writer-specific partner key (`pk_live_474f6f895dfec144a70b841db0d7a3fe1cd1fc7317540bc7`)
returns the engine's catch-all error:

> An error occurred. Please try again later.

That string is the `except Exception:` arm in
`Talk2View-Platform/packages/server/src/t2v/core/agent.py:541`. The
real exception only logs on the engine; the client sees a 200 OK with
the error message in a single SSE chunk and `finish_reason: stop`. The
partner config (`/v1/config`) for the Writer key returns `default_llm_model: null` and `allowed_llm_models: null` — the
partner profile in the engine database was created but never wired up
with LLM credentials.

Cross-checking with sibling integrations using the same engine:

| Integration | Partner key | Chat completes? |
|---|---|---|
| Talk2View-Word | `pk_live_45c…ec2c70d7` | yes |
| Talk2View-OHIF | `pk_live_a9d…effb56cd` | yes |
| JoyMatrix | `pk_live_50f…8d989d0` | yes |
| Talk2View-Writer | `pk_live_474…17540bc7` | **no — same engine, broken partner** |

The engine itself is healthy; the **Writer partner key is the broken
piece**, and it's broken in the engine's database — there is no
client-side change that can make it work.

## Decision

Switch the Writer's client-side partner key to the Word partner key
(`pk_live_45c878caa500cdf6ea1a72f3e9a4ad324df061b7ec2c70d7`) and
override the system prompt with our Writer-specific one via the SDK's
`<Talk2View systemPrompt={...}>` prop, bundling
`SYSTEM_PROMPT.md` from the repo root into the JS bundle via
webpack's `asset/source` loader.

Concretely in `src/web/src/App.tsx`:

```tsx
import SYSTEM_PROMPT from '../../../SYSTEM_PROMPT.md';

const PARTNER_KEY = 'pk_live_45c878caa500cdf6ea1a72f3e9a4ad324df061b7ec2c70d7'; // Word
...
<Talk2View
  partnerKey={PARTNER_KEY}
  baseUrl={BASE_URL}
  systemPrompt={SYSTEM_PROMPT}
  tools={writerTools}
>
```

This is what ADR-0010 originally prescribed back in Phase A — "reuse
Word partner key until a Writer key is issued" — except in practice
the Writer partner key was issued but never fully provisioned. We
revert to the working configuration. The Writer-specific behaviour
(skills, Writer-deltas, tool surface) is preserved via the SDK
`systemPrompt` prop override and the `tools={writerTools}` array.

## Alternatives considered

- **Wait for the engine's Writer partner profile to be configured.**
  Correct long-term answer, but it requires engine-side access that is
  outside this repo. We need the chat to work now.
- **Stand up a local engine and point Writer at it.** ADR-0024 +
  Talk2View-Platform/docker-compose.yml could make this work for dev,
  but ships a non-production base URL to end users.
- **Provision a new Writer partner key from scratch.** Same problem as
  the first option — depends on engine-side provisioning we don't
  control. Reusing the Word key is identical in effect from the
  engine's perspective: same partner, same model, same allowlist.

## Consequences

- **Pros**
  - Chat completions work immediately — same backend the Word task
    pane uses.
  - Writer-specific system prompt + tools remain Writer-specific
    (system prompt is a client-supplied string per-session; tools are
    registered via `/v1/tools/register` with the Writer's tool
    schemas).
  - One source of truth for SYSTEM_PROMPT.md (repo root), inlined into
    the bundle at build time. Drift between the prompt the engine sees
    and the prompt the developer reads is impossible.

- **Cons**
  - Writer's engine-side traffic is now mixed into Word's partner
    metrics. If we want per-app analytics on the engine, we'll need to
    revisit once the Writer key is properly provisioned.
  - The Writer key remains broken on the engine side; this ADR doesn't
    fix it, it just routes around it. Track the engine-side fix as a
    follow-up (out-of-repo TODO).

- **Follow-up**
  - Engine team: provision `pk_live_474…17540bc7` with the right LLM
    model + credentials, then switch the Writer client back to the
    Writer key. Until then this ADR holds.
  - Investigation #34 in `docs/investigations.md` captures the
    diagnostic chain.

## References

- Code: `src/web/src/App.tsx` (partner key + systemPrompt prop)
- Code: `src/web/webpack.config.js` (asset/source rule for `*.md`)
- Code: `src/web/src/declarations.d.ts` (TS declaration for `*.md`)
- Related ADRs: ADR-0010 (the original "reuse Word key" decision),
  ADR-0030 (web architecture), ADR-0031 (E2E strategy)
- Engine code: `Talk2View-Platform/packages/server/src/t2v/core/agent.py:541`
