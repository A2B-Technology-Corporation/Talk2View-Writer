# ADR-0001: New sibling project rather than fork Word or evolve SpeedWriter

**Status:** Accepted
**Date:** 2026-05-17
**Phase:** A

## Context

The Talk2View product line already has three host-app integrations in this
workspace:

- `Talk2View-Word` — TypeScript / React, Microsoft Word Office Add-in,
  cloud `@talk2view/sdk` backend, 13 skills + 26 tools, production-ready.
- `Talk2View-Module` — 3D Slicer extension, Python + Qt, local RPyC to
  `Talk2View-Core` binary.
- `SpeedWriter-LibreOffice` — voice-first LibreOffice Writer extension,
  Python + UNO, local RPyC to `Talk2View-Core`.

The user asked for "Talk2View-Word for LibreOffice Writer". None of the
three existing projects are a fit:

- Talk2View-Word is React + Office.js; neither runs inside LibreOffice.
- Talk2View-Module is Qt + Slicer; the chat UI and Slicer-only tool
  surface aren't reusable.
- SpeedWriter is a Writer extension, but its scope is voice
  transcription with a voice-recording modeless dialog, not a
  general-purpose document chat.

We need a Python + UNO extension that pairs SpeedWriter's packaging
skeleton with Talk2View-Word's skill / tool catalog.

## Decision

Create a new sibling project `Talk2View-Writer/` next to the existing
three integrations. Borrow:

- packaging, Makefile, extension XML layout, UNO entry pattern, dialog
  threading model — from **SpeedWriter-LibreOffice**;
- skills, system prompt, tool taxonomy, partner-key model, cloud
  backend — from **Talk2View-Word**.

Implement net-new code only where the host-app surface or backend
binding differ.

## Alternatives considered

- **Fork Talk2View-Word into a dual-target codebase** — would require
  bolting a Python + UNO runtime onto a React + webpack project.
  Office.js and UNO have no common substrate, so almost all the React
  tooling would carry no value.
- **Evolve SpeedWriter into the new extension** — SpeedWriter's
  identity is voice-first and HIPAA-scoped; absorbing a general-purpose
  document chat would dilute its product story and add cloud
  dependencies to a project that markets local-only privacy.
- **Add Writer support to the Talk2View cloud platform server** —
  this is server-side AI orchestration; the integration we need is on
  the *client* side, inside LibreOffice. Different layer entirely.

## Consequences

**Pros**
- Clean separation: no risk to SpeedWriter's voice-first product, no
  hybrid React/UNO build.
- Free to copy SpeedWriter's packaging exactly (proven, tested) and
  Word's prompt/skill catalog exactly (proven, tested).
- Each project remains independently releasable.

**Cons**
- Two LibreOffice extensions will eventually co-exist in users' Tools →
  Extension Manager (SpeedWriter and Talk2View-Writer). Brand confusion
  risk — see Investigation #1 in `docs/investigations.md`.
- Duplicated UNO helper code between SpeedWriter and Talk2View-Writer
  (cursor handling, message boxes, etc.). If divergence costs grow,
  consider extracting a shared `talk2view-uno-utils` library — but
  defer until pattern is clearer.

**Follow-up**
- Phase E copies `Talk2View-Word/skills/` and `SYSTEM_PROMPT.md`
  verbatim (see ADR-0013).
- Document the dual-extension overlap in user-facing docs eventually.

## References

- Plan: `/home/ben/.claude/plans/i-want-to-make-rustling-eich.md` —
  "Context" and "Project Layout" sections
- Code: `Talk2View-Writer/README.md`
- Related ADRs: ADR-0002 (backend), ADR-0013 (skills copy)
