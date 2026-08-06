"""Privacy-safe agent status classification and storage.

The classifiers may inspect local log fields to choose a coarse activity, but
only the compact :class:`Event` metadata is retained.  Prompt text, message
text, command contents, and file contents never enter the store or snapshots.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional


LEASE_S = 120.0
POLL_S = 0.5
STATES = {"idle", "working", "waiting", "done", "error", "unknown"}
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


@dataclass(frozen=True)
class Event:
    state: str
    activity: str | None
    task_id: str
    source_id: str
    project: str | None


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
    combined = json.dumps([session_id, source_id], ensure_ascii=True,
                          separators=(",", ":"))
    return (
        hashlib.sha256(combined.encode()).hexdigest(),
        source_id,
        sanitize_project(event.get("cwd")),
    )


def _claude_event(event: Dict[str, Any], state: str,
                  activity: Optional[str]) -> Optional[Event]:
    identity = _claude_identity(event)
    if identity is None:
        return None
    task_id, source_id, project = identity
    return Event(state, activity, task_id, source_id, project)


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


def classify_codex(event: Any) -> Optional[Event]:
    """Classify stable Codex task lifecycle events conservatively."""
    if not isinstance(event, dict) or event.get("type") != "event_msg":
        return None
    payload = event.get("payload")
    if not isinstance(payload, dict):
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
        observed_at = now()
        self._agents = {
            provider: {
                "task_id": "",
                "event_id": "",
                "state": "idle",
                "project": None,
                "activity": None,
                "observed_at": observed_at,
            }
            for provider in ("claude", "codex")
        }

    def apply(self, provider: str, event: Event,
              observed_at: Optional[float] = None) -> bool:
        if provider not in self._agents:
            raise ValueError(f"unsupported provider: {provider}")
        if event.state not in STATES:
            raise ValueError(f"unsupported state: {event.state}")
        if event.activity is not None and event.activity not in ACTIVITIES:
            raise ValueError(f"unsupported activity: {event.activity}")

        seen_at = self._now() if observed_at is None else observed_at
        replacement = {
            "task_id": _bounded_task_id(event.task_id),
            "event_id": stable_event_id(provider, event),
            "state": event.state,
            "project": sanitize_project(event.project),
            "activity": event.activity,
            "observed_at": seen_at,
        }
        public_fields = ("task_id", "event_id", "state", "project", "activity")
        with self._lock:
            current = self._agents[provider]
            changed = any(current[key] != replacement[key]
                          for key in public_fields)
            if changed:
                self._agents[provider] = replacement
                self._seq += 1
            else:
                current["observed_at"] = max(current["observed_at"], seen_at)
            return changed

    def snapshot(self) -> Dict[str, Any]:
        now = self._now()
        agents = {}
        with self._lock:
            for provider, stored in self._agents.items():
                age_s = max(0.0, now - stored["observed_at"])
                updated_ms = min(0xFFFFFFFF, int(age_s * 1000))
                state = stored["state"]
                activity = stored["activity"]
                if state == "working" and age_s > LEASE_S:
                    state = "unknown"
                    activity = None
                agents[provider] = {
                    "task_id": stored["task_id"],
                    "event_id": stored["event_id"],
                    "state": state,
                    "project": stored["project"],
                    "activity": activity,
                    "updated_ms": updated_ms,
                }
            seq = self._seq
        return {"v": 1, "seq": seq, "agents": agents}


class JsonlTailer:
    """Incrementally decode complete JSON objects from multiple JSONL files."""

    _MISSING_POLLS = 3

    def __init__(self):
        self._files = {}
        self._identities = {}

    @staticmethod
    def _fresh_state(identity: tuple) -> Dict[str, Any]:
        return {
            "identity": identity,
            "offset": 0,
            "partial": b"",
            "prefix_digest": hashlib.sha256(b"").digest(),
            "stat_signature": None,
            "missing_polls": 0,
        }

    def _prune_state(self, state: Dict[str, Any]) -> None:
        identity = state["identity"]
        if self._identities.get(identity) is state:
            del self._identities[identity]
        for alias, candidate in list(self._files.items()):
            if candidate is state:
                del self._files[alias]

    def _note_missing_path(self, path: Path) -> None:
        state = self._files.get(path)
        if state is None:
            return
        state["missing_polls"] += 1
        if state["missing_polls"] >= self._MISSING_POLLS:
            self._prune_state(state)

    def retain_paths(self, paths: Any) -> None:
        active_paths = {Path(path) for path in paths}
        active_identities = set()
        path_identities = {}
        for path in active_paths:
            try:
                stat = path.stat()
            except OSError:
                continue
            identity = (stat.st_dev, stat.st_ino)
            path_identities[path] = identity
            active_identities.add(identity)

        for alias, state in list(self._files.items()):
            actual_identity = path_identities.get(alias)
            if (actual_identity != state["identity"] and
                    state["identity"] in active_identities):
                del self._files[alias]

        for identity, state in list(self._identities.items()):
            if identity in active_identities:
                state["missing_polls"] = 0
                continue
            state["missing_polls"] += 1
            if state["missing_polls"] >= self._MISSING_POLLS:
                self._prune_state(state)

    def _state_for_identity(self, path: Path,
                            identity: tuple) -> Dict[str, Any]:
        previous = self._files.get(path)
        if previous is not None and previous["identity"] != identity:
            del self._files[path]

        state = self._files.get(path)
        if state is None:
            state = self._identities.get(identity)
            if state is None:
                state = self._fresh_state(identity)
                self._identities[identity] = state
            self._files[path] = state
        state["missing_polls"] = 0

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
        state.update(self._fresh_state(identity))
        if self._identities.get(old_identity) is state:
            del self._identities[old_identity]
        self._identities[identity] = state

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

    def read(self, path: Any) -> list:
        path = Path(path)
        try:
            stat = path.stat()
        except FileNotFoundError:
            self._note_missing_path(path)
            return []
        except OSError:
            return []

        identity = (stat.st_dev, stat.st_ino)
        state = self._state_for_identity(path, identity)
        if stat.st_size < state["offset"]:
            self._reset_state(state, identity)
        elif (stat.st_size == state["offset"] and
              self._stat_signature(stat) == state["stat_signature"]):
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

                prefix_digest = self._digest_prefix(
                    stream, state["offset"])
                if (prefix_digest is None or
                        prefix_digest.digest() != state["prefix_digest"]):
                    self._reset_state(state, opened_identity)
                    prefix_digest = hashlib.sha256()

                stream.seek(state["offset"])
                appended = stream.read()
                prefix_digest.update(appended)
                offset = stream.tell()
                final_stat = os.fstat(stream.fileno())
        except FileNotFoundError:
            self._note_missing_path(path)
            return []
        except OSError:
            return []

        chunks = (state["partial"] + appended).split(b"\n")
        state["offset"] = offset
        state["partial"] = chunks.pop()
        state["prefix_digest"] = prefix_digest.digest()
        final_signature = self._stat_signature(final_stat)
        state["stat_signature"] = (
            final_signature if final_signature == opened_signature else None)
        self._files[path] = state

        records = []
        for raw_line in chunks:
            if not raw_line.strip():
                continue
            try:
                records.append(json.loads(raw_line.decode(
                    "utf-8", errors="replace")))
            except (json.JSONDecodeError, UnicodeError):
                continue
        return records


class AgentStatusService:
    """Poll recent Claude/Codex logs outside HTTP request threads."""

    _RECENT_FILE_LIMIT = 12

    def __init__(self, projects_dir: Any, codex_sessions: Any,
                 now: Callable[[], float] = time.monotonic, *,
                 _wall_time: Callable[[], float] = time.time):
        self.projects_dir = Path(projects_dir)
        self.codex_sessions = Path(codex_sessions)
        self._now = now
        self._wall_time = _wall_time
        self._store = AgentStatusStore(now=now)
        self._tailer = JsonlTailer()
        self._poll_lock = threading.Lock()
        self._thread_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._stopping = False

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

    def _observed_at(self, record: Any, file_wall_time: Optional[float],
                     monotonic_now: float, wall_now: float) -> float:
        event_wall_time = self._event_wall_time(record)
        if event_wall_time is None or event_wall_time > wall_now:
            event_wall_time = file_wall_time
        if event_wall_time is None or not math.isfinite(event_wall_time):
            return monotonic_now
        age = max(0.0, wall_now - event_wall_time)
        return monotonic_now - age

    def _discover_paths(self, root: Path, pattern: str) -> tuple:
        if not root.is_dir():
            return [], []
        candidates = []
        try:
            paths = root.glob(pattern)
            for path in paths:
                try:
                    stat = path.stat()
                except OSError:
                    continue
                if path.is_file():
                    candidates.append((stat.st_mtime_ns, str(path), path))
        except OSError:
            return [], []
        all_paths = [item[2] for item in sorted(candidates)]
        newest = sorted(candidates, reverse=True)[:self._RECENT_FILE_LIMIT]
        recent_paths = [item[2] for item in sorted(newest)]
        return all_paths, recent_paths

    def _recent_paths(self, root: Path, pattern: str) -> list:
        return self._discover_paths(root, pattern)[1]

    def poll_once(self) -> int:
        """Apply newly read state changes and return the number changed."""
        changed = 0
        sources = (
            ("claude", self.projects_dir, "**/*.jsonl", classify_claude),
            ("codex", self.codex_sessions, "**/rollout-*.jsonl",
             classify_codex),
        )
        with self._poll_lock:
            discovered = []
            retained_paths = []
            for provider, root, pattern, classifier in sources:
                all_paths, recent_paths = self._discover_paths(root, pattern)
                retained_paths.extend(all_paths)
                discovered.append((provider, classifier, recent_paths))
            self._tailer.retain_paths(retained_paths)
            for provider, classifier, paths in discovered:
                for path in paths:
                    records = self._tailer.read(path)
                    if not records:
                        continue
                    try:
                        file_wall_time = path.stat().st_mtime
                    except OSError:
                        file_wall_time = None
                    monotonic_now = self._now()
                    wall_now = self._wall_time()
                    for record in records:
                        event = classifier(record)
                        if event is None:
                            continue
                        if self._store.apply(
                                provider, event, observed_at=self._observed_at(
                                    record, file_wall_time,
                                    monotonic_now, wall_now)):
                            changed += 1
        return changed

    def snapshot(self) -> Dict[str, Any]:
        return self._store.snapshot()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:
                # A transient filesystem or malformed-record failure must not
                # kill status updates for future appends.
                pass
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
