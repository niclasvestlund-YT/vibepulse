"""Bounded, content-free quota history and weekly pace forecasts."""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

try:
    from .state_files import fsync_parent, quarantine_corrupt
except ImportError:  # run as a script / from the directory itself
    from state_files import fsync_parent, quarantine_corrupt

log = logging.getLogger("tokenserver.state")


class _PostReplaceError(Exception):
    """The new file is in place; only its directory entry's sync failed."""


SAMPLE_INTERVAL_S = 15 * 60
RETENTION_S = 8 * 24 * 60 * 60
FORECAST_WINDOW_S = 24 * 60 * 60
MIN_FORECAST_SPAN_S = 90 * 60
MIN_FORECAST_DELTA = 1.0
RESET_QUANTUM_S = 5 * 60

_PROVIDERS = {"claude", "codex"}
_WINDOWS = {"session", "week", "model_week"}
# Cycle lengths per window: if the cycle start (reset_at - length) lies
# AFTER the delta query's "since", the pool provably started at zero inside
# the period -- the baseline is then 0 by definition and no sample history
# is needed. Without that derivation "today" was under-reported every time
# the history had gaps (the 429 blackout and the unnamed Fable pool, both
# 2026-08-14).
_WINDOW_LENGTH_S = {
    "session": 5 * 3600.0,
    "week": 7 * 86400.0,
    "model_week": 7 * 86400.0,
}


@dataclass(frozen=True)
class Forecast:
    state: str
    pct_at_reset: Optional[int] = None
    pace_factor: Optional[float] = None
    exhausts_at: Optional[int] = None
    offset_minutes: Optional[int] = None


def _finite_number(value: Any) -> bool:
    return (isinstance(value, (int, float)) and
            not isinstance(value, bool) and math.isfinite(value))


def _reset_cycle(reset_at: float) -> int:
    return int(math.floor((reset_at + RESET_QUANTUM_S / 2) /
                          RESET_QUANTUM_S) * RESET_QUANTUM_S)


