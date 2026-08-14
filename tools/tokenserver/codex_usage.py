"""Month-to-date Codex usage value, scanned from the rollout logs.

The Claude side of the value multiple rides on the transcript scan that
already exists in :mod:`tokenserver`. Codex has no equivalent scan -- the
server only ever opened the newest rollout, and only for rate limits -- so
this module provides one, with the same shape of incremental cache: skip
files older than the month by mtime, remember a parse offset per file, and
re-read only what was appended.

Two facts about the rollout format drive the design, both verified against
the Codex source rather than assumed:

* ``token_count`` events carry no model. The model lives on a separate
  ``turn_context`` line, so the scan is stateful: the most recent
  ``turn_context`` names the model that later ``token_count`` events are
  priced against. Usage seen before any ``turn_context`` cannot be priced and
  is counted as unpriced rather than dropped.
* ``TokenUsageInfo`` carries both a cumulative ``total_token_usage`` and a
  per-call ``last_token_usage``. Only the latter is summed; adding up totals
  would multiply each session's usage by its number of events.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

if __package__:
    from .codex_rollout import (codex_rollout_last_token_usage,
                                codex_rollout_turn_model)
    from . import value_meter
else:  # direct execution
    from codex_rollout import (codex_rollout_last_token_usage,
                               codex_rollout_turn_model)
    import value_meter


DEFAULT_SESSIONS_DIR = Path.home() / ".codex" / "sessions"

_lock = threading.Lock()
# path -> {"stat", "identity", "offset", "month", "model", "records"}
# ``model`` persists across incremental reads: a turn_context seen in an
# earlier chunk still names the model for token_counts appended later.
_file_cache: dict[Path, dict] = {}


def _parse_file(path: Path, month_start: datetime, start_offset: int = 0,
                model: Optional[str] = None,
                table: Optional[value_meter.PriceTable] = None):
    """Parse complete rollout rows from ``start_offset``.

    Returns ``(records, parsed_until, model)`` where each record is
    ``(day, usd, priced_tokens, unpriced_tokens)`` and ``model`` is the
    turn_context model in force at the end of the chunk, to be carried into
    the next incremental read.
    """
    records = []
    parsed_until = start_offset
    try:
        with open(path, "rb") as handle:
            handle.seek(start_offset)
            while True:
                line_start = handle.tell()
                raw = handle.readline()
                if not raw:
                    break
                if not raw.endswith(b"\n"):
                    # A writer may still be appending this row; resume at its
                    # start next time rather than consuming a fragment.
                    parsed_until = line_start
                    break
                parsed_until = handle.tell()
                try:
                    obj = json.loads(raw)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue

                turn_model = codex_rollout_turn_model(obj)
                if turn_model is not None:
                    model = turn_model
                    continue

                usage = codex_rollout_last_token_usage(obj)
                if usage is None:
                    continue
                stamp = _timestamp(obj.get("timestamp"))
                if stamp is None or stamp < month_start:
                    continue
                day = stamp.strftime("%Y-%m-%d")
                usd, unpriced = (table or value_meter.default_table()).price(
                    model, usage)
                priced = 0 if unpriced else _counted(usage)
                if usd or unpriced or priced:
                    records.append((day, usd, priced, unpriced))
    except OSError:
        pass  # removed mid-read; the next scan sees it
    return records, parsed_until, model


def _counted(usage: dict) -> int:
    """Tokens a priced Codex record contributes, under Codex's convention.

    ``input_tokens`` already includes ``cached_input_tokens``, so the cached
    figure is not added again, and ``reasoning_output_tokens`` is a subset of
    ``output_tokens``.
    """
    def count(key):
        value = usage.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return 0
        return int(value) if value > 0 else 0
    return (count("input_tokens") + count("output_tokens")
            + count("cache_write_input_tokens"))


def _timestamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()
    except (ValueError, OverflowError):
        return None


def month_value(sessions_dir: Optional[Path] = None,
                now: Optional[datetime] = None,
                table: Optional[value_meter.PriceTable] = None) -> tuple:
    """Return ``(value_usd, priced_tokens, unpriced_tokens)`` for this month.

    A missing ``~/.codex`` is the normal case on a Claude-only machine and
    returns zeros -- not an error, and not an unpriced tally, because there is
    genuinely nothing there to price.
    """
    root = Path(sessions_dir) if sessions_dir else DEFAULT_SESSIONS_DIR
    if not root.is_dir():
        return 0.0, 0, 0

    now = now or datetime.now().astimezone()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_key = month_start.strftime("%Y-%m")

    live: set[Path] = set()
    with _lock:
        for path in root.glob("**/rollout-*.jsonl"):
            try:
                stat = path.stat()
            except OSError:
                continue
            if datetime.fromtimestamp(stat.st_mtime).astimezone() < month_start:
                continue
            live.add(path)
            cached = _file_cache.get(path)
            stat_key = (stat.st_mtime, stat.st_size)
            identity = (stat.st_dev, stat.st_ino)
            if (cached and cached["stat"] == stat_key
                    and cached.get("identity") == identity):
                continue
            can_append = (
                cached is not None
                and cached.get("month") == month_key
                and cached.get("identity") == identity
                and stat.st_size > cached["stat"][1])
            if can_append:
                new, until, model = _parse_file(
                    path, month_start, cached["offset"], cached.get("model"),
                    table)
                cached["records"].extend(new)
                cached.update(stat=stat_key, offset=until, model=model)
            else:
                records, until, model = _parse_file(
                    path, month_start, 0, None, table)
                _file_cache[path] = {
                    "stat": stat_key, "identity": identity, "offset": until,
                    "month": month_key, "model": model, "records": records}

        for stale in set(_file_cache) - live:
            del _file_cache[stale]

        value_usd = 0.0
        priced = 0
        unpriced = 0
        for entry in _file_cache.values():
            for _day, usd, rec_priced, rec_unpriced in entry["records"]:
                value_usd += usd
                priced += rec_priced
                unpriced += rec_unpriced
    return value_usd, priced, unpriced


def reset_cache() -> None:
    """Drop the incremental cache. For tests and for a changed prices file."""
    with _lock:
        _file_cache.clear()
