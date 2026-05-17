# ADR-0004: Two UNO components in one entry file

**Status:** Accepted
**Date:** 2026-05-17
**Phase:** A

## Context

Talk2View-Writer needs to register two distinct UNO services:

1. **`Talk2ViewJob`** (`com.sun.star.task.Job`) — handles menu commands
   from `Addons.xcu` (`?showPanel`, `?login`, `?logout`, `?settings`).
2. **`ChatPanelFactory`** (`com.sun.star.ui.UIElementFactory`) — called
   by LibreOffice when the sidebar deck is opened, returns the panel's
   `XUIElement`.

These have unrelated responsibilities but both must be discoverable by
LibreOffice at extension load time. The `META-INF/manifest.xml` lists
one or more `.py` files as UNO components; each file declares its
implementations via the module-level
`g_ImplementationHelper.addImplementation(...)` calls.

Options for organising this:

1. Both components in `extension/talk2view_writer.py`, registered via
   a single `g_ImplementationHelper`.
2. Two separate files (`extension/job.py` + `extension/panel_factory.py`),
   both listed in `manifest.xml`.

## Decision

Put both components in **`extension/talk2view_writer.py`** with one
`g_ImplementationHelper` registering both. SpeedWriter has only one
component (the `Job`); we extend that file's pattern to host both.

## Alternatives considered

- **Two separate `.py` files** — cleaner separation, but doubles the
  per-entry-file `sys.path` setup boilerplate (each UNO entry file has
  to re-insert `pythonpath/` into `sys.path` before any
  `talk2view_writer` import works). Also makes the
  `META-INF/manifest.xml` longer for no functional gain.
- **A single combined component** — UNO doesn't support a single
  service implementing both `XJobExecutor` and `XUIElementFactory`
  cleanly; the dispatch surfaces are unrelated and LibreOffice looks
  them up by different service names.

## Consequences

**Pros**
- One file to read when debugging extension load failures.
- `sys.path` shim runs once.
- Mirrors SpeedWriter's "one entry file per extension" pattern, which
  developers familiar with that project will recognise.

**Cons**
- The entry file does two things. As the extension grows we may want
  to split it; revisit if it crosses ~400 LOC.

## References

- Code: `extension/talk2view_writer.py` —
  `g_ImplementationHelper.addImplementation(...)` calls
- Code: `extension/META-INF/manifest.xml`
- Related ADRs: ADR-0003 (sidebar deck wiring)
