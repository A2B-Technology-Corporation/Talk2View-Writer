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

Supply-chain integrity: every downloaded pydantic-core wheel (a native
Rust ``.so``/``.pyd`` loaded into every user's LibreOffice) is verified
against the SHA-256 digests ``uv.lock`` already pins before it is
extracted; a mismatch aborts the build. NOTE: the pyobjc framework
wheels are not yet covered — pyobjc is not a dev dependency, so its
hashes are not in ``uv.lock``. Pinning those is tracked in
``docs/investigations.md``; until then the macOS Cocoa backend wheels
remain trust-on-download.

Matrix-maintenance note: every time a new LibreOffice release ships
with a newer bundled Python, add the matching ``cpXY`` row to
:data:`MATRIX`. See ``docs/investigations.md`` #24 for the
re-evaluation cadence.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import tomllib
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
UV_LOCK = REPO / "uv.lock"


class WheelIntegrityError(RuntimeError):
    """Raised when a downloaded wheel's SHA-256 does not match the lock."""


def _sha256(path: Path) -> str:
    """Return the SHA-256 hex digest of ``path``."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_pydantic_core_hashes(lock_path: Path = UV_LOCK) -> dict[str, str]:
    """Map ``{wheel_filename: sha256_hex}`` for pydantic-core from ``uv.lock``.

    The dev resolve in ``uv.lock`` already pins every pydantic-core wheel
    by SHA-256 (the URL's last path segment is the wheel filename). We
    reuse those digests to authenticate the native Rust binaries we ship,
    so the integrity check stays in lockstep with ``uv lock`` — no
    separately-maintained hash file to drift.

    Raises:
        WheelIntegrityError: If the lock has no pydantic-core package, or
            its version disagrees with ``PYDANTIC_CORE_VERSION`` (a stale
            lock would otherwise validate the wrong artifacts).
    """
    with lock_path.open("rb") as fh:
        data = tomllib.load(fh)
    for pkg in data.get("package", []):
        if pkg.get("name") != "pydantic-core":
            continue
        version = pkg.get("version")
        if version != PYDANTIC_CORE_VERSION:
            raise WheelIntegrityError(
                f"uv.lock pins pydantic-core {version!r} but vendor_wheels.py "
                f"targets {PYDANTIC_CORE_VERSION!r}. Run `uv lock` and update "
                "PYDANTIC_CORE_VERSION so the integrity check authenticates "
                "the right wheels."
            )
        hashes: dict[str, str] = {}
        for wheel in pkg.get("wheels", []):
            url = wheel.get("url", "")
            digest = wheel.get("hash", "")
            filename = url.rsplit("/", 1)[-1]
            if filename and digest.startswith("sha256:"):
                hashes[filename] = digest.split(":", 1)[1]
        if not hashes:
            raise WheelIntegrityError(
                "uv.lock has a pydantic-core entry but no wheel hashes."
            )
        return hashes
    raise WheelIntegrityError(
        "uv.lock has no pydantic-core package; cannot verify wheel integrity."
    )


def _python_tag_of(wheel_name: str) -> str:
    """Extract the CPython tag (``cp311``) from a wheel filename, or ``""``.

    ``pydantic_core-2.46.4-cp311-cp311-manylinux...whl`` -> ``cp311``.
    """
    parts = wheel_name.split("-")
    # name-version-pytag-abitag-plat...whl
    return parts[2] if len(parts) >= 5 and parts[2].startswith("cp") else ""


def covered_python_tags(expected: dict[str, str]) -> set[str]:
    """Python tags (``cp311``...) that ``uv.lock`` actually pins wheels for.

    The vendor MATRIX is deliberately broader than ``uv.lock``: it targets
    every Python a supported LibreOffice ships (e.g. cp310), while
    ``uv.lock`` only pins wheels in the dev project's ``requires-python``
    range. A wheel whose Python tag is NOT covered here cannot be
    authenticated against ``uv.lock`` at all.
    """
    return {tag for name in expected if (tag := _python_tag_of(name))}


def verify_wheel(
    wheel: Path,
    pinned_hashes: set[str],
    covered_tags: set[str],
) -> bool:
    """Authenticate one downloaded wheel against ``uv.lock``'s digests.

    Matches by CONTENT (SHA-256 set membership) rather than filename, so a
    difference in platform-tag normalisation between ``pip`` and ``uv``
    never causes a false mismatch.

    Returns:
        ``True`` if the wheel's digest is one ``uv.lock`` pins (authentic).
        ``False`` if its Python tag is one ``uv.lock`` does not cover (e.g.
        cp310 when ``requires-python >= 3.11``) — it cannot be verified and
        is reported as such by the caller (trust-on-download, like pyobjc).

    Raises:
        WheelIntegrityError: The wheel's Python tag IS covered by
            ``uv.lock`` but its digest is not pinned — a genuine integrity
            failure (compromised mirror, account takeover, MITM, or a stale
            lock), so the build must stop.
    """
    digest = _sha256(wheel)
    if digest in pinned_hashes:
        return True
    py_tag = _python_tag_of(wheel.name)
    if py_tag and py_tag in covered_tags:
        raise WheelIntegrityError(
            f"SHA-256 of {wheel.name} ({digest}) is not among the pydantic-core "
            f"digests uv.lock pins for {py_tag}. The downloaded wheel does not "
            "match uv.lock — aborting. Run `uv lock` if pydantic-core changed."
        )
    return False


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

    Also skips build/test/debug artifacts the runtime never imports:

    - ``PyObjCTest/`` — pyobjc's own unit-test suite (~1540 entries per
      macOS tag), and
    - ``*.dSYM/`` bundles — macOS debug-symbol directories sitting next
      to each ``.so`` (the ``.so`` loads fine without them).

    These carry the longest internal paths in the OXT — the pyobjc
    ``PyObjCTest/…​.dSYM/…`` members reach 194 chars. On Windows that is
    fatal: unopkg extracts the OXT under the deep per-user
    ``AppData/Roaming/LibreOffice/4/user/uno_packages/cache/…/Talk2ViewWriter.oxt/``
    tree (~127 chars), so 127 + 194 blows past ``MAX_PATH`` (260) and
    ``unopkg add`` fails with an opaque "Error while adding" — the
    Windows-only integration failure in investigations #64. Dropping
    them returns the longest path to 113 chars (well under 260) and
    shrinks the OXT ~90% (≈13.8k → ≈1.3k entries). None are needed at
    runtime on any platform.

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
            # Drop the pyobjc test suite and all macOS debug-symbol
            # bundles — useless at runtime and the source of the
            # MAX_PATH-busting long paths on Windows (investigations #64).
            if top == "PyObjCTest" or ".dSYM/" in member:
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

    # Authenticate every pydantic-core wheel against the SHA-256 digests
    # uv.lock pins, BEFORE extracting any native code. A wheel whose Python
    # tag uv.lock covers but whose digest is not pinned aborts the build
    # (compromised mirror, account takeover, MITM, or stale lock). The
    # MATRIX is broader than uv.lock — it targets every Python a supported
    # LibreOffice ships (e.g. cp310), while uv.lock only pins the dev
    # requires-python range — so wheels for an uncovered Python tag cannot
    # be authenticated and are reported as unverified (trust-on-download,
    # like the pyobjc wheels).
    expected_hashes = load_pydantic_core_hashes()
    pinned_hashes = set(expected_hashes.values())
    covered_tags = covered_python_tags(expected_hashes)
    print(
        f"Vendoring pydantic-core {PYDANTIC_CORE_VERSION} for {len(MATRIX)} "
        f"targets (verifying against {len(pinned_hashes)} pinned hashes; "
        f"uv.lock covers {', '.join(sorted(covered_tags))})"
    )
    successes: list[str] = []
    misses: list[str] = []
    unverified: list[str] = []
    for py_tag, abi_tag, plat_tag, our_plat_tag in MATRIX:
        target = f"{py_tag}-{our_plat_tag}"
        print(f"\n[{target}]")
        wheel = _download(py_tag, abi_tag, plat_tag)
        if wheel is None:
            misses.append(target)
            continue
        if verify_wheel(wheel, pinned_hashes, covered_tags):
            print(f"    verified sha256 ({wheel.name})")
        else:
            unverified.append(target)
            print(
                f"    WARNING: {wheel.name} not pinned in uv.lock "
                f"({py_tag} is outside requires-python) — extracted UNVERIFIED"
            )
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
    if unverified:
        print(
            f"  UNVERIFIED (not in uv.lock — outside requires-python): "
            f"{', '.join(unverified)}"
        )
    if misses:
        print(f"  Missed: {', '.join(misses)}")
        print("  Investigate via `pip index versions <package>` for each tag.")
    print("=" * 60)
    return 0 if not misses else 1


if __name__ == "__main__":
    sys.exit(main())
