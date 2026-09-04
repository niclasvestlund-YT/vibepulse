#!/usr/bin/env python3
"""Provide the narrow, fail-safe VibePulse question guidance to Codex."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys

from loopback import get_json, strict_json_loads


MAX_INPUT_BYTES = 64 * 1024
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
HEALTH_CONNECT_TIMEOUT_SECONDS = 0.25
HEALTH_READ_TIMEOUT_SECONDS = 0.6
CODEX_CONFIG_MAX_BYTES = 64 * 1024
_CODEX_STRING_SETTING = re.compile(
    r'^[ \t]*(approval_policy|approvals_reviewer|sandbox_mode)[ \t]*='
    r'[ \t]*(["\'])([^"\']*)\2[ \t]*(?:#.*)?$')


def _port():
    raw = os.environ.get("VIBEPULSE_PORT")
    if raw is None:
        return 8737
    if not raw.isascii() or not raw.isdecimal():
        return None
    value = int(raw)
    return value if 1 <= value <= 65535 else None


def _saved_codex_permission_fix():
    """Return a content-free fix when saved Codex settings suppress cards."""
    codex_home = os.environ.get("CODEX_HOME")
    config_path = ((Path(codex_home) if codex_home else Path.home() / ".codex") /
                   "config.toml")
    try:
        raw = config_path.read_bytes()
        if len(raw) > CODEX_CONFIG_MAX_BYTES:
            raise ValueError("oversized config")
        text = raw.decode("utf-8", errors="strict")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, ValueError):
        return ("VibePulse startup health: FIX — saved Codex config is "
                "unreadable; verify /permissions on this computer.")
    config = {}
    for line in text.splitlines():
        if line.lstrip().startswith("["):
            break
        match = _CODEX_STRING_SETTING.fullmatch(line)
        if match is not None:
            config[match.group(1)] = match.group(3)
    if config.get("approval_policy") == "never":
        return ("VibePulse startup health: FIX — Codex approval_policy is "
                "never, so no permission card can reach the panel. Use "
                "on-request and start a new task.")
    if config.get("approvals_reviewer") == "auto_review":
        return ("VibePulse startup health: FIX — Codex approvals are routed "
                "to auto_review instead of the user. Use the user reviewer "
                "and start a new task.")
    if config.get("sandbox_mode") == "danger-full-access":
        return ("VibePulse startup health: FIX — Codex sandbox_mode is "
                "danger-full-access, so workspace permission boundaries are "
                "bypassed. Use workspace-write and start a new task.")
    return None


def _startup_health():
    port = _port()
    if port is None:
        return ("VibePulse startup health: FIX — invalid local port; use "
                "computer fallback and tell the user once.")
    permission_fix = _saved_codex_permission_fix()
    if permission_fix is not None:
        return permission_fix
    payload = get_json(
        f"http://127.0.0.1:{port}/",
        connect_timeout=HEALTH_CONNECT_TIMEOUT_SECONDS,
        read_timeout=HEALTH_READ_TIMEOUT_SECONDS)
    if not isinstance(payload, dict) or \
            payload.get("service") != "torget-tokenserver":
        return ("VibePulse startup health: FIX — local bridge unavailable; "
                "use computer fallback and tell the user once.")
    interactions = payload.get("interactions")
    if not isinstance(interactions, dict) or \
            interactions.get("codex") is not True:
        return ("VibePulse startup health: FIX — Codex routing is off or "
                "stale; use computer fallback and tell the user once.")
    relay = interactions.get("relay")
    if isinstance(relay, dict) and relay.get("status") == "disabled":
        return ("VibePulse startup health: FIX — the configured interaction "
                "relay is unavailable; use computer fallback and tell the "
                "user once.")
    panel = interactions.get("panel")
    if isinstance(panel, dict) and panel.get("status") == "ready":
        transport = ("LAN and encrypted relay" if isinstance(relay, dict)
                     and relay.get("status") == "ready" else "LAN")
        panel_health = (
            "VibePulse startup health: READY — local bridge, Codex route, "
            f"and recent panel polling are healthy over {transport}.")
        quota_health = _claude_quota_health(payload, interactions)
        return (f"{panel_health} {quota_health}" if quota_health
                else panel_health)
    if not isinstance(panel, dict):
        return ("VibePulse startup health: FIX — bridge diagnostics are too "
                "old to prove panel reachability; use computer fallback and "
                "tell the user once.")
    return ("VibePulse startup health: FIX — no recent direct panel poll was "
            "observed; relay-only reachability is not proven. Use computer "
            "fallback and tell the user once.")


def _claude_quota_health(payload, interactions):
    """Return a content-free startup warning before Fable can go stale."""
    if interactions.get("claude") is not True:
        return None
    credential = payload.get("claudeCredential")
    if not isinstance(credential, dict):
        return ("Claude quota health: FIX — expiry guard is missing; restart "
                "the VibePulse tokenserver and tell the user.")
    status = credential.get("status")
    remaining = credential.get("expiresInMin")
    if status == "ready":
        return None
    if status == "expiring" and isinstance(remaining, int) and remaining >= 0:
        return (f"Claude quota health: FIX — the saved credential expires in "
                f"{remaining} min; tell the user to start a new Claude Code "
                "CLI turn before then.")
    if status == "expired":
        return ("Claude quota health: FIX — the saved credential expired; "
                "tell the user that a new Claude Code CLI turn is required "
                "before Fable can be fresh again.")
    if status == "unavailable":
        return ("Claude quota health: FIX — no supported Claude credential "
                "is available; tell the user.")
    return ("Claude quota health: FIX — credential expiry cannot be guarded; "
            "tell the user to run VibePulse doctor.")


def main():
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            return 0
        event = strict_json_loads(raw.decode("utf-8", errors="strict"))
        if not isinstance(event, dict):
            return 0
        context = CONTEXT + " " + _startup_health()
        result = {"hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }}
        if len(context) > 1400:
            return 0
        sys.stdout.write(json.dumps(
            result, ensure_ascii=False, separators=(",", ":")) + "\n")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
