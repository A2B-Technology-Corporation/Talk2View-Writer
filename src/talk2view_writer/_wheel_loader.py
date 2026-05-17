"""Runtime selector for the bundled ``pydantic_core`` wheel.

LibreOffice extensions cannot ship a single C-extension binary that
works across every {OS, CPU arch, Python minor version} combination —
``pydantic_core`` is a Rust extension and its ``.so`` / ``.pyd`` is
ABI-tagged. We bundle one extracted wheel per supported combination
under ``pythonpath/_vendored_wheels/<py>-<plat>/`` at build time;
this loader picks the one matching the *running* interpreter.

See ADR-0023 for the decision rationale and ``scripts/vendor_wheels.py``
for the build-time download step.

Order of preference at runtime:

  1. If ``pydantic_core`` is already importable (the user installed it
     themselves, or a previous load already prepared sys.path),
     do nothing.
  2. Otherwise, compute the runtime tag (``cp313-manylinux_x86_64``,
     ``cp312-macosx_arm64``, ``cp311-win_amd64``, …) and prepend
     the matching extracted-wheel directory to ``sys.path``.
  3. If no matching wheel is bundled, raise an :class:`ImportError`
     with a copy-paste install command so the user can recover
     manually.
"""

from __future__ import annotations

import logging
import os
import platform
import sys

logger = logging.getLogger(__name__)

# Resolve to the extension's ``pythonpath/`` — one parent up from the
# ``talk2view_writer/`` package directory this file lives in. The
# build step stages all bundled pure-Python deps (typing_extensions,
# pydantic, httpx, …) directly under here, and the per-platform
# wheel directories under ``_vendored_wheels/``.
_PYTHONPATH_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_VENDORED_ROOT = os.path.join(_PYTHONPATH_ROOT, "_vendored_wheels")

# Packages we bundle that often collide with system-Python copies of
# the same name. If LibreOffice (or any startup code) already imported
# an older system version, the cached ``sys.modules`` entry would win
# over our newer bundled copy. We evict + re-prepend before importing
# pydantic_core.
#
# typing_extensions: pydantic_core 2.46.4 imports ``Sentinel`` which
# only exists in typing_extensions >= 4.10. Debian ships 4.x in the
# system Python's dist-packages — older builds lack Sentinel.
# pydantic: 2.10+ adds the typing_inspection dependency that the
# system pydantic (if any) typically lacks.
_BUNDLED_OVERRIDE_PACKAGES: tuple[str, ...] = (
    "typing_extensions",
    "typing_inspection",
    "annotated_types",
    "pydantic",
)


def _python_tag() -> str:
    """Return ``cpXY`` for the running interpreter (e.g. ``cp313``)."""
    return f"cp{sys.version_info.major}{sys.version_info.minor}"


def _platform_tag() -> str:
    """Return a coarse platform tag matching the wheel-extraction names.

    Coarser than PEP 425 (we collapse ``manylinux_2_17_x86_64`` and
    ``manylinux2014_x86_64`` to a single ``manylinux_x86_64``) because
    the extracted directory only carries the binary — once unpacked,
    any glibc-compatible Linux can load it.
    """
    sys_name = platform.system()
    machine = platform.machine().lower()
    if sys_name == "Linux":
        if machine in ("x86_64", "amd64"):
            return "manylinux_x86_64"
        if machine in ("aarch64", "arm64"):
            return "manylinux_aarch64"
        return f"linux_{machine}"
    if sys_name == "Darwin":
        if machine == "arm64":
            return "macosx_arm64"
        return "macosx_x86_64"
    if sys_name == "Windows":
        if machine in ("amd64", "x86_64"):
            return "win_amd64"
        return f"win_{machine}"
    return f"{sys_name.lower()}_{machine}"


def runtime_tag() -> str:
    """Return ``<py-tag>-<plat-tag>`` for the running interpreter."""
    return f"{_python_tag()}-{_platform_tag()}"


def _candidate_directory() -> str:
    return os.path.join(_VENDORED_ROOT, runtime_tag())


def _manual_install_hint() -> str:
    return (
        f"Manual recovery — run this with your LibreOffice's Python:\n"
        f"  {sys.executable} -m pip install --user pydantic-core==2.46.4"
    )


