"""Download + extract ``pydantic_core`` wheels for the release matrix.

Run once per ``pydantic_core`` version bump:

    uv run python scripts/vendor_wheels.py

The script:

  1. Reads the target version from ``PYDANTIC_CORE_VERSION`` below
     (kept in sync with the resolved version in ``uv.lock``).
  2. For every (python-tag, platform-tag) in :data:`MATRIX`, runs
     ``pip download --only-binary=:all: --no-deps`` and saves the
     wheel under ``vendor/wheels/``.
  3. Extracts each wheel's ``pydantic_core/`` directory into
     ``vendor/extracted/<py-tag>-<plat-tag>/pydantic_core/``.
  4. ``make build`` copies the matching extracted directory under
     ``pythonpath/_vendored_wheels/`` at package time.

The ``vendor/`` directory is gitignored; CI / release builds
re-run this script before packaging. Storing the wheels locally
saves bandwidth and lets ``make package`` work offline once
the matrix has been populated.

Matrix-maintenance note: every time a new LibreOffice release ships
with a newer bundled Python, add the matching ``cpXY`` row to
:data:`MATRIX`. See ``docs/investigations.md`` #24 for the
re-evaluation cadence.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

# Keep in lockstep with the version in ``uv.lock``.
# Bump in tandem when ``uv lock --upgrade`` selects a new pydantic-core.
PYDANTIC_CORE_VERSION = "2.46.4"

# pyobjc packages bundled for the macOS Cocoa backend of pywebview.
# Per pywebview's official install docs the minimum sufficient set is:
#
#   pyobjc-core, pyobjc-framework-Cocoa,
#   pyobjc-framework-WebKit, pyobjc-framework-security
#
# We list ``-WebKit`` + ``-security`` as direct requests; pip resolves
# ``-core`` and ``-Cocoa`` transitively. All four ship as ``universal2``
# wheels (arm64 + x86_64 in one binary) per Python minor version, so
# the same set is extracted into BOTH macOS arch directories — the
# loader prepends one path per runtime tag and finds AppKit /
# Foundation / WebKit / objc / PyObjCTools regardless of arch. See
# ADR-0038.
PYOBJC_DIRECT_REQUIREMENTS: tuple[str, ...] = (
    "pyobjc-framework-WebKit",
    "pyobjc-framework-security",
)
# Pin the pyobjc major version that the OXT was last validated
# against. Bump in tandem with a manual test that webview.start()
# still succeeds — pyobjc occasionally drops macOS-version support
# in major releases, so this is the matrix-maintenance counterpart
# to PYDANTIC_CORE_VERSION above.
PYOBJC_VERSION_SPEC = ">=12.1,<13"


# (python_tag, abi_tag, platform_tag, our_short_plat_tag)
#
# - python_tag + abi_tag drive ``pip download --python-version --abi``
# - platform_tag drives ``pip download --platform``
# - our_short_plat_tag is what the runtime loader looks for (matches
#   the format produced by ``_wheel_loader._platform_tag()``)
MATRIX: tuple[tuple[str, str, str, str], ...] = (
    # ---- Linux x86_64 ----
    ("cp310", "cp310", "manylinux_2_17_x86_64", "manylinux_x86_64"),
    ("cp311", "cp311", "manylinux_2_17_x86_64", "manylinux_x86_64"),
    ("cp312", "cp312", "manylinux_2_17_x86_64", "manylinux_x86_64"),
    ("cp313", "cp313", "manylinux_2_17_x86_64", "manylinux_x86_64"),
    # ---- Linux aarch64 ----
    ("cp310", "cp310", "manylinux_2_17_aarch64", "manylinux_aarch64"),
    ("cp311", "cp311", "manylinux_2_17_aarch64", "manylinux_aarch64"),
    ("cp312", "cp312", "manylinux_2_17_aarch64", "manylinux_aarch64"),
    ("cp313", "cp313", "manylinux_2_17_aarch64", "manylinux_aarch64"),
    # ---- macOS x86_64 (Intel) ----
    ("cp310", "cp310", "macosx_11_0_x86_64", "macosx_x86_64"),
    ("cp311", "cp311", "macosx_11_0_x86_64", "macosx_x86_64"),
    ("cp312", "cp312", "macosx_11_0_x86_64", "macosx_x86_64"),
    ("cp313", "cp313", "macosx_11_0_x86_64", "macosx_x86_64"),
    # ---- macOS arm64 (Apple silicon) ----
    ("cp310", "cp310", "macosx_11_0_arm64", "macosx_arm64"),
    ("cp311", "cp311", "macosx_11_0_arm64", "macosx_arm64"),
    ("cp312", "cp312", "macosx_11_0_arm64", "macosx_arm64"),
    ("cp313", "cp313", "macosx_11_0_arm64", "macosx_arm64"),
    # ---- Windows x86_64 ----
    ("cp310", "cp310", "win_amd64", "win_amd64"),
    ("cp311", "cp311", "win_amd64", "win_amd64"),
    ("cp312", "cp312", "win_amd64", "win_amd64"),
    ("cp313", "cp313", "win_amd64", "win_amd64"),
)


REPO = Path(__file__).resolve().parents[1]
WHEELS_DIR = REPO / "vendor" / "wheels"
EXTRACTED_DIR = REPO / "vendor" / "extracted"


def _short_python_tag(py_tag: str) -> str:
    """``cp313`` -> ``cp313`` (passthrough; kept for symmetry)."""
    return py_tag


def _download(py_tag: str, abi_tag: str, plat_tag: str) -> Path | None:
    """Download one wheel; return its filesystem path or ``None`` on miss."""
    WHEELS_DIR.mkdir(parents=True, exist_ok=True)
    py_ver = f"{py_tag[2]}.{py_tag[3:]}"  # cp313 -> 3.13
    # ``uvx pip download`` runs pip in an ephemeral environment so we
    # don't need pip installed inside our uv-managed venv (uv does not
    # ship pip by default).
    cmd = [
        "uvx",
        "pip",
        "download",
        "--only-binary=:all:",
        "--no-deps",
        "--python-version",
        py_ver,
        "--platform",
        plat_tag,
        "--implementation",
        "cp",
        "--abi",
        abi_tag,
        "--dest",
        str(WHEELS_DIR),
        f"pydantic-core=={PYDANTIC_CORE_VERSION}",
    ]
    print(f"  $ {' '.join(cmd[2:])}")
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        print(f"    SKIP — pip download failed:\n    {result.stderr.strip()}")
        return None
    # pip's ``--platform`` flag filters for *compatibility*, then pip
    # downloads whatever wheel actually satisfies the request — that
    # filename may carry an older / coarser platform tag (e.g. asking
    # for ``macosx_11_0_x86_64`` yields ``macosx_10_12_x86_64.whl``
    # because the older deployment target is forward-compatible). Match
    # by python + abi tag only and pick the most recently written file.
    candidates = sorted(
        WHEELS_DIR.glob(f"pydantic_core-{PYDANTIC_CORE_VERSION}-{py_tag}-{abi_tag}-*.whl"),
        key=lambda p: p.stat().st_mtime,
    )
    return candidates[-1] if candidates else None


def _extract(wheel: Path, dest: Path) -> None:
    """Extract only the ``pydantic_core/`` directory from ``wheel`` to ``dest``."""
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(wheel) as zf:
        for member in zf.namelist():
            # Skip dist-info; we only need the importable package.
            if member.startswith("pydantic_core/"):
                zf.extract(member, dest)


def _extract_all_top_level(wheel: Path, dest: Path) -> list[str]:
    """Extract every top-level package from ``wheel`` into ``dest``.

    Unlike :func:`_extract` (single-package), this is the right tool
    for the pyobjc framework wheels which each contribute multiple
    sibling top-level directories (e.g. ``pyobjc_framework_cocoa``
    contributes ``AppKit/``, ``Foundation/``, ``Cocoa/``,
    ``CoreFoundation/``, and ``PyObjCTools/``).

    Skips ``*.dist-info/`` metadata directories — the loader doesn't
    need them and keeping them out keeps the OXT smaller.

    Returns the sorted list of top-level directory names extracted,
    useful for the script's per-target summary log.
    """
    dest.mkdir(parents=True, exist_ok=True)
    extracted: set[str] = set()
    with zipfile.ZipFile(wheel) as zf:
        for member in zf.namelist():
            top = member.split("/", 1)[0]
            if not top or top.endswith(".dist-info"):
                continue
            zf.extract(member, dest)
            extracted.add(top)
    return sorted(extracted)


def _download_pyobjc(py_tag: str, abi_tag: str, plat_tag: str) -> list[Path]:
    """Download pyobjc wheels (direct + transitive) for one target.

    Unlike ``_download``, this resolves transitive deps so pip pulls
    in pyobjc-core + pyobjc-framework-Cocoa automatically alongside
    the direct requests. Returns the list of wheel paths matching
    the requested python + abi tag — empty ⇒ pip download failed
    (the matrix entry will be reported as a miss in the script
    summary).

    NOTE: pyobjc ships ``universal2`` wheels, so calling pip with
    ``--platform macosx_11_0_arm64`` AND ``--platform macosx_11_0_x86_64``
    in sequence will resolve to the SAME wheel filename for both
    arches. pip skips the re-download on the second call; matching
    by ``cpXY-cpXY-*.whl`` filename instead of a set-diff handles
    that without losing the second arch's results.
    """
    WHEELS_DIR.mkdir(parents=True, exist_ok=True)
    py_ver = f"{py_tag[2]}.{py_tag[3:]}"  # cp313 -> 3.13
    cmd = [
        "uvx",
        "pip",
        "download",
        "--only-binary=:all:",
        "--python-version",
        py_ver,
        "--platform",
        plat_tag,
        "--implementation",
        "cp",
        "--abi",
        abi_tag,
        "--dest",
        str(WHEELS_DIR),
        *[f"{req}{PYOBJC_VERSION_SPEC}" for req in PYOBJC_DIRECT_REQUIREMENTS],
    ]
    print(f"  $ {' '.join(cmd[2:])}")
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        print(f"    SKIP — pip download failed:\n    {result.stderr.strip()}")
        return []
    return sorted(WHEELS_DIR.glob(f"pyobjc_*-*-{py_tag}-{abi_tag}-*.whl"))


def _vendor_pyobjc_for_macos_rows(misses: list[str]) -> list[str]:
    """Download + extract pyobjc into every macOS row of MATRIX.

    pyobjc ships universal2 wheels — the same binary content lands
    in both ``cpXY-macosx_arm64`` and ``cpXY-macosx_x86_64``
    extracted directories. Pip caches downloads across runs so the
    second arch is essentially free.

    Appends per-target failure tags to ``misses`` (passed by
    reference) and returns the list of successful targets so the
    script's main summary can roll both wheel sets together.
    """
    successes: list[str] = []
    print("\nVendoring pyobjc (Cocoa backend of pywebview) for macOS rows")
    for py_tag, abi_tag, plat_tag, our_plat_tag in MATRIX:
        if not our_plat_tag.startswith("macosx_"):
            continue
        target = f"{py_tag}-{our_plat_tag}"
        print(f"\n[{target}] (pyobjc)")
        wheels = _download_pyobjc(py_tag, abi_tag, plat_tag)
        if not wheels:
            misses.append(f"pyobjc:{target}")
            continue
        dest = EXTRACTED_DIR / target
        # NOTE: deliberately don't shutil.rmtree(dest) here — the
        # pydantic-core extraction already populated this directory
        # earlier in main(). pyobjc and pydantic_core have disjoint
        # top-level dir names, so adding pyobjc's modules alongside
        # is safe.
        for wheel in wheels:
            extracted = _extract_all_top_level(wheel, dest)
            print(f"    + {wheel.name} -> {', '.join(extracted)}")
        successes.append(target)
    return successes


def main() -> int:
    """Refresh the cross-platform wheel matrix (pydantic-core + pyobjc).

    Clears ``vendor/wheels`` + ``vendor/extracted``, downloads the
    pinned pydantic_core wheels for every row of :data:`MATRIX`, then
    layers pyobjc wheels on the macOS rows. Exits 1 if any target
    failed to download (the summary names which ones).
    """
    if WHEELS_DIR.exists():
        print(f"Clearing previous wheels under {WHEELS_DIR}")
        shutil.rmtree(WHEELS_DIR)
    if EXTRACTED_DIR.exists():
        print(f"Clearing previous extraction under {EXTRACTED_DIR}")
        shutil.rmtree(EXTRACTED_DIR)

    print(f"Vendoring pydantic-core {PYDANTIC_CORE_VERSION} for {len(MATRIX)} targets")
    successes: list[str] = []
    misses: list[str] = []
    for py_tag, abi_tag, plat_tag, our_plat_tag in MATRIX:
        target = f"{py_tag}-{our_plat_tag}"
        print(f"\n[{target}]")
        wheel = _download(py_tag, abi_tag, plat_tag)
        if wheel is None:
            misses.append(target)
            continue
        dest = EXTRACTED_DIR / target
        if dest.exists():
            shutil.rmtree(dest)
        _extract(wheel, dest)
        print(f"    -> extracted to {dest.relative_to(REPO)}")
        successes.append(target)

    pyobjc_successes = _vendor_pyobjc_for_macos_rows(misses)

    print("\n" + "=" * 60)
    print(f"Vendored pydantic_core: {len(successes)} of {len(MATRIX)} targets.")
    macos_rows = sum(1 for _, _, _, plat in MATRIX if plat.startswith("macosx_"))
    print(f"Vendored pyobjc:        {len(pyobjc_successes)} of {macos_rows} macOS rows.")
    if misses:
        print(f"  Missed: {', '.join(misses)}")
        print("  Investigate via `pip index versions <package>` for each tag.")
    print("=" * 60)
    return 0 if not misses else 1


if __name__ == "__main__":
    sys.exit(main())
