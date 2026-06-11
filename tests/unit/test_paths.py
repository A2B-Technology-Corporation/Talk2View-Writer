"""Tests for cross-platform file:// URL → path conversion (_paths)."""

from __future__ import annotations

import nturl2path
from pathlib import Path

import pytest

from talk2view_writer._paths import file_url_to_path


@pytest.mark.unit
class TestFileUrlToPath:
    def test_posix_path_round_trips(self) -> None:
        assert file_url_to_path("file:///home/user/ext/web/index.html") == Path(
            "/home/user/ext/web/index.html"
        )

    def test_percent_encoded_spaces_are_decoded(self) -> None:
        assert file_url_to_path("file:///home/a%20b/index.html") == Path(
            "/home/a b/index.html"
        )

    def test_non_file_scheme_raises(self) -> None:
        with pytest.raises(ValueError, match="file://"):
            file_url_to_path("https://example.com/x")

    def test_windows_drive_form_converts_correctly(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The Windows bug regression, made OS-independent.

        On Windows, urlparse("file:///C:/Users/x").path is "/C:/Users/x";
        the old code did Path("/C:/Users/x") — not a usable Windows path,
        so every resource lookup failed and the chat window never opened.
        url2pathname on Windows dispatches to nturl2path, which produces a
        real drive path. Force that converter here to prove file_url_to_path
        yields the right thing regardless of the host OS.
        """
        monkeypatch.setattr(
            "talk2view_writer._paths.url2pathname", nturl2path.url2pathname
        )
        result = file_url_to_path("file:///C:/Users/Clinician/ext/web/index.html")
        # nturl2path yields a backslash drive path; compare on the raw string
        # so the assertion is meaningful when the test runs on POSIX.
        assert str(result) == r"C:\Users\Clinician\ext\web\index.html"
        # Crucially, NOT the broken leading-slash form the old code produced.
        assert not str(result).startswith("/C:")
