# ADR-0038: Spawn LO-bundled Python on macOS + bundle pyobjc as universal2 wheels

**Status:** Accepted
**Date:** 2026-05-27
**Phase:** F (cross-platform packaging)
**Supersedes:** the macOS-Python-discovery assumption in ADR-0030.
**Superseded by:** —

## Context

ADR-0030 chose pywebview-in-a-subprocess for the chat UI. Its
"Implementation" notes assumed pyobjc would be "pre-installed with
system Python" on macOS — i.e. that the subprocess could be launched
with whatever Python `shutil.which("python3")` returned and pywebview's
Cocoa backend would resolve transparently. Two related facts make that
assumption unworkable in production:

1. **Apple's system `/usr/bin/python3` ships without pyobjc** on
   modern macOS. The "system Python with PyObjC" assumption was true
   for the pre-3.x macOS Python era; current Command-Line-Tools Python
   (3.9-class) has only the standard library.
2. **LibreOffice sets `PYTHONHOME` to its own bundled Python
   framework in its process environment.** A child process spawned
   with `env=os.environ.copy()` inherits that, so the system
   `python3` (3.9) tries to load LibreOffice's bundled stdlib
   (3.12), and dies at module-load with::

       Fatal Python error: init_sys_streams
       ImportError: cannot import name 'text_encoding' from 'io'

   `io.text_encoding` was added in Python 3.10, so the 3.9 interpreter
   crashes immediately on the 3.12 stdlib.

The combination — wrong Python *and* missing pyobjc — blocked
"Open Talk2View Chat" on every macOS install, with no entry in the
user-visible UI: the dispatch logged "completed cleanly" while the
subprocess crashed in <100 ms before pywebview could draw a window.

## Decision

Two layered changes, both production-grade:

### 1. Spawn the LibreOffice-bundled Python interpreter, not the system one.

`WebWindow._resolve_python` on macOS now resolves the canonical
LO Python wrapper at `<install>/Contents/Resources/python`. Discovery
order, most-authoritative first:

1. **`URE_BOOTSTRAP` env var.** LO sets this on every process it
   owns; its value (`vnd.sun.star.pathname:<path>`) encodes the
   install root unambiguously. This is the official LO install-
   discovery mechanism and handles portable / non-`/Applications`
   installs without guessing.
2. **`.app`-ancestor walk of `sys.executable`.** Iterates *every*
   `.app` ancestor (not just the first) because LO's bundled Python
   actually lives inside a nested `Python.app` *inside* the outer
   `LibreOffice.app` — the inner has no wrapper, the outer one does.
3. **Hardcoded `/Applications/LibreOffice.app/Contents/Resources/python`.**
   Last-resort fallback for hosts that aren't LO (e.g. unit tests
   running under the user's own Python).

The existing `T2V_PYTHON` env-var override remains as a development /
support-engineer escape hatch.

### 2. Bundle pyobjc as universal2 wheels alongside `pydantic_core`.

Following the ADR-0023 pattern: `scripts/vendor_wheels.py` now also
downloads pyobjc for each macOS row of the matrix and extracts every
top-level package into the same `vendor/extracted/<py-tag>-<plat-tag>/`
directory the loader already prepends to `sys.path`.

Direct requirements (`PYOBJC_DIRECT_REQUIREMENTS` in the script):

- `pyobjc-framework-WebKit` (`WebKit`, `JavaScriptCore`)
- `pyobjc-framework-security` (cert / keychain APIs pywebview's
  Cocoa backend pulls in for HTTPS)

Pip resolves the transitive deps automatically: `pyobjc-core` (`objc`,
`PyObjCTools`) and `pyobjc-framework-Cocoa` (`AppKit`, `Foundation`,
`Cocoa`, `CoreFoundation`). All four ship as `universal2` wheels —
one binary covers arm64 + x86_64 — so the same content is extracted
into both `macosx_arm64` and `macosx_x86_64` rows.

Runtime selection: `talk2view_writer._wheel_loader.ensure_vendored_pyobjc()`
mirrors `ensure_vendored_pydantic_core`, with `objc` as the canary
import. Called from `web_runner.main()` immediately before
`import webview` so pywebview's `initialize(gui='cocoa')` resolves
cleanly without touching system Python's `dist-packages`.

This expressly reverses ADR-0030's assumption. ADR-0030 remains
otherwise authoritative — pywebview subprocess + Unix-socket bridge
is unchanged.

## Alternatives considered

- **Strip `PYTHONHOME` from the subprocess env and use system
  Python.** The system Python would then load its own stdlib instead
  of LO's 3.12 stdlib, avoiding the `text_encoding` crash. Rejected
  because (1) Apple's CLT Python still has no pyobjc, so we'd hit
  the AppKit ImportError anyway, and (2) Apple has been quietly
  deprecating system-Python and may remove it entirely in a future
  macOS release.

