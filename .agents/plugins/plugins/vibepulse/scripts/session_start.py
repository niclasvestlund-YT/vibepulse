#!/usr/bin/env python3
"""Provide fail-safe VibePulse guidance plus bounded startup health."""

from __future__ import annotations

import json
import os
import sys

from loopback import get_json, strict_json_loads


MAX_INPUT_BYTES = 64 * 1024
DEFAULT_PORT = 8737
HEALTH_TIMEOUT_SECONDS = 0.45
# Content fingerprint of the tokenserver Python sources shipped beside this
# plugin release. A test forces this marker to move whenever host code moves.
EXPECTED_HOST_SOURCE_FINGERPRINT = "4c3dc7ddef6c"
CONTEXT = (
    "For short, non-secret, single-choice 2–3 option questions or "
    "recommendations, use mcp__vibepulse__ask. Mark at most one option "
    "recommended only when you genuinely recommend it. Use built-in "
    "request_user_input for free-form text, secrets, multiple questions, "
    "multi-select, or anything the tool cannot represent exactly. If the "
    "VibePulse tool is unavailable, times out, or reports computer fallback, "
    "use built-in request_user_input immediately. Never treat silence, panel "
    "absence, or fallback as approval. Permission decisions remain subject "
    "to Codex policy."
)


def _port():
    raw = os.environ.get("VIBEPULSE_PORT")
    if raw is None:
        return DEFAULT_PORT
    if not raw.isascii() or not raw.isdecimal():
        return None
    value = int(raw)
    return value if 1 <= value <= 65535 else None


def _credential_risk(root, interactions):
    if interactions.get("claude") is not True:
        return ""
    credential = root.get("claudeCredential")
    if not isinstance(credential, dict):
        return " Saved Claude credential health is unavailable."
    status = credential.get("status")
    if status == "expiring":
        return " Saved Claude credential is expiring; refresh it in Claude Code."
    if status == "expired":
        return (" Saved Claude credential is expired; refresh it in Claude "
                "Code before the next source interruption.")
    if status in {"unavailable", "unknown"}:
        return " Saved Claude credential is unavailable."
    return ""


def classify_startup_health(root, tokens):
    """Return one content-free fault class from trusted local snapshots."""
    if root is None:
        return ("VibePulse startup health: SERVER UNAVAILABLE. Run "
                "`python3 tools/vibepulse_setup.py doctor`; do not assume "
                "the panel can receive or approve anything.")
    if (not isinstance(root, dict) or
            root.get("service") != "torget-tokenserver"):
        return ("VibePulse startup health: WRONG OR OLD LOCAL SERVICE. Run "
                "the setup doctor and tokenserver smoke test.")
    if root.get("srcFingerprint") != EXPECTED_HOST_SOURCE_FINGERPRINT:
        return ("VibePulse startup health: SERVICE VERSION DRIFT. The loaded "
                "plugin and live tokenserver are from different source "
                "revisions; repair all integrations from one durable checkout "
                "and run the tokenserver smoke test.")
    interactions = root.get("interactions")
    if not isinstance(interactions, dict) or not isinstance(tokens, dict):
        return ("VibePulse startup health: LOCAL API DEGRADED. Run the setup "
                "doctor and tokenserver smoke test.")

    required_flags = (
        "claudeWeekStale", "claudeModelWeekStale", "codexWeekStale")
    if any(not isinstance(tokens.get(key), bool) for key in required_flags):
        return ("VibePulse startup health: OLD OR INCOMPLETE DIAGNOSTICS. "
                "Run the setup doctor and tokenserver smoke test.")

    stale = []
    if interactions.get("claude") is True and (
            tokens["claudeWeekStale"] or tokens["claudeModelWeekStale"]):
        stale.append("Claude")
    if (interactions.get("codex") is True and
            tokens["codexWeekStale"]):
        stale.append("Codex")
    risk = _credential_risk(root, interactions)
    if stale:
        providers = "+".join(stale)
        probe = root.get("claudeProbe")
        if "Claude" in stale and probe == "usage_http_200 + ok":
            action = ("The active Claude probe is live but published data is "
                      "stale; run the tokenserver smoke test.")
        elif "Claude" in stale and probe == "not_run":
            action = "The first Claude probe has not completed; recheck shortly."
        else:
            action = "Run the setup doctor and tokenserver smoke test."
        return (f"VibePulse startup health: PROVIDER DATA STALE ({providers}). "
                f"{action}{risk}")

    panel = interactions.get("panel")
    if not isinstance(panel, dict):
        return ("VibePulse startup health: PANEL DIAGNOSTICS UNAVAILABLE; "
                "provider data is fresh. Run the tokenserver smoke test."
                f"{risk}")
    panel_status = panel.get("status")
    if panel_status == "stale":
        return ("VibePulse startup health: DEVICE PATH STALE; provider data "
                "is fresh but direct panel polling is not. Check panel power, "
                "network, discovery, and firmware before refreshing Claude "
                f"or restarting a healthy tokenserver.{risk}")
    if panel_status == "waiting":
        return ("VibePulse startup health: PANEL LAN WAITING; provider data "
                "is fresh but no direct panel poll is confirmed since service "
                f"start. Relay-only use may still be healthy.{risk}")
    if panel_status != "ready":
        return ("VibePulse startup health: UNKNOWN PANEL STATE; provider data "
                f"is fresh. Run the tokenserver smoke test.{risk}")
    if panel.get("httpStallRecoveryBoot") is True:
        return ("VibePulse startup health: HEALTHY AFTER DEVICE SELF-RECOVERY; "
                "provider data and direct panel polling are fresh, and this "
                f"panel boot followed the bounded HTTP-stall restart.{risk}")
    return ("VibePulse startup health: HEALTHY; provider data is fresh and "
            f"recent direct panel polling is confirmed.{risk}")


def _startup_health():
    port = _port()
    if port is None:
        return classify_startup_health(None, None)
    base = f"http://127.0.0.1:{port}"
    root = get_json(
        f"{base}/", connect_timeout=HEALTH_TIMEOUT_SECONDS,
        read_timeout=HEALTH_TIMEOUT_SECONDS)
    if root is None:
        return classify_startup_health(None, None)
    tokens = get_json(
        f"{base}/api/tokens", connect_timeout=HEALTH_TIMEOUT_SECONDS,
        read_timeout=HEALTH_TIMEOUT_SECONDS)
    return classify_startup_health(root, tokens)


def main():
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            return 0
        event = strict_json_loads(raw.decode("utf-8", errors="strict"))
        if not isinstance(event, dict):
            return 0
        additional_context = f"{CONTEXT}\n\n{_startup_health()}"
        result = {"hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": additional_context,
        }}
        if len(additional_context) > 1750:
            return 0
        encoded = (json.dumps(
            result, ensure_ascii=False, separators=(",", ":")) +
            "\n").encode("utf-8")
        sys.stdout.buffer.write(encoded)
        sys.stdout.buffer.flush()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