class UsageHistory:
    """Persist only coarse quota percentages needed by the VibePulse UI."""

    def __init__(self, path: Path,
                 now: Callable[[], float] = time.time):
        self.path = Path(path)
        self._now = now
        # Reentrant: record() enters record_many() and HTTP threads share
        # one instance; every state read/mutation plus its persist happens
        # under this lock so a batch stays atomic in memory and on disk.
        self._lock = threading.RLock()
        # See MaxTrackerStore._load_error: an existing file that cannot be
        # read is never overwritten by a store that started empty.
        self._load_error: str | None = None
        self._records = self._load()

    @property
    def records(self) -> tuple:
        with self._lock:
            return tuple(dict(record) for record in self._records)

    @staticmethod
    def _valid_record(record: Any) -> bool:
        if not isinstance(record, dict) or set(record) != {
                "at", "provider", "window", "pct", "reset"}:
            return False
        return (
            record["provider"] in _PROVIDERS and
            record["window"] in _WINDOWS and
            _finite_number(record["at"]) and
            _finite_number(record["pct"]) and
            0 <= record["pct"] <= 100 and
            _finite_number(record["reset"])
        )

    def _load(self) -> list:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except UnicodeError:
            quarantine_corrupt(self.path, "not UTF-8")
            return []
        except OSError as error:
            self._load_error = type(error).__name__
            log.warning("%s exists but could not be read (%s): starting "
                        "empty and refusing to save over it until the "
                        "service restarts with a readable file",
                        self.path.name, self._load_error)
            return []
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as error:
            quarantine_corrupt(self.path, f"invalid JSON at byte {error.pos}")
            return []
        if (not isinstance(payload, dict) or payload.get("v") != 1 or
                not isinstance(payload.get("samples"), list)):
            quarantine_corrupt(self.path, "not a {v: 1, samples: [...]} file")
            return []
        records = [dict(record) for record in payload["samples"]
                   if self._valid_record(record)]
        records.sort(key=lambda record: record["at"])
        cutoff = self._now() - RETENTION_S
        return [record for record in records if record["at"] >= cutoff]

    def _persist(self) -> None:
        if self._load_error is not None:
            raise OSError(
                f"{self.path.name} was unreadable at startup "
                f"({self._load_error}); refusing to overwrite it")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=self.path.parent,
                    prefix=f".{self.path.name}.", suffix=".tmp",
                    delete=False) as stream:
                temporary = Path(stream.name)
                json.dump({"v": 1, "samples": self._records}, stream,
                          ensure_ascii=False, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            temporary = None
            try:
                fsync_parent(self.path)  # OBS-21: the rename is not durable
            except OSError as error:
                raise _PostReplaceError() from error
        finally:
            if temporary is not None:
                try:
                    temporary.unlink()
                except OSError:
                    pass

    def record(self, provider: str, window: str, pct: float,
               reset_at: float, at: Optional[float] = None) -> bool:
        return bool(self.record_many(
            ((provider, window, pct, reset_at),), at=at))

    def record_many(self, samples, at: Optional[float] = None) -> int:
        with self._lock:
            timestamp = self._now() if at is None else at
            if not _finite_number(timestamp):
                return 0
            timestamp = int(round(timestamp))

            old_records = self._records
            cutoff = timestamp - RETENTION_S
            self._records = [record for record in self._records
                             if record["at"] >= cutoff]
            added = 0
            for provider, window, pct, reset_at in samples:
                if (provider not in _PROVIDERS or window not in _WINDOWS or
                        not _finite_number(pct) or not 0 <= pct <= 100 or
                        not _finite_number(reset_at)):
                    continue
                cycle = _reset_cycle(reset_at)
                previous = next(
                    (record for record in reversed(self._records)
                     if record["provider"] == provider and
                     record["window"] == window and
                     record["reset"] == cycle), None)
                if (previous is not None and
                        timestamp - previous["at"] < SAMPLE_INTERVAL_S):
                    continue
                self._records.append({
                    "at": timestamp,
                    "provider": provider,
                    "window": window,
                    "pct": float(pct),
                    "reset": cycle,
                })
                added += 1
            if not added:
                self._records = old_records
                return 0
            self._records.sort(key=lambda record: record["at"])
            try:
                self._persist()
            except _PostReplaceError as error:
                # The replace landed: disk and memory agree, only the
                # directory entry's durability is unproven. Rolling memory
                # back here would make the next successful save drop a
                # sample that is on disk (Codex review of #105); keep it
                # and say so.
                log.warning("%s was saved but its directory fsync failed "
                            "(%s): the file may not survive power loss "
                            "until the next save",
                            self.path.name, type(error.__cause__).__name__)
                return added
            except OSError:
                self._records = old_records
                return 0
            return added

    def forecast(self, provider: str, window: str, reset_at: float,
                 now: Optional[float] = None) -> Forecast:
        if (provider not in _PROVIDERS or window not in _WINDOWS or
                not _finite_number(reset_at)):
            return Forecast(state="unavailable")
        current_time = self._now() if now is None else now
        if not _finite_number(current_time):
            return Forecast(state="unavailable")

        cycle = _reset_cycle(reset_at)
        cutoff = current_time - FORECAST_WINDOW_S
        with self._lock:
            samples = [dict(record) for record in self._records
                       if record["provider"] == provider and
                       record["window"] == window and
                       record["reset"] == cycle and
                       cutoff <= record["at"] <= current_time]
        if not samples:
            return Forecast(state="unavailable")
        samples.sort(key=lambda record: record["at"])
        latest = samples[-1]
        if reset_at <= latest["at"]:
            return Forecast(state="unavailable")
        span = samples[-1]["at"] - samples[0]["at"]
        movement = max(record["pct"] for record in samples) - min(
            record["pct"] for record in samples)
        if (len(samples) < 3 or span < MIN_FORECAST_SPAN_S or
                movement < MIN_FORECAST_DELTA):
            return Forecast(state="collecting")

        xs = [record["at"] - samples[0]["at"] for record in samples]
        ys = [record["pct"] for record in samples]
        mean_x = sum(xs) / len(xs)
        mean_y = sum(ys) / len(ys)
        denominator = sum((x - mean_x) ** 2 for x in xs)
        if denominator <= 0:
            return Forecast(state="collecting")
        slope = sum((x - mean_x) * (y - mean_y)
                    for x, y in zip(xs, ys, strict=True)) / denominator
        if not math.isfinite(slope) or slope <= 0:
            return Forecast(state="unavailable")

        seconds_left = reset_at - latest["at"]
        projected_gain = slope * seconds_left
        projected_pct = latest["pct"] + projected_gain
        if projected_pct >= 100:
            exhausts_at = int(round(
                latest["at"] + (100 - latest["pct"]) / slope))
            return Forecast(
                state="exhausts",
                exhausts_at=exhausts_at,
                offset_minutes=int(round((exhausts_at - reset_at) / 60)),
            )

        pace_factor = ((100 - latest["pct"]) / projected_gain
                       if projected_gain > 0 else None)
        return Forecast(
            state="at_reset",
            pct_at_reset=max(0, min(100, int(round(projected_pct)))),
            pace_factor=(None if pace_factor is None
                         else round(pace_factor, 1)),
        )

    def delta_since(self, provider: str, window: str, since: float,
                    reset_at: float,
                    now: Optional[float] = None) -> Optional[float]:
        if (provider not in _PROVIDERS or window not in _WINDOWS or
                not _finite_number(since) or
                not _finite_number(reset_at)):
            return None
        current_time = self._now() if now is None else now
        if not _finite_number(current_time):
            return None

        cycle = _reset_cycle(reset_at)
        with self._lock:
            samples = sorted(
                (dict(record) for record in self._records
                 if record["provider"] == provider and
                 record["window"] == window and
                 record["reset"] == cycle and
                 record["at"] <= current_time),
                key=lambda record: record["at"])
        if not samples:
            return None
        latest = samples[-1]

        # If the cycle started after "since" the baseline is 0 by
        # definition -- the whole current percentage fell inside the period,
        # however sparse the recorded history happens to be.
        cycle_start = reset_at - _WINDOW_LENGTH_S[window]
        if cycle_start >= since:
            return round(max(0.0, float(latest["pct"])), 1)

        if len(samples) < 2:
            return None
        earlier = [record for record in samples if record["at"] <= since]
        baseline = earlier[-1] if earlier else samples[0]
        if baseline is latest:
            return None
        delta = latest["pct"] - baseline["pct"]
        return None if delta < 0 else round(delta, 1)
