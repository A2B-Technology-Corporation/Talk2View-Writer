"""Tests for ``_wheel_loader`` — runtime selection of bundled wheels.

Covers tag-detection logic (cross-platform parametrised cases) and
the error paths in :func:`ensure_vendored_pydantic_core`. The "happy
path" — actually importing the bundled wheel — is exercised
end-to-end during ``make build`` since the build only succeeds if the
runtime tag matches one of the extracted directories.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from talk2view_writer import _wheel_loader


@pytest.mark.unit
class TestPythonTag:
    def test_matches_running_interpreter(self) -> None:
        tag = _wheel_loader._python_tag()
        expected = f"cp{sys.version_info.major}{sys.version_info.minor}"
        assert tag == expected
        assert tag.startswith("cp")


@pytest.mark.unit
class TestPlatformTag:
    @pytest.mark.parametrize(
        "system,machine,expected",
        [
            ("Linux", "x86_64", "manylinux_x86_64"),
            ("Linux", "amd64", "manylinux_x86_64"),
            ("Linux", "aarch64", "manylinux_aarch64"),
            ("Linux", "arm64", "manylinux_aarch64"),
            ("Darwin", "x86_64", "macosx_x86_64"),
            ("Darwin", "arm64", "macosx_arm64"),
            ("Windows", "AMD64", "win_amd64"),
            ("Windows", "x86_64", "win_amd64"),
        ],
    )
    def test_known_platforms(self, system: str, machine: str, expected: str) -> None:
        with (
            patch("platform.system", return_value=system),
            patch("platform.machine", return_value=machine),
        ):
            assert _wheel_loader._platform_tag() == expected

    def test_unknown_linux_falls_back_gracefully(self) -> None:
        with (
            patch("platform.system", return_value="Linux"),
            patch("platform.machine", return_value="riscv64"),
        ):
            assert _wheel_loader._platform_tag() == "linux_riscv64"

    def test_unknown_system_uses_lowercase_form(self) -> None:
        with (
            patch("platform.system", return_value="OpenBSD"),
            patch("platform.machine", return_value="amd64"),
        ):
            assert _wheel_loader._platform_tag() == "openbsd_amd64"


@pytest.mark.unit
class TestRuntimeTag:
    def test_combines_python_and_platform_tags(self) -> None:
        with (
            patch.object(_wheel_loader, "_python_tag", return_value="cp313"),
            patch.object(_wheel_loader, "_platform_tag", return_value="manylinux_x86_64"),
        ):
            assert _wheel_loader.runtime_tag() == "cp313-manylinux_x86_64"


@pytest.mark.unit
class TestEnsureVendoredPydanticCore:
    def test_short_circuits_when_already_importable(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Short-circuit when pydantic_core already importable.

        The loader returns without touching the wheel directory.
        """
        # Point _VENDORED_ROOT at an empty directory; the loader should
        # NOT try to read it because pydantic_core (or our stub) imports
        # fine without help.
        monkeypatch.setattr(_wheel_loader, "_VENDORED_ROOT", str(tmp_path))

        # pydantic_core may or may not be importable in the test env;
        # what we need to assert is that *if it is importable*, the
        # loader doesn't raise. Install a fake module so the test is
        # deterministic.
        import types

        fake_pc = types.ModuleType("pydantic_core")
        monkeypatch.setitem(sys.modules, "pydantic_core", fake_pc)

        _wheel_loader.ensure_vendored_pydantic_core()  # must not raise

    def test_raises_with_actionable_message_when_no_matching_wheel(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Missing wheel → clear error with the detected tag + install hint."""
        # Empty vendored root with no extracted wheels.
        monkeypatch.setattr(_wheel_loader, "_VENDORED_ROOT", str(tmp_path))
        # Force the importable-check to fail.
        monkeypatch.setitem(sys.modules, "pydantic_core", None)

        with pytest.raises(ImportError) as exc_info:
            _wheel_loader.ensure_vendored_pydantic_core()
        msg = str(exc_info.value)
        assert "No bundled pydantic_core wheel" in msg
        assert "Manual recovery" in msg
        # The detected runtime tag should be in the message.
        assert _wheel_loader.runtime_tag() in msg

    def test_prefer_bundled_evicts_stale_system_typing_extensions(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Cached module from outside pythonpath/ must be evicted.

        This is the regression for the LibreOffice failure where the
        system typing_extensions (older, no Sentinel) was cached in
        sys.modules before our extension loaded, so pydantic_core's
        ``from typing_extensions import Sentinel`` resolved to the
        wrong copy.
        """
        # Pretend pythonpath/ is somewhere unrelated to the cached
        # module's __file__.
        monkeypatch.setattr(_wheel_loader, "_PYTHONPATH_ROOT", str(tmp_path))

        import types

        stale = types.ModuleType("typing_extensions")
        stale.__file__ = "/usr/lib/python3/dist-packages/typing_extensions.py"
        # Add a child to verify submodules get evicted too.
        stale_child = types.ModuleType("pydantic.foo")
        stale_child.__file__ = "/usr/lib/python3/dist-packages/pydantic/foo.py"
        stale_parent = types.ModuleType("pydantic")
        stale_parent.__file__ = "/usr/lib/python3/dist-packages/pydantic/__init__.py"
        monkeypatch.setitem(sys.modules, "typing_extensions", stale)
        monkeypatch.setitem(sys.modules, "pydantic", stale_parent)
        monkeypatch.setitem(sys.modules, "pydantic.foo", stale_child)

        _wheel_loader._prefer_bundled_pure_python_deps()

        # All three stale entries must be gone — re-import would then
        # resolve from sys.path (pythonpath/ now at position 0).
        assert "typing_extensions" not in sys.modules
        assert "pydantic" not in sys.modules
        assert "pydantic.foo" not in sys.modules
        assert sys.path[0] == str(tmp_path)

    def test_prefer_bundled_does_not_evict_already_bundled_modules(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Idempotent: re-running the loader doesn't disturb working state."""
        monkeypatch.setattr(_wheel_loader, "_PYTHONPATH_ROOT", str(tmp_path))

        import types

        good = types.ModuleType("typing_extensions")
        # __file__ already points inside our pythonpath — the loader
        # should leave it alone.
        good.__file__ = str(tmp_path / "typing_extensions.py")
        monkeypatch.setitem(sys.modules, "typing_extensions", good)

        _wheel_loader._prefer_bundled_pure_python_deps()

        # Same module object — not evicted.
        assert sys.modules.get("typing_extensions") is good

    def test_candidate_directory_matches_runtime_tag(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """``_candidate_directory()`` always resolves under the vendored root.

        We deliberately do NOT unit-test the "prepend then re-import"
        happy path: blocking ``import pydantic_core`` while
        simultaneously allowing it to succeed from a temp directory
        requires intercepting CPython's import machinery, which
        produces tests fragile to interpreter changes. The happy path
        runs end-to-end the first time the sidebar panel sends a
        chat message after the .oxt is installed — see the build
        sanity check in ``Makefile`` (``build`` requires
        ``vendor/extracted/`` to exist).
        """
        monkeypatch.setattr(_wheel_loader, "_VENDORED_ROOT", str(tmp_path))
        tag = _wheel_loader.runtime_tag()
        expected = str(tmp_path / tag)
        assert _wheel_loader._candidate_directory() == expected


@pytest.mark.unit
class TestEnsureVendoredPyobjc:
    """Loader for pyobjc bundle (macOS Cocoa backend of pywebview)."""

    def test_no_op_on_non_macos(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Linux + Windows return immediately without touching sys.path.

        pyobjc is macOS-only — bundling and loading it on other OSes
        is meaningless and would surface as a confusing
        ImportError("No bundled pyobjc...") on Linux / Windows
        builds. Guarded at the top of the function instead.
        """
        for platform_name in ("linux", "win32"):
            monkeypatch.setattr(sys, "platform", platform_name)
            # Don't need to set up vendored root — should never be
            # consulted on these platforms.
            _wheel_loader.ensure_vendored_pyobjc()  # must not raise

    def test_short_circuits_when_objc_already_importable(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """``objc`` already in sys.modules → no path mutation."""
        import types

        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(_wheel_loader, "_VENDORED_ROOT", str(tmp_path))
        fake_objc = types.ModuleType("objc")
        monkeypatch.setitem(sys.modules, "objc", fake_objc)

        sys_path_before = list(sys.path)
        _wheel_loader.ensure_vendored_pyobjc()  # must not raise
        # ``_prefer_bundled_pure_python_deps`` may prepend the
        # pythonpath root, but no vendored-wheel dir should appear.
        candidate = _wheel_loader._candidate_directory()
        assert candidate not in sys.path, (
            "should not prepend vendored-wheel dir when objc is "
            f"already importable; got sys.path={sys.path}"
        )
        _ = sys_path_before  # documentation of the snapshot intent

    def test_raises_with_pyobjc_hint_when_no_matching_wheel(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Missing wheel on macOS → clear ImportError naming pyobjc.

        The hint must mention the user-recoverable manual install
        command so a user hitting this in the wild has a path
        forward.
        """
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(_wheel_loader, "_VENDORED_ROOT", str(tmp_path))
        monkeypatch.setitem(sys.modules, "objc", None)

        with pytest.raises(ImportError) as exc_info:
            _wheel_loader.ensure_vendored_pyobjc()
        msg = str(exc_info.value)
        assert "No bundled pyobjc wheel" in msg
        assert "pyobjc-framework-WebKit" in msg
        assert _wheel_loader.runtime_tag() in msg
