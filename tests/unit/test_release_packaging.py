"""Release-packaging guarantees for the vendored-wheel bundle.

The ``.oxt`` ships pre-extracted binary wheels under
``pythonpath/_vendored_wheels/<runtime-tag>/`` — ``pydantic_core`` for
every platform (ADR-0023) and ``pyobjc`` for the macOS rows (ADR-0038).
For an installed ``.oxt`` to actually work, two things must hold:

1. The directory names the vendoring script writes
   (:data:`scripts/vendor_wheels.MATRIX` → ``<py_tag>-<our_plat_tag>``)
   must be exactly what the runtime loader looks for
   (:func:`_wheel_loader.runtime_tag`). A mismatch means a
   correctly-built package ships wheels into a directory nothing reads.
2. The loader must actually prepend the matching bundle directory to
   ``sys.path`` and import the module from it.

These tests need no network and no real ``.so`` — (1) is a pure
name-contract check, (2) uses a stub package. A third class validates a
*real* ``vendor/extracted/`` tree when one is present (after
``make vendor-wheels``), and skips otherwise.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from talk2view_writer import _wheel_loader

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXTRACTED = _REPO_ROOT / "vendor" / "extracted"

# Representative ``(platform.system(), platform.machine())`` for each
# coarse plat tag the vendoring MATRIX emits. The loader's
# ``_platform_tag`` must map each of these back to the same tag.
_PLATFORM_REPRESENTATIVES: dict[str, tuple[str, str]] = {
    "manylinux_x86_64": ("Linux", "x86_64"),
    "manylinux_aarch64": ("Linux", "aarch64"),
    "macosx_x86_64": ("Darwin", "x86_64"),
    "macosx_arm64": ("Darwin", "arm64"),
    "win_amd64": ("Windows", "AMD64"),
}


def _load_vendor_wheels() -> ModuleType:
    """Import ``scripts/vendor_wheels.py`` by path (it isn't a package)."""
    spec = importlib.util.spec_from_file_location(
        "vendor_wheels", _REPO_ROOT / "scripts" / "vendor_wheels.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def isolate_import_state() -> Iterator[None]:
    """Snapshot + restore ``sys.path`` / ``sys.modules`` around a test.

    The loader prepends paths and may evict cached modules; without
    this the mutations would leak into sibling tests.
    """
    saved_path = list(sys.path)
    saved_modules = dict(sys.modules)
    yield
    sys.path[:] = saved_path
    for name in list(sys.modules):
        if name not in saved_modules:
            del sys.modules[name]
    sys.modules.update(saved_modules)


@pytest.mark.unit
class TestVendorMatrixMatchesLoader:
    """The script's output dir names must equal the loader's runtime tag."""

    def test_matrix_python_tags_are_cpxy(self) -> None:
        vw = _load_vendor_wheels()
        for py_tag, _abi, _plat, _our in vw.MATRIX:
            assert py_tag.startswith("cp") and py_tag[2:].isdigit(), py_tag

    def test_every_matrix_plat_tag_has_a_known_runtime_mapping(self) -> None:
        """No MATRIX row may emit a plat tag the loader can't resolve."""
        vw = _load_vendor_wheels()
        emitted = {our for *_rest, our in vw.MATRIX}
        unknown = emitted - set(_PLATFORM_REPRESENTATIVES)
        assert not unknown, (
            f"vendor_wheels.MATRIX emits plat tags with no _platform_tag "
            f"mapping (the .oxt would ship wheels into a dir the loader "
            f"never reads): {sorted(unknown)}"
        )

    def test_loader_resolves_each_matrix_dir_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Each MATRIX row's dir name equals the loader's runtime tag.

        ``runtime_tag()`` on the matching interpreter + platform must
        equal the ``<py_tag>-<our_plat_tag>`` directory the script writes.
        """
        vw = _load_vendor_wheels()
        for py_tag, _abi, _plat, our_tag in vw.MATRIX:
            sysname, machine = _PLATFORM_REPRESENTATIVES[our_tag]
            monkeypatch.setattr("platform.system", lambda s=sysname: s)
            monkeypatch.setattr("platform.machine", lambda m=machine: m)
            monkeypatch.setattr(_wheel_loader, "_python_tag", lambda pt=py_tag: pt)
            assert _wheel_loader.runtime_tag() == f"{py_tag}-{our_tag}"


@pytest.mark.unit
class TestBundledModuleLoads:
    """The loader must import a module from the bundled directory."""

    def test_pyobjc_loads_from_synthetic_bundle(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        isolate_import_state: None,
    ) -> None:
        """A stub ``objc`` placed in the runtime-tag dir is imported.

        Proves the macOS happy path end-to-end without a real pyobjc
        ``.so``: guard passed (darwin), objc not already importable,
        candidate dir prepended, import resolves to the bundled copy.
        """
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(_wheel_loader, "_VENDORED_ROOT", str(tmp_path))
        monkeypatch.delitem(sys.modules, "objc", raising=False)

        # The loader computes the dir from runtime_tag(); build the stub
        # there so the lookup matches whatever this interpreter resolves.
        tag = _wheel_loader.runtime_tag()
        stub_pkg = tmp_path / tag / "objc"
        stub_pkg.mkdir(parents=True)
        (stub_pkg / "__init__.py").write_text("MARKER = 'bundled-objc'\n")

        _wheel_loader.ensure_vendored_pyobjc()

        candidate = _wheel_loader._candidate_directory()
        assert candidate in sys.path, "loader did not prepend the bundle dir"

        import objc  # the stub, resolved from the bundle dir

        assert objc.MARKER == "bundled-objc"
        assert (objc.__file__ or "").startswith(str(tmp_path)), (
            f"objc resolved from {objc.__file__!r}, not the bundle"
        )


@pytest.mark.unit
@pytest.mark.skipif(
    not _EXTRACTED.is_dir(),
    reason="vendor/extracted/ absent — run `make vendor-wheels` to validate real bundle",
)
class TestRealVendoredBundle:
    """Validate an actually-built ``vendor/extracted/`` tree when present.

    Skipped on a fresh checkout / the CI unit job (no vendored tree).
    Exercised locally after ``make vendor-wheels`` and documents the
    per-row contents the ``.oxt`` is expected to carry. A *missing* row
    directory is treated as a download miss (the script reports those),
    not a packaging bug — we only assert the contents of rows that exist.
    """

    def test_present_rows_contain_pydantic_core(self) -> None:
        vw = _load_vendor_wheels()
        checked = 0
        for py_tag, _abi, _plat, our_tag in vw.MATRIX:
            row = _EXTRACTED / f"{py_tag}-{our_tag}"
            if not row.is_dir():
                continue
            assert (row / "pydantic_core").is_dir(), f"{row} missing pydantic_core/"
            checked += 1
        assert checked > 0, "vendor/extracted/ exists but has no recognizable rows"

    def test_present_macos_rows_contain_pyobjc_modules(self) -> None:
        vw = _load_vendor_wheels()
        expected = {"objc", "AppKit", "Foundation", "WebKit", "PyObjCTools"}
        macos_rows = [
            _EXTRACTED / f"{py_tag}-{our_tag}"
            for py_tag, _abi, _plat, our_tag in vw.MATRIX
            if our_tag.startswith("macosx_")
        ]
        existing = [r for r in macos_rows if r.is_dir()]
        # If no macOS row carries any pyobjc package, this tree was built
        # by a pre-ADR-0038 `make vendor-wheels` (pydantic_core only).
        # Treat that as "not yet vendored" and skip rather than fail — the
        # release job re-vendors from scratch.
        if not any((row / "objc").is_dir() for row in existing):
            pytest.skip("vendor/extracted/ predates pyobjc — re-run `make vendor-wheels`")
        for row in existing:
            present = {p.name for p in row.iterdir() if p.is_dir()}
            missing = expected - present
            assert not missing, f"{row} missing pyobjc modules: {sorted(missing)}"
