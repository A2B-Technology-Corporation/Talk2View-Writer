"""Options dialog: row builder, preference metadata, and menu wiring.

The dialog *rendering* needs LibreOffice (UNO) and is verified manually
(like About), but the pure row builder, the preference-metadata
completeness invariant, the Addons.xcu menu entry, and the
ProtocolHandler dispatch routing are tested here without UNO.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from talk2view_writer import options
from talk2view_writer.preferences import (
    DEFAULTS,
    PREF_AI_TRACK_CHANGES,
    PREFERENCE_SPECS,
    Preferences,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ADDONS = _REPO_ROOT / "extension" / "Addons.xcu"
_UNO_ENTRY = _REPO_ROOT / "extension" / "talk2view_writer.py"


@pytest.mark.unit
class TestPreferenceMetadata:
    def test_every_default_has_a_spec(self) -> None:
        """Every preference must carry display metadata for the dialog.

        Guards the failure mode where someone adds a key to DEFAULTS but
        forgets PREFERENCE_SPECS, so it would render with the bare key as
        its label (or be skipped).
        """
        missing = set(DEFAULTS) - set(PREFERENCE_SPECS)
        assert not missing, f"preferences without a PreferenceSpec: {sorted(missing)}"

    def test_specs_have_no_orphans(self) -> None:
        orphans = set(PREFERENCE_SPECS) - set(DEFAULTS)
        assert not orphans, f"PreferenceSpec for unknown keys: {sorted(orphans)}"

    def test_specs_are_nonempty(self) -> None:
        for key, spec in PREFERENCE_SPECS.items():
            assert spec.label.strip(), f"{key} has an empty label"
            assert spec.description.strip(), f"{key} has an empty description"


@pytest.mark.unit
class TestBuildOptionsRows:
    def test_reflects_default_when_unset(self, tmp_path: Path) -> None:
        prefs = Preferences(tmp_path / "preferences.json")
        rows = options.build_options_rows(prefs)
        by_key = {r.key: r for r in rows}
        row = by_key[PREF_AI_TRACK_CHANGES]
        assert row.default is True
        assert row.value is True  # no override -> default
        assert row.label == PREFERENCE_SPECS[PREF_AI_TRACK_CHANGES].label

    def test_reflects_override(self, tmp_path: Path) -> None:
        prefs = Preferences(tmp_path / "preferences.json")
        prefs.set(PREF_AI_TRACK_CHANGES, False)
        rows = options.build_options_rows(prefs)
        by_key = {r.key: r for r in rows}
        assert by_key[PREF_AI_TRACK_CHANGES].value is False
        # Default is unchanged even when the stored value differs.
        assert by_key[PREF_AI_TRACK_CHANGES].default is True

    def test_one_row_per_boolean_default(self, tmp_path: Path) -> None:
        prefs = Preferences(tmp_path / "preferences.json")
        rows = options.build_options_rows(prefs)
        bool_keys = {k for k, v in DEFAULTS.items() if isinstance(v, bool)}
        assert {r.key for r in rows} == bool_keys

    def test_order_matches_defaults(self, tmp_path: Path) -> None:
        prefs = Preferences(tmp_path / "preferences.json")
        rows = options.build_options_rows(prefs)
        expected = [k for k, v in DEFAULTS.items() if isinstance(v, bool)]
        assert [r.key for r in rows] == expected


@pytest.mark.unit
class TestMenuWiring:
    def test_addons_declares_options_item(self) -> None:
        xcu = _ADDONS.read_text(encoding="utf-8")
        assert "vnd.com.talk2view.writer:options" in xcu
        assert "Options..." in xcu

    def test_dispatch_routes_options(self) -> None:
        src = _UNO_ENTRY.read_text(encoding="utf-8")
        assert 'command in {"options", "settings"}' in src
        assert "show_options" in src

    def test_legacy_settings_no_longer_funnels_to_chat(self) -> None:
        """The legacy 'settings' URL now opens Options, not the chat window."""
        src = _UNO_ENTRY.read_text(encoding="utf-8")
        # login/logout still funnel to chat, but settings is no longer in
        # that branch.
        assert 'command in {"login", "logout"}' in src
