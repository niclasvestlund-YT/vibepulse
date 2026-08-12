"""Identity-safe, content-free persistence for current quota observations."""

from __future__ import annotations

import json
import math
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple


_PROVIDERS = {"claude", "codex"}
_SCOPES = {"general_session", "general_weekly", "model_weekly"}
_MAX_IDENTITY_LENGTH = 128
_MAX_LABEL_LENGTH = 128


@dataclass(frozen=True)
class CachedQuota:
    provider: str
    scope: str
    identity: str
    pct: float
    reset_at: int
    observed_at: int
    label: Optional[str] = None


def _finite_number(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _timestamp(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _identity(value: Any) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= _MAX_IDENTITY_LENGTH
            and all(" " <= character <= "~" for character in value))


def _label(value: Any) -> bool:
    if value is None:
        return True
    if not isinstance(value, str) or len(value) > _MAX_LABEL_LENGTH:
        return False
    try:
        value.encode("utf-8")
    except UnicodeError:
        return False
    return True


class QuotaCache:
    """Keep the latest value per provider, semantic scope, and identity."""

    def __init__(self, path: Path,
                 now: Callable[[], float] = time.time):
        self.path = Path(path)
        self._now = now
        self._records = self._load()

    @staticmethod
    def _valid(record: CachedQuota) -> bool:
        return (isinstance(record.provider, str) and
                record.provider in _PROVIDERS and
                isinstance(record.scope, str) and record.scope in _SCOPES and
                _identity(record.identity) and _finite_number(record.pct) and
                0 <= record.pct <= 100 and _timestamp(record.reset_at) and
                _timestamp(record.observed_at) and _label(record.label))

    @classmethod
    def _from_mapping(cls, value: Any) -> Optional[CachedQuota]:
        keys = {"provider", "scope", "identity", "pct", "reset_at",
                "observed_at", "label"}
        if not isinstance(value, dict) or set(value) != keys:
            return None
        try:
            record = CachedQuota(**value)
        except TypeError:
            return None
        return record if cls._valid(record) else None

    @staticmethod
    def _key(record: CachedQuota) -> Tuple[str, str, str]:
        return record.provider, record.scope, record.identity

    def _load(self) -> Dict[Tuple[str, str, str], CachedQuota]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}
        if (not isinstance(payload, dict) or set(payload) != {"v", "records"}
                or payload["v"] != 1 or not isinstance(payload["records"], list)):
            return {}
        records = {}
        for value in payload["records"]:
            record = self._from_mapping(value)
            if record is None:
                continue
            key = self._key(record)
            previous = records.get(key)
            if previous is None or record.observed_at > previous.observed_at:
                records[key] = record
        return records

    def _payload(self) -> Dict[str, Any]:
        records = sorted(self._records.values(), key=lambda record: (
            record.provider, record.scope, record.identity))
        return {
            "v": 1,
            "records": [{
                "provider": record.provider,
                "scope": record.scope,
                "identity": record.identity,
                "pct": float(record.pct),
                "reset_at": record.reset_at,
                "observed_at": record.observed_at,
                "label": record.label,
            } for record in records],
        }

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=self.path.parent,
                    prefix=".%s." % self.path.name, suffix=".tmp",
                    delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(self._payload(), stream, ensure_ascii=False,
                          separators=(",", ":"))
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

    def put(self, record: CachedQuota) -> bool:
        if not isinstance(record, CachedQuota) or not self._valid(record):
            return False
        key = self._key(record)
        old_records = self._records
        previous = old_records.get(key)
        if previous is not None and record.observed_at < previous.observed_at:
            return False
        updated = dict(old_records)
        updated[key] = record
        self._records = updated
        try:
            self._persist()
        except (OSError, UnicodeError, TypeError, ValueError):
            self._records = old_records
            return False
        return True

    def latest(self, provider: str, scope: str,
               now: Optional[float] = None) -> Optional[CachedQuota]:
        if provider not in _PROVIDERS or scope not in _SCOPES:
            return None
        current_time = self._now() if now is None else now
        if not _finite_number(current_time):
            return None
        candidates = [record for record in self._records.values()
                      if record.provider == provider and record.scope == scope
                      and record.reset_at > current_time]
        if not candidates:
            return None
        return max(candidates, key=lambda record: (
            record.observed_at, record.identity))
