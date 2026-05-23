"""Tests for ``talk2view_writer.preferences``."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from talk2view_writer.preferences import (
    DEFAULTS,
    PREF_AI_TRACK_CHANGES,
    Preferences,
    _reset_singleton_for_tests,
    default_preferences_path,
    get_preferences,
)


@pytest.mark.unit
class TestDefaults:
    def test_ai_track_changes_default_is_true(self) -> None:
        """The user-visible default for AI redlining must be True.

        This is the load-bearing contract for ADR-0035: track-changes
        is ON by default so AI edits land as redlines the user can
        review. A test guards against an accidental flip during refactor.
        """
        assert DEFAULTS[PREF_AI_TRACK_CHANGES] is True


@pytest.mark.unit
class TestDefaultPreferencesPath:
    def test_linux_uses_xdg_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("XDG_CONFIG_HOME", "/custom/xdg")
        assert (
            default_preferences_path()
            == Path("/custom/xdg/talk2view-writer/preferences.json")
        )

    def test_linux_falls_back_to_dot_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        path = default_preferences_path()
        assert path.name == "preferences.json"
        assert path.parent.name == "talk2view-writer"
        assert path.parent.parent == Path.home() / ".config"

    def test_macos_uses_application_support(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        path = default_preferences_path()
        assert path == (
            Path.home() / "Library" / "Application Support"
            / "talk2view-writer" / "preferences.json"
        )

    def test_windows_uses_appdata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("APPDATA", "C:\\Users\\test\\AppData\\Roaming")
        path = default_preferences_path()
        assert path.parts[-1] == "preferences.json"
        assert path.parts[-2] == "talk2view-writer"


@pytest.mark.unit
class TestPreferences:
    def test_get_unset_returns_default(self, tmp_path: Path) -> None:
        prefs = Preferences(tmp_path / "preferences.json")
        assert prefs.get(PREF_AI_TRACK_CHANGES) is True

    def test_set_then_get_roundtrip(self, tmp_path: Path) -> None:
        prefs = Preferences(tmp_path / "preferences.json")
        prefs.set(PREF_AI_TRACK_CHANGES, False)
        assert prefs.get(PREF_AI_TRACK_CHANGES) is False

    def test_persistence_across_instances(self, tmp_path: Path) -> None:
        path = tmp_path / "preferences.json"
        Preferences(path).set(PREF_AI_TRACK_CHANGES, False)
        # Fresh instance reads from disk.
        assert Preferences(path).get(PREF_AI_TRACK_CHANGES) is False

    def test_get_unknown_key_raises(self, tmp_path: Path) -> None:
        prefs = Preferences(tmp_path / "preferences.json")
        with pytest.raises(KeyError, match="Unknown preference key"):
            prefs.get("totally_made_up_key")

    def test_set_unknown_key_raises(self, tmp_path: Path) -> None:
        prefs = Preferences(tmp_path / "preferences.json")
        with pytest.raises(KeyError, match="Unknown preference key"):
            prefs.set("typo_key", True)

    def test_reset_restores_default(self, tmp_path: Path) -> None:
        prefs = Preferences(tmp_path / "preferences.json")
        prefs.set(PREF_AI_TRACK_CHANGES, False)
        prefs.reset(PREF_AI_TRACK_CHANGES)
        assert prefs.get(PREF_AI_TRACK_CHANGES) is True

    def test_reset_missing_key_is_noop(self, tmp_path: Path) -> None:
        prefs = Preferences(tmp_path / "preferences.json")
        prefs.reset(PREF_AI_TRACK_CHANGES)  # never set — must not raise

    def test_all_merges_defaults_with_overrides(self, tmp_path: Path) -> None:
        prefs = Preferences(tmp_path / "preferences.json")
        snapshot = prefs.all()
        assert snapshot == DEFAULTS
        prefs.set(PREF_AI_TRACK_CHANGES, False)
        snapshot = prefs.all()
        assert snapshot[PREF_AI_TRACK_CHANGES] is False

    def test_atomic_write_via_tmp_rename(self, tmp_path: Path) -> None:
        path = tmp_path / "preferences.json"
        prefs = Preferences(path)
        prefs.set(PREF_AI_TRACK_CHANGES, False)
        assert not (tmp_path / "preferences.json.tmp").exists()
        assert path.exists()
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert on_disk == {PREF_AI_TRACK_CHANGES: False}

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX perms only")
    def test_file_mode_is_0600(self, tmp_path: Path) -> None:
        path = tmp_path / "preferences.json"
        prefs = Preferences(path)
        prefs.set(PREF_AI_TRACK_CHANGES, False)
        mode = path.stat().st_mode & 0o777
        assert mode == 0o600

    def test_corrupt_file_resets_to_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "preferences.json"
        path.write_text("not valid json {{{", encoding="utf-8")
        prefs = Preferences(path)
        # Default still applies once the corruption is shrugged off.
        assert prefs.get(PREF_AI_TRACK_CHANGES) is True

    def test_unknown_keys_in_file_are_dropped_silently(self, tmp_path: Path) -> None:
        """Future-build forward compat — drop unknown keys on load."""
        path = tmp_path / "preferences.json"
        path.write_text(
            json.dumps({
                PREF_AI_TRACK_CHANGES: False,
                "unknown_future_key": "whatever",
            }),
            encoding="utf-8",
        )
        prefs = Preferences(path)
        assert prefs.get(PREF_AI_TRACK_CHANGES) is False

    def test_non_dict_json_resets_to_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "preferences.json"
        path.write_text('["not", "a", "dict"]', encoding="utf-8")
        prefs = Preferences(path)
        assert prefs.get(PREF_AI_TRACK_CHANGES) is True

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "deeper" / "preferences.json"
        prefs = Preferences(path)
        prefs.set(PREF_AI_TRACK_CHANGES, False)
        assert path.exists()


@pytest.mark.unit
class TestGetPreferences:
    def test_returns_singleton(self) -> None:
        _reset_singleton_for_tests()
        try:
            assert get_preferences() is get_preferences()
        finally:
            _reset_singleton_for_tests()

    def test_reset_drops_cached_singleton(self) -> None:
        _reset_singleton_for_tests()
        first = get_preferences()
        _reset_singleton_for_tests()
        second = get_preferences()
        assert first is not second
        _reset_singleton_for_tests()
