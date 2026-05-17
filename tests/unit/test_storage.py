"""Tests for ``talk2view_writer.storage.FileTokenStorage``."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from talk2view_writer.storage import FileTokenStorage, default_storage_path


@pytest.mark.unit
class TestFileTokenStorage:
    def test_get_missing_returns_none(self, tmp_path: Path) -> None:
        store = FileTokenStorage(tmp_path / "tokens.json")
        assert store.get("access_token") is None

    def test_set_then_get_roundtrip(self, tmp_path: Path) -> None:
        store = FileTokenStorage(tmp_path / "tokens.json")
        store.set("access_token", "abc123")
        assert store.get("access_token") == "abc123"

    def test_persistence_across_instances(self, tmp_path: Path) -> None:
        path = tmp_path / "tokens.json"
        FileTokenStorage(path).set("refresh_token", "rrr")
        # A fresh instance reads from disk.
        assert FileTokenStorage(path).get("refresh_token") == "rrr"

    def test_delete_removes_key(self, tmp_path: Path) -> None:
        store = FileTokenStorage(tmp_path / "tokens.json")
        store.set("k", "v")
        store.delete("k")
        assert store.get("k") is None

    def test_delete_missing_key_is_noop(self, tmp_path: Path) -> None:
        store = FileTokenStorage(tmp_path / "tokens.json")
        store.delete("never_set")  # must not raise

    def test_overwrite_updates_value(self, tmp_path: Path) -> None:
        store = FileTokenStorage(tmp_path / "tokens.json")
        store.set("k", "v1")
        store.set("k", "v2")
        assert store.get("k") == "v2"

    def test_atomic_write_via_tmp_rename(self, tmp_path: Path) -> None:
        path = tmp_path / "tokens.json"
        store = FileTokenStorage(path)
        store.set("k", "v")
        # The tmp file must not linger.
        assert not (tmp_path / "tokens.json.tmp").exists()
        assert path.exists()
        # The on-disk content must be valid JSON with our value.
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data == {"k": "v"}

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX perms only")
    def test_file_mode_is_0600(self, tmp_path: Path) -> None:
        path = tmp_path / "tokens.json"
        FileTokenStorage(path).set("k", "v")
        mode = path.stat().st_mode & 0o777
        assert mode == 0o600, f"expected 0o600, got {oct(mode)}"

    def test_corrupt_file_resets_to_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "tokens.json"
        path.write_text("this is not json", encoding="utf-8")
        store = FileTokenStorage(path)
        # A corrupt file becomes an empty store (no crash).
        assert store.get("anything") is None
        # And a subsequent write works.
        store.set("k", "v")
        assert store.get("k") == "v"

    def test_non_dict_json_resets_to_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "tokens.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")  # JSON list, not object
        store = FileTokenStorage(path)
        assert store.get("anything") is None

    def test_empty_file_is_handled(self, tmp_path: Path) -> None:
        path = tmp_path / "tokens.json"
        path.write_text("", encoding="utf-8")
        store = FileTokenStorage(path)
        assert store.get("anything") is None

    def test_values_are_coerced_to_str(self, tmp_path: Path) -> None:
        path = tmp_path / "tokens.json"
        path.write_text(json.dumps({"k": 42}), encoding="utf-8")
        store = FileTokenStorage(path)
        # Per the SDK protocol, values must be strings.
        assert store.get("k") == "42"

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        nested = tmp_path / "a" / "b" / "c" / "tokens.json"
        store = FileTokenStorage(nested)
        store.set("k", "v")
        assert nested.exists()


@pytest.mark.unit
class TestDefaultStoragePath:
    def test_returns_under_user_home(self) -> None:
        path = default_storage_path()
        assert "talk2view-writer" in str(path)
        assert path.name == "tokens.json"

    def test_xdg_config_home_respected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        if sys.platform in ("darwin", "win32"):
            pytest.skip("XDG only applies on Linux/BSD")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        path = default_storage_path()
        assert str(path).startswith(str(tmp_path))
