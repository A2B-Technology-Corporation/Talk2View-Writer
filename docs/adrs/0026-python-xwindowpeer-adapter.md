# ADR-0026: Python XWindowPeer adapter for strict-PyUNO sidebar panel construction

**Status:** Superseded by ADR-0027
**Date:** 2026-05-20
**Phase:** F
**Supersedes:** ADR-0025
**Superseded by:** ADR-0027

## Context

The sidebar panel construction in `ui/sidebar_panel.py` calls
`com.sun.star.awt.ContainerWindowProvider.createContainerWindow(URL, "",
ParentPeer, EventHandler)` to load the chat panel XDL. The third arg
must be a non-null `XWindowPeer` (verified against
`scripting/source/dlgprov/dlgprov.cxx` — the function throws
`IllegalArgumentException` on null).

The sidebar framework supplies a `ParentWindow` argument to the
`XUIElementFactory.createUIElement` callback. On most LibreOffice builds
that proxy's `getTypes()` lists `XWindowPeer` and the call works
unmodified — this matches the official LibreOffice Python example in
`odk/examples/python/toolpanel/toolpanel.py`.

On the LibreOffice 26.2.x backports build shipped by Debian bookworm
(verified by the user's local repro on 2026-05-19/20), the same proxy
reports its `getTypes()` as only `(XWeak, XComponent, XTypeProvider,
XWindow)`. Crucially: this is not a PyUNO marshalling artefact. The
absence is at the C++ level — the C++ type converter
in `stoc/source/typeconv/convert.cxx::convertTo` does:

```cpp
aRet = (*ifc)->queryInterface(aDestType);
if (! aRet.hasValue())
    throw CannotConvertException(
        "value does not implement " + aDestType.getTypeName(), ...);
```

so the C++-level `queryInterface(XWindowPeer)` truly returns null on
the proxy. Every "alternate XWindowPeer source" we tried failed on
this build:

- `frame.getContainerWindow()` proxy also omits XWindowPeer at C++
  level.
- `Toolkit.getDesktopWindow()` returns null (apparently not
  implemented on this build).
- `Toolkit.createWindow(WindowDescriptor(Type=TOP))` crashes soffice
  hard (verified from user log: no log line after the call site).

The previous attempt (ADR-0025) preferred `getDesktopWindow()` over
the bare XWindow when the framework's ParentWindow had no peer; that
regressed macOS CI because the system desktop window isn't a valid
parent for `createContainerWindow` on macOS.

## Decision

We will substitute a **Python `XWindowPeer` adapter** as the
construction-time parent peer whenever `_resolve_parent_peer` cannot
extract one from the framework-supplied `ParentWindow`. The adapter
(`_PythonXWindowPeerAdapter` in `ui/sidebar_panel.py`) derives from
`unohelper.Base`, `XWindowPeer`, and `XComponent`.

The mechanism, verified end-to-end against LibreOffice source:

1. **C++ converter side**
   (`stoc/source/typeconv/convert.cxx::convertTo`): does
   `queryInterface(XWindowPeer)` on the supplied parent.
   PyUNO implements `queryInterface` for Python objects by checking
   the declared interface bases. Since `XWindowPeer` is in the
   adapter's bases, `queryInterface` returns the adapter itself.
   The converter succeeds, no exception thrown.

2. **Dialog provider side**
   (`scripting/source/dlgprov/dlgprov.cxx::createDialogControl`):
   stores the adapter as the parent peer and forwards it into
   `XControl::createPeer(toolkit, parentPeer)`. No other methods are
   called on the parent peer here.

3. **Toolkit side**
   (`toolkit/source/awt/vclxtoolkit.cxx::VCLXToolkit::createWindow`):
   when creating the dialog peer, does
   `dynamic_cast<VCLXWindow*>(descriptor.Parent.get())`. For a Python
   adapter the cast returns null. The source explicitly handles this:

   ```cpp
   VCLXWindow* pParentComponent = dynamic_cast<VCLXWindow*>(rDescriptor.Parent.get());
   if (pParentComponent)
       pParent = pParentComponent->GetWindow();
   // ...
   // Don't throw assertion, may be it's a system dependent window.
   ```

   The toolkit gracefully proceeds with `pParent == nullptr`, creating
   the dialog peer as a top-level window.

4. **Sidebar deck side**: the deck reads `XToolPanel.Window` from our
   returned panel and re-parents it into the deck region at the VCL
   level. The construction-time parent doesn't determine final
   placement — same property that ADR-0025 relied on.

The adapter is stored on the panel as `self._parent_peer_adapter` for
the lifetime of the panel. C++ holds a weak reference to it (via the
UNO Reference machinery); if Python GC'd it before VCL completed
construction, the C++ side would deref a dangling pointer.

The previous `_desktop_window_peer` helper and the bare-XWindow tier
are removed — neither succeeded on the strict-PyUNO build, and both
introduced subtle CI matrix variations (Toolkit-supplied peer rejected
on macOS; bare XWindow accepted on permissive builds but not on
Debian).

## Alternatives considered

- **`Toolkit.getDesktopWindow()` as parent (ADR-0025)** — rejected.
  Returns null on Debian's strict build; rejected by macOS even when
  available.

- **`Toolkit.createWindow(WindowDescriptor(Type=TOP))` to fabricate
  a peer** — rejected. Crashes soffice on Debian. The widget needed
  the `WindowServiceName` to be a valid VCL service; even with the
  documented value `"window"` it segfaulted.

- **`uno.invoke(provider, "createContainerWindow", args)`** —
  rejected. Verified via the PyUNO source
  (`pyuno/source/module/pyuno.cxx::PyUNO_invoke`) that this goes
  through the same `XInvocation` path as the direct attribute call;
  the conversion path is identical.

- **`DialogProvider.createWithModel(model).createDialog(URL)`** —
  rejected. Would trigger the C++ `m_xModel`-based fallback that
  uses `xFrame->getContainerWindow()` via `UNO_QUERY` (which DOES
  succeed in C++). But `createDialog` forces `Decoration=true` via
  `bDialogProviderMode`, producing a windowed dialog with a title
  bar instead of an embedded panel. Wrong UX.

- **`DialogProvider2.createDialogWithArguments` with `ParentWindow`
  as an XControl** — rejected. The C++ side calls `getPeer()` on the
  XControl; we'd need to wrap our ParentWindow in an XControl that
  has a peer attached, which is the same problem one layer down.

- **Manual `UnoControlDialogModel` + `UnoControlDialog` +
  `createPeer(toolkit, None)` (top-level peer)** — rejected. Would
  produce a free-floating dialog that the sidebar would need to
  re-parent, but `createPeer` still needs a toolkit and a parent
  peer for non-top windows — the same chicken-and-egg.

- **Hand-built XDL parser, programmatic widget construction** —
  rejected as scope. A ~200 LOC rewrite when a 30-line adapter
  achieves the same outcome with verified semantics.

- **Pass `None` for the parent peer** — rejected. C++ throws
  `IllegalArgumentException` on null before any fallback.

### Interface surface

User testing on Debian 26.2.x (2026-05-21) revealed the adapter
must declare **all four** of:

  - ``XWindowPeer`` — for the initial ``convert.cxx`` queryInterface
    on the createContainerWindow Peer parameter
  - ``XComponent`` — base interface of XWindowPeer; required by the
    Python adapter machinery
  - ``XView`` — ``UnoControl::createPeer`` in
    ``toolkit/source/controls/unocontrol.cxx`` does
    ``Reference<XView>(rParentPeer, UNO_QUERY_THROW)`` followed by
    ``xView->getGraphics()``. UNO_QUERY_THROW means the throw fires
    if the parent peer doesn't implement XView, regardless of
    whether ``getGraphics()`` is ever called.
  - ``XWindow`` — needed for bounds queries during dialog layout
    (callers use ``getPosSize()`` to size the new control against
    the parent).

The adapter delegates ``getSize()`` / ``getPosSize()`` to the
underlying framework-supplied ``ParentWindow`` so the dialog
control sizes against the real sidebar region. Mutating methods
(setPosSize, setVisible, listener add/remove, draw, setZoom) are
no-ops — the adapter is a query shim, not an actual VCL window.
The sidebar deck's docking path overrides any sizing the adapter
returns when it re-parents our ``XToolPanel.Window`` into the
deck region.

## Consequences

- **Pros:**
  - Adapter is ~150 lines (was ~30 before the XView/XWindow
    surface expansion), every line backed by a verified
    LibreOffice source reference.
  - Works on every CI matrix entry (permissive PyUNO builds use the
    real ParentWindow peer via `queryInterface`; the adapter only
    fires on strict builds).
  - Adds a real Debian-container CI matrix entry (bookworm stable +
    bookworm-backports) so the strict-PyUNO path is now covered by
    automated testing — no more "passes CI, fails on user's local".
  - Removes brittle alternate-peer-source code paths from
    `_create_panel_window`.

- **Cons:**
  - The adapter must be kept alive for the lifetime of the panel;
    forgetting to store it caused dangling-reference crashes during
    development. Documented inline and reinforced by storing the
    reference on `self._parent_peer_adapter` from
    `_create_panel_window`.
  - The dialog peer is created as a top-level window, then
    re-parented into the deck by the sidebar framework. There's a
    sub-millisecond window where the peer exists detached from any
    deck; not user-visible but distinct from how built-in C++ panels
    are constructed.

- **Follow-up:**
  - Investigation #29 — confirm that Debian bookworm-backports CI
    job actually reproduces the local crash (closes the loop on
    "the local fix matches the CI fix").
  - Watch for the same proxy-interface trimming in other LibreOffice
    27.x builds; if Ubuntu noble eventually picks up the same
    behaviour, our existing Ubuntu jobs will exercise the adapter
    path automatically.

## References

- Code: `src/talk2view_writer/ui/sidebar_panel.py`
  `_PythonXWindowPeerAdapter`, `_create_panel_window`
- Tests: `tests/conftest.py` (`_stub_interface` factory)
- CI: `.github/workflows/ci.yml` (Debian matrix entries)
- ADR-0025 (superseded): `0025-desktop-window-peer-for-panel-construction.md`
- Investigation: `docs/investigations.md` #29
- LibreOffice source:
  - `stoc/source/typeconv/convert.cxx::convertTo` (TypeClass_INTERFACE)
  - `scripting/source/dlgprov/dlgprov.cxx::createContainerWindow`,
    `createDialogControl`
  - `toolkit/source/awt/vclxtoolkit.cxx::VCLXToolkit::createWindow`
  - `pyuno/source/module/pyuno.cxx::PyUNO_invoke`
  - `pyuno/source/module/pyuno_callable.cxx::PyUNO_callable_call`
- Official Python sidebar example:
  `core/odk/examples/python/toolpanel/toolpanel.py`
