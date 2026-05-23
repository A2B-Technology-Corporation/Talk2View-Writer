"""User-facing preferences for Talk2View-Writer.

This is the persistence layer for behavioural toggles the user can change
through the chat (``manage_preferences`` tool) or — eventually — through
a settings dialog. Stored as a JSON file in the OS-appropriate config
directory, written atomically (tmp-file + rename), 0o600 on POSIX.

The token store (:class:`talk2view_writer.storage.FileTokenStorage`)
shares the same directory but a different file (``tokens.json``). We
keep them separate so cleanup / reset of one does not affect the other.

Preference keys and their defaults are declared in :data:`DEFAULTS`.
Adding a new preference is a three-step change:

1. Add the key (snake_case) to :data:`DEFAULTS` with its default value.
2. Document its effect in the docstring of whichever module reads it.
3. Optionally expose it through ``manage_preferences`` if the user
   should be able to change it from chat.

ADR-0035 documents the rationale for ``ai_track_changes_enabled`` —
why it defaults to True, why we toggle redlining per-tool-call instead
of globally, and how it interacts with the user's global track-changes
setting.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


PREF_AI_TRACK_CHANGES = "ai_track_changes_enabled"

DEFAULTS: dict[str, Any] = {
    # When True (the default), every mutating AI tool call wraps its
    # body in a "save → enable RecordChanges → run → restore" envelope
    # so every AI edit appears as a tracked change the user can
    # review / accept / reject. Toggle off via the manage_preferences
    # tool: "turn off AI track changes".
    PREF_AI_TRACK_CHANGES: True,
}


def default_preferences_path() -> Path:
    """Return the OS-appropriate path for the preferences file.

    Platform conventions:

    - Linux / BSD: ``$XDG_CONFIG_HOME/talk2view-writer/preferences.json``
      if ``$XDG_CONFIG_HOME`` is set, otherwise
      ``~/.config/talk2view-writer/preferences.json``.
    - macOS: ``~/Library/Application Support/talk2view-writer/preferences.json``.
    - Windows: ``%APPDATA%/talk2view-writer/preferences.json``.
    """
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "talk2view-writer" / "preferences.json"


class Preferences:
    """File-backed key-value store for user behaviour toggles.

    Thread-safe (operations serialise on an internal lock). The on-disk
    representation is a single JSON object. Atomic writes via tmp-file
    + rename. Unknown keys raise to surface typos early; use
    :attr:`DEFAULTS` to declare what a key means.

    Raises:
        OSError: If the storage directory cannot be created or the file
            cannot be written. Surfaces — failure to persist preferences
            is something the user should know about (memory:
            ``feedback_never_hide_errors``).
        KeyError: From :meth:`get` / :meth:`set` if the key is not in
            :data:`DEFAULTS`. Catches typos at the call site.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path: Path = path or default_preferences_path()
        self._lock = threading.Lock()
        self._cache: dict[str, Any] | None = None
        logger.info("Preferences at %s", self._path)

    @property
    def path(self) -> Path:
        """The on-disk file backing this preferences store."""
        return self._path

    def get(self, key: str) -> Any:
        """Return the value for ``key``, or its default if unset.

        Raises:
            KeyError: If ``key`` is not declared in :data:`DEFAULTS`.
        """
        if key not in DEFAULTS:
            raise KeyError(
                f"Unknown preference key {key!r}. "
                f"Known keys: {sorted(DEFAULTS)}"
            )
        with self._lock:
            data = self._load_locked()
            if key in data:
                return data[key]
            return DEFAULTS[key]

    def set(self, key: str, value: Any) -> None:
        """Persist ``value`` under ``key``.

        Raises:
            KeyError: If ``key`` is not declared in :data:`DEFAULTS`.
            TypeError: If ``value`` is not JSON-serialisable.
        """
        if key not in DEFAULTS:
            raise KeyError(
                f"Unknown preference key {key!r}. "
                f"Known keys: {sorted(DEFAULTS)}"
            )
        with self._lock:
            data = self._load_locked()
            data[key] = value
            self._save_locked(data)

    def reset(self, key: str) -> None:
        """Restore ``key`` to its default by removing any override.

        Raises:
            KeyError: If ``key`` is not declared in :data:`DEFAULTS`.
        """
        if key not in DEFAULTS:
            raise KeyError(
                f"Unknown preference key {key!r}. "
                f"Known keys: {sorted(DEFAULTS)}"
            )
        with self._lock:
            data = self._load_locked()
            if key in data:
                del data[key]
                self._save_locked(data)

    def all(self) -> dict[str, Any]:
        """Return a snapshot of every preference (overrides merged with defaults)."""
        with self._lock:
            data = self._load_locked()
        merged = dict(DEFAULTS)
        merged.update(data)
        return merged

    def _load_locked(self) -> dict[str, Any]:
        if self._cache is not None:
            return self._cache
        if not self._path.exists():
            self._cache = {}
            return self._cache
        try:
            raw = self._path.read_text(encoding="utf-8")
            parsed = json.loads(raw) if raw.strip() else {}
        except (json.JSONDecodeError, OSError):
            logger.exception("Corrupt preferences file at %s; resetting", self._path)
            parsed = {}
        if not isinstance(parsed, dict):
            logger.warning(
                "Preferences file at %s is not a JSON object; resetting", self._path
            )
            parsed = {}
        # Drop any unknown keys (likely from an older/newer build).
        self._cache = {k: v for k, v in parsed.items() if k in DEFAULTS}
        return self._cache

    def _save_locked(self, data: dict[str, Any]) -> None:
        self._cache = dict(data)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        if sys.platform != "win32":
            try:
                os.chmod(tmp_path, 0o600)
            except OSError:
                logger.exception("Could not chmod preferences file at %s", tmp_path)
        os.replace(tmp_path, self._path)


_INSTANCE: Preferences | None = None
_INSTANCE_LOCK = threading.Lock()


def get_preferences() -> Preferences:
    """Return the process-wide :class:`Preferences` singleton.

    Lazy-instantiated so import is cheap and tests can monkeypatch
    ``_INSTANCE`` directly. Once created, all callers (the
    track-changes wrapper, the manage_preferences tool, future
    settings UI) read from the same in-memory cache, so a write in
    one place is visible everywhere.
    """
    global _INSTANCE
    with _INSTANCE_LOCK:
        if _INSTANCE is None:
            _INSTANCE = Preferences()
        return _INSTANCE


def _reset_singleton_for_tests() -> None:
    """Drop the cached singleton so the next get_preferences() rebuilds.

    Test helper. Tests that monkeypatch the singleton to a
    :class:`tmp_path`-backed instance should call this in teardown.
    """
    global _INSTANCE
    with _INSTANCE_LOCK:
        _INSTANCE = None
