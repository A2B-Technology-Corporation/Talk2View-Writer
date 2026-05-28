"""About + License dialog content, license reading, and menu wiring.

The dialog *rendering* needs LibreOffice (UNO) and is covered manually, but
the text/URL builders and the License-file reader are pure and tested here,
plus the Addons.xcu menu entries and the ProtocolHandler dispatch routing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from talk2view_writer import __version__, about

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ADDONS = _REPO_ROOT / "extension" / "Addons.xcu"
_UNO_ENTRY = _REPO_ROOT / "extension" / "talk2view_writer.py"


class _FakePip:
    def __init__(self, root: Path) -> None:
        self._root = root

    def getPackageLocation(self, identifier: str) -> str:  # noqa: N802 (UNO name)
        assert identifier == "com.talk2view.writer"
        return self._root.as_uri()


class _FakeCtx:
    """Minimal duck-typed XComponentContext for read_license_text."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def getValueByName(self, name: str) -> Any:  # noqa: N802 (UNO name)
        assert "PackageInformationProvider" in name
        return _FakePip(self._root)


@pytest.mark.unit
class TestAboutText:
    def test_includes_version(self) -> None:
        assert f"Version {__version__}" in about.build_about_text()

    def test_includes_verbatim_copyright(self) -> None:
        # Footer copyright line from the website, reproduced exactly.
        assert "Talk2View Pty Ltd" in about.COPYRIGHT
        assert "A2B Technology Corporation Pty Ltd" in about.COPYRIGHT
        assert about.COPYRIGHT in about.build_about_text()

    def test_includes_medical_disclaimer(self) -> None:
        text = about.build_about_text()
        assert "not cleared or approved by the FDA" in text
        assert "must not be used for clinical diagnosis" in text

    def test_includes_license_summary(self) -> None:
        assert "Mozilla Public License 2.0" in about.build_about_text()

    def test_links_cover_required_destinations(self) -> None:
        urls = {url for _label, url in about.LINKS}
        assert about.WEBSITE_URL in urls
        assert about.GITHUB_URL in urls
        assert about.PRIVACY_URL in urls
        assert about.TERMS_URL in urls
        for _label, url in about.LINKS:
            assert url.startswith("https://"), url


@pytest.mark.unit
class TestReadLicense:
    def test_reads_bundled_license(self, tmp_path: Path) -> None:
        reg = tmp_path / "registration"
        reg.mkdir()
        (reg / "LICENSE").write_text("Mozilla Public License Version 2.0\n...\n", encoding="utf-8")
        text = about.read_license_text(_FakeCtx(tmp_path))
        assert "Mozilla Public License Version 2.0" in text

    def test_falls_back_when_license_missing(self, tmp_path: Path) -> None:
        text = about.read_license_text(_FakeCtx(tmp_path))
        assert about.GITHUB_URL in text
        assert "Mozilla Public License 2.0" in text


@pytest.mark.unit
class TestMenuWiring:
    def test_addons_declares_about_and_license_items(self) -> None:
        xcu = _ADDONS.read_text(encoding="utf-8")
        assert "vnd.com.talk2view.writer:about" in xcu
        assert "vnd.com.talk2view.writer:license" in xcu
        assert "About Talk2View" in xcu
        assert "License Information" in xcu
        assert "private:separator" in xcu

    def test_dispatch_routes_about_and_license(self) -> None:
        src = _UNO_ENTRY.read_text(encoding="utf-8")
        assert 'command == "about"' in src
        assert 'command == "license"' in src
        assert "show_about" in src and "show_license" in src
