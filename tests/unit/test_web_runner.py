"""Unit tests for web_runner's js_api (_Api)."""

from __future__ import annotations

import webbrowser

import pytest

from talk2view_writer.web_runner import _Api


@pytest.mark.unit
class TestOpenExternal:
    def test_opens_https_in_browser(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[str] = []
        monkeypatch.setattr(
            webbrowser, "open", lambda url, new=0: calls.append(url) or True
        )
        result = _Api(None).open_external(
            "https://github.com/A2B-Technology-Corporation/Talk2View-Writer/releases/latest"
        )
        assert result == {"opened": True}
        assert calls == [
            "https://github.com/A2B-Technology-Corporation/Talk2View-Writer/releases/latest"
        ]

    @pytest.mark.parametrize(
        "url", ["file:///etc/passwd", "javascript:alert(1)", "vnd.evil:x", ""]
    )
    def test_rejects_non_http(self, monkeypatch: pytest.MonkeyPatch, url: str) -> None:
        called: list[str] = []
        monkeypatch.setattr(webbrowser, "open", lambda *a, **k: called.append(a) or True)
        assert _Api(None).open_external(url) == {"opened": False}
        assert called == [], "webbrowser.open must not run for non-http(s) URLs"

    def test_open_failure_is_swallowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(*_a: object, **_k: object) -> bool:
            raise OSError("no browser")

        monkeypatch.setattr(webbrowser, "open", boom)
        assert _Api(None).open_external("https://example.com") == {"opened": False}
