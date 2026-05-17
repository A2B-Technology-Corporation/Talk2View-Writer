# ADR-0013: Skills + system prompt copied verbatim from Word

**Status:** Accepted (planned for Phase E)
**Date:** 2026-05-17
**Phase:** A (planning), Phase E (action)

## Context

Talk2View-Word ships:

- `SYSTEM_PROMPT.md` — instructions to the cloud agent (priority
  ordering, security rules, tool-batching guidance).
- `skills/` — 13 directories each with a `SKILL.md` describing a
  high-level workflow (`document-creation`, `formatting-standards`,
  `rewrite-in-place`, etc.). The cloud agent reads skills as part of
  its system context and applies them to multi-step user requests.

Skills and the system prompt are **content**, not code: they're
read by the cloud agent without ever touching the host application's
APIs.

## Decision

Phase E copies `Talk2View-Word/skills/*/SKILL.md` and
`Talk2View-Word/SYSTEM_PROMPT.md` verbatim into Talk2View-Writer.
The only edits:

- Change "Microsoft Word" → "LibreOffice Writer" in the system prompt
  intro.
- Append a "Writer deltas" section to the system prompt for any tools
  where Writer's behaviour diverges from Word's (e.g. partial track-
  changes support, comment threading differences). The list is built
  during Phase D as each tool is ported.

## Alternatives considered

- **Write skills from scratch.** Wasteful; the existing skills have
  been iterated by the Word team and the cloud agent has been tuned
  against them.
- **Reference Word's skills via a `git submodule`.** Tempting but
  fragile — Word's release cadence shouldn't gate Writer. Verbatim
  copy with periodic re-sync is more honest about the relationship.
- **Generate skills dynamically.** Skills are version-controlled
  documentation; generation adds no value.

## Consequences

**Pros**
- Talk2View-Writer gets a battle-tested skill catalog on day one.
- Cloud agent behaves consistently between Word and Writer (same
  priority order, same security rules, same skill names).

**Cons**
- We will fall out of sync with Word over time. Need a process to
  re-pull when Word updates skills. Tracked as Investigation #8.
- Skills reference "Word" indirectly (style names like `Heading 1`,
  comment behaviours). Most align with Writer, but exceptions need
  the "Writer deltas" section.
- If Writer-specific skills emerge (e.g. ODF-only features), they
  belong here only and won't be visible to the Word team — a
  one-way fork.

**Follow-up**
- Phase D commit messages must call out any tool whose Writer
  behaviour diverges from Word's, so Phase E can compile the deltas.
- Investigation #8: define a re-sync cadence with Word's skill set.

## References

- Source: `Talk2View-Word/skills/`,
  `Talk2View-Word/SYSTEM_PROMPT.md`
- Destination: `src/talk2view_writer/skills/` (Phase E)
- Related ADRs: ADR-0001 (sibling project), ADR-0008 (tool decorator)
- Investigations: `docs/investigations.md` #8
