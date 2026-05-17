# ADR-0022: Bundle SYSTEM_PROMPT.md in the .oxt, pass it per-session

**Status:** Accepted
**Date:** 2026-05-17
**Phase:** E

## Context

The Talk2View cloud engine stores an authoritative system prompt for
each partner key. Talk2View-Word does not load a local copy of
`SYSTEM_PROMPT.md` at runtime — the engine's copy is used directly.

For Writer we need to decide:

1. Is `SYSTEM_PROMPT.md` purely documentation, or do we ship it inside
   the extension and pass it to the SDK per session?
2. How should the extension locate the bundled copy at runtime?

The SDK accepts an optional `system_prompt=` argument on every chat
call (see `talk2view.sessions.Session.chat` in the Python SDK). If we
pass one, it overrides the engine's copy for that session.

## Decision

**Bundle `SYSTEM_PROMPT.md` and `skills/` into the `.oxt` under
`resources/`, and pass the bundled prompt to `sdk.chat()` on every
message.**

Resolution order in `talk2view_writer/system_prompt.py`:

1. `$TALK2VIEW_WRITER_SYSTEM_PROMPT` environment variable (dev /
   CI overrides).
2. `<extension-install-root>/resources/SYSTEM_PROMPT.md` (production
   install, set by Makefile's `build` target).
3. Repo-root `SYSTEM_PROMPT.md` (development checkout, walking up
   from `src/talk2view_writer/`).
4. `None` — falls back to the engine's server-side prompt.

The bundling also includes `skills/` for transparency — anyone
unzipping the `.oxt` can read exactly what skill catalog the build
was designed against — but the skill files themselves are not loaded
at runtime (the engine has its own copies indexed by name).

## Alternatives considered

- **Don't bundle, rely on engine.** Cleanest but means a Writer
  installation cannot be QA'd against a local edit of the system
  prompt without re-uploading to the engine and rebuilding the
  partner-key association.
- **Bundle but never pass.** Documentation-only — same as not
  bundling for the runtime behaviour. Rejected because the marginal
  cost of also passing it (~3 KB per chat call) is trivial and the
  feedback loop for prompt edits gets shorter.
- **Hard-code the system prompt as a Python string.** Loses the
  human-readable Markdown and the "single file" property that lets
  the Word team review the Writer prompt as a diff.

## Consequences

**Pros**
- Editing `SYSTEM_PROMPT.md` and rebuilding the `.oxt` is enough to
  ship a behavioural change — no engine-side deploy required for
  prompt iteration.
- Investigators (Word team, future maintainers) can read the
  Writer-deltas section straight from the installed extension.
- `TALK2VIEW_WRITER_SYSTEM_PROMPT` enables rapid experimentation
  without rebuilding the `.oxt`.

**Cons**
- The engine prompt and the bundled prompt can drift. The bundled
  copy wins per-session, so a Writer release with a stale prompt
  will run the stale prompt regardless of any engine update. We
  accept this — engine and extension move together at release time.
- ~3 KB of payload added to every chat request. Negligible compared
  to the message stream.
- `load_system_prompt` is `lru_cache`d, so changes mid-session
  require a process restart.

## References

- ADR-0013 — Skills + system prompt copied verbatim from Word.
- `src/talk2view_writer/system_prompt.py`
- `Talk2View-Platform/packages/sdk-python/src/talk2view/sessions.py`
  (`Session.chat(system_prompt=...)`)
