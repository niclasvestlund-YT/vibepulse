"""Bounded, content-free quota history and weekly pace forecasts."""

from __future__ import annotations

import json
import math
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional


SAMPLE_INTERVAL_S = 15 * 60
RETENTION_S = 8 * 24 * 60 * 60
FORECAST_WINDOW_S = 24 * 60 * 60
MIN_FORECAST_SPAN_S = 90 * 60
MIN_FORECAST_DELTA = 1.0
RESET_QUANTUM_S = 5 * 60

_PROVIDERS = {"claude", "codex"}
_WINDOWS = {"session", "week", "model_week"}


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
        self._records = self._load()

    @property
    def records(self) -> tuple:
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
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return []
        if (not isinstance(payload, dict) or payload.get("v") != 1 or
                not isinstance(payload.get("samples"), list)):
            return []
        records = [dict(record) for record in payload["samples"]
                   if self._valid_record(record)]
        records.sort(key=lambda record: record["at"])
        cutoff = self._now() - RETENTION_S
        return [record for record in records if record["at"] >= cutoff]

    def _persist(self) -> None:
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
        samples = [record for record in self._records
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
                    for x, y in zip(xs, ys)) / denominator
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
        samples = sorted(
            (record for record in self._records
             if record["provider"] == provider and
             record["window"] == window and
             record["reset"] == cycle and
             record["at"] <= current_time),
            key=lambda record: record["at"])
        if len(samples) < 2:
            return None
        earlier = [record for record in samples if record["at"] <= since]
        baseline = earlier[-1] if earlier else samples[0]
        latest = samples[-1]
        if baseline is latest:
            return None
        delta = latest["pct"] - baseline["pct"]
        return None if delta < 0 else round(delta, 1)
