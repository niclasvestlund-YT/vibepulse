"""Privacy-safe agent status classification and storage.

The classifiers may inspect local log fields to choose a coarse activity, but
only the compact :class:`Event` metadata is retained.  Prompt text, message
text, command contents, and file contents never enter the store or snapshots.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import math
import os
import re
import stat as stat_module
import sys
import threading
import time
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional


LEASE_S = 120.0
# Attention is a signal, not a todo list: even a genuine unanswered chat ages
# out after two hours rather than remaining indefinitely actionable.
WAITING_LEASE_S = 2 * 60 * 60
POLL_S = 0.5
PUBLIC_JOB_LIMIT = 4
TRACKED_JOB_LIMIT = 16
STATES = {"idle", "working", "waiting", "done", "error", "unknown"}
STATE_PRIORITY = {
    "waiting": 5,
    "error": 4,
    "working": 3,
    "done": 2,
    "idle": 1,
    "unknown": 0,
}
ACTIVITIES = {
    "thinking",
    "reading",
    "editing",
    "searching",
    "running",
    "testing",
    "building",
    "waiting_input",
    "waiting_approval",
}

MODEL_LABELS = {
    "claude-fable-5": "FABLE 5",
    "claude-opus-5": "OPUS 5",
    "claude-sonnet-5": "SONNET 5",
    "gpt-5.6-sol": "GPT-5.6 SOL",
}


def _bounded_display(value: Any, max_bytes: int) -> Optional[str]:
    if not isinstance(value, str) or not value:
        return None
    clean = "".join(
        char for char in value
        if not unicodedata.category(char).startswith("C")
    ).strip()
    prefix = []
    encoded_bytes = 0
    for char in clean:
        char_bytes = len(char.encode("utf-8"))
        if encoded_bytes + char_bytes > max_bytes:
            break
        prefix.append(char)
        encoded_bytes += char_bytes
    return "".join(prefix) or None


def normalize_model(value: Any) -> Optional[str]:
    bounded = _bounded_display(value, 24)
    if bounded is None:
        return None
    return MODEL_LABELS.get(bounded.lower(), bounded)


def normalize_effort(value: Any) -> Optional[str]:
    bounded = _bounded_display(value, 12)
    return None if bounded is None else bounded.upper()


@dataclass(frozen=True)
class Event:
    state: str
    activity: str | None
    task_id: str
    source_id: str
    project: str | None
    model: str | None = None
    effort: str | None = None


@dataclass(frozen=True)
class _Observation:
    event: Event
    event_id: str
    observed_at: float
    order_at: float
    sequence: int


def stable_event_id(provider: str, event: Event) -> str:
    raw = f"{provider}|{event.task_id}|{event.state}|{event.source_id}"
    return hashlib.sha256(
        raw.encode("utf-8", errors="surrogatepass")).hexdigest()[:32]


def sanitize_project(value: Any) -> Optional[str]:
    """Return a control-free, short project basename suitable for the UI."""
    if not isinstance(value, str) or not value:
        return None
    name = Path(value).name
    clean = "".join(
        char for char in name
        if not unicodedata.category(char).startswith("C")
    ).strip()
    prefix = []
    encoded_bytes = 0
    for char in clean:
        char_bytes = len(char.encode("utf-8"))
        if encoded_bytes + char_bytes > 16:
            break
        prefix.append(char)
        encoded_bytes += char_bytes
    return "".join(prefix) or None


def _bounded_task_id(value: str) -> str:
    raw = value.encode("utf-8", errors="surrogatepass")
    has_controls = any(unicodedata.category(char).startswith("C")
                       for char in value)
    if len(raw) <= 64 and not has_controls:
        return value
    return hashlib.sha256(raw).hexdigest()


def _claude_identity(event: Dict[str, Any]) -> Optional[tuple]:
    session_id = event.get("sessionId")
    source_id = event.get("uuid")
    if not isinstance(session_id, str) or not session_id:
        return None
    if not isinstance(source_id, str) or not source_id:
        return None
    return (
        hashlib.sha256(session_id.encode(
            "utf-8", errors="surrogatepass")).hexdigest(),
        source_id,
        sanitize_project(event.get("cwd")),
    )


def _claude_event(event: Dict[str, Any], state: str,
                  activity: Optional[str]) -> Optional[Event]:
    identity = _claude_identity(event)
    if identity is None:
        return None
    task_id, source_id, project = identity
    message = event.get("message")
    if isinstance(message, dict):
        model = normalize_model(message.get("model"))
        effort = normalize_effort(message.get("effort"))
    else:
        model = None
        effort = None
    return Event(state, activity, task_id, source_id, project, model, effort)


def _has_explicit_error(event: Dict[str, Any]) -> bool:
    return (event.get("is_error") is True or
            ("error" in event and event.get("error") is not None))


_TEST_COMMAND = re.compile(
    r"(?:^|[\s;&|])(?:\./test/run\.sh|pytest|python3?\s+-m\s+unittest|"
    r"npm\s+(?:run\s+)?test|cargo\s+test|go\s+test|ctest)(?:\s|$)",
    re.IGNORECASE,
)
_BUILD_COMMAND = re.compile(
    r"(?:^|[\s;&|])(?:cmake\s+--build|ninja|make|cargo\s+build|"
    r"npm\s+run\s+build|idf\.py\s+build)(?:\s|$)",
    re.IGNORECASE,
)


def _claude_tool_activity(tool: Dict[str, Any]) -> Optional[tuple]:
    name = tool.get("name")
    if not isinstance(name, str) or not name:
        return None
    normalized = name.lower()
    if normalized == "askuserquestion":
        return "waiting", "waiting_input"
    if "permission" in normalized:
        return "waiting", "waiting_approval"
    if normalized in {"edit", "write", "apply_patch"}:
        return "working", "editing"
    if normalized == "read":
        return "working", "reading"
    if normalized in {"glob", "grep", "websearch", "web_search"}:
        return "working", "searching"
    if normalized in {"bash", "shell", "exec", "exec_command"}:
        tool_input = tool.get("input")
        command = tool_input.get("command") if isinstance(tool_input, dict) else None
        if not isinstance(command, str):
            return "working", "running"
        if _TEST_COMMAND.search(command):
            return "working", "testing"
        if _BUILD_COMMAND.search(command):
            return "working", "building"
        return "working", "running"
    return None


def classify_claude(event: Any) -> Optional[Event]:
    """Classify a recognized Claude JSONL entry without retaining content."""
    if not isinstance(event, dict):
        return None
    entry_type = event.get("type")
    if entry_type == "user":
        return _claude_event(event, "working", "thinking")
    if entry_type == "result":
        subtype = event.get("subtype")
        if _has_explicit_error(event):
            return _claude_event(event, "error", None)
        if subtype == "success":
            return _claude_event(event, "done", None)
        if subtype in {"error", "failure", "failed"}:
            return _claude_event(event, "error", None)
        return None
    if entry_type == "system":
        status_values = (event.get("subtype"), event.get("status"))
        if any(isinstance(value, str) and "permission" in value.lower()
               for value in status_values):
            return _claude_event(event, "waiting", "waiting_approval")
        return None
    if entry_type != "assistant":
        return None

    message = event.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, list):
        classifications = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "tool_use":
                classification = _claude_tool_activity(item)
                if classification is not None:
                    classifications.append(classification)
        for preferred in (
                ("waiting", "waiting_input"),
                ("waiting", "waiting_approval")):
            if preferred in classifications:
                return _claude_event(event, *preferred)
        if classifications:
            return _claude_event(event, *classifications[-1])

    if message.get("stop_reason") == "end_turn":
        return _claude_event(event, "waiting", None)
    if isinstance(content, list) and any(
            isinstance(item, dict) and item.get("type") in {"thinking", "text"}
            for item in content):
        return _claude_event(event, "working", "thinking")
    return None


def _codex_response_activity(payload: Dict[str, Any]) -> Optional[str]:
    kind = payload.get("type")
    if kind in {"reasoning", "function_call_output",
                "custom_tool_call_output"}:
        return "thinking"
    if kind not in {"function_call", "custom_tool_call"}:
        return None
    name = payload.get("name")
    if not isinstance(name, str) or not name:
        return "running"
    normalized = name.lower()
    if normalized in {"apply_patch", "edit", "write"}:
        return "editing"
    if normalized in {"read", "view_image"}:
        return "reading"
    if ("search" in normalized or normalized in
            {"web", "web__run", "find"}):
        return "searching"
    return "running"


def classify_codex(event: Any) -> Optional[Event]:
    """Classify Codex lifecycle and privacy-safe live activity events."""
    if not isinstance(event, dict):
        return None
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return None
    if event.get("type") == "turn_context":
        turn_id = payload.get("turn_id")
        if not isinstance(turn_id, str) or not turn_id:
            return None
        return Event(
            "working", "thinking", _bounded_task_id(turn_id), turn_id,
            sanitize_project(payload.get("cwd")),
            normalize_model(payload.get("model")),
            normalize_effort(payload.get("effort")))
    if event.get("type") == "response_item":
        source_id = payload.get("id")
        if not isinstance(source_id, str) or not source_id:
            return None
        activity = _codex_response_activity(payload)
        if activity is None:
            return None
        metadata = payload.get("internal_chat_message_metadata_passthrough")
        turn_id = metadata.get("turn_id") if isinstance(metadata, dict) else None
        if not isinstance(turn_id, str) or not turn_id:
            turn_id = source_id
        return Event("working", activity, _bounded_task_id(turn_id),
                     source_id, None)
    if event.get("type") != "event_msg":
        return None
    kind = payload.get("type")
    turn_id = payload.get("turn_id")
    if not isinstance(turn_id, str) or not turn_id:
        return None
    if kind == "task_started":
        return Event("working", "thinking", _bounded_task_id(turn_id),
                     turn_id, None)
    if kind == "task_complete":
        state = "error" if _has_explicit_error(payload) else "done"
        return Event(state, None, _bounded_task_id(turn_id), turn_id, None)
    return None


class AgentStatusStore:
    """Thread-safe, privacy-limited status snapshots for both providers."""

    def __init__(self, now: Callable[[], float] = time.monotonic):
        self._now = now
        self._lock = threading.Lock()
        self._seq = 0
        self._agents = {provider: {} for provider in ("claude", "codex")}

    def apply(self, provider: str, event: Event,
              observed_at: Optional[float] = None,
              order_at: Optional[float] = None,
              refresh_unchanged: bool = True,
              event_id_override: Optional[str] = None) -> bool:
        if provider not in self._agents:
            raise ValueError(f"unsupported provider: {provider}")
        if event.state not in STATES:
            raise ValueError(f"unsupported state: {event.state}")
        if event.activity is not None and event.activity not in ACTIVITIES:
            raise ValueError(f"unsupported activity: {event.activity}")

        seen_at = self._now() if observed_at is None else observed_at
        if not math.isfinite(seen_at):
            seen_at = self._now()
        ordered_at = seen_at if order_at is None else order_at
        if not math.isfinite(ordered_at):
            ordered_at = seen_at
        task_id = _bounded_task_id(event.task_id)
        replacement = {
            "task_id": task_id,
            "event_id": (event_id_override or
                         stable_event_id(provider, event)),
            "state": event.state,
            "project": sanitize_project(event.project),
            "activity": event.activity,
            "model": normalize_model(event.model),
            "effort": normalize_effort(event.effort),
            "observed_at": seen_at,
            "order_at": ordered_at,
        }
        public_fields = (
            "task_id", "event_id", "state", "project", "activity",
            "model", "effort")
        with self._lock:
            records = self._agents[provider]
            current = records.get(task_id)
            if (current is not None and current["order_at"] is not None and
                    ordered_at < current["order_at"]):
                return False
            if current is not None:
                if replacement["model"] is None:
                    replacement["model"] = current["model"]
                if replacement["effort"] is None:
                    replacement["effort"] = current["effort"]
            changed = (current is None or any(
                current[key] != replacement[key] for key in public_fields))
            if changed:
                records[task_id] = replacement
                if len(records) > TRACKED_JOB_LIMIT:
                    evicted = min(records.values(), key=lambda record: (
                        STATE_PRIORITY[record["state"]],
                        record["order_at"],
                        record["task_id"],
                    ))
                    del records[evicted["task_id"]]
                self._seq += 1
            elif refresh_unchanged:
                current["observed_at"] = max(current["observed_at"], seen_at)
                current["order_at"] = max(current["order_at"], ordered_at)
            return changed

    @staticmethod
    def _effective(record: Dict[str, Any], now: float) -> Dict[str, Any]:
        age_s = max(0.0, now - record["observed_at"])
        state = record["state"]
        activity = record["activity"]
        if ((state == "working" and age_s > LEASE_S) or
                (state in ("waiting", "error") and
                 age_s > WAITING_LEASE_S)):
            state = "unknown"
            activity = None
        return {
            "task_id": record["task_id"],
            "event_id": record["event_id"],
            "state": state,
            "project": record["project"],
            "activity": activity,
            "model": record["model"],
            "effort": record["effort"],
            "updated_ms": min(0xFFFFFFFF, int(age_s * 1000)),
        }

    def snapshot(self) -> Dict[str, Any]:
        now = self._now()
        agents = {}
        with self._lock:
            for provider, records in self._agents.items():
                public = [self._effective(record, now)
                          for record in records.values()]
                public = [job for job in public
                          if job["state"] not in ("idle", "unknown")]
                active_count = sum(
                    job["state"] in ("working", "waiting", "error")
                    for job in public
                )
                public.sort(key=lambda job: (
                    -STATE_PRIORITY[job["state"]],
                    job["updated_ms"],
                    job["task_id"],
                ))
                agents[provider] = {
                    "active_count": active_count,
                    "jobs": public[:PUBLIC_JOB_LIMIT],
                }
            seq = self._seq
        return {"v": 2, "seq": seq, "agents": agents}


class JsonlTailer:
    """Incrementally decode complete JSON objects from multiple JSONL files."""

    _MISSING_POLLS = 3
    _READ_CHUNK_BYTES = 64 * 1024
    _READ_BYTES_PER_POLL = 1024 * 1024
    _MAX_LINE_BYTES = _READ_BYTES_PER_POLL
    _MAX_RECORDS_PER_POLL = 256
    _VERIFY_INTERVAL_S = 5.0
    _VERIFY_BYTES_PER_POLL = 1024 * 1024
    _SAMPLE_BYTES = 4096
    _MAX_ACTIVE_IDENTITIES = 24
    _MAX_TRACKED_IDENTITIES = 48

    def __init__(self, now: Callable[[], float] = time.monotonic):
        self._now = now
        self._files = {}
        self._identities = {}
        self._use_counter = 0
        self._discovery_needed = False

    def _fresh_state(self, identity: tuple,
                     backfilling: bool = False) -> Dict[str, Any]:
        return {
            "identity": identity,
            "offset": 0,
            "partial": b"",
            "discarding_line": False,
            "prefix_hasher": hashlib.sha256(),
            "sample_digest": None,
            "stat_signature": None,
            "missing_polls": 0,
            "last_used": self._use_counter,
            "next_verify_at": self._now() + self._VERIFY_INTERVAL_S,
            "verify_offset": None,
            "verify_length": None,
            "verify_expected": None,
            "verify_hasher": None,
            "backfilling": backfilling,
            "last_read_backfill": False,
            "last_read_caught_up": False,
        }

    def _touch(self, state: Dict[str, Any]) -> None:
        self._use_counter += 1
        state["last_used"] = self._use_counter

    def _prune_state(self, state: Dict[str, Any]) -> None:
        identity = state["identity"]
        if self._identities.get(identity) is state:
            del self._identities[identity]
        for alias, candidate in list(self._files.items()):
            if candidate is state:
                del self._files[alias]

    def _enforce_identity_limit(
            self, protected: Optional[Dict[str, Any]] = None) -> None:
        while len(self._identities) > self._MAX_TRACKED_IDENTITIES:
            aliased = {id(state) for state in self._files.values()}
            candidates = [
                state for state in self._identities.values()
                if state is not protected and id(state) not in aliased
            ]
            if not candidates:
                candidates = [
                    state for state in self._identities.values()
                    if state is not protected
                ]
            if not candidates:
                return
            self._prune_state(min(
                candidates, key=lambda state: state["last_used"]))

    def _note_missing_path(self, path: Path) -> None:
        state = self._files.get(path)
        if state is None:
            return
        state["last_read_backfill"] = state["backfilling"]
        state["last_read_caught_up"] = False
        self._discovery_needed = True
        state["missing_polls"] += 1
        if state["missing_polls"] >= self._MISSING_POLLS:
            self._prune_state(state)

    def take_discovery_request(self) -> bool:
        requested = self._discovery_needed
        self._discovery_needed = False
        return requested

    def retain_paths(self, paths: Any, active_paths: Any = None) -> None:
        if isinstance(paths, dict):
            path_identities = {
                Path(path): identity for path, identity in paths.items()
            }
        else:
            path_identities = {}
            for raw_path in paths:
                path = Path(raw_path)
                try:
                    stat = path.stat()
                except OSError:
                    continue
                path_identities[path] = (stat.st_dev, stat.st_ino)

        if active_paths is None:
            active = set(path_identities)
        else:
            active = {Path(path) for path in active_paths}
        existing_identities = set(path_identities.values())
        active_identities = {
            path_identities[path] for path in active
            if path in path_identities
        }
        recovering_identities = {
            state["identity"] for alias, state in self._files.items()
            if alias in active and alias not in path_identities
        }

        for alias, state in list(self._files.items()):
            actual_identity = path_identities.get(alias)
            if ((actual_identity is not None and
                 actual_identity != state["identity"]) or
                    (actual_identity is None and
                     state["identity"] in existing_identities) or
                    alias not in active):
                del self._files[alias]

        for identity, state in list(self._identities.items()):
            if identity in existing_identities:
                state["missing_polls"] = 0
                if identity not in active_identities:
                    state["partial"] = b""
                    state["discarding_line"] = False
            else:
                if identity not in recovering_identities:
                    state["missing_polls"] += 1
                    if state["missing_polls"] >= self._MISSING_POLLS:
                        self._prune_state(state)

        self._enforce_identity_limit()

    def _state_for_identity(self, path: Path,
                            identity: tuple) -> Dict[str, Any]:
        previous = self._files.get(path)
        replaced = (previous is not None and
                    previous["identity"] != identity)
        if previous is not None and previous["identity"] != identity:
            del self._files[path]

        state = self._files.get(path)
        if state is None:
            state = self._identities.get(identity)
            if state is None:
                state = self._fresh_state(
                    identity, backfilling=replaced)
                self._identities[identity] = state
            self._files[path] = state
        state["missing_polls"] = 0
        self._touch(state)
        self._enforce_identity_limit(protected=state)

        for alias, candidate in list(self._files.items()):
            if alias == path or candidate is not state:
                continue
            try:
                alias_stat = alias.stat()
                alias_identity = (alias_stat.st_dev, alias_stat.st_ino)
            except OSError:
                alias_identity = None
            if alias_identity != identity:
                del self._files[alias]
        return state

    def _reset_state(self, state: Dict[str, Any], identity: tuple) -> None:
        old_identity = state["identity"]
        state.clear()
        state.update(self._fresh_state(identity, backfilling=True))
        if self._identities.get(old_identity) is state:
            del self._identities[old_identity]
        self._identities[identity] = state

    @staticmethod
    def _finish_read(state: Dict[str, Any], caught_up: bool) -> None:
        backfill = state["backfilling"]
        state["last_read_backfill"] = backfill
        state["last_read_caught_up"] = caught_up
        if backfill and caught_up:
            state["backfilling"] = False

    def read_status(self, path: Any) -> tuple:
        state = self._files.get(Path(path))
        if state is None:
            return False, False
        return (state["last_read_backfill"],
                state["last_read_caught_up"])

    def read_identity(self, path: Any) -> Optional[tuple]:
        state = self._files.get(Path(path))
        return None if state is None else state["identity"]

    def continue_backfill(self, path: Any) -> None:
        state = self._files.get(Path(path))
        if state is not None:
            state["backfilling"] = True
            state["last_read_backfill"] = True

    @staticmethod
    def _stat_signature(stat: os.stat_result) -> tuple:
        return (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)

    @staticmethod
    def _digest_prefix(stream: Any, length: int) -> Optional[Any]:
        digest = hashlib.sha256()
        remaining = length
        stream.seek(0)
        while remaining:
            chunk = stream.read(min(remaining, 64 * 1024))
            if not chunk:
                return None
            digest.update(chunk)
            remaining -= len(chunk)
        return digest

    @classmethod
    def _sample_prefix(cls, stream: Any, length: int) -> Optional[bytes]:
        digest = hashlib.sha256()
        digest.update(str(length).encode("ascii"))
        ranges = [(0, min(length, cls._SAMPLE_BYTES))]
        if length > cls._SAMPLE_BYTES:
            start = max(cls._SAMPLE_BYTES, length - cls._SAMPLE_BYTES)
            ranges.append((start, length - start))
        for start, size in ranges:
            stream.seek(start)
            chunk = stream.read(size)
            if len(chunk) != size:
                return None
            digest.update(start.to_bytes(8, "big"))
            digest.update(chunk)
        return digest.digest()

    @staticmethod
    def _clear_verification(state: Dict[str, Any]) -> None:
        state["verify_offset"] = None
        state["verify_length"] = None
        state["verify_expected"] = None
        state["verify_hasher"] = None

    def _start_verification(self, state: Dict[str, Any]) -> None:
        state["verify_offset"] = 0
        state["verify_length"] = state["offset"]
        state["verify_expected"] = state["prefix_hasher"].digest()
        state["verify_hasher"] = hashlib.sha256()

    def _advance_verification(self, stream: Any,
                              state: Dict[str, Any]) -> Optional[bool]:
        """Advance a bounded full-prefix check; return False on mismatch."""
        if state["verify_hasher"] is None:
            self._start_verification(state)
        length = state["verify_length"]
        offset = state["verify_offset"]
        remaining = length - offset
        budget = min(remaining, self._VERIFY_BYTES_PER_POLL)

        if offset == 0 and length <= self._VERIFY_BYTES_PER_POLL:
            digest = self._digest_prefix(stream, length)
            if digest is None:
                return False
            state["verify_hasher"] = digest
            state["verify_offset"] = length
        else:
            stream.seek(offset)
            while budget:
                chunk = stream.read(min(budget, self._READ_CHUNK_BYTES))
                if not chunk:
                    return False
                state["verify_hasher"].update(chunk)
                state["verify_offset"] += len(chunk)
                budget -= len(chunk)

        if state["verify_offset"] < length:
            return None
        matches = (state["verify_hasher"].digest() ==
                   state["verify_expected"])
        self._clear_verification(state)
        if matches:
            state["next_verify_at"] = self._now() + self._VERIFY_INTERVAL_S
        return matches

    def _consume_chunk(self, state: Dict[str, Any], chunk: bytes,
                       records: list) -> int:
        position = 0
        while position < len(chunk):
            newline = chunk.find(b"\n", position)
            if newline < 0:
                fragment = chunk[position:]
                if not state["discarding_line"]:
                    if len(state["partial"]) + len(fragment) <= self._MAX_LINE_BYTES:
                        state["partial"] += fragment
                    else:
                        state["partial"] = b""
                        state["discarding_line"] = True
                return len(chunk)

            fragment = chunk[position:newline]
            consumed = newline + 1
            if state["discarding_line"]:
                state["discarding_line"] = False
                state["partial"] = b""
                position = consumed
                continue
            if len(state["partial"]) + len(fragment) > self._MAX_LINE_BYTES:
                state["partial"] = b""
                position = consumed
                continue

            raw_line = state["partial"] + fragment
            state["partial"] = b""
            if raw_line.strip():
                try:
                    record = json.loads(raw_line.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeError):
                    record = None
                if record is not None:
                    records.append(record)
                    if len(records) >= self._MAX_RECORDS_PER_POLL:
                        return consumed
            position = consumed
        return len(chunk)

    def read(self, path: Any, backfill: bool = False) -> list:
        path = Path(path)
        try:
            stat = path.stat()
        except FileNotFoundError:
            self._note_missing_path(path)
            return []
        except OSError:
            state = self._files.get(path)
            if state is not None:
                state["last_read_backfill"] = state["backfilling"]
                state["last_read_caught_up"] = False
            return []

        identity = (stat.st_dev, stat.st_ino)
        state = self._state_for_identity(path, identity)
        if backfill:
            state["backfilling"] = True
        state["last_read_backfill"] = state["backfilling"]
        state["last_read_caught_up"] = False
        now = self._now()
        if stat.st_size < state["offset"]:
            self._reset_state(state, identity)
        current_signature = self._stat_signature(stat)
        verification_due = (
            state["verify_hasher"] is not None or
            now >= state["next_verify_at"] or
            (stat.st_size == state["offset"] and
             current_signature != state["stat_signature"])
        )
        if (stat.st_size == state["offset"] and
                current_signature == state["stat_signature"] and
                not verification_due):
            self._finish_read(state, caught_up=True)
            return []

        try:
            with path.open("rb") as stream:
                opened = os.fstat(stream.fileno())
                opened_identity = (opened.st_dev, opened.st_ino)
                opened_signature = self._stat_signature(opened)
                if opened_identity != state["identity"]:
                    state = self._state_for_identity(path, opened_identity)
                if opened.st_size < state["offset"]:
                    self._reset_state(state, opened_identity)

                if state["offset"] and opened.st_size > state["offset"]:
                    sample = self._sample_prefix(stream, state["offset"])
                    if sample != state["sample_digest"]:
                        self._reset_state(state, opened_identity)

                verification_due = (
                    state["verify_hasher"] is not None or
                    self._now() >= state["next_verify_at"] or
                    (opened.st_size == state["offset"] and
                     opened_signature != state["stat_signature"])
                )
                if verification_due:
                    verification = self._advance_verification(stream, state)
                else:
                    verification = True
                if verification is False:
                    self._reset_state(state, opened_identity)

                records = []
                offset = state["offset"]
                stream.seek(offset)
                remaining = self._READ_BYTES_PER_POLL
                while remaining and len(records) < self._MAX_RECORDS_PER_POLL:
                    chunk = stream.read(min(remaining, self._READ_CHUNK_BYTES))
                    if not chunk:
                        break
                    consumed = self._consume_chunk(state, chunk, records)
                    accepted = chunk[:consumed]
                    state["prefix_hasher"].update(accepted)
                    offset += consumed
                    remaining -= consumed
                    if consumed < len(chunk):
                        stream.seek(offset)
                        break
                state["offset"] = offset
                state["sample_digest"] = self._sample_prefix(stream, offset)
                final_stat = os.fstat(stream.fileno())
        except FileNotFoundError:
            self._note_missing_path(path)
            return []
        except OSError:
            state["last_read_backfill"] = state["backfilling"]
            state["last_read_caught_up"] = False
            return []

        final_signature = self._stat_signature(final_stat)
        state["stat_signature"] = (
            final_signature if final_signature == opened_signature else None)
        self._files[path] = state
        self._finish_read(
            state, caught_up=state["offset"] >= final_stat.st_size)
        return records


class AgentStatusService:
    """Poll recent Claude/Codex logs outside HTTP request threads."""

    _RECENT_FILE_LIMIT = 12
    _DISCOVERY_INTERVAL_S = 5.0
    _DIAGNOSTIC_INTERVAL_S = 30.0
    _SEEN_PATH_LIMIT = 96

    def __init__(self, projects_dir: Any, codex_sessions: Any,
                 now: Callable[[], float] = time.monotonic, *,
                 _wall_time: Callable[[], float] = time.time,
                 _diagnostic: Optional[Callable[[str], None]] = None):
        self.projects_dir = Path(projects_dir)
        self.codex_sessions = Path(codex_sessions)
        self._now = now
        self._wall_time = _wall_time
        self._store = AgentStatusStore(now=now)
        self._tailer = JsonlTailer(now=now)
        self._poll_lock = threading.Lock()
        self._thread_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._stopping = False
        self._active_paths = {"claude": [], "codex": []}
        self._next_discovery_at = float("-inf")
        self._diagnostic = _diagnostic or self._default_diagnostic
        self._last_diagnostic = {}
        self._seen_paths = OrderedDict()
        self._seen_overflow = False
        self._backfills = {}
        self._stream_tasks = {}
        self._observation_sequence = 0

    @staticmethod
    def _default_diagnostic(message: str) -> None:
        print(message, file=sys.stderr)

    def _report_error(self, context: str, error: Exception) -> None:
        error_name = type(error).__name__
        key = (context, error_name)
        now = self._now()
        last = self._last_diagnostic.get(key)
        if last is not None and now - last < self._DIAGNOSTIC_INTERVAL_S:
            return
        self._last_diagnostic[key] = now
        try:
            self._diagnostic(f"agent-status {context}: {error_name}")
        except Exception:
            pass

    @staticmethod
    def _event_wall_time(record: Any) -> Optional[float]:
        if not isinstance(record, dict):
            return None
        candidates = [record.get("timestamp")]
        payload = record.get("payload")
        if isinstance(payload, dict):
            if payload.get("type") == "task_complete":
                candidates.extend((payload.get("completed_at"),
                                   payload.get("started_at")))
            else:
                candidates.extend((payload.get("started_at"),
                                   payload.get("completed_at")))
        for value in candidates:
            if not isinstance(value, str) or not value:
                continue
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                timestamp = parsed.timestamp()
            except (OverflowError, ValueError):
                continue
            if math.isfinite(timestamp):
                return timestamp
        return None

    def _resolved_wall_time(self, record: Any,
                            file_wall_time: Optional[float],
                            wall_now: float) -> float:
        event_wall_time = self._event_wall_time(record)
        if event_wall_time is None or event_wall_time > wall_now:
            event_wall_time = file_wall_time
        if (event_wall_time is None or not math.isfinite(event_wall_time) or
                event_wall_time > wall_now):
            return wall_now
        return event_wall_time

    def _observed_at(self, record: Any, file_wall_time: Optional[float],
                     monotonic_now: float, wall_now: float) -> float:
        event_wall_time = self._resolved_wall_time(
            record, file_wall_time, wall_now)
        age = max(0.0, wall_now - event_wall_time)
        return monotonic_now - age

    def _discover_paths(self, root: Path, pattern: str) -> tuple:
        if not root.is_dir():
            return {}, []
        candidates = []
        identities = {}
        try:
            paths = root.glob(pattern)
            for path in paths:
                try:
                    stat = path.stat()
                except OSError:
                    continue
                if stat_module.S_ISREG(stat.st_mode):
                    identities[path] = (stat.st_dev, stat.st_ino)
                    candidates.append((stat.st_mtime_ns, str(path), path))
        except OSError:
            return {}, []
        newest = heapq.nlargest(self._RECENT_FILE_LIMIT, candidates)
        recent_paths = [item[2] for item in sorted(newest)]
        return identities, recent_paths

    def _recent_paths(self, root: Path, pattern: str) -> list:
        return self._discover_paths(root, pattern)[1]

    def _sources(self) -> tuple:
        return (
            ("claude", self.projects_dir, "**/*.jsonl", classify_claude),
            ("codex", self.codex_sessions, "**/rollout-*.jsonl",
             classify_codex),
        )

    def _path_was_seen(self, path: Path) -> bool:
        return self._seen_overflow or path in self._seen_paths

    def _remember_path(self, path: Path) -> None:
        self._seen_paths[path] = True
        self._seen_paths.move_to_end(path)
        if len(self._seen_paths) > self._SEEN_PATH_LIMIT:
            self._seen_paths.popitem(last=False)
            self._seen_overflow = True

    def _observation(self, provider: str, event: Event, record: Any,
                     file_wall_time: Optional[float],
                     monotonic_now: float, wall_now: float) -> _Observation:
        order_at = self._resolved_wall_time(
            record, file_wall_time, wall_now)
        observed_at = monotonic_now - max(0.0, wall_now - order_at)
        self._observation_sequence += 1
        compact_event = Event(
            event.state, event.activity, _bounded_task_id(event.task_id), "",
            sanitize_project(event.project), normalize_model(event.model),
            normalize_effort(event.effort))
        return _Observation(
            compact_event, stable_event_id(provider, event), observed_at,
            order_at, self._observation_sequence)

    @staticmethod
    def _fallback_stream_task(key: tuple) -> str:
        provider, path, identity = key
        raw = json.dumps(
            [provider, str(path), list(identity)], ensure_ascii=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(raw.encode()).hexdigest()

    def _bind_stream_task(self, provider: str, key: tuple,
                          record: Any, event: Event) -> Event:
        """Keep Codex response items attached to their current turn.

        Some live tool records omit turn metadata even though the surrounding
        lifecycle records identify it. The mapping stores only a bounded task
        identifier per file identity; tool arguments and output never enter
        it.
        """
        if provider != "codex" or not isinstance(record, dict):
            return event
        payload = record.get("payload")
        if not isinstance(payload, dict):
            return event

        if record.get("type") != "response_item":
            self._stream_tasks[key] = event.task_id
            return event

        metadata = payload.get("internal_chat_message_metadata_passthrough")
        turn_id = metadata.get("turn_id") if isinstance(metadata, dict) else None
        if isinstance(turn_id, str) and turn_id:
            task_id = _bounded_task_id(turn_id)
            self._stream_tasks[key] = task_id
        else:
            task_id = self._stream_tasks.get(key)
            if task_id is None:
                task_id = self._fallback_stream_task(key)
                self._stream_tasks[key] = task_id
        return Event(
            event.state, event.activity, task_id, event.source_id,
            event.project, event.model, event.effort,
        )

    def _apply_observation(self, provider: str, observation: _Observation,
                           refresh_unchanged: bool = True) -> bool:
        return self._store.apply(
            provider, observation.event,
            observed_at=observation.observed_at,
            order_at=observation.order_at,
            refresh_unchanged=refresh_unchanged,
            event_id_override=observation.event_id)

    def _refresh_discovery(self, sources: tuple) -> None:
        retained = {}
        active = []
        previous_active = self._active_paths
        next_active = {"claude": [], "codex": []}
        for provider, root, pattern, _ in sources:
            try:
                identities, recent_paths = self._discover_paths(root, pattern)
            except Exception as error:
                self._report_error("discovery", error)
                continue
            retained.update(identities)
            active.extend(recent_paths)
            next_active[provider] = recent_paths
        missing_previous = [
            path for paths in previous_active.values() for path in paths
            if path not in retained
        ]
        self._tailer.retain_paths(
            retained, active_paths=active + missing_previous)
        for provider, paths in previous_active.items():
            grace = [
                path for path in paths
                if path not in retained and path in self._tailer._files
            ]
            room = max(0, self._RECENT_FILE_LIMIT -
                       len(next_active[provider]))
            next_active[provider].extend(grace[:room])
        self._active_paths = next_active
        tracked_paths = set(self._tailer._files)
        tracked_paths.update(
            path for paths in next_active.values() for path in paths)
        for key in list(self._backfills):
            if key[1] not in tracked_paths:
                del self._backfills[key]
        tracked_streams = set()
        for provider, paths in next_active.items():
            for path in paths:
                identity = self._tailer.read_identity(path)
                if identity is not None:
                    tracked_streams.add((provider, path, identity))
        for key in list(self._stream_tasks):
            if key not in tracked_streams:
                del self._stream_tasks[key]
        self._tailer.take_discovery_request()
        self._next_discovery_at = self._now() + self._DISCOVERY_INTERVAL_S

    def _poll_active(self, sources: tuple,
                     skip_paths: Optional[set] = None) -> int:
        changed = 0
        skipped = skip_paths or set()
        for provider, _, _, classifier in sources:
            for path in self._active_paths[provider]:
                if path in skipped:
                    continue
                was_seen = self._path_was_seen(path)
                had_state = path in self._tailer._files
                cold_backfill = was_seen and not had_state
                first_seen_backfill = not was_seen and not had_state
                try:
                    records = self._tailer.read(
                        path, backfill=(cold_backfill or
                                       first_seen_backfill))
                except Exception as error:
                    self._report_error("tail-read", error)
                    continue
                read_backfill, caught_up = self._tailer.read_status(path)
                if not was_seen and not had_state and not caught_up:
                    self._tailer.continue_backfill(path)
                    read_backfill = True
                if path in self._tailer._files:
                    self._remember_path(path)
                identity = self._tailer.read_identity(path)
                if identity is None:
                    continue
                key = (provider, path, identity)
                for pending_key in list(self._backfills):
                    if (pending_key[:2] == key[:2] and
                            pending_key[2] != identity):
                        del self._backfills[pending_key]
                is_backfill = read_backfill or key in self._backfills
                try:
                    file_wall_time = path.stat().st_mtime
                except OSError:
                    file_wall_time = None
                monotonic_now = self._now()
                wall_now = self._wall_time()
                for record in records:
                    try:
                        event = classifier(record)
                    except Exception as error:
                        self._report_error("classify", error)
                        continue
                    if event is None:
                        continue
                    try:
                        event = self._bind_stream_task(
                            provider, key, record, event)
                        observation = self._observation(
                            provider, event, record, file_wall_time,
                            monotonic_now, wall_now)
                        if is_backfill:
                            current = self._backfills.get(key)
                            if (current is None or
                                    (observation.order_at,
                                     observation.sequence) >=
                                    (current.order_at, current.sequence)):
                                self._backfills[key] = observation
                        elif self._apply_observation(provider, observation):
                            changed += 1
                    except Exception as error:
                        self._report_error("apply", error)
                        continue
                if is_backfill and caught_up:
                    observation = self._backfills.pop(key, None)
                    if observation is not None:
                        try:
                            if self._apply_observation(
                                    provider, observation,
                                    refresh_unchanged=False):
                                changed += 1
                        except Exception as error:
                            self._report_error("apply", error)
        return changed

    def poll_once(self) -> int:
        """Apply newly read state changes and return the number changed."""
        sources = self._sources()
        with self._poll_lock:
            if self._now() >= self._next_discovery_at:
                self._refresh_discovery(sources)
            polled_paths = {
                path for paths in self._active_paths.values()
                for path in paths
            }
            changed = self._poll_active(sources)
            if self._tailer.take_discovery_request():
                self._refresh_discovery(sources)
                changed += self._poll_active(sources, skip_paths=polled_paths)
            return changed

    def snapshot(self) -> Dict[str, Any]:
        return self._store.snapshot()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception as error:
                self._report_error("poll", error)
            if self._stop.wait(POLL_S):
                break

    def start(self) -> None:
        with self._thread_lock:
            if self._stopping:
                return
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            thread = threading.Thread(
                target=self._run,
                name="agent-status-poller",
                daemon=True,
            )
            self._thread = thread
            thread.start()

    def stop(self) -> None:
        with self._thread_lock:
            if self._stopping:
                return
            self._stopping = True
            self._stop.set()
            thread = self._thread
        try:
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=max(1.0, POLL_S * 4))
        finally:
            with self._thread_lock:
                if self._thread is thread and (
                        thread is None or not thread.is_alive()):
                    self._thread = None
                self._stopping = False
