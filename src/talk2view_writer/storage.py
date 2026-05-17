"""Token storage backends for the Talk2View Python SDK.

The SDK exposes a :class:`talk2view.storage.TokenStorage` Protocol with
``get``/``set``/``delete`` methods. We implement a file-backed store so
the user JWT + refresh token survive LibreOffice restarts. See
``docs/adrs/0014-file-token-storage.md`` for the security trade-offs.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)


def default_storage_path() -> Path:
    """Return the OS-appropriate config-dir path for Talk2View-Writer tokens.

    Platform conventions:

    - Linux / BSD: ``$XDG_CONFIG_HOME/talk2view-writer/tokens.json`` if
      ``$XDG_CONFIG_HOME`` is set, otherwise ``~/.config/talk2view-writer/tokens.json``.
    - macOS: ``~/Library/Application Support/talk2view-writer/tokens.json``.
    - Windows: ``%APPDATA%/talk2view-writer/tokens.json``.

    Falls back to ``~/.talk2view-writer/tokens.json`` on platforms we do
    not specifically recognise.
    """
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "talk2view-writer" / "tokens.json"


class FileTokenStorage:
    """SDK-compatible token store backed by a single JSON file.

    Thread-safe (operations serialise on an internal lock). The file is
    written atomically via the temp-file + rename pattern, and on POSIX
    systems is chmod'd to ``0o600`` (user read/write only).

    Implements the :class:`talk2view.storage.TokenStorage` Protocol —
    ``get(key) -> str | None``, ``set(key, value)``, ``delete(key)``.
    Keys used by the SDK include ``access_token``, ``refresh_token``,
    ``user``.

    Raises:
        OSError: If the storage directory cannot be created or the file
            cannot be written. We intentionally do *not* swallow these —
            failure to persist credentials is a real problem the user
            should know about.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path: Path = path or default_storage_path()
        self._lock = threading.Lock()
        self._cache: Optional[Dict[str, str]] = None  # lazy-loaded
        logger.info("FileTokenStorage at %s", self._path)

    @property
    def path(self) -> Path:
        """The on-disk file backing this storage."""
        return self._path

    # ----- TokenStorage protocol -----

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            return self._load_locked().get(key)

    def set(self, key: str, value: str) -> None:
        with self._lock:
            data = self._load_locked()
            data[key] = value
            self._save_locked(data)

    def delete(self, key: str) -> None:
        with self._lock:
            data = self._load_locked()
            if key in data:
                del data[key]
                self._save_locked(data)

    # ----- internals -----

    def _load_locked(self) -> Dict[str, str]:
        if self._cache is not None:
            return self._cache
        if not self._path.exists():
            self._cache = {}
            return self._cache
        try:
            raw = self._path.read_text(encoding="utf-8")
            parsed = json.loads(raw) if raw.strip() else {}
        except (json.JSONDecodeError, OSError):
            logger.exception("Corrupt token file at %s; resetting", self._path)
            parsed = {}
        if not isinstance(parsed, dict):
            logger.warning(
                "Token file at %s is not a JSON object; resetting", self._path
            )
            parsed = {}
        # Coerce all values to str — SDK contract.
        self._cache = {str(k): str(v) for k, v in parsed.items()}
        return self._cache

    def _save_locked(self, data: Dict[str, str]) -> None:
        self._cache = dict(data)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        if sys.platform != "win32":
            try:
                os.chmod(tmp_path, 0o600)
            except OSError:
                logger.exception("Could not chmod token file at %s", tmp_path)
        os.replace(tmp_path, self._path)
