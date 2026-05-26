"""`manage_preferences` tool — read / write Talk2View-Writer preferences.

This is the chat surface for ADR-0035's user-facing toggles. The agent
calls this tool when the user says e.g. "turn off AI track changes" or
"what's the current AI track-changes setting?".

Storage and key catalogue live in :mod:`talk2view_writer.preferences`.
This module is intentionally thin — it adapts the Python API to a
JSON-shaped tool result the chat UI can render.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from talk2view import tool  # type: ignore[import-not-found]

from talk2view_writer.preferences import DEFAULTS, get_preferences
from talk2view_writer.tools._constants import lower_enum

logger = logging.getLogger(__name__)


@tool
def manage_preferences(
    action: str,
    key: str | None = None,
    value: Any = None,
) -> str:
    """Read or change Talk2View-Writer preferences.

    Preferences are persistent user settings that change the extension's
    behaviour across sessions (separate from the LibreOffice document's
    own settings). The catalogue of valid keys is fixed; trying to
    read or write an unknown key returns a structured error so the
    agent can recover.

    Args:
        action: One of:

            - ``"list"``  — return every preference and its current value.
            - ``"get"``   — return a single preference; ``key`` required.
            - ``"set"``   — change a preference; ``key`` and ``value`` required.
            - ``"reset"`` — clear a single preference back to its default;
              ``key`` required.

        key: Preference name (e.g. ``ai_track_changes_enabled``). Required
            for ``get``, ``set``, ``reset``.
        value: New value when ``action="set"``. Must JSON-serialise.

    Returns:
        JSON string. For ``list``: ``{success, preferences: {key: value, ...},
        defaults: {key: value, ...}}``. For ``get``: ``{success, key, value,
        default, overridden}``. For ``set`` / ``reset``: ``{success, key,
        value, default}``.

        On error: ``{error, recovery}``.

    Examples:
        Turn off AI track changes:
            ``manage_preferences(action="set",
            key="ai_track_changes_enabled", value=False)``

        Check current value:
            ``manage_preferences(action="get",
            key="ai_track_changes_enabled")``

        Reset to default (True):
            ``manage_preferences(action="reset",
            key="ai_track_changes_enabled")``
    """
    # Case-insensitive enum arg (schema enum dropped — see Writer #5).
    action = lower_enum(action) or ""

    valid_actions = ("list", "get", "set", "reset")
    if action not in valid_actions:
        return json.dumps({
            "error": f"Unknown action {action!r}.",
            "recovery": f"Use one of: {', '.join(valid_actions)}.",
        })

    prefs = get_preferences()

    if action == "list":
        return json.dumps({
            "success": True,
            "preferences": prefs.all(),
            "defaults": dict(DEFAULTS),
        })

    if key is None:
        return json.dumps({
            "error": f"action={action!r} requires a 'key' argument.",
            "recovery": (
                f"Pass key=<one of {sorted(DEFAULTS)}>."
            ),
        })

    if key not in DEFAULTS:
        return json.dumps({
            "error": f"Unknown preference key {key!r}.",
            "recovery": f"Use one of: {', '.join(sorted(DEFAULTS))}.",
        })

    if action == "get":
        current = prefs.get(key)
        return json.dumps({
            "success": True,
            "key": key,
            "value": current,
            "default": DEFAULTS[key],
            "overridden": current != DEFAULTS[key],
        })

    if action == "set":
        if value is None:
            return json.dumps({
                "error": "action='set' requires a 'value' argument.",
                "recovery": (
                    f"Pass value=<the new value>. For {key} the default "
                    f"is {DEFAULTS[key]!r}."
                ),
            })
        # Validate type compatibility with the default — we don't allow
        # e.g. setting a bool preference to a string. This catches a
        # whole class of agent-error before it lands in the file.
        default = DEFAULTS[key]
        if isinstance(default, bool) and not isinstance(value, bool):
            return json.dumps({
                "error": (
                    f"Preference {key!r} expects a boolean; got "
                    f"{type(value).__name__}."
                ),
                "recovery": "Pass value=true or value=false.",
            })
        try:
            prefs.set(key, value)
        except (TypeError, OSError) as exc:
            logger.exception("manage_preferences set %s failed", key)
            return json.dumps({
                "error": f"Could not save preference: {exc}",
                "recovery": "Try again, or check that the config dir is writable.",
            })
        return json.dumps({
            "success": True,
            "key": key,
            "value": value,
            "default": DEFAULTS[key],
        })

    # action == "reset"
    try:
        prefs.reset(key)
    except OSError as exc:
        logger.exception("manage_preferences reset %s failed", key)
        return json.dumps({
            "error": f"Could not reset preference: {exc}",
            "recovery": "Try again, or check that the config dir is writable.",
        })
    return json.dumps({
        "success": True,
        "key": key,
        "value": prefs.get(key),  # back to default
        "default": DEFAULTS[key],
    })


TOOLS = [manage_preferences]
