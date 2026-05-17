"""Static configuration for Talk2View-Writer.

Values here are intentionally module-level constants for Phase A. Phase F
introduces a settings dialog (``Settings.xcu`` registry-backed) that
overrides these at runtime — see ADR-0010 (partner key) and ADR-0012
(token storage) under ``docs/adrs/``.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------

# Shared with Talk2View-Word (src/taskpane/App.tsx, line 6) until a
# Writer-specific partner key is provisioned. See ADR-0010.
PARTNER_KEY: str = "pk_live_45c878caa500cdf6ea1a72f3e9a4ad324df061b7ec2c70d7"

# Production Talk2View engine. Override only for staging / local server runs.
BASE_URL: str = "https://engine.talk2view.com"

# ---------------------------------------------------------------------------
# Identity / branding
# ---------------------------------------------------------------------------

EXTENSION_ID: str = "com.talk2view.writer"
DECK_ID: str = "com.talk2view.writer.Deck"
PANEL_ID: str = "com.talk2view.writer.ChatPanel"
JOB_SERVICE_NAME: str = "com.talk2view.writer.Talk2ViewJob"
PANEL_FACTORY_SERVICE_NAME: str = "com.talk2view.writer.ChatPanelFactory"
