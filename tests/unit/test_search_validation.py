"""Tests for ``search_document`` argument validation.

The early validation paths fire before any UNO access, so they're
testable directly via the underlying ``_validate_replace_format``
helper. The full ``search_document`` body requires a running
LibreOffice instance — covered by integration tests (Phase F).
"""

from __future__ import annotations

import json

import pytest

from talk2view_writer.tools.search import _validate_replace_format


@pytest.mark.unit
class TestValidateReplaceFormat:
    def test_returns_none_on_valid_input(self) -> None:
        assert _validate_replace_format({"bold": True}) is None
        assert _validate_replace_format({"color": "FF0000"}) is None
        assert _validate_replace_format({"size": 12, "italic": True}) is None
        assert _validate_replace_format({"highlight": "Yellow"}) is None

    def test_color_with_hash_prefix_rejected(self) -> None:
        err = _validate_replace_format({"color": "#FF0000"})
        assert err is not None
        assert "should not include the # prefix" in err["error"]
        # Recovery message must show the corrected value.
        assert "FF0000" in err["recovery"]
        assert "#FF0000" in err["recovery"]

    def test_color_non_hex_rejected(self) -> None:
        err = _validate_replace_format({"color": "not-hex"})
        assert err is not None
        assert "Invalid replace_format.color" in err["error"]
        assert "6-character hex" in err["recovery"]

    def test_color_short_hex_rejected(self) -> None:
        err = _validate_replace_format({"color": "FFF"})
        assert err is not None
        assert "Invalid replace_format.color" in err["error"]

    def test_color_lowercase_hex_accepted(self) -> None:
        # The regex is case-insensitive.
        assert _validate_replace_format({"color": "ff00aa"}) is None

    def test_size_zero_rejected(self) -> None:
        err = _validate_replace_format({"size": 0})
        assert err is not None
        assert "size must be > 0" in err["error"]

    def test_size_negative_rejected(self) -> None:
        err = _validate_replace_format({"size": -5})
        assert err is not None

    def test_size_bool_skipped(self) -> None:
        # ``True`` is an int subclass; the validator must skip booleans.
        assert _validate_replace_format({"size": True}) is None

    def test_size_string_skipped(self) -> None:
        # We only validate numeric sizes — string would be a type error
        # caught upstream by JSON-schema, not here.
        assert _validate_replace_format({"size": "12"}) is None

    def test_invalid_highlight_rejected(self) -> None:
        err = _validate_replace_format({"highlight": "Periwinkle"})
        assert err is not None
        assert "Periwinkle" in err["error"]
        assert "Yellow" in err["recovery"]

    def test_known_highlight_accepted(self) -> None:
        for name in ("Yellow", "Red", "NoColor"):
            assert _validate_replace_format({"highlight": name}) is None


@pytest.mark.unit
class TestValidationErrorsAreJsonSerialisable:
    """Every error path must yield a dict that ``json.dumps`` accepts."""

    def test_color_error_serialisable(self) -> None:
        err = _validate_replace_format({"color": "#abc"})
        assert err is not None
        json.dumps(err)  # must not raise

    def test_highlight_error_serialisable(self) -> None:
        err = _validate_replace_format({"highlight": "Bogus"})
        assert err is not None
        json.dumps(err)
