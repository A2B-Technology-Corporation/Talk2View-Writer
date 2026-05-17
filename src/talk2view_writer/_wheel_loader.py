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

# Directory holding ``<py-tag>-<plat-tag>/pydantic_core/...`` subdirs.
# Resolves to the extension's installed location: walking up from
# ``pythonpath/talk2view_writer/_wheel_loader.py`` two parents reaches
# ``pythonpath/`` which is where the build step staged the wheels.
_VENDORED_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "_vendored_wheels",
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


def ensure_vendored_pydantic_core() -> None:
    """Make ``pydantic_core`` importable.

    Idempotent. Safe to call multiple times.

    Raises:
        ImportError: If no bundled wheel matches the runtime tag and
            ``pydantic_core`` is not already importable. The exception
            message includes the detected tag and a manual-install
            command the user can copy-paste.
    """
    try:
        import pydantic_core

        logger.debug("pydantic_core already importable; no vendored load needed")
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

    # Final verification: re-attempt the import. If it still fails, we
    # bundled a wheel that doesn't match the ABI of the running Python
    # (a build-step bug rather than a missing-wheel one).
    try:
        import pydantic_core  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            f"Bundled wheel at {candidate} matched runtime tag '{tag}' "
            f"but pydantic_core import still fails: {exc}.\n"
            f"This indicates an ABI mismatch in the bundled wheel "
            f"(likely a release-time error).\n"
            f"{_manual_install_hint()}"
        ) from exc
