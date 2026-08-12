"""Contract test: the ESP token body buffer holds a worst-case payload.

The VibePulse fetch task rejects the whole /api/tokens body when it
overflows BODY_MAX (components/app_tokens/net.c via torget_http_get's
all-or-nothing contract), so the buffer needs agreed headroom above the
largest payload the tokenserver can serialize — including the contract v2
stale booleans (claudeWeekStale, claudeModelWeekStale, codexWeekStale).

Standalone: not yet registered in test/run.sh; run with
    python3 test/test_token_body_capacity.py
(the stdlib `test` package shadows this directory for -m unittest).
"""

import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
NET_C = REPO_ROOT / "components" / "app_tokens" / "net.c"

# Agreed margin: the serialized worst case may use at most 75 % of the
# buffer, leaving the rest for growth and the buffer's terminator byte.
REQUIRED_MARGIN = 0.75
LEGACY_BODY_MAX = 1024


def read_body_max() -> int:
    source = NET_C.read_text(encoding="utf-8")
    matches = re.findall(r"^#define\s+BODY_MAX\s+(\d+)\s*$",
                         source, flags=re.MULTILINE)
    if len(matches) != 1:
        raise AssertionError(
            f"expected exactly one BODY_MAX define in {NET_C}, "
            f"found {len(matches)}")
    return int(matches[0])


def worst_case_payload() -> dict:
    """Field-wise maxima for every key get_snapshot can emit, plus the
    three v2 stale booleans. Values are per-field upper bounds (a superset
    of any single reachable payload), so the byte count is a true ceiling.
    """
    payload = {
        # _compute base
        "v": 2,
        "dayTokens": 999_999_999_999,
        "dayTokensPerHour": 999_999_999_999,
        "daySessions": 9999,
        "monthTokens": 9_999_999_999_999,
        "at": "2026-12-31T23:59:59+00:00",
        # limits (floats as the server publishes them)
        "claudeSessionPct": 100.0,
        "claudeSessionResetMin": 999_999,
        "claudeWeekPct": 100.0,
        "claudeWeekResetMin": 999_999,
        "claudeModelWeekPct": 100.0,
        "claudeModelWeekResetMin": 999_999,
        # 14 UTF-8 bytes, inside the ESP's 17-byte label cap; json.dumps
        # (ensure_ascii default, as the server sends it) escapes the
        # middle dot to · which widens it further on the wire.
        "claudeModelWeekLabel": "SONNET · WEEK",
        "codexSessionPct": 100.0,
        "codexSessionResetMin": 999_999,
        "codexWeekPct": 100.0,
        "codexWeekResetMin": 999_999,
        # deltas
        "claudeWeekTodayDeltaPct": 100.0,
        "claudeModelWeekTodayDeltaPct": 100.0,
        "claudeSessionHourDeltaPct": 100.0,
        "codexWeekTodayDeltaPct": 100.0,
        # contract v2 provenance (false is the widest serialization)
        "claudeWeekStale": False,
        "claudeModelWeekStale": False,
        "codexWeekStale": False,
    }
    for prefix in ("claude", "codex"):
        payload[f"{prefix}ForecastState"] = "unavailable"
        payload[f"{prefix}ForecastPctAtReset"] = 100
        payload[f"{prefix}ForecastPaceFactor"] = 9999.9
        payload[f"{prefix}ForecastAt"] = 9_999_999_999
        payload[f"{prefix}ForecastOffsetMin"] = -999_999
    return payload


def serialized_size() -> int:
    # Mirror Handler._send exactly: json.dumps defaults (spaces after
    # ':' and ',', ensure_ascii=True), UTF-8 encoded.
    return len(json.dumps(worst_case_payload()).encode())


class TokenBodyCapacityTests(unittest.TestCase):
    def test_worst_case_payload_fits_body_max_with_agreed_margin(self):
        body_max = read_body_max()
        size = serialized_size()
        self.assertLessEqual(
            size, REQUIRED_MARGIN * body_max,
            f"worst-case /api/tokens payload is {size} B but BODY_MAX "
            f"{body_max} only affords {int(REQUIRED_MARGIN * body_max)} B "
            f"at the agreed {int(REQUIRED_MARGIN * 100)} % margin; "
            f"overflow rejects the whole body and freezes the display "
            f"stale")

    def test_legacy_1024_cap_lacked_the_agreed_margin(self):
        size = serialized_size()
        self.assertGreater(
            size, REQUIRED_MARGIN * LEGACY_BODY_MAX,
            "expected the worst-case payload to prove the old 1024-byte "
            "cap insufficient; if this fails the margin analysis must be "
            "redone")


if __name__ == "__main__":
    unittest.main()
