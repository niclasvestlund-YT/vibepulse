"""Codex rollout event acceptance — shared by the live probe and the Max
Tracker backfill so both apply the exact same rule.

Kept as its own leaf module (no imports from :mod:`tokenserver` or
:mod:`max_tracker`) so both of those can import it without ever forming an
import cycle between them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional


def codex_rollout_rate_limits(obj: Any) -> Optional[dict]:
    """Accept only Codex's observed token-count rollout event envelope.

    Only a direct ``{"type": "event_msg", "payload": {"type": "token_count",
    "rate_limits": {...}}}`` shape is accepted. Quoted (stringified) or
    nested ``rate_limits`` (for example under ``payload.message`` or inside
    a JSON-encoded ``payload.content`` string) is rejected — this is the
    single acceptance rule every Codex rollout reader must share.
    """
    if not isinstance(obj, dict) or obj.get("type") != "event_msg":
        return None
    payload = obj.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "token_count":
        return None
    rate_limits = payload.get("rate_limits")
    return rate_limits if isinstance(rate_limits, dict) else None


def observation_timestamp(value: Any) -> Optional[int]:
    """Parse a rollout event's own ISO ``timestamp`` field to epoch seconds."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return int(parsed.timestamp())
    except (ValueError, OverflowError):
        return None
