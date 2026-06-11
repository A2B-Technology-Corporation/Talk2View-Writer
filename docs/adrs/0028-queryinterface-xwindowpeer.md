# ADR-0028: Obtain XWindowPeer via `queryInterface` before `createContainerWindow`

**Status:** Superseded by [ADR-0029](0029-floating-chat-window.md)
**Date:** 2026-05-21
**Phase:** F
**Supersedes:** the "unsupported build" follow-up clause of ADR-0027

## Context

ADR-0027 adopted LibreOffice's canonical Python toolpanel pattern
verbatim and accepted that strict-PyUNO downstream builds (notably
Debian's apt-packaged LO 26.2.x) would be "unsupported environments"
that the extension does not run on. The follow-up plan was: detect
the failure, show a user-facing message, ask the user to install a
TDF/Flathub/Snap build.

When a Debian user actually hit the failure (2026-05-21 repro on
LO 26.2.3.2 with our newly-complete diagnostic logging — see
investigation #29 close-out comments and the full traceback in
`talk2view.log`), the trace exposed two facts that change the
decision:

1. The bare `XWindow` proxy the sidebar framework hands us has a
   restricted `XTypeProvider.getTypes()` that advertises only
   `XWindow`, `XComponent`, `XTypeProvider`, `XWeak` — **not**
   `XWindowPeer`. Hence the `CannotConvertException: value does not
   implement com.sun.star.awt.XWindowPeer` at PyUNO's argument
   marshalling step.
2. PyUNO's argument marshaller uses `getTypes()` to validate
   argument types, but `XInterface.queryInterface(Type)` does a real
   C++-side RTTI lookup that **bypasses** that cache. The underlying
   C++ object is a `VCLXWindow`, which does implement `XWindowPeer`
   in C++ regardless of what `getTypes()` advertises.

So the strict-PyUNO failure is not the absence of `XWindowPeer` on
the C++ object; it is the bridge's argument-type validator looking
at the wrong place. `queryInterface` is the documented UNO API for
this exact situation.

Telling enterprise users on Debian (a major Linux distribution) to
re-install LibreOffice from a different source is not a viable
support story. The original ADR-0027 stated re-evaluation should
happen if "a meaningful Debian-apt user base materialises." The
first user we tested with is on that exact build; the threshold is
met.

## Decision

Insert one extra line before `provider.createContainerWindow(...)`:

```python
xwp_type = uno.getTypeByName("com.sun.star.awt.XWindowPeer")
parent_peer = self._parent_window.queryInterface(xwp_type)
if parent_peer is None:
    raise UnsupportedLibreOfficeBuildError(...)
window = provider.createContainerWindow(
    dialog_url, "", parent_peer, None
)
```

The `queryInterface` call:

- On strict-PyUNO builds (Debian 26.2.x and similar): returns a
  PyUNO proxy whose runtime type info now says `XWindowPeer`. The
  next marshal step accepts it cleanly and the canonical path
  proceeds.
- On non-strict builds (TDF, Flathub, Snap, AppImage): returns
  effectively the same XWindow under its XWindowPeer face. The
  canonical path proceeds with no observable behavioural change.

This is the canonical UNO mechanism for obtaining a typed reference
to an interface that an object implements. It is documented in the
UNO IDL spec for `XInterface` and is the same call pattern that LO's
C++ code internally performs (`UNO_QUERY`/`UNO_QUERY_THROW`).

If `queryInterface` returns null — meaning the C++ object truly
does not implement XWindowPeer — we surface
`UnsupportedLibreOfficeBuildError` with the same message ADR-0027
defined. That code path remains the truthful "this build is
unsupported" signal; it is just no longer triggered by the strict
Debian PyUNO marshaller.

The detector `_is_strict_pyuno_xwindowpeer_failure` is updated to
match the leaf of the exception class name
(`type(exc).__name__.rsplit(".", 1)[-1]`) because PyUNO 26.2 names
the exception class with its full dotted path
(`com.sun.star.script.CannotConvertException`) — the previous
literal-name match failed. This is retained as defence-in-depth for
the edge case where `queryInterface` succeeds but `createContainerWindow`
still rejects the peer (a deeper C++ bug we cannot reach from
Python).

## Alternatives considered

- **Status quo (ADR-0027 — "tell users to switch build")** —
  rejected. Empirically untenable for enterprise distribution on
  the Linux distro that ships with the bug.

- **`uno.invoke()` to bypass argument marshalling** — investigated.
  `uno.invoke` routes through `XInvocation`, which has its own
  type-checking pipeline that is no more lenient than the regular
  marshaller. Not a real workaround.

- **Programmatic widget construction via `UnoControlContainer` +
  `createPeer(toolkit, parent_window)`** — rejected.
  `createPeer` has the same XWindowPeer argument-type constraint
  and fails identically. Even if we got past that, the
  pre-ADR-0027 attempt that bypassed peer creation entirely
  produced an empty panel because LO's sidebar dock code calls
  `VCLUnoHelper::GetWindow(xPanelWindow)` to reparent into the
  deck slot, and an unpeered UnoControlContainer returns null from
  that helper.

- **Switch to a non-modal floating dialog (`XDialogProvider2`)** —
  rejected for this iteration. Sidesteps the bug, but materially
  changes the product UX from "tab in the sidebar deck" to
  "separate window". The sidebar UX is in ADR-0003 and is the
  Talk2View brand contract.

- **Rewrite the panel in Java** — rejected (still). The full
  ADR-0027 reasoning still applies: Java runtime dependency, loss
  of Python tool-surface parity, weeks of porting. With the
  `queryInterface` workaround being a single line of well-supported
  Python, the cost/benefit no longer favours Java.

## Consequences

- **Pros:**
  - Single canonical code path that works on every LibreOffice
    build we have evidence for: Debian apt 26.2.x, TDF .deb,
    Flathub, Snap, AppImage. No conditional fallback selection.
  - Uses the same UNO mechanism (`queryInterface`) that
    LibreOffice's own C++ code uses internally to obtain typed
    interface references. Not a workaround; the documented API.
  - Five lines of new code, including the error path.
  - "Unsupported build" remains a possible outcome (if
    `queryInterface` returns null), but only when the underlying
    C++ object genuinely lacks XWindowPeer — a much rarer case
    than the previous false-positive.

- **Cons:**
  - `queryInterface` adds one extra UNO round-trip during panel
    construction. Negligible (microseconds) but non-zero.
  - We now ship code that exists because of a PyUNO bridge quirk.
    The comment in `_create_panel_window` documents the why so
    future maintainers don't strip it as redundant.

- **Follow-up:**
  - Verify against the originally-failing Debian build (LO 26.2.3.2
    apt) and against a TDF build to confirm both paths.
  - File the underlying PyUNO bridge bug upstream against
    LibreOffice. The argument marshaller checking `getTypes()`
    rather than calling `queryInterface` for XWindowPeer
    arguments is the root cause; fixing it upstream would remove
    the need for this extra line everywhere.
  - Update `README.md`: remove the "supported LibreOffice builds"
    caveat that was added in the wake of ADR-0027.

## References

- Code: `src/talk2view_writer/ui/sidebar_panel.py::_create_panel_window`
  (the `queryInterface(XWindowPeer)` call, ~10 lines including
  comment and error path)
- Tests: `tests/unit/test_sidebar_panel.py::TestCreatePanelWindowErrorPath`
  (queryInterface-null path) and `TestStrictPyUNOFailureDetection`
  (dotted-class-name detector)
- LibreOffice canonical example:
  `core/odk/examples/python/toolpanel/toolpanel.py`
  (does not use `queryInterface` because the build it was authored
  against did not require it)
- ADRs superseded (in part): ADR-0027's "unsupported build"
  follow-up clause
- Investigation: `docs/investigations.md` #29 (closure updated)
