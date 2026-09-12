#!/usr/bin/env python3
"""Forward a bounded Codex permission hook to the local VibePulse service."""

from __future__ import annotations

import json
import os
import sys
import unicodedata

from loopback import READ_TIMEOUT_SECONDS, post_json, strict_json_loads


MAX_INPUT_BYTES = 64 * 1024


def _port():
    raw = os.environ.get("VIBEPULSE_PORT")
    if raw is None:
        return 8737
    if not raw.isascii() or not raw.isdecimal():
        return None
    value = int(raw)
    return value if 1 <= value <= 65535 else None


def _read_object():
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        return None
    try:
        value = strict_json_loads(raw.decode("utf-8", errors="strict"))
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def _clean_message(value):
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 1024:
        return False
    return not any(unicodedata.category(char).startswith("C") for char in value)


def _valid_decision(value):
    if not isinstance(value, dict) or set(value) != {"hookSpecificOutput"}:
        return False
    output = value["hookSpecificOutput"]
    if not isinstance(output, dict) or set(output) != {"hookEventName", "decision"}:
        return False
    if output["hookEventName"] != "PermissionRequest":
        return False
    decision = output["decision"]
    if not isinstance(decision, dict):
        return False
    if decision == {"behavior": "allow"}:
        return True
    return set(decision) == {"behavior", "message"} and \
        decision["behavior"] == "deny" and _clean_message(decision["message"])


def _read_timeout():
    """Permit deterministic fail-silence timeout tests without changing defaults."""
    raw = os.environ.get("_VIBEPULSE_TEST_READ_TIMEOUT")
    if raw is None:
        return READ_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return READ_TIMEOUT_SECONDS
    return value if 0 < value <= READ_TIMEOUT_SECONDS else READ_TIMEOUT_SECONDS


def main():
    try:
        event = _read_object()
        port = _port()
        if event is None or port is None:
            return 0
        result = post_json(
            f"http://127.0.0.1:{port}/api/codex/permission", event,
            read_timeout=_read_timeout())
        if _valid_decision(result):
            encoded = (json.dumps(
                result, ensure_ascii=False, separators=(",", ":")) +
                "\n").encode("utf-8")
            sys.stdout.buffer.write(encoded)
            sys.stdout.buffer.flush()
    except Exception:  # noqa: S110 - a hook must exit 0 quietly; Codex treats any noise as a failure
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
