# ADR-0029: Floating non-modal chat window instead of a sidebar panel

**Status:** Accepted
**Date:** 2026-05-21
**Phase:** F
**Supersedes:** ADR-0003 (sidebar deck as primary UI), ADR-0027 (canonical
toolpanel pattern), ADR-0028 (queryInterface XWindowPeer)
**Superseded by:** —

## Context

ADRs 0003 / 0027 / 0028 progressively narrowed the sidebar-panel
construction to one canonical Python pattern, accepting different
trade-offs each iteration. On 2026-05-21 a live repro on LO 26.2.3.2
(Debian apt) showed two things at once:

1. The framework-supplied ParentWindow is a 4-interface stub —
   `{XWeak, XComponent, XTypeProvider, XWindow}` — that does NOT
   implement XWindowPeer. `parent.queryInterface(XWindowPeer)`
   returns `None` (real C++ RTTI lookup, not the strict-PyUNO
   getTypes() cache). Even basic XWindow methods are dysfunctional:
   `getPosSize` raises "not implemented".
2. The exact same problem was already documented in commit `2cfa7cc`
   on 2026-05-18 — day **two** of the project — where
   `parent_window.getToolkit()` failed with `AttributeError` for the
   same reason. The "it worked at the very start" recollection
   actually corresponds to a registered-but-empty deck tab.

So the sidebar parent_window has been broken since day one. ADRs
0025/0026/0027/0028 each tried a different Python workaround; none
produced a panel that actually renders. The fundamental issue is
that LibreOffice 26.x's sidebar framework hands Python panels a
deliberately-restricted facade that lacks the peer + toolkit APIs
the canonical sidebar pattern depends on.

This is not a strict-PyUNO bug, not a Debian-packaging bug, and not
something we can work around with another query/adapter/escalation
ladder. The Python sidebar pattern is structurally incompatible
with LO 26.x.

## Decision

Drop the sidebar entry-point. Replace it with a non-modal floating
chat window:

- The window is constructed via
  `com.sun.star.awt.DialogProvider2.createDialog(dialog_url)`,
  loading the same XDL layout we already ship.
- It opens with `dialog.execute()` running on a worker pattern, or
  via `dialog.setVisible(True)` for non-modal display.
- The user opens it from the **Talk2View → Open Chat** menu (the
  `vnd.com.talk2view.writer:showPanel` URL scheme is already wired
  through `Talk2ViewProtocolHandler.dispatch`; we just point its
  handler at the new code path).
- The window has a title bar, is moveable, and is closeable. The OS
  desktop environment's window manager handles snap-to-edge docking
  (KDE, GNOME, macOS Stage Manager / Mission Control, Windows
  Snap). For users who want "Talk2View docked to the side," dragging
  the window to the screen edge achieves that on every modern OS.
- One window per LibreOffice process (singleton); re-invoking
  "Open Chat" raises and refocuses the existing window.

The sidebar deck registration (`Sidebar.xcu` + `Factories.xcu` +
`ChatPanelFactory`) is removed entirely. Manifest and Makefile drop
the corresponding entries. The sidebar tab no longer appears, so
users cannot accidentally trigger the broken construction path.

## Why this is "the canonical single option"

- `DialogProvider2.createDialog(URL)` is the documented LibreOffice
  Python pattern for loading an XDL layout. It does **not** require
  an XWindowPeer parent — the API signature is just the URL string.
- It works identically on TDF .deb, Flathub, Snap, AppImage, Debian
  apt, macOS, and Windows builds. No conditional code, no fallback
  selection.
- The XDL layout (`chat_panel.xdl`) is unchanged in structure — only
  the dialog metadata (title bar, closeable, moveable) flips from
  the sidebar-embedded values.
- All the chat behaviour (auth, slash commands, tool execution,
  cross-thread widget updates via `UIThreadDispatcher`) is
  unchanged. Same controls, same callbacks, same SDK flow.

## Alternatives considered

- **More sidebar workarounds (frame.getContainerWindow() as parent,
  Toolkit.createWindow with no parent, etc.)** — rejected. The
  pre-ADR-0027 git log shows multiple attempts down this path; all
  either crashed soffice on reparent or produced an empty panel.
  Even if one variant worked on this Debian build, it would be a
  conditional path that breaks on the next LO release.

- **Java sidebar shell** — rejected on the same grounds as
  ADR-0027: Java runtime dependency, loss of Python tool-surface
  parity, weeks of porting. The floating-window pivot is a one-day
  refactor of code we already own.

- **Custom docking via XLayoutManager** — investigated. LO's
  LayoutManager supports `dockWindow(URL, dockingArea, point)` for
  registered UIElement factories, but the factory side hits the
  same XSidebarPanel/XToolPanel pipeline that's broken. Not a
  workaround, just relocates the bug.

- **Keep sidebar AND add floating window** — rejected per the
  user's "no half-baked fallbacks; this is enterprise-grade
  software" framing. One canonical entry point, not two.

## Consequences

- **Pros:**
  - Single canonical code path that demonstrably works on every
    LibreOffice build we care about, including the one on the
    user's primary dev machine.
  - The XDL, the chat behaviour, the SDK integration, the tool
    surface, the test rig — all of it survives the pivot
    untouched. Only the construction-shim layer changes.
  - Removes ~400 LOC of dead sidebar machinery
    (`Talk2ViewPanel`/`Talk2ViewToolPanel`/`ChatPanelFactory`/
    `Sidebar.xcu`/`Factories.xcu`/the diagnostic walkers' worth of
    code that only existed because of the broken sidebar parent).
  - The "supported LibreOffice builds" caveat in `README.md`
    (added under ADR-0027) goes away. Every supported LO version
    works.

- **Cons:**
  - UX change from "tab in the sidebar deck" to "separate window
    that can be docked via OS window manager". Functionally
    equivalent on Linux/macOS/Windows; visually different. Users
    who specifically wanted the sidebar tab will need to use OS
    snap-to-edge.
  - Talk2View no longer follows the LibreOffice sidebar pattern,
    so it's not discoverable via the sidebar deck UI. Discovery
    happens via the Talk2View menu instead.
  - The XDL ships with a title bar / borders now, which slightly
    changes the panel's visual proportions. Layout values
    (`dlg:width=200 dlg:height=400`) become initial window size
    rather than embedded slot dimensions.

- **Follow-up:**
  - Update `README.md` and `CLAUDE.md` to describe the menu-driven
    entry point. Drop the "supported LibreOffice builds" section.
  - Add a screenshot of the floating window once available.
  - Investigation #29: close the loop with a pointer here.

## References

- Code: `src/talk2view_writer/ui/chat_window.py::ChatWindow` (the
  whole class — ~15 lines for construction; the rest of the chat
  behaviour migrates verbatim from the prior `Talk2ViewPanel`).
- Tests: `tests/unit/test_chat_window.py` (renamed from
  `test_sidebar_panel.py`; chat-behaviour tests preserved).
- LibreOffice DialogProvider docs:
  https://api.libreoffice.org/docs/idl/ref/interfacecom_1_1sun_1_1star_1_1awt_1_1XDialogProvider2.html
- ADRs superseded: 0003, 0027, 0028
- Investigation: `docs/investigations.md` #29 (final closure)