- **Ask the user to install pyobjc into LO's Python at first run.**
  Rejected for the same reasons ADR-0023 rejected lazy-pip-install:
  pip may not ship in LO's bundled Python, PEP 668 blocks
  installs on increasingly many distros, corporate firewalls
  intercept PyPI traffic, and silent failures destroy the UX.

- **Switch off pywebview entirely; write a native Cocoa
  NSWindow + WKWebView host using pyobjc directly.** Removes the
  pywebview indirection but still requires pyobjc to be bundled.
  Also requires writing separate Linux (GTK) and Windows
  (EdgeChromium) backends to keep ADR-0030's cross-platform UI
  promise. Net: more code, more surface area, identical bundle
  size. Revisit only if pywebview becomes a maintenance burden.

- **Bundle the whole `pyobjc` umbrella package.** ~80 MB extracted;
  pulls in every macOS framework binding (Quartz, Security, Accounts,
  Photos, ...). Rejected as gross over-fetch — the four packages
  pywebview's docs list as "suffice" are ~1.1 MB combined.

- **Bundle the minimum (`-WebKit` only, no `-security`).** Tempting
  per "don't add deps beyond what the module-level imports require"
  — pywebview's bundled `cocoa.py` imports only `AppKit`,
  `Foundation`, `WebKit`, `objc`, `PyObjCTools` at the top level.
  Rejected because pywebview's own docs list `pyobjc-framework-security`
  in the "these packages suffice" set, implying runtime use we
  can't see from a static read (likely cert / keychain access on
  HTTPS calls inside the WebKit view). Cost is ~50 KB; skipping
  it would be premature optimization that fails in the field on
  cert-strict networks.

## Consequences

**Pros**

- "Open Talk2View Chat" actually opens a chat on macOS.
- Aligned with the official LO discovery mechanism (URE_BOOTSTRAP);
  works on `/Applications` installs and arbitrary portable installs.
- Aligned with the pyobjc + pywebview maintainers' documented
  minimum dep set; not relying on undocumented behaviour.
- Tested without any real `.so` import — unit tests cover discovery
  paths + canary-import failure modes; the happy path is exercised
  end-to-end whenever the bundle is rebuilt with `make vendor-wheels`.

**Cons**

- OXT grows by ~1.1 MB per macOS matrix row (8 rows = 9 MB total
  for the pyobjc layer, on top of pydantic_core's ~94 MB).
- Two more `PYOBJC_VERSION_SPEC`-style version pins to maintain
  in `scripts/vendor_wheels.py` whenever LO ships a new bundled-Python
  minor version. Same maintenance burden as pydantic_core (ADR-0023).
- The Windows port still falls through `_resolve_python`'s
  `shutil.which("python")` branch and will hit the same ABI-mismatch
  + missing-pythonnet problem the second a Windows user tries the
  chat. Tracked in `docs/investigations.md` as the natural
  follow-up.

**Follow-up**

- Investigation #48 (`docs/investigations.md`): same gap exists on
  Windows — `_resolve_python` falls through to `shutil.which`,
  which won't match LO's bundled Python ABI. Mirror the macOS
  approach (URE_BOOTSTRAP parse + bundle pythonnet wheels) when
  Windows users land.
- Bump `PYOBJC_VERSION_SPEC` when LO upgrades its bundled Python
  minor version. The bump must be paired with a manual
  "click Open Chat, see window appear" smoke test on each
  supported macOS arch.

## References

- Code: `src/talk2view_writer/ui/web_window.py` (`_wrapper_from_ure_bootstrap`,
  `_find_lo_bundled_python_darwin`)
- Code: `src/talk2view_writer/_wheel_loader.py` (`ensure_vendored_pyobjc`)
- Code: `src/talk2view_writer/web_runner.py` (call site, before
  `import webview`)
- Code: `scripts/vendor_wheels.py` (`PYOBJC_DIRECT_REQUIREMENTS`,
  `_vendor_pyobjc_for_macos_rows`)
- Tests: `tests/unit/test_web_window.py::TestResolvePython` (URE_BOOTSTRAP,
  nested-`.app` walk, hardcoded fallback)
- Tests: `tests/unit/test_wheel_loader.py::TestEnsureVendoredPyobjc`
- Related ADRs: ADR-0023 (wheel-bundling pattern), ADR-0030
  (pywebview subprocess — the assumption this ADR corrects)
- External: pywebview install guide
  (<https://pywebview.flowrl.com/guide/installation.html>)
  — quotes the "pyobjc-core / -Cocoa / -WebKit / -security suffice"
  set this ADR pins to.
