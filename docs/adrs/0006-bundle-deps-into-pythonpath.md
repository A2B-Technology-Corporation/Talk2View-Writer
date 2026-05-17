# ADR-0006: Bundle Python deps into `pythonpath/` inside the `.oxt`

**Status:** Accepted
**Date:** 2026-05-17
**Phase:** A

## Context

LibreOffice runs Python extensions under its **bundled Python**
interpreter, not the user's system Python. The bundled Python has only
the LibreOffice-shipped standard library plus `uno` / `unohelper` —
no `httpx`, no `pydantic`, no Talk2View SDK.

There are three established patterns for getting third-party packages
into a LibreOffice extension:

1. **Bundle into `extension/pythonpath/`** — extension installer
   ships the packages, the entry file inserts `pythonpath/` into
   `sys.path` at import time. (Used by SpeedWriter for `rpyc`,
   `plumbum`.)
2. **Document a `pip install` step** — user installs deps into
   LibreOffice's bundled Python manually via the bundled
   `python -m pip install …`. (Used by SpeedWriter for `langchain`,
   per its README.)
3. **Subprocess to an external Python** — extension shells out to a
   side-car interpreter that has the deps. Adds an IPC boundary.

## Decision

Bundle the SDK + its transitive deps into `extension/pythonpath/` at
`make build` time. Specifically: copy
`talk2view`, `httpx`, `httpcore`, `h11`, `certifi`, `sniffio`,
`idna`, `anyio`, `pydantic`, `pydantic_core`, `typing_extensions`,
`annotated_types` from `.venv/lib/python*/site-packages/` into the
build tree, scrub `__pycache__`, then zip into `.oxt`.

The entry file `extension/talk2view_writer.py` inserts `pythonpath/`
at `sys.path[0]` before any `talk2view_writer` import.

## Alternatives considered

- **`pip install` documentation** — friction-heavy and error-prone
  (users must find LibreOffice's bundled `python`, run pip with the
  right flags, hope no permissions issues). SpeedWriter does this for
  some deps; we want to be more turnkey.
- **Subprocess side-car** — adds an IPC boundary that we don't need
  (the SDK is pure-Python over HTTPS; no need for a separate
  process).

## Consequences

**Pros**
- One-step install: `unopkg add Talk2ViewWriter.oxt` and everything
  needed is present.
- No "did you remember to install httpx?" support tickets.
- Cross-platform — the `.oxt` is the deliverable, and LibreOffice
  unpacks it the same way on Windows / macOS / Linux.

**Cons**
- **`httpx` and `pydantic_core` have C-extension wheels.** A `.oxt`
  produced on Linux contains Linux-only binaries — running it on
  Windows or macOS would fail at import time. We will need
  per-platform builds, similar to how `Talk2View-Core` ships
  platform-specific compiled binaries.
- Larger `.oxt` (~5–10 MB vs ~50 KB for a pure-Python extension).
- We are responsible for tracking transitive dep versions; if `httpx`
  upgrades its C ABI, our `.oxt` breaks until rebuilt.

**Follow-up**
- Phase B verifies the bundle approach end-to-end on Linux; cross-
  platform builds tracked as `docs/investigations.md` #3.
- Consider switching the SDK's HTTP layer from `httpx` to `urllib3`
  (pure-Python) to dodge the C-extension cross-platform problem.

## References

- Code: `Makefile` — `build` target; pythonpath copy loop
- Code: `extension/talk2view_writer.py` — `sys.path` shim
- Pattern reference: `SpeedWriter-LibreOffice/Makefile` lines 99-104
- Related ADRs: ADR-0005 (path dep), ADR-0002 (cloud SDK)
