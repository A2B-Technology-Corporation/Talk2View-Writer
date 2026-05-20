# ADR-0025: Use Toolkit.getDesktopWindow() as XWindowPeer for sidebar panel construction

**Status:** Superseded by ADR-0026
**Date:** 2026-05-20
**Phase:** F
**Supersedes:** —
**Superseded by:** ADR-0026

## Context

The sidebar panel construction path in `ui/sidebar_panel.py` calls
`com.sun.star.awt.ContainerWindowProvider.createContainerWindow(URL, "",
ParentPeer, EventHandler)` to load the XDL widget tree. The third
argument is declared `[in] com.sun.star.awt.XWindowPeer Peer` in the
IDL and the C++ implementation
(`scripting/source/dlgprov/dlgprov.cxx::createContainerWindow`) raises
`IllegalArgumentException` if it is null.

The sidebar dock framework supplies a `ParentWindow` argument to our
`XUIElementFactory.createUIElement` callback. On the stricter PyUNO
builds shipped with Debian's LibreOffice 26.2.x — verified on the
user's `LibreOffice 26.2.3.2 620(Build:2)` install on 2026-05-19 —
that `ParentWindow` reports its interfaces as just
`(XWeak, XComponent, XTypeProvider, XWindow)`. There is no
`XWindowPeer` in the proxy's interface set, so PyUNO's argument
marshaller raises
`com.sun.star.script.CannotConvertException: value does not implement
com.sun.star.awt.XWindowPeer` *before* the call reaches the C++ side.
The C++ implementation's own `UNO_QUERY` on that pointer would have
succeeded (the underlying VCL window is-an `XWindowPeer` at the C++
level), but PyUNO's strict type check gates the call.

The official LibreOffice Python sidebar example
(`odk/examples/python/toolpanel/toolpanel.py`) hits exactly the same
PyUNO marshalling layer and fails identically on the same builds, so
this is not a regression in our extension — it's a stricter PyUNO
default that some LibreOffice 26.x distributions ship.

## Decision

We will source the `XWindowPeer` argument from
`com.sun.star.awt.Toolkit.getDesktopWindow()`. The IDL
(`offapi/com/sun/star/awt/XToolkit.idl`) declares the return type as
`XWindowPeer`, so PyUNO marshals it without any cross-interface query
— there is no conversion friction.

The fallback chain in `_create_panel_window` becomes:

1. `ParentWindow.queryInterface(XWindowPeer)`
2. `ParentWindow.getPeer()`
3. `Toolkit.getDesktopWindow()` (the new addition)

Steps 1 and 2 succeed on permissive PyUNO builds (LibreOffice 24.2,
25.x, the TDF Fresh PPA on Ubuntu noble) and the previous behaviour
is preserved. Step 3 fires on strict PyUNO builds (Debian 26.2.x).

The desktop window is the system-level top-level window, not the
sidebar deck's allocated region. That is acceptable because the
LibreOffice sidebar deck re-parents our returned
`XToolPanel.Window` into the deck region via the docking path —
verified by reading the `sidebar/Panel.cxx` integration code. The
construction-time visual parent does not determine the panel's
final placement.

The previous bare-`XWindow` last-resort tier is removed. It only
papered over the failure mode briefly on the permissive builds (the
C++ side did its own `UNO_QUERY`) and produced confusing crash
patterns on strict builds.

## Alternatives considered

- **Python `XWindowPeer` adapter wrapping the bare `XWindow`** —
  rejected. A Python proxy can satisfy PyUNO's interface-type check
  for marshalling, but it cannot satisfy the toolkit's parent-peer
  requirement at the VCL level. When the C++ side tries to use the
  wrapper as a real VCL parent for layout, it has no underlying
  VCL window to work with and crashes. The wrapper is a
  marshalling-layer hack with a VCL-layer landmine.

- **`uno.invoke(provider, "createContainerWindow", args)`** —
  rejected. Verified via the PyUNO source
  (`pyuno/source/module/pyuno.cxx::PyUNO_invoke`) that this path also
  goes through `XInvocation::invoke`, which uses the same argument
  conversion as the direct attribute call. Same error path.

- **`Frame.getContainerWindow()` as the parent peer** — partially
  effective. The frame's container window IS a real VCL window with
  a peer at the C++ level, but on the same strict PyUNO builds its
  proxy also lacks `XWindowPeer` in its declared interface set, so
  the conversion still fails. Verified empirically in commit
  `12174be` which regressed all CI matrix entries.

- **Replace XDL load with programmatic `UnoControlDialogModel`
  construction** — viable but a 150+ LOC rewrite that buys us
  nothing once `Toolkit.getDesktopWindow()` already supplies a
  peer. Filed as ADR follow-up only if the desktop-peer approach
  reveals secondary issues.

- **Pass `None` as the parent peer** — rejected. The C++
  implementation explicitly throws `IllegalArgumentException` on
  null `xParent` before falling through to its own peer-lookup
  fallback. We cannot trigger that internal fallback from outside.

## Consequences

- **Pros:**
  - One-line code change with a verified API contract
    (`XToolkit.getDesktopWindow()` returns `XWindowPeer` per IDL).
  - The pattern matches what `DialogProviderImpl::createDialogControl`
    does internally when no explicit parent is supplied (`xPeer.set(
    xFrame->getContainerWindow(), UNO_QUERY )`) — we're just doing
    the equivalent lookup from Python.
  - Works on both strict (Debian 26.2.x) and permissive
    (Ubuntu noble 24.2.x, TDF PPAs) PyUNO builds.
  - The sidebar deck's docking path handles the re-parenting, so
    the user-visible placement is identical to the
    `ParentWindow`-as-peer path.

- **Cons:**
  - Adds a Toolkit service instantiation per panel construction
    (microseconds, one-time).
  - The peer is the system desktop, so any introspection of the
    panel's parent before docking will see "desktop" rather than
    "sidebar deck". The deck framework overwrites this on the next
    docking event; nothing in our code observes it in between.

- **Follow-up:**
  - Integration test in `tests/integration/test_sidebar_dock.py`
    must assert the panel constructs without raising on a
    `ParentWindow` that has no peer (the strict-PyUNO scenario).
    Currently the test passes a `ParentWindow=frame.getContainerWindow()`
    which is always peered on CI; that masks the
    strict-PyUNO failure mode. Tracked by investigation #29.

## References

- Code: `src/talk2view_writer/ui/sidebar_panel.py`
  `_desktop_window_peer()`, `_create_panel_window()`
- Investigation: `docs/investigations.md` #29
- LibreOffice source:
  `scripting/source/dlgprov/dlgprov.cxx::createContainerWindow`,
  `scripting/source/dlgprov/dlgprov.cxx::createDialogControl`,
  `offapi/com/sun/star/awt/XToolkit.idl`,
  `pyuno/source/module/pyuno.cxx::PyUNO_invoke`
- Official Python sidebar example:
  `core/odk/examples/python/toolpanel/toolpanel.py`
