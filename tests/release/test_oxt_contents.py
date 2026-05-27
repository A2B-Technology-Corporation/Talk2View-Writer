"""Validate a packaged / published Talk2View-Writer ``.oxt`` artifact.

This checks the **shipped** ``.oxt`` the way a user receives it — that it
carries the per-platform binary wheels LibreOffice's bundled Python will need.
It complements ``tests/unit/test_release_packaging.py`` (which checks the
local ``vendor/extracted`` build tree and the script↔loader name contract).

Point ``T2V_RELEASE_OXT`` at the ``.oxt`` (skips otherwise). The release-smoke
workflow sets it to the downloaded Release asset; locally::

    make package
    T2V_RELEASE_OXT=dist/Talk2ViewWriter.oxt uv run pytest -m release_smoke -v

Optionally set ``T2V_RELEASE_TAG`` (e.g. ``v1.0.0-alpha.4``) to assert the
manifest version matches the tag base.
"""

from __future__ import annotations

import importlib.util
import os
import re
import zipfile
from pathlib import Path

import pytest

from talk2view_writer import _wheel_loader

pytestmark = pytest.mark.release_smoke

_OXT_ENV = "T2V_RELEASE_OXT"
_TAG_ENV = "T2V_RELEASE_TAG"
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _matrix_py_tags_for(plat_tag: str) -> list[str]:
    """Python tags (``cp310``…) the vendoring MATRIX ships for ``plat_tag``."""
    spec = importlib.util.spec_from_file_location(
        "vendor_wheels", _REPO_ROOT / "scripts" / "vendor_wheels.py"
    )
    assert spec is not None and spec.loader is not None
    vw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vw)
    return sorted({py for py, _abi, _plat, our in vw.MATRIX if our == plat_tag})


@pytest.fixture(scope="module")
def oxt_path() -> Path:
    raw = os.environ.get(_OXT_ENV)
    if not raw:
        pytest.skip(f"{_OXT_ENV} not set — point it at the .oxt to validate")
    path = Path(raw)
    if not path.is_file():
        pytest.fail(f"{_OXT_ENV}={raw!r} is not a file")
    return path


@pytest.fixture(scope="module")
def oxt_names(oxt_path: Path) -> list[str]:
    with zipfile.ZipFile(oxt_path) as zf:
        return zf.namelist()


def test_bundles_pydantic_core_for_this_platform(oxt_names: list[str]) -> None:
    """Every supported LO Python on this platform has a pydantic_core dir.

    The bundle ships all matrix Python versions per platform because the
    user's LibreOffice picks its own bundled interpreter — a missing row
    means the extension can't load on that LO build.
    """
    plat = _wheel_loader._platform_tag()
    py_tags = _matrix_py_tags_for(plat)
    assert py_tags, f"no vendoring-MATRIX rows for platform tag {plat!r}"
    for py in py_tags:
        prefix = f"pythonpath/_vendored_wheels/{py}-{plat}/pydantic_core/"
        assert any(n.startswith(prefix) for n in oxt_names), (
            f".oxt missing {prefix} — LibreOffice on {plat} with Python "
            f"{py} would have no pydantic_core"
        )


def test_bundles_pyobjc_on_macos(oxt_names: list[str]) -> None:
    """Each macOS row carries the pyobjc packages the Cocoa backend needs."""
    plat = _wheel_loader._platform_tag()
    if not plat.startswith("macosx_"):
        pytest.skip("pyobjc is only bundled for macOS rows")
    expected = ("objc", "AppKit", "Foundation", "WebKit", "PyObjCTools")
    for py in _matrix_py_tags_for(plat):
        base = f"pythonpath/_vendored_wheels/{py}-{plat}/"
        for module in expected:
            assert any(n.startswith(base + module + "/") for n in oxt_names), (
                f".oxt missing {base}{module}/ — pywebview Cocoa backend "
                f"would fail to import on {plat} / {py}"
            )


def test_manifest_version_present_and_matches_tag(oxt_path: Path) -> None:
    """``description.xml`` carries a version, matching the tag base if given."""
    with zipfile.ZipFile(oxt_path) as zf:
        description = zf.read("description.xml").decode("utf-8")
    match = re.search(r'<version\s+value="([^"]+)"', description)
    assert match, "description.xml has no <version value=...>"
    version = match.group(1)

    tag = os.environ.get(_TAG_ENV)
    if tag:
        base = tag.lstrip("v").split("-", 1)[0]  # v1.0.0-alpha.4 -> 1.0.0
        assert version == base, (
            f"manifest version {version!r} != release tag base {base!r} "
            f"(from {_TAG_ENV}={tag!r})"
        )


def test_manifest_ships_update_feed(oxt_path: Path) -> None:
    """The shipped manifest carries the update-information update feed."""
    with zipfile.ZipFile(oxt_path) as zf:
        description = zf.read("description.xml").decode("utf-8")
    assert "update-information" in description
    assert "releases/latest/download/update.xml" in description
