# ADR-0023: Bundle pre-built ``pydantic_core`` wheels for the cross-platform matrix

**Status:** Accepted
**Date:** 2026-05-17
**Phase:** F (cross-platform packaging)
**Supersedes:** the in-flight Phase-F "subprocess pip install" sketch (never committed).

## Context

The Talk2View Python SDK transitively depends on ``pydantic`` v2,
which in turn depends on ``pydantic_core`` — a Rust extension whose
binary (``.so`` / ``.pyd``) is ABI-tagged to a specific
{OS, CPU arch, Python minor version} combination. Every other runtime
dep in our tree (``httpx``, ``httpcore``, ``h11``, ``certifi``,
``idna``, ``anyio``, ``sniffio``, ``pydantic`` proper, ``talk2view``,
``annotated_types``, ``typing_extensions``) is pure Python.

LibreOffice ships **its own Python interpreter** on Windows and macOS
and uses the **system Python** on Linux. The combination of LibreOffice
release + host OS + host CPU yields a different ABI tag on each user's
machine — there is no single ``pydantic_core`` binary we can bundle
that works for everyone.

Bundling against the dev-venv's Python (3.12 in our case) was the
initial naïve approach; it produces a ``.oxt`` that fails to import
under LibreOffice's interpreter on any other Python minor version.

## Decision

**Vendor pre-built ``pydantic_core`` wheels for a fixed
cross-platform matrix at *build* time, and pick the matching wheel at
runtime via a small loader.**

Matrix (in ``scripts/vendor_wheels.py``):

  Python: ``cp310``, ``cp311``, ``cp312``, ``cp313``
  Platform: ``manylinux_x86_64``, ``manylinux_aarch64``,
            ``macosx_x86_64``, ``macosx_arm64``, ``win_amd64``

  4 × 5 = **20 wheels**, ~39 MB of raw ``.whl`` files, ~94 MB once
  each is extracted. The extracted directory for each tag lands under
  ``vendor/extracted/<py-tag>-<plat-tag>/pydantic_core/`` and is
  copied verbatim into ``pythonpath/_vendored_wheels/`` by the
  Makefile's ``build`` target.

Runtime selection lives in
``src/talk2view_writer/_wheel_loader.py``:

  1. If ``pydantic_core`` is already importable, do nothing
     (a user-side ``pip install`` of ``pydantic-core`` wins over
     the bundled copy).
  2. Otherwise compute ``runtime_tag()`` and prepend
     ``_vendored_wheels/<tag>/`` to ``sys.path``.
  3. If no matching tag exists, raise ``ImportError`` with the
     detected tag, the list of bundled tags, and a manual-install
     command the user can copy-paste.

The loader is invoked exactly once, lazily, inside
``Talk2ViewWriterExtension.sdk`` (the property the sidebar panel
hits on its first chat message). Tests live in
``tests/unit/test_wheel_loader.py``.

The vendor matrix is **gitignored** — ``make vendor-wheels``
downloads it via ``uvx pip download`` (uv-managed venvs don't ship
pip, so we run pip in an ephemeral environment). Release builds
re-run this target before ``make package``.

## Alternatives considered

- **Bundle whatever ``.so`` lives in the dev venv.**
  Tried first, abandoned — only works for users whose interpreter
  happens to match the dev machine's Python minor version + arch.
  Failed even on the dev machine because LibreOffice 26.x on Debian
  uses Python 3.13 while uv had given us a 3.12 venv.

- **Subprocess ``pip install --user`` at first run.**
  Sketched out; rejected because:
  - PEP 668 (``externally-managed-environment``) blocks system-Python
    installs on modern Debian/Ubuntu/Fedora without
    ``--break-system-packages``.
  - LibreOffice's bundled Python on Windows / macOS doesn't ship
    pip in every distribution.
  - Corporate firewalls block PyPI at first launch.
  - sys.path doesn't always pick up ``--user`` installs without a
    process restart.
  Acceptable for "click-through to install" UIs (e.g. SlicerCAT-style
  package managers); not acceptable for an extension targeting
  general LibreOffice users.

- **Drop pydantic entirely and ship a pure-Python SDK wrapper.**
  Forking the Talk2View Python SDK to use ``dataclasses`` instead of
  pydantic for event models would eliminate the C-extension
  problem — but it would also create a maintenance fork against
  the canonical SDK and slow iteration when new event types are added.
  Revisit if the wheel matrix becomes painful.

- **Ship one ``.oxt`` per platform.**
  Honest but the user-facing UX (pick-your-platform install) is bad,
  and we'd still need the matrix logic — just split across multiple
  release artifacts.

- **Bundle wheels and ``zipimport`` them in place.**
  Pure-Python zip imports work; ``.so`` files inside a zip do **not**
  load (CPython's dynamic loader needs a real filesystem path).
  Extracting at build time avoids any runtime extraction cost.

## Consequences

**Pros**
- Works offline. No network at install time, no network at first
  launch.
- Works for ~95% of LibreOffice users on x86_64 / arm64 Linux,
  Intel / Apple-silicon macOS, and x86_64 Windows, on Python 3.10
  through 3.13.
- The loader's pre-check honours a user-side ``pip install`` so
  power users can override the bundled copy.
- Clear, copy-pasteable error when an exotic platform isn't covered.

**Cons**
- ``.oxt`` grows from ~5 MB to ~100 MB.
- Matrix needs maintenance: when LibreOffice ships with a newer
  Python minor version, ``MATRIX`` in ``scripts/vendor_wheels.py``
  must add a new ``cpXY`` row, then ``make vendor-wheels && make
  package``. Tracked as Investigation #24.
- We must re-vendor when ``pydantic_core`` is bumped (``uv lock
  --upgrade``); the version constant
  ``PYDANTIC_CORE_VERSION`` in ``scripts/vendor_wheels.py`` and the
  hint in ``_wheel_loader._manual_install_hint`` need to be kept
  in lockstep with ``uv.lock``.

**Follow-up**
- Investigation #24: matrix-maintenance cadence + automation.
- If the matrix grows beyond ~25 targets, consider lazy on-demand
  download from PyPI as a fallback instead of bundling every tag.

## References

- ``src/talk2view_writer/_wheel_loader.py``
- ``scripts/vendor_wheels.py``
- ``tests/unit/test_wheel_loader.py``
- ``docs/investigations.md`` #24
- ADR-0006 (deps bundling pattern this builds on)
