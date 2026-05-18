# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Repository Overview

Talk2View-Writer is the LibreOffice Writer sibling of `Talk2View-Word`. It uses:

- the **cloud** Talk2View engine (`engine.talk2view.com`) via the
  [`talk2view` Python SDK](../Talk2View-Platform/packages/sdk-python/) — same
  backend Talk2View-Word uses,
- a **sidebar deck** (UNO `XSidebarPanel`) for the chat UI — registered via
  `extension/Sidebar.xcu`,
- **26 Python tools** registered via the SDK's `@tool` decorator, each invoking
  UNO APIs to manipulate the Writer document.

It does **not** depend on Talk2View-Core or RPyC (unlike SpeedWriter-LibreOffice
and Talk2View-Module). Auth uses a partner key + Supabase login through the SDK.

## Build / Test

```bash
make dev            # uv sync --dev
make lint test      # ruff + pytest
make build          # stage extension into build/Talk2ViewWriter/
make package        # dist/Talk2ViewWriter.oxt
make install-oxt    # unopkg add --force dist/Talk2ViewWriter.oxt (refuses if soffice running)
```

**Never** invoke `unopkg add --force` directly while LibreOffice is
running. `--force` mutates the user-profile extension registry, and
doing so against a live soffice can corrupt the deployment pmap —
silently wiping **all** user-installed extensions (Talk2View, Zotero,
everything). The cache files get orphaned and `unopkg list` reports
`<none>` on next launch. `make install-oxt` checks for a running
soffice and refuses; the manual `unopkg add` does not. Always kill
soffice first, or use the make target.

## Project Layout

See `README.md`.

## Code Standards (inherited from `SpeedWriter-LibreOffice/CLAUDE.md`)

- **Exception handling:** fail fast. Never catch `Exception` with a default
  return. Catch specific types at UI boundaries only. Document `Raises:` in
  docstrings.
- **Logging:** every module — `logger = logging.getLogger(__name__)`. Use INFO
  liberally; use `logger.exception()` in except blocks.
- **No silent failures.** Surface errors via `MessageBoxType.ERRORBOX` at the
  UI boundary (memory: `feedback_never_hide_errors`).
- **Strict typing.** mypy `disallow_untyped_defs = true`.
- **No emojis in code.**

## UNO threading

UNO is **not** thread-safe. The SDK's `t2v.chat()` returns an iterator that
blocks on SSE reads. The pattern in `ui/sidebar_panel.py` is:

1. UI submit handler spawns a worker thread.
2. Worker iterates `t2v.chat(...)`, pushing `ChatEvent`s into a
   `queue.Queue`.
3. A `XTopWindowListener` timer (or `vcl.scheduleAction`-style callback)
   drains the queue on the UI thread and updates widgets.

Tools registered via `@tool` execute on the worker thread when the SDK
receives an `interrupt`. The tool implementations must marshal any UNO
calls back to the UI thread before mutating the document — use the same
queue + drain pattern, or grab the `solar_mutex` if available.

## Tool surface

Tools mirror `Talk2View-Word/src/taskpane/tools/*.ts` one-for-one. The
mapping table lives in the plan file. When porting a tool:

1. Read the TypeScript source for argument schema + return shape.
2. Implement in `src/talk2view_writer/tools/<group>.py` as a Python
   function with type hints + Google-style docstring (SDK uses these to
   build the schema for the LLM).
3. Wrap UNO calls through helpers in `uno_helpers/` to keep tool bodies
   declarative.
4. If the Writer behaviour can't match Word exactly, note the delta in
   the commit body and in `SYSTEM_PROMPT.md` "Writer deltas" section.

## Architecture Decision Records

**Every substantive decision is recorded as an ADR under `docs/adrs/`.**
Read `docs/adrs/README.md` for the index. When making a new substantive
decision:

1. Copy `docs/adrs/0000-template.md` to the next sequential number.
2. Fill in Context / Decision / Alternatives / Consequences sections.
3. Add a row to the index in `docs/adrs/README.md`.
4. Link to it from any code or other ADR that depends on it.

If you supersede an existing ADR, update the old ADR's Status to
**Superseded by ADR-NNNN** and add a forward link. Do not delete or
rewrite past ADRs.

## Investigation log

When you notice something **wrong, surprising, or worth revisiting later**
in `Talk2View-Word`, `Talk2View-Platform`, or LibreOffice itself —
something you aren't going to fix right now — add it as a numbered entry
in `docs/investigations.md`. Each entry needs: What / Where / Why it matters
/ Next step. Never reorder; new entries get the next sequential number.

## Related projects

- `../Talk2View-Word/` — reference for skills, system prompt, tool taxonomy.
- `../Talk2View-Platform/packages/sdk-python/` — the SDK we depend on.
- `../SpeedWriter-LibreOffice/` — reference for UNO packaging, dialogs,
  threading patterns.
