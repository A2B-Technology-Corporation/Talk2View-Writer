# ADR-0027: Follow LibreOffice's canonical Python toolpanel pattern verbatim

**Status:** Accepted (the "unsupported build" follow-up clause is superseded by ADR-0028)
**Date:** 2026-05-21
**Phase:** F
**Supersedes:** ADR-0025, ADR-0026
**Superseded by:** ADR-0028 (only the "tell users to install a different build" response to strict-PyUNO failures; the canonical-pattern decision itself stands)

## Context

LibreOffice's only documented Python sidebar panel example is
[`odk/examples/python/toolpanel/toolpanel.py`](https://github.com/LibreOffice/core/blob/master/odk/examples/python/toolpanel/toolpanel.py).
Its panel-construction code is exactly three calls:

```python
pip = self.ctx.getValueByName(
    "/singletons/com.sun.star.deployment.PackageInformationProvider"
)
dialog_url = f"{pip.getPackageLocation(extensionID)}/{xdlPath}"
provider = self.ctx.ServiceManager.createInstanceWithContext(
    "com.sun.star.awt.ContainerWindowProvider", self.ctx
)
self.m_panelRootWindow = provider.createContainerWindow(
    dialog_url, "", self.xParentWindow, None
)
```

The third arg is the framework-supplied `ParentWindow` `XWindow`
passed through verbatim — no XWindowPeer query, no adapter,
no peer-resolution ladder. The container-window provider does its
own `UNO_QUERY` on the underlying VCL window to obtain the peer
it needs internally.

This pattern works correctly on every LibreOffice build except a
handful of stricter downstream PyUNO repackages (Debian's
`libreoffice-fresh` apt package for LO 26.2.x being the
specific case that motivated the earlier ADRs). On those builds,
PyUNO's bridge marshaller rejects the bare `XWindow` proxy at the
`XWindowPeer` slot of `createContainerWindow`.

ADR-0025 attempted to source the peer from
`Toolkit.getDesktopWindow()`. Returns null on the broken build.

ADR-0026 attempted a Python `XWindowPeer` adapter implementing
the C++ side's `UNO_QUERY` targets. Empirically (user repro
2026-05-21), even with `unohelper.Base.getTypes()` correctly
reporting the adapter implements XWindowPeer + XView + XWindow,
the PyUNO C++-side Adapter wrapper still returns null for
`queryInterface(XView)` and `UNO_QUERY_THROW` raises. This is a
limit of the PyUNO bridge's C++ Adapter implementation that we
cannot reach from inside Python.

Both workaround ADRs added unsupported code paths that deviate
from LibreOffice's documented Python pattern, and neither
actually fixed the underlying Debian-packaging bug.

## Decision

Implement the panel-construction path exactly as the LibreOffice
SDK's canonical Python toolpanel example does. One code path. No
peer-resolution ladder. No Python `XWindowPeer` adapter. No
programmatic-widget fallback. Pass the framework-supplied
`ParentWindow` directly into `ContainerWindowProvider.createContainerWindow`.

For builds where this canonical pattern fails (i.e. Debian's
broken PyUNO repackage), the correct response per LibreOffice
documentation is:

- **End users:** install a working LibreOffice — TDF's `.deb` from
  documentfoundation.org, Flatpak from Flathub, Snap, or
  AppImage. All of these ship a stock PyUNO bridge that the
  canonical Python toolpanel pattern works against.
- **Downstream maintainers:** file a bug with the packager whose
  PyUNO bridge config breaks the canonical example. The LibreOffice
  SDK's own example is a regression-test gating signal — if it
  fails on a downstream build, the build is broken, not the
  pattern.

The previously-added workaround scaffolding (`_PythonXWindowPeerAdapter`,
`_resolve_parent_peer`, `_desktop_window_peer`, `_build_panel_programmatic`)
is removed from `ui/sidebar_panel.py`. ADR-0026's `XView`/`XWindow`
test-conftest stubs and the corresponding production imports are
removed. `tests/conftest.py` drops the `_lang.XComponent`,
`_awt.XView`, `_awt.XWindow` stubs that only existed to support
the adapter.

