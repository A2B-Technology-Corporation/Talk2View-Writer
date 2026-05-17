# ADR-0021: Tools return JSON-encoded strings, not Python dicts

**Status:** Accepted
**Date:** 2026-05-17
**Phase:** D (Group 1: Reading)

## Context

Talk2View-Word's tools all return `JSON.stringify(response)` — a
string the cloud agent then parses. Examples from
`Talk2View-Word/src/taskpane/tools/reading.ts`:

```typescript
return JSON.stringify(response);
return JSON.stringify({ text: selection.text, hint: '…' });
return JSON.stringify({ error: '…', recovery: '…' });
```

Per ADR-0013 the cloud agent has been trained on Word's schemas
including this return convention. When we port tools to Python, we
have a choice:

1. **Return JSON strings** — exactly mirror Word's payload shape.
2. **Return Python dicts/lists** — let the SDK serialise them. The
   agent ends up with the same JSON regardless, but the inner shape
   depends on the SDK's serialiser.

## Decision

Phase D tools return **`json.dumps(response)`** strings, matching
Word's convention exactly. Every tool's return shape (key names,
nesting, hint/error/recovery fields) mirrors the corresponding
`Talk2View-Word/src/taskpane/tools/<group>.ts` payload.

Errors that the agent should reason about (rather than crash on) are
returned as `{"error": "…", "recovery": "…"}` JSON, not raised as
Python exceptions. Exceptions are reserved for **bugs and
environmental problems** — `WriterDocumentRequired`, `ValueError` for
schema violations, etc. — which the SDK turns into agent-visible
error messages.

## Alternatives considered

- **Return Python dicts and let the SDK serialise.** The SDK might
  use different key ordering, camelCase ↔ snake_case conversion, or
  encode `None` differently. Since the agent has been trained against
  Word's exact serialiser output, divergence here means subtle
  prompt drift.
- **Use Pydantic models for tool returns.** Stronger typing but adds
  ceremony per tool and the SDK would still serialise differently
  than Word's `JSON.stringify`. Pydantic v2's `model_dump_json` is
  close but not identical to JS JSON.

## Consequences

**Pros**
- Byte-for-byte payload parity with Talk2View-Word for fields that
  exist in both implementations.
- The agent's chain-of-thought template (trained on Word) parses
  Writer tool returns without adaptation.
- Tool authors do not depend on the SDK's serialisation behaviour.

**Cons**
- More boilerplate — every tool ends with `return json.dumps({...})`.
- We must keep the response keys in sync with Word as Word's tools
  evolve (ADR-0013 / Investigation #8).
- Distinction between "raise" and "return error JSON" is a judgment
  call per tool; needs reviewer attention.

**Follow-up**
- Phase D Group 2–6: every tool returns `json.dumps(...)` strings.
- A unit-test helper that parses each tool's return and checks the
  shape against a fixture would catch divergence early; add in
  Phase F.

## References

- Code: `src/talk2view_writer/tools/reading.py` — all three tools
  return `json.dumps(...)`.
- Word source: `Talk2View-Word/src/taskpane/tools/reading.ts` lines
  136, 158, 221, 246, 257, 266, 276.
- Related ADRs: ADR-0008 (tool decorator), ADR-0013 (skill / prompt
  copy), ADR-0019 (tool registry)
- Investigations: `docs/investigations.md` #8 (sync cadence)
