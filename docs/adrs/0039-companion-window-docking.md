# ADR-0039: Integrated companion window (WM identity + host-window docking)

**Status:** Accepted
**Date:** 2026-06-05
**Phase:** G
**Supersedes:** —
**Superseded by:** —

## Context

ADR-0030 moved the chat UI into a pywebview subprocess — a WebKitGTK
top-level window running the React + Talk2View SDK app, connected to
LibreOffice over a Unix-socket bridge. It works, but it opens as a
generic centred window: a separate `python3` taskbar entry, no
Talk2View branding, floating over the middle of the screen with no
relationship to the LibreOffice document window. The user asked to
"move the chat into the sidebar for better integration."

A literal LibreOffice **sidebar deck** is not available to us. ADR-0029
documented — and we re-confirmed on the same build (LO 26.2.3.2) — that
the LO 26.x sidebar framework hands Python panels a 4-interface stub
with no `XWindowPeer` (`queryInterface(XWindowPeer)` → `None`,
`getPosSize` raises "not implemented"). Every docking path
(`createContainerWindow`, `SystemChildWindow`/`createSystemChild`,
native reparenting) consumes that missing peer and hits the same wall.
The chat being a separate-process webview makes embedding *harder*, not
easier. This LO build also ships **no GTK3 VCL plugin** (only gen/X11,
Qt6, KF6), so there is no `GtkSocket` host even on X11.

The dev/target session is **Wayland**, which additionally forbids
cross-process window reparenting and forbids a client from positioning
its own toplevel.

The user reviewed these constraints and chose to make the existing
pywebview window *behave* like a docked side panel, degrading
gracefully per platform, rather than reopen the dead sidebar path or
take on a Qt/QtWebEngine rewrite.

## Decision

Keep ADR-0030's pywebview window. Make it read and behave as an
integrated companion of the LibreOffice document window via three
client-side capabilities, all best-effort and gracefully degrading:

1. **WM identity** — set the GTK program name (`GLib.set_prgname`/
   `set_application_name`) to `Talk2View` and pass the bundled icon to
   `webview.start(icon=...)`, so the window carries the Talk2View name +
   icon and groups consistently in the taskbar/overview instead of
   showing as a generic `python3` entry. Robust on **both** Wayland and
   X11.
2. **Host-window handoff** — a new `get_host_window` method on the
   existing bridge (`bridge_server.py`) reports LO's main-window
   geometry and, where extractable, a native parent handle. The reads
   run on LO's UI thread via `UIThreadDispatcher` (mirroring the proven
   `frame.getContainerWindow()` pattern in `about.py`).
3. **Geometry policy** — a pure `_window_geometry()` function decides
   size/position/chrome from `(host descriptor, persisted geometry,
   platform, session)`. Platforms that allow client-side positioning
   (everything except Linux/Wayland) auto-dock the window onto LO's
   right edge; on X11 it is additionally made `transient-for` LO via the
   reported XID. The last user geometry is persisted and restored.

`get_host_window` is **bridge infrastructure**, not a document tool — it
is deliberately not added to `_MVP_TOOL_NAMES`, so the engine cannot
invoke it.

## Alternatives considered

- **Reopen the LibreOffice sidebar deck** — rejected. Structurally
  blocked on LO 26.x (ADR-0029); re-confirmed on the same build. Would
  reproduce the empty grey tab.
- **Native-reparent the webview into LO's window** — rejected. Wayland
  forbids cross-process reparenting; this LO has no GTK3 plugin /
  `GtkSocket` host even on X11.
- **Qt/QtWebEngine in-process panel** — rejected for this iteration.
  The only path to a literal in-process tab, but it loses pywebview,
  adds ~80 MB Chromium per platform to the `.oxt`, is weeks of work, and
  still fights the panel peer model.
- **Frameless window + client-side drag strip now** — deferred to a
  follow-up. pywebview's `easy_drag` uses absolute `move()` (a no-op on
  Wayland), so a frameless window would be undraggable there. v1 keeps
  the title bar so the compositor's drag + edge-snap work everywhere.

## Consequences

- **Pros:**
  - The biggest felt win — Talk2View branding + taskbar/overview
    grouping — works on the dev machine's Wayland session, not just
    X11.
  - On X11/macOS/Windows the window auto-docks beside LO and (X11)
    stacks with it; it reads and behaves like a docked deck.
  - All changes are isolated to the subprocess + one bridge method;
    the chat UI, SDK flow, tools, and test rig are untouched.
  - The docking policy is a pure function, unit-tested across the full
    platform/session matrix.
- **Cons:**
  - On Wayland the integration is honestly limited: branding + grouping
    + a tall persisted panel + drag-to-snap, but the compositor will
    not let us pin or reparent the window. Full docking is X11/macOS/
    Windows only.
  - The native-handle extraction depends on
    `XSystemDependentWindowPeer`, which strict-PyUNO builds may not
    expose (ADR-0026); transient-for then silently no-ops and we fall
    back to geometry-only positioning.
- **Follow-up:**
  - Frameless panel + client-side drag strip (`begin_move_drag`, which
    works on Wayland) in `src/web/`.
  - Full grouping/owner relationships on macOS (`addChildWindow:`) and
    Windows (`GWLP_HWNDPARENT`) — folds into the ADR-0030 port work.
  - Live re-dock when LO moves (X11): currently a one-time nudge.
  - See investigation #49 (LO does not expose an xdg-foreign token via
    UNO, so Wayland transient-for is unavailable to extensions).

## References

- Code: `src/talk2view_writer/web_runner.py`
  (`_window_geometry`, `_session_type`, `_apply_window_identity`,
  `_patch_gtk_window_transient`, `_track_window_geometry`, geometry
  persistence), `src/talk2view_writer/bridge_server.py`
  (`get_host_window` / `_read_host_window` / `_native_handle`),
  `src/talk2view_writer/ui/web_window.py` (`_resolve_icon_path`, `--icon`).
- Tests: `tests/unit/test_web_runner_geometry.py`,
  `tests/unit/test_bridge_server.py::TestGetHostWindow`.
- Plan: `/.claude/plans/move-the-talk2view-chat-precious-cascade.md`
- Related ADRs: builds on ADR-0030 (pywebview window); ADR-0029 (why the
  sidebar deck is dead); ADR-0026 (strict-PyUNO peer interface gaps).
- Investigation: `docs/investigations.md` #49.