## Alternatives considered

- **Keep the Python `XWindowPeer` adapter (ADR-0026)** — rejected.
  Empirically it doesn't fix the bug on the targeted build (the
  PyUNO C++ Adapter doesn't honour declared types beyond
  XWindowPeer itself; `queryInterface(XView)` still returns null).
  Adds ~150 LOC of UNO-shim code to the production hot path for
  zero realised benefit. Deviates from the only documented
  pattern.

- **Programmatic widget construction via `UnoControlDialogModel`
  + `createPeer(toolkit, None)`** — rejected. Would have worked
  around the strict-PyUNO XWindowPeer requirement (no parent
  peer needed), but the path isn't documented in LibreOffice's
  SDK as a sidebar pattern. Adopting an undocumented approach
  for an FDA-track codebase that values "single explicit code
  path, easy security review" is the wrong trade.

- **Rewrite the panel UI in Java or C++** — rejected for this
  iteration. The Java sidebar pattern (e.g. AnalogClock-style)
  does avoid the PyUNO bridge limitations because the Java UNO
  bridge handles multi-interface implementations properly. But
  rewriting would:
    - require a Java runtime dependency on every install;
    - lose our Python-tool-surface parity with Talk2View-Word's
      TypeScript tools (filed in repo overview);
    - cost weeks of porting time.
  Re-evaluate only if (a) the Debian-packaging bug isn't fixed
  upstream and (b) a meaningful Debian-apt user base materialises.

- **Run a multi-path "try canonical then fall back" mechanism** —
  rejected. Different code paths on different builds means
  different runtime behaviour on different deployments. For an
  FDA-track codebase, "deterministic single path" wins over
  "covers more edge cases via runtime selection." If the canonical
  pattern doesn't work on a build, the build is unsupported.

## Consequences

- **Pros:**
  - Sidebar panel implementation matches LibreOffice's
    documented canonical Python pattern verbatim. Single explicit
    code path. No conditional behaviour, no fallback selection.
  - Easier security review and audit for the
    FDA-track release. Anyone familiar with LibreOffice extension
    development can read this code and verify it follows the
    canonical pattern at a glance.
  - Removed ~400 LOC of speculation code (adapter, peer-resolution
    ladder, programmatic builder, Toolkit-peer escalation).
  - When the canonical pattern fails, the failure is loud and
    diagnosable (a `CannotConvertException` traceback with the
    interface type name) rather than producing partially-built or
    empty panels.

- **Cons:**
  - Users on Debian's apt-packaged LO 26.2.x (and any other
    downstream build with the same PyUNO strictness setting)
    cannot use the extension as-is. They must switch to TDF
    builds or wait for Debian to fix their packaging.
  - We've accepted a hard "supported LibreOffice builds" list
    instead of trying to work around every downstream variant.

- **Follow-up:**
  - Document the supported-builds list in `README.md` under
    "Installation".
  - Add a runtime check that detects the strict-PyUNO failure
    mode and shows a user-facing message ("This LibreOffice
    build has a known incompatibility; please install LibreOffice
    from documentfoundation.org") instead of an empty panel.
  - File a bug report against Debian's `libreoffice-fresh`
    package describing the regression vs the official Python
    toolpanel example.
  - Close investigation #29 with a pointer to this ADR.

## References

- Code: `src/talk2view_writer/ui/sidebar_panel.py::_create_panel_window`
  (the entire body is ~30 lines, mirroring `toolpanel.py`)
- Tests: `tests/integration/test_sidebar_dock.py` (verifies the
  canonical pattern produces a peered window on supported builds)
- LibreOffice canonical example:
  `core/odk/examples/python/toolpanel/toolpanel.py`
- ADRs superseded: ADR-0025, ADR-0026
- Investigation: `docs/investigations.md` #29
