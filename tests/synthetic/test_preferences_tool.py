"""Synthetic-UNO tests for the ``manage_preferences`` tool."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from talk2view_writer.preferences import (
    PREF_AI_TRACK_CHANGES,
    Preferences,
    _reset_singleton_for_tests,
)

pytestmark = pytest.mark.synthetic


@pytest.fixture
def prefs_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Per-test preferences singleton backed by tmp_path."""
    import talk2view_writer.preferences as prefs_mod

    path = tmp_path / "preferences.json"
    monkeypatch.setattr(prefs_mod, "_INSTANCE", Preferences(path))
    yield path
    _reset_singleton_for_tests()


class TestListAction:
    def test_returns_all_preferences_and_defaults(
        self, patched_extension: object, prefs_path: Path
    ) -> None:
        from talk2view_writer.tools.preferences_tool import manage_preferences

        result = json.loads(manage_preferences(action="list"))
        assert result["success"] is True
        assert PREF_AI_TRACK_CHANGES in result["preferences"]
        assert PREF_AI_TRACK_CHANGES in result["defaults"]
        assert result["preferences"][PREF_AI_TRACK_CHANGES] is True


class TestGetAction:
    def test_returns_current_value(
        self, patched_extension: object, prefs_path: Path
    ) -> None:
        from talk2view_writer.tools.preferences_tool import manage_preferences

        result = json.loads(
            manage_preferences(action="get", key=PREF_AI_TRACK_CHANGES)
        )
        assert result["success"] is True
        assert result["value"] is True
        assert result["overridden"] is False

    def test_get_without_key_errors(
        self, patched_extension: object, prefs_path: Path
    ) -> None:
        from talk2view_writer.tools.preferences_tool import manage_preferences

        result = json.loads(manage_preferences(action="get"))
        assert "error" in result
        assert "key" in result["error"]

    def test_get_unknown_key_errors(
        self, patched_extension: object, prefs_path: Path
    ) -> None:
        from talk2view_writer.tools.preferences_tool import manage_preferences

        result = json.loads(
            manage_preferences(action="get", key="totally_invented_key")
        )
        assert "error" in result
        assert "Unknown preference key" in result["error"]


class TestSetAction:
    def test_set_writes_value(
        self, patched_extension: object, prefs_path: Path
    ) -> None:
        from talk2view_writer.preferences import get_preferences
        from talk2view_writer.tools.preferences_tool import manage_preferences

        result = json.loads(
            manage_preferences(
                action="set", key=PREF_AI_TRACK_CHANGES, value=False
            )
        )
        assert result["success"] is True
        assert result["value"] is False
        # Confirm persistence — a fresh read sees the change.
        assert get_preferences().get(PREF_AI_TRACK_CHANGES) is False

    def test_set_without_value_errors(
        self, patched_extension: object, prefs_path: Path
    ) -> None:
        from talk2view_writer.tools.preferences_tool import manage_preferences

        result = json.loads(
            manage_preferences(action="set", key=PREF_AI_TRACK_CHANGES)
        )
        assert "error" in result
        assert "value" in result["error"]

    def test_set_wrong_type_errors(
        self, patched_extension: object, prefs_path: Path
    ) -> None:
        """A bool preference can't accept a string."""
        from talk2view_writer.tools.preferences_tool import manage_preferences

        result = json.loads(
            manage_preferences(
                action="set",
                key=PREF_AI_TRACK_CHANGES,
                value="yes please",
            )
        )
        assert "error" in result
        assert "boolean" in result["error"]


class TestResetAction:
    def test_reset_restores_default(
        self, patched_extension: object, prefs_path: Path
    ) -> None:
        from talk2view_writer.preferences import get_preferences
        from talk2view_writer.tools.preferences_tool import manage_preferences

        get_preferences().set(PREF_AI_TRACK_CHANGES, False)
        result = json.loads(
            manage_preferences(action="reset", key=PREF_AI_TRACK_CHANGES)
        )
        assert result["success"] is True
        assert result["value"] is True  # back to default
        assert get_preferences().get(PREF_AI_TRACK_CHANGES) is True


class TestInvalidAction:
    def test_unknown_action_errors(
        self, patched_extension: object, prefs_path: Path
    ) -> None:
        from talk2view_writer.tools.preferences_tool import manage_preferences

        result = json.loads(manage_preferences(action="frobnicate"))
        assert "error" in result
        assert "Unknown action" in result["error"]
