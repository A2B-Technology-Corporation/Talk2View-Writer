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

# Writer-specific partner key (provisioned 2026-05-17). See ADR-0010
# for the historical Word-key-sharing context.
PARTNER_KEY: str = "pk_live_474f6f895dfec144a70b841db0d7a3fe1cd1fc7317540bc7"

# Production Talk2View engine. Override only for staging / local server runs.
BASE_URL: str = "https://engine.talk2view.com"

# ---------------------------------------------------------------------------
# Identity / branding
# ---------------------------------------------------------------------------

EXTENSION_ID: str = "com.talk2view.writer"
JOB_SERVICE_NAME: str = "com.talk2view.writer.Talk2ViewJob"
PROTOCOL_HANDLER_SERVICE_NAME: str = "com.talk2view.writer.ProtocolHandler"