def _prefer_bundled_pure_python_deps() -> None:
    """Force our pythonpath/ to win over the system Python for known deps.

    LibreOffice's pythonloader appends the extension's ``pythonpath/``
    to ``sys.path``, so the system's ``dist-packages`` resolves first
    for any module name they share. Worse, LibreOffice startup may
    have already imported the system copy into ``sys.modules`` before
    our extension loads.

    To fix:

      1. Insert ``pythonpath/`` at position 0 of ``sys.path``.
      2. Evict each entry in :data:`_BUNDLED_OVERRIDE_PACKAGES` from
         ``sys.modules`` if the cached copy did NOT come from
         ``pythonpath/`` (so the next import picks up the bundled
         file).

    The eviction is conservative — we only remove cached modules
    whose ``__file__`` lives outside our pythonpath. Anything already
    pointing at the bundled copy is left untouched (idempotent
    re-invocations don't disturb working state).
    """
    if _PYTHONPATH_ROOT not in sys.path:
        sys.path.insert(0, _PYTHONPATH_ROOT)
        logger.info("Prepended pythonpath root %s to sys.path", _PYTHONPATH_ROOT)
    elif sys.path.index(_PYTHONPATH_ROOT) != 0:
        sys.path.remove(_PYTHONPATH_ROOT)
        sys.path.insert(0, _PYTHONPATH_ROOT)
        logger.info("Moved pythonpath root %s to the front of sys.path", _PYTHONPATH_ROOT)

    for name in _BUNDLED_OVERRIDE_PACKAGES:
        cached = sys.modules.get(name)
        if cached is None:
            continue
        cached_file = getattr(cached, "__file__", None) or ""
        if cached_file.startswith(_PYTHONPATH_ROOT):
            continue
        # Also evict submodules so a stale ``pydantic.foo`` doesn't
        # outlive its parent.
        for cached_name in list(sys.modules):
            if cached_name == name or cached_name.startswith(name + "."):
                logger.info(
                    "Evicting cached %s (was loaded from %s)",
                    cached_name,
                    getattr(sys.modules[cached_name], "__file__", "<unknown>"),
                )
                del sys.modules[cached_name]


def ensure_vendored_pydantic_core() -> None:
    """Make ``pydantic_core`` importable from the bundled wheel.

    Idempotent. Safe to call multiple times.

    Steps:

      1. Repoint ``sys.path`` + evict stale ``sys.modules`` entries
         so our bundled pure-Python deps (typing_extensions, pydantic
         proper, …) win over the system Python's copies.
      2. If pydantic_core is *still* importable after step 1, return
         (the user / system already has a compatible copy).
      3. Otherwise prepend the per-platform wheel directory and
         re-attempt the import.

    Raises:
        ImportError: If no bundled wheel matches the runtime tag and
            ``pydantic_core`` is not already importable, OR the
            bundled wheel matched but a transitive import failed (the
            error wraps the original cause with a copy-paste install
            command).
    """
    _prefer_bundled_pure_python_deps()

    try:
        import pydantic_core

        logger.debug(
            "pydantic_core already importable from %s",
            getattr(pydantic_core, "__file__", "<unknown>"),
        )
        return
    except ImportError:
        pass

    tag = runtime_tag()
    candidate = _candidate_directory()
    if not os.path.isdir(candidate):
        bundled = sorted(os.listdir(_VENDORED_ROOT)) if os.path.isdir(_VENDORED_ROOT) else []
        raise ImportError(
            f"No bundled pydantic_core wheel for runtime tag '{tag}'. "
            f"Bundled tags: {bundled or '(none — was the .oxt built with make vendor-wheels?)'}.\n"
            f"{_manual_install_hint()}"
        )

    if candidate not in sys.path:
        sys.path.insert(0, candidate)
        logger.info("Prepended %s to sys.path", candidate)

    # Final verification: re-attempt the import. If it still fails,
    # either the wheel ABI is wrong (release-time bug) or a transitive
    # dep failed to resolve from the bundled pure-Python tree (a
    # missing dep in PY_RUNTIME_DEPS — the error message will name it).
    try:
        import pydantic_core

        logger.info("pydantic_core loaded from %s", getattr(pydantic_core, "__file__", candidate))
    except ImportError as exc:
        raise ImportError(
            f"Bundled wheel at {candidate} matched runtime tag '{tag}' "
            f"but pydantic_core import still fails: {exc}.\n"
            f"Common causes:\n"
            f"  - A transitive dep (look at the error text above) is "
            f"missing from the bundled pythonpath/. Add it to "
            f"PY_RUNTIME_DEPS in the Makefile and rebuild.\n"
            f"  - The bundled wheel ABI does not match the running "
            f"Python (a release-time error).\n"
            f"{_manual_install_hint()}"
        ) from exc
