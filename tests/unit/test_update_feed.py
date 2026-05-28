"""The LibreOffice extension update feed is wired correctly.

`extension/description.xml` must declare an `<update-information>` pointing
at the GitHub `releases/latest/download/update.xml` URL so LibreOffice's
"Check for Updates" can find new versions, and `release.yml` must generate
that `update.xml` with a matching identifier + a release-asset download URL.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DESC = _REPO_ROOT / "extension" / "description.xml"
_RELEASE_YML = _REPO_ROOT / ".github" / "workflows" / "release.yml"
_FEED_URL = (
    "https://github.com/A2B-Technology-Corporation/Talk2View-Writer"
    "/releases/latest/download/update.xml"
)
_NS = {
    "d": "http://openoffice.org/extensions/description/2006",
    "xlink": "http://www.w3.org/1999/xlink",
}


@pytest.mark.unit
def test_description_declares_update_feed() -> None:
    root = ET.parse(_DESC).getroot()
    update_info = root.find("d:update-information", _NS)
    assert update_info is not None, "description.xml has no <update-information>"
    src = update_info.find("d:src", _NS)
    assert src is not None, "<update-information> has no <src>"
    href = src.get(f"{{{_NS['xlink']}}}href")
    assert href == _FEED_URL, f"feed URL is {href!r}, expected {_FEED_URL!r}"


@pytest.mark.unit
def test_description_identifier_is_canonical() -> None:
    root = ET.parse(_DESC).getroot()
    ident = root.find("d:identifier", _NS)
    assert ident is not None
    assert ident.get("value") == "com.talk2view.writer"


@pytest.mark.unit
def test_release_workflow_generates_matching_update_xml() -> None:
    """release.yml emits update.xml with matching identifier + download URL."""
    yml = _RELEASE_YML.read_text(encoding="utf-8")
    assert 'identifier value="com.talk2view.writer"' in yml
    assert "/releases/download/" in yml and "Talk2ViewWriter.oxt" in yml
    assert "dist/update.xml" in yml, "update.xml not added to the release files"
