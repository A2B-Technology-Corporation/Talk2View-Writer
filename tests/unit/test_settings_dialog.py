"""Tests for ``settings_dialog`` helpers.

The dialog body itself requires a UNO Toolkit; the masking + text
assembly helpers are pure Python and tested here.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from talk2view_writer.ui.settings_dialog import (
    _build_status_text,
    _mask_partner_key,
)


@pytest.mark.unit
class TestMaskPartnerKey:
    def test_long_key_keeps_prefix_and_suffix(self) -> None:
        key = "pk_live_45c878caa500cdf6ea1a72f3e9a4ad324df061b7ec2c70d7"
        masked = _mask_partner_key(key)
        # Keep the brand prefix so users know it's a live key, and the
        # last 4 chars so they can disambiguate between rotations.
        assert masked.startswith("pk_live_")
        assert masked.endswith("70d7")
        # The middle must be hidden.
        assert "45c878" not in masked

    def test_short_key_fully_masked(self) -> None:
        assert _mask_partner_key("abc") == "***"

    def test_empty_key_fully_masked(self) -> None:
        assert _mask_partner_key("") == "***"


@pytest.mark.unit
class TestBuildStatusText:
    def _sdk(
        self, authenticated: bool = False, email: str | None = None
    ) -> MagicMock:
        sdk = MagicMock()
        sdk.is_authenticated.return_value = authenticated
        if authenticated:
            user = MagicMock()
            user.email = email
            sdk.current_user = user
        else:
            sdk.current_user = None
        return sdk

    def test_includes_backend_url(self) -> None:
        text = _build_status_text(self._sdk(False))
        assert "engine.talk2view.com" in text

    def test_logged_out_shows_login_hint(self) -> None:
        text = _build_status_text(self._sdk(False))
        assert "Logged out" in text
        assert "Talk2View → Login" in text

    def test_logged_in_shows_email(self) -> None:
        text = _build_status_text(self._sdk(True, email="ben@example.com"))
        assert "Logged in as: ben@example.com" in text

    def test_does_not_leak_full_partner_key(self) -> None:
        text = _build_status_text(self._sdk(False))
        # The middle of the key (post-prefix, pre-suffix-4) should be
        # masked. Pull the value from config to keep this test
        # responsive to key rotations.
        from talk2view_writer import config

        key = config.PARTNER_KEY
        middle = key[8:-4]
        assert middle not in text
