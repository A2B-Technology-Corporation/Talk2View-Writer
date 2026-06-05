# ADR-0040: Guided demo as a partner skill, not a client-side prompt injection

**Status:** Accepted
**Date:** 2026-06-05
**Phase:** G
**Supersedes:** —
**Superseded by:** —

## Context

We want an onboarding/demo experience: when a user says "show me what you
can do", the agent runs an interactive feature tour (start a project →
Track Changes → accept → more edits) instead of doing a single task.

Where should the demo content live? Two facts decide it:

1. **The system prompt and skills live in the platform, not this repo.**
   The Writer partner's system prompt (`lm_system_prompt`) and skills
   (`partner_skills`) are Supabase rows in Talk2View-Platform, edited
   through the platform dashboard (`api/dashboard/partner_settings.py`,
   `partner_skills.py`). They are served to the client via `/v1/config`,
   keyed by the partner key. There is **no repo→engine sync**; this repo's
   `SYSTEM_PROMPT.md` + `./skills/` are the version-controlled *authoring
   mirror* of what someone provisioned by hand. The agent loads skills at
   runtime via the engine's `list_skills` / `load_skill` platform tools.

2. **A client-side `systemPrompt` override is available but was
   deliberately removed.** The SDK's `<Talk2View systemPrompt={...}>` prop
   is additive — `agent.py::inject_system_prompt` appends it under
   "## Additional Instructions" on top of the partner prompt (it does not
   replace it). ADR-0034 used this to ship a Writer prompt while the
   Writer partner key was unprovisioned; investigation #34 reverted it
   once the key was fixed, returning to "the platform is the source of
   truth". Re-adding a client override to carry the demo would re-introduce
   exactly that divergence.

## Decision

Implement the demo as a **skill**, authored in this repo and provisioned
into the Writer partner config — keeping prompt/skill content where it
belongs (the platform), not injected from the client.

- Add `skills/guided-tour/SKILL.md` — a Writer-native, strictly
  interactive tour (one step per turn, then stop and wait for the user).
  Its `description` frontmatter carries the trigger phrases ("show me what
  you can do", "demo", "tour", …) so the engine surfaces it through
  `list_skills`. The tour builds a short sample document and demonstrates
  writing-with-styles → Track Changes (Edit → Track Changes → Accept All)
  → formatting → lists → tables → find & replace → comments → page layout
  → undo → wrap-up, using the existing tools only (no new tool/engine
  feature).
- Add a routing row to `SYSTEM_PROMPT.md`'s skill table:
  "Show me what you can do; give me a demo or tour; … → `guided-tour`".
- Add `guided-tour` to the test catalog (`EXPECTED_SKILLS`) plus content
  tests asserting the trigger phrases, the one-step-at-a-time rule, the
  Track-Changes-accept step, and core-feature coverage.

**These two files are the source mirror. They do not take effect until the
content is provisioned into the Writer partner config via the dashboard**
(the skill markdown into `partner_skills`; the updated prompt into
`lm_system_prompt`). Provisioning is a manual copy-paste step today — see
Consequences.

## Alternatives considered

- **Client-side `systemPrompt` addendum (ship the demo in the .oxt).**
  Rejected as the primary mechanism. It works (additive merge, verified in
  the engine) and needs no dashboard access, but re-introduces the
  client/platform prompt divergence that investigation #34 deliberately
  removed. The platform is the agreed source of truth for prompt + skills.
- **Bake the whole tour into the system-prompt text instead of a skill.**
  Rejected. Skills are the established pattern for detailed, on-demand
  workflows; inlining a long tour bloats the always-on prompt and doesn't
  match the other 13 skills. A skill is loaded only when triggered.
- **A new engine feature / platform tool.** Rejected. The tour needs no
  new capability — it orchestrates the existing Writer tools.

## Consequences

- **Pros:**
  - Prompt/skill content stays in the platform partner config — no
    client-side override, no divergence; consistent with the post-#34
    architecture.
  - Uses the existing skill mechanism; the tour is loaded only when the
    user asks for a demo, so it costs nothing on normal turns.
  - The source is version-controlled and reviewable in this repo, and the
    content tests guard the load-bearing properties (triggers, interactive
    pacing, Track Changes step).
- **Cons / Follow-up:**
  - **Not live until provisioned.** Editing `skills/guided-tour/SKILL.md`
    or `SYSTEM_PROMPT.md` here has no runtime effect by itself — the
    content must be copy-pasted into the Writer partner config in the
    Talk2View-Platform dashboard (`partner_skills` + `lm_system_prompt`).
    There is no repo→engine sync; a future improvement is a provisioning
    script/CI that pushes these source files via the dashboard API.
  - The interactive "one step per turn" behaviour depends on the model
    honouring the skill's pacing instructions; the engine has no hard
    turn-gating. The skill states the rule emphatically and repeats it,
    but a model may still occasionally run ahead.

## References

- Source: `skills/guided-tour/SKILL.md`, `SYSTEM_PROMPT.md` (skill-table
  row).
- Tests: `tests/unit/test_skills_and_prompt.py`
  (`TestGuidedTourSkill`, `EXPECTED_SKILLS`).
- Engine behaviour: Talk2View-Platform `packages/server/src/t2v/core/`
  `agent.py::inject_system_prompt`, `system_prompt.py::merge_system_prompt`
  (additive client prompt); `partner_skill_registry.py`,
  `services/partner_skills.py`, `api/dashboard/partner_skills.py`
  (partner skill storage).
- Related ADRs: ADR-0034 (client systemPrompt override, reverted),
  ADR-0035 (Track Changes default for AI edits — the tour's headline
  step), ADR-0013 (skills copied from Word; this is the first
  Writer-authored skill).
- Investigation: `docs/investigations.md` #34 (revert to engine-side
  prompt).
