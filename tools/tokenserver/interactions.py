"""Pending "Needs You" interactions: park a hook, show it, answer it once.

Claude Code hooks POST here when a session needs the human. The request is
parked — the hook's HTTP connection stays open — while the panel shows it. One
signed answer from the device resolves that exact request and the hook returns,
so the same live session continues. Nothing is resumed, because nothing ever
stopped.

Four properties this module exists to guarantee:

* **The held connection is the pending interaction.** Resolving deletes the
  entry, so a second tap (flaky WiFi, double press, a stale screen) cannot land
  on a later prompt that happens to occupy the same glass.
* **Nothing is approved by silence.** A timeout returns *no decision at all*,
  which is precisely what makes Claude Code fall back to its normal terminal
  prompt. Walking away is always safe.
* **You can never approve what you cannot read.** When the panel may not show
  the command (privacy) or cannot fit it, APPROVE is not offered. DENY and
  LEAVE IT always are, because refusing reveals nothing and risks nothing.
* **A hook `allow` is not authority.** Claude Code still evaluates deny rules
  and managed policy after this answer, so the worst case is bounded by the
  user's settings file rather than by this code being correct.

Privacy: commands and question text are exactly the content the project's
contract keeps on the Mac, so showing them on the panel is a deliberate,
separately gated opt-in (``reveal_detail``). With it off, the panel learns only
that *something* is waiting and in which project — enough to walk over, not
enough to leak a prompt to anyone who can see the shelf.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import math
import re
import secrets
import threading
import time
import unicodedata
from collections import deque
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Dict, Mapping, Optional, Protocol, Tuple

if __package__:
    from .agent_status import sanitize_project
    from .interaction_types import InteractionProvider, InteractionResult
else:  # direct execution, same convention as tokenserver.py
    from agent_status import sanitize_project
    from interaction_types import InteractionProvider, InteractionResult


KINDS = ("question", "approval")
VERDICTS = ("approve", "deny", "leave_it")

# At most this many interactions may be parked at once. Concurrent sessions are
# expected (that is the multi-agent case); an unbounded queue is not.
MAX_PENDING = 8

# Keep recent request IDs unavailable for reuse without retaining every ID for
# the life of the process.  The window is deliberately much larger than the
# live queue and any plausible human interaction volume during one signature
# freshness interval.
ISSUED_ID_HISTORY_LIMIT = 256

# The device parses hold_ms into a uint32_t.  Refuse a duration before its
# seconds-to-milliseconds conversion can overflow either Python or firmware.
MAX_HOLD_MS = 0xFFFFFFFF

# A device answer must be signed no more than this many seconds ago. Bounds
# replay to the window in which the tap could plausibly have happened.
FRESHNESS_S = 90.0

# How often a held hook glances at its own connection while waiting. A dead
# client is noticed within this bound instead of at the timeout.
ALIVE_POLL_S = 2.0

# The device discards the WHOLE /api/agent-status body when it exceeds its
# 4096-byte buffer (TK_AGENT_HTTP_BODY_CAP), which would take the agent list
# down with it. The pending item is therefore hard-bounded, and the caller
# drops it entirely rather than risk the existing contract. See
# response_fits().
PENDING_BUDGET_BYTES = 640
# The firmware rejects a body whose length reaches its 4096-byte array size.
# Keep a 512-byte transport margin while the standalone capacity test holds
# the field-wise worst case below 80 % of the full firmware envelope.
RESPONSE_CEILING_BYTES = 3584

# Per-field display bounds, chosen to be readable at 480x480 rather than to be
# generous. Anything longer is a thing you should read at your desk.
PROMPT_MAX = 96
TITLE_MAX = 64
SUBTITLE_MAX = 64

# Claude Code's convention: a recommended option is listed first AND carries
# this suffix. We accept either signal — suffix if present, first option
# otherwise — so the panel works whether or not a given version marks it.
RECOMMENDED_SUFFIX = "(recommended)"

# Commands safe enough to approve from across the room. Deliberately narrow:
# this is noise reduction on top of the user's deny rules, never the security
# boundary. Reuses the classifier vocabulary agent_status already trusts.
_APPROVABLE_COMMAND = re.compile(
    r"^(?:\s*)(?:"
    r"\./test/run\.sh|pytest|python3?\s+-m\s+unittest|npm\s+(?:run\s+)?test|"
    r"cargo\s+test|go\s+test|ctest|"
    r"cmake\s+--build|ninja|make|cargo\s+build|npm\s+run\s+build|"
    r"idf\.py\s+build|"
    r"git\s+(?:status|diff|log|show|branch)|ls|cat|head|tail|wc|grep|rg"
    r")(?:\s|$)",
    re.IGNORECASE,
)

# Shell metacharacters that turn a recognised prefix into something else
# entirely. Their presence disqualifies a command from device approval; the
# terminal can still approve it with full context.
_COMMAND_CHAINING = re.compile(r"[;&|><`$\n]")

_APPROVABLE_TOOLS = frozenset({"read", "glob", "grep", "notebookread"})

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_QUESTION_VIEW_FIELDS = frozenset({
    "kind", "options_total", "marked", "prompt", "title", "subtitle",
    "can_approve",
})
_APPROVAL_VIEW_FIELDS = frozenset({
    "kind", "tool", "title", "subtitle", "can_approve",
})


def _is_safe_text(value: Any) -> bool:
    """Strict UTF-8 text with no control or format code points."""
    if not isinstance(value, str):
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return not any(unicodedata.category(char).startswith("C")
                   for char in value)


def _optional_text_is_safe(value: Any) -> bool:
    return not isinstance(value, str) or _is_safe_text(value)


def _is_internal_question_permission(kind: str,
                                     event: Dict[str, Any]) -> bool:
    """The dedicated question hook owns Claude's AskUserQuestion tool."""
    tool = event.get("tool_name")
    return (kind == "approval" and isinstance(tool, str) and
            tool.strip().casefold() == "askuserquestion")


def _claude_event_text_is_safe(kind: str, event: Dict[str, Any]) -> bool:
    """Validate raw Claude text before cleanup can change its semantics."""
    if not _optional_text_is_safe(event.get("cwd")):
        return False
    tool_input = event.get("tool_input")
    if kind == "question":
        question = first_question(tool_input)
        if question is None:
            return False
        fields = [question["question"], question.get("header")]
        for option in question["options"]:
            fields.extend((option["label"], option.get("description")))
        return all(_optional_text_is_safe(value) for value in fields)
    fields = [event.get("tool_name")]
    if isinstance(tool_input, dict):
        fields.extend((tool_input.get("command"),
                       tool_input.get("description")))
    return all(_optional_text_is_safe(value) for value in fields)


def _clean_text(value: Any, limit: int) -> Optional[str]:
    """Control-free, firmware-byte-bounded display text, or None.

    Claude's established behavior shortens text that is over the display's
    character limit.  The suffix itself is multibyte, so build that shortened
    form against the actual UTF-8 byte budget.  A value that looks within the
    character limit but exceeds the firmware byte buffer is rejected instead
    of silently changing international text.
    """
    if not isinstance(value, str):
        return None
    collapsed = " ".join(value.split())
    if not collapsed:
        return None
    if len(collapsed) > limit:
        suffix = "…"
        budget = limit - len(suffix.encode("utf-8"))
        prefix = []
        used = 0
        for char in collapsed:
            encoded = char.encode("utf-8")
            if used + len(encoded) > budget:
                break
            prefix.append(char)
            used += len(encoded)
        collapsed = "".join(prefix).rstrip() + suffix
    try:
        if len(collapsed.encode("utf-8")) > limit:
            return None
    except UnicodeEncodeError:
        return None
    return collapsed


def _is_truncated(value: Any, limit: int) -> bool:
    if not isinstance(value, str):
        return False
    collapsed = " ".join(value.split())
    try:
        return (len(collapsed) > limit or
                len(collapsed.encode("utf-8")) > limit)
    except UnicodeEncodeError:
        return True


def _byte_overflow_inside_character_limit(value: Any, limit: int) -> bool:
    """Would character-counting accept text the firmware byte cap rejects?"""
    if not isinstance(value, str):
        return False
    collapsed = " ".join(value.split())
    try:
        return (len(collapsed) <= limit and
                len(collapsed.encode("utf-8")) > limit)
    except UnicodeEncodeError:
        return True


def recommended_index(options: Any) -> int:
    """Index of the option Claude recommends.

    The suffix wins when present; otherwise the first option, which is the
    convention. Never guesses beyond that — an unmarked list is simply
    "first", which is what the terminal picker highlights too.
    """
    if not isinstance(options, list) or not options:
        return 0
    for index, option in enumerate(options):
        if not isinstance(option, dict):
            continue
        label = option.get("label")
        if isinstance(label, str) and \
                label.strip().lower().endswith(RECOMMENDED_SUFFIX):
            return index
    return 0


def has_recommendation(options: Any) -> bool:
    """True when an option is explicitly marked as recommended."""
    if not isinstance(options, list):
        return False
    return any(
        isinstance(option, dict) and isinstance(option.get("label"), str) and
        option["label"].strip().lower().endswith(RECOMMENDED_SUFFIX)
        for option in options
    )


def strip_recommended(label: str) -> str:
    """Display form of a label: the marker is chrome, not part of the answer."""
    stripped = label.strip()
    if stripped.lower().endswith(RECOMMENDED_SUFFIX):
        stripped = stripped[:-len(RECOMMENDED_SUFFIX)].strip()
    return stripped


def first_question(tool_input: Any) -> Optional[Dict[str, Any]]:
    """The one question v1 can render, or None if this call is not renderable.

    A multi-question call is deliberately not answerable from the panel: the
    device would have to commit to answers it never displayed.
    """
    if not isinstance(tool_input, dict):
        return None
    questions = tool_input.get("questions")
    if not isinstance(questions, list) or len(questions) != 1:
        return None
    question = questions[0]
    if not isinstance(question, dict):
        return None
    options = question.get("options")
    if not isinstance(options, list) or not 1 <= len(options) <= 255:
        return None
    if not isinstance(question.get("question"), str):
        return None
    if question.get("multiSelect"):
        return None  # v1 answers one option, so it must not claim to do more
    for option in options:
        if not isinstance(option, dict) or \
                not isinstance(option.get("label"), str):
            return None
    return question


def command_of(tool_input: Any) -> Optional[str]:
    if not isinstance(tool_input, dict):
        return None
    command = tool_input.get("command")
    return command if isinstance(command, str) else None


def approvable_tool(tool_name: Any, tool_input: Any) -> bool:
    """Tier 2: may this be approved from the device at all?

    Everything else still reaches the panel — it can be denied, or left to the
    terminal — but APPROVE is not offered for it.
    """
    if not isinstance(tool_name, str):
        return False
    name = tool_name.strip().lower()
    if name in _APPROVABLE_TOOLS:
        return True
    if name not in ("bash", "shell"):
        return False
    command = command_of(tool_input)
    if not command or _COMMAND_CHAINING.search(command):
        return False
    return bool(_APPROVABLE_COMMAND.match(command))


def question_view(question: Dict[str, Any],
                  reveal: bool) -> Tuple[Dict[str, Any], int]:
    """Panel view of a question, plus the option index APPROVE would pick."""
    options = question["options"]
    marked = has_recommendation(options)
    index = recommended_index(options)
    label = strip_recommended(options[index]["label"])
    description = options[index].get("description")
    view: Dict[str, Any] = {
        "kind": "question",
        "options_total": len(options),
        "marked": marked,
    }
    if reveal:
        view["prompt"] = _clean_text(question.get("question"), PROMPT_MAX)
        view["title"] = _clean_text(label, TITLE_MAX)
        view["subtitle"] = _clean_text(description, SUBTITLE_MAX)
        # Two conditions, both about honesty. The label must be readable in
        # full — an answer you could not read is not an answer you gave. And
        # Claude must have MARKED the option as recommended: an unmarked
        # first option is only a convention, and a convention is not a
        # recommendation the panel may claim and commit on one tap. Unmarked
        # questions are alert-only (the design doc's "never invent a
        # recommendation"), so the terminal — which shows every option —
        # takes those.
        view["can_approve"] = marked and bool(view["prompt"]) and \
            bool(view["title"]) and \
            not _is_truncated(question.get("question"), PROMPT_MAX) and \
            not _is_truncated(label, TITLE_MAX) and \
            not _is_truncated(description, SUBTITLE_MAX)
    else:
        view["can_approve"] = False
    return view, index


def approval_view(tool_name: Any, tool_input: Any,
                  reveal: bool) -> Dict[str, Any]:
    """Panel view of a permission request."""
    command = command_of(tool_input)
    subject = command if command is not None else ""
    view: Dict[str, Any] = {
        "kind": "approval",
        "tool": _clean_text(tool_name, 24),
    }
    if reveal:
        view["title"] = _clean_text(subject, TITLE_MAX) or view["tool"]
        view["subtitle"] = _clean_text(tool_input.get("description")
                                       if isinstance(tool_input, dict)
                                       else None, SUBTITLE_MAX)
        readable = bool(command) and not _is_truncated(command, TITLE_MAX)
        description = (tool_input.get("description")
                       if isinstance(tool_input, dict) else None)
        view["can_approve"] = readable and \
            not _is_truncated(description, SUBTITLE_MAX) and \
            approvable_tool(tool_name, tool_input)
    else:
        view["can_approve"] = False
    return view


def _claude_display_fits(kind: str, event: Dict[str, Any],
                         reveal: bool) -> bool:
    """Reject character-valid text that cannot fit firmware UTF-8 buffers."""
    tool_input = event.get("tool_input")
    if kind == "question":
        if not reveal:
            return True
        question = first_question(tool_input)
        if question is None:
            return False
        options = question["options"]
        selected = options[recommended_index(options)]
        return not any((
            _byte_overflow_inside_character_limit(
                question.get("question"), PROMPT_MAX),
            _byte_overflow_inside_character_limit(
                strip_recommended(selected["label"]), TITLE_MAX),
            _byte_overflow_inside_character_limit(
                selected.get("description"), SUBTITLE_MAX),
        ))
    if _byte_overflow_inside_character_limit(event.get("tool_name"), 24):
        return False
    if not reveal:
        return True
    return not any((
        _byte_overflow_inside_character_limit(
            command_of(tool_input), TITLE_MAX),
        _byte_overflow_inside_character_limit(
            tool_input.get("description")
            if isinstance(tool_input, dict) else None, SUBTITLE_MAX),
    ))


def hook_response(kind: str, verdict: str, event: Dict[str, Any],
                  option_index: int = 0) -> Optional[Dict[str, Any]]:
    """The JSON body the parked hook returns, or None for "no decision".

    None is not a failure mode — it is the documented way to leave the flow
    untouched so Claude Code renders its own prompt. Timeout, LEAVE IT and an
    unanswerable payload all land here on purpose.
    """
    if verdict == "leave_it":
        return None
    if kind == "question":
        if verdict == "deny":
            return {"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "Denied from VibePulse"}}
        question = first_question(event.get("tool_input"))
        if question is None:
            return None
        options = question["options"]
        if not 0 <= option_index < len(options):
            return None
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": {
                "questions": event["tool_input"]["questions"],
                "answers": {
                    question["question"]: options[option_index]["label"],
                },
            }}}
    if kind == "approval":
        if verdict == "approve":
            decision: Dict[str, Any] = {"behavior": "allow"}
        else:
            decision = {"behavior": "deny",
                        "message": "Denied from VibePulse"}
        return {"hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": decision}}
    return None


def view_bytes(view: Mapping[str, Any]) -> bytes:
    """Canonical bytes for the stable decision screen.

    The countdown is transport state rather than part of what the person saw
    and approved.  The digest itself is likewise excluded so a published view
    can be fed back into this helper and reproduce its stored digest exactly.
    Every other field remains bound.
    """
    if not isinstance(view, Mapping):
        raise TypeError("view must be a mapping")
    stable = {
        key: value for key, value in view.items()
        if key not in ("expires_in_ms", "view_sha256")
    }
    return json.dumps(
        stable, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")


def view_digest(view: Mapping[str, Any]) -> str:
    """SHA-256 of the exact stable decision-screen payload."""
    return hashlib.sha256(view_bytes(view)).hexdigest()


def sign_answer(secret: str, request_id: str, verdict: str,
                ts: int) -> str:
    """HMAC a device answer. Same helper the fake CLI device uses."""
    message = f"{request_id}|{verdict}|{int(ts)}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify_answer(secret: str, request_id: str, verdict: str, ts: Any,
                  mac: Any, now_wall: float) -> bool:
    """Constant-time signature check inside a freshness window."""
    if not secret or not isinstance(mac, str):
        return False
    try:
        stamp = int(ts)
    except (TypeError, ValueError):
        return False
    if abs(now_wall - stamp) > FRESHNESS_S:
        return False
    expected = sign_answer(secret, request_id, verdict, stamp)
    return hmac.compare_digest(expected, mac)


def _provider(value: Any) -> Optional[InteractionProvider]:
    if isinstance(value, InteractionProvider):
        return value
    if not isinstance(value, str):
        return None
    try:
        return InteractionProvider(value)
    except ValueError:
        return None


def _v2_stamp(value: Any) -> Optional[int]:
    # JSON booleans are ints in Python, and floats would be silently truncated
    # by int().  Neither is an acceptable timestamp on a signed boundary.
    if type(value) is not int or not 0 <= value <= 0x7FFFFFFFFFFFFFFF:
        return None
    return value


def _v2_fields(provider: Any, request_id: Any, digest: Any,
               verdict: Any, ts: Any) -> Optional[Tuple[str, int]]:
    parsed_provider = _provider(provider)
    stamp = _v2_stamp(ts)
    if parsed_provider is None or not isinstance(request_id, str) or \
            not request_id or not isinstance(digest, str) or \
            _SHA256_HEX.fullmatch(digest) is None or verdict not in VERDICTS or \
            stamp is None:
        return None
    return parsed_provider.value, stamp


def sign_answer_v2(secret: str, provider: Any, request_id: str,
                   digest: str, verdict: str, ts: int) -> str:
    """Sign a verdict bound to its provider and exact published view."""
    fields = _v2_fields(provider, request_id, digest, verdict, ts)
    if not isinstance(secret, str) or not secret or fields is None:
        raise ValueError("invalid v2 answer")
    provider_value, stamp = fields
    message = (
        f"v2|{provider_value}|{request_id}|{digest}|{verdict}|{int(stamp)}"
    ).encode("utf-8")
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify_answer_v2(secret: str, provider: Any, request_id: Any,
                     digest: Any, verdict: Any, ts: Any, mac: Any,
                     now_wall: float) -> bool:
    """Strict, constant-time v2 verification inside the replay window."""
    fields = _v2_fields(provider, request_id, digest, verdict, ts)
    if not isinstance(secret, str) or not secret or fields is None or \
            not isinstance(mac, str) or _SHA256_HEX.fullmatch(mac) is None or \
            isinstance(now_wall, bool) or \
            not isinstance(now_wall, (int, float)) or \
            not math.isfinite(float(now_wall)):
        return False
    _, stamp = fields
    if abs(float(now_wall) - stamp) > FRESHNESS_S:
        return False
    expected = sign_answer_v2(
        secret, provider, request_id, digest, verdict, stamp)
    return hmac.compare_digest(expected, mac)


def response_fits(payload: Dict[str, Any]) -> bool:
    """Would this /api/agent-status body still parse on the device?

    The firmware discards the entire body past its 4096-byte cap, taking the
    agent list with it. The pending item is the new, optional thing, so it is
    the thing that gets dropped.
    """
    try:
        encoded = json.dumps(payload).encode()
    except (TypeError, ValueError):
        return False
    return len(encoded) <= RESPONSE_CEILING_BYTES


def _normalized_view(kind: str, raw: Any) -> Optional[Mapping[str, Any]]:
    """Validate and freeze the small provider-neutral display boundary."""
    if not isinstance(raw, dict):
        return None
    allowed = _QUESTION_VIEW_FIELDS if kind == "question" else \
        _APPROVAL_VIEW_FIELDS
    if set(raw) - allowed or raw.get("kind") != kind or \
            type(raw.get("can_approve")) is not bool:
        return None

    view = {key: value for key, value in raw.items() if value is not None}
    text_limits = {
        "prompt": PROMPT_MAX, "title": TITLE_MAX,
        "subtitle": SUBTITLE_MAX, "tool": 24,
    }
    for field_name, limit in text_limits.items():
        if field_name in view and (
                not _is_safe_text(view[field_name]) or
                not view[field_name] or
                len(view[field_name].encode("utf-8")) > limit):
            return None

    if kind == "question":
        if type(view.get("options_total")) is not int or \
                not 1 <= view["options_total"] <= 255 or \
                type(view.get("marked")) is not bool:
            return None
        if view["can_approve"] and (
                not view["marked"] or not view.get("prompt") or
                not view.get("title")):
            return None
    elif view["can_approve"] and not view.get("title"):
        return None
    return MappingProxyType(view)


def _codex_question_is_normalized(normalized: Dict[str, Any],
                                  view: Mapping[str, Any],
                                  recommended: Optional[int]) -> bool:
    expected_fields = {
        "provider", "kind", "project", "session_id", "turn_id", "options",
        "recommended_index", "view",
    }
    if set(normalized) != expected_fields:
        return False
    options = normalized.get("options")
    if not isinstance(options, list) or len(options) not in (2, 3) or \
            view.get("options_total") != len(options):
        return False
    marked = []
    for index, option in enumerate(options):
        if not isinstance(option, dict) or "label" not in option or \
                set(option) - {"label", "description", "recommended"} or \
                not _is_safe_text(option["label"]) or not option["label"]:
            return False
        if "description" in option and (
                not _is_safe_text(option["description"]) or
                not option["description"]):
            return False
        if "recommended" in option and \
                type(option["recommended"]) is not bool:
            return False
        if option.get("recommended") is True:
            marked.append(index)
    expected_recommended = marked[0] if len(marked) == 1 else None
    if len(marked) > 1 or recommended != expected_recommended or \
            view.get("marked") != (recommended is not None) or \
            view.get("can_approve") != (recommended is not None):
        return False
    if recommended is not None:
        selected = options[recommended]
        if view.get("title") != selected["label"] or \
                view.get("subtitle") != selected.get("description"):
            return False
    return True


def _codex_approval_is_normalized(normalized: Dict[str, Any],
                                  recommended: Optional[int],
                                  reveal: bool) -> bool:
    expected_fields = {
        "provider", "kind", "project", "session_id", "turn_id", "event",
        "recommended_index", "view",
    }
    event = normalized.get("event")
    if set(normalized) != expected_fields or recommended is not None or \
            not isinstance(event, dict) or \
            event.get("hook_event_name") != "PermissionRequest" or \
            event.get("session_id") != normalized.get("session_id") or \
            event.get("turn_id") != normalized.get("turn_id") or \
            not isinstance(event.get("cwd"), str) or \
            not isinstance(event.get("tool_name"), str) or \
            not isinstance(event.get("tool_input"), dict):
        return False
    if not _is_safe_text(event["cwd"]) or \
            sanitize_project(event["cwd"]) != normalized.get("project"):
        return False
    try:
        if __package__:
            from .codex_interactions import normalize_codex_permission
        else:  # direct execution, same convention as tokenserver.py
            from codex_interactions import normalize_codex_permission
        canonical = normalize_codex_permission(event, reveal=reveal)
    except Exception:
        return False
    return canonical == normalized


@dataclass(frozen=True)
class RelayPublishJob:
    """Immutable, plaintext-minimal handoff to the optional relay thread."""
    request_id: str
    challenge: bytes
    view_bytes: bytes
    view_sha256: bytes
    expires_at: int
    provider: str
    can_approve: bool


@dataclass(frozen=True)
class RelayResolution:
    """Decrypted relay verdict passed back across the store boundary."""
    request_id: str
    challenge: bytes
    view_sha256: bytes
    verdict: str
    mac: bytes


class InteractionRelayListener(Protocol):
    def on_park(self, job: RelayPublishJob) -> None:
        ...

    def on_remove(self, request_id: str, reason: str) -> None:
        ...


@dataclass
class _Pending:
    request_id: str
    provider: InteractionProvider
    kind: str
    event: Optional[Dict[str, Any]]
    view: Mapping[str, Any]
    recommended_index: Optional[int]
    view_sha256: str
    requires_v2: bool
    project: Optional[str]
    session_key: Optional[str]
    hold_ms: int
    created_at: float
    arrival_index: int
    expires_at: float
    relay_job: RelayPublishJob
    done: threading.Event = field(default_factory=threading.Event)
    verdict: Optional[str] = None


class InteractionStore:
    """Thread-safe parking lot for interactions awaiting a human.

    One lock, no I/O. ``ThreadingHTTPServer`` gives each parked hook its own
    thread, so a held request costs a thread and nothing else.
    """

    def __init__(self, secret: str = "", reveal_detail: bool = False,
                 now: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time,
                 audit: Optional[Callable[[str, Dict[str, Any]], None]] = None,
                 random_bytes: Callable[[int], bytes] = secrets.token_bytes,
                 relay_listener: Optional[InteractionRelayListener] = None,
                 relay_random_bytes: Callable[[int], bytes] =
                 secrets.token_bytes):
        self._secret = secret
        self._reveal = bool(reveal_detail)
        self._now = now
        self._wall = wall
        self._audit = audit
        self._random_bytes = random_bytes
        self._relay_random_bytes = relay_random_bytes
        self._relay_listener = relay_listener
        self._lock = threading.Lock()
        self._pending: Dict[str, _Pending] = {}
        self._next_arrival_index = 0
        self._issued_ids = set()
        self._issued_order = deque()
        self._protected_ids = set()
        self._relay_notifications = deque()

    def set_relay_listener(
            self, listener: Optional[InteractionRelayListener]) -> None:
        """Attach or detach the optional background relay boundary."""
        with self._lock:
            self._relay_listener = listener

    # -- creation ---------------------------------------------------------

    def _mint_id_locked(self) -> Optional[str]:
        # Sixteen random bytes are 128 bits; unpadded base64url is always 22
        # characters and fits both the current and legacy 33-byte firmware
        # request-id buffers.  Retaining issued IDs also makes an injected
        # collision unable to revive an old double-tap target.
        while len(self._issued_ids) >= ISSUED_ID_HISTORY_LIMIT:
            removable = None
            for _ in range(len(self._issued_order)):
                candidate = self._issued_order.popleft()
                if candidate in self._protected_ids:
                    self._issued_order.append(candidate)
                else:
                    removable = candidate
                    break
            if removable is None:
                return None
            self._issued_ids.discard(removable)

        for _ in range(32):
            try:
                raw = self._random_bytes(16)
            except Exception:
                return None
            if not isinstance(raw, bytes) or len(raw) != 16:
                return None
            request_id = base64.urlsafe_b64encode(raw).decode(
                "ascii").rstrip("=")
            if request_id not in self._issued_ids:
                self._issued_ids.add(request_id)
                self._issued_order.append(request_id)
                return request_id
        return None

    def park(self, kind: str, event: Dict[str, Any],
             hold_s: float) -> Optional[_Pending]:
        """Register an interaction. None means "we cannot show this" — the
        caller returns no decision and the terminal handles it.

        Claude's hook input remains compatible, but a newly published panel
        view is v2-bound exactly like Codex. ``park_legacy`` is the explicit
        compatibility boundary for a genuine old panel payload.
        """
        return self._park_claude(kind, event, hold_s, requires_v2=True)

    def park_legacy(self, kind: str, event: Dict[str, Any],
                    hold_s: float) -> Optional[_Pending]:
        """Create the genuine provider/digest-free Claude v1 contract.

        This explicit marker prevents stripping the v2 fields from a current
        Claude entry from silently changing how the same stored request is
        authenticated.
        """
        return self._park_claude(kind, event, hold_s, requires_v2=False)

    def _park_claude(self, kind: str, event: Dict[str, Any], hold_s: float,
                     *, requires_v2: bool) -> Optional[_Pending]:
        if kind not in KINDS or not isinstance(event, dict):
            return None
        if _is_internal_question_permission(kind, event):
            return None
        if not _claude_event_text_is_safe(kind, event):
            return None
        if not _claude_display_fits(kind, event, self._reveal):
            return None
        cwd = event.get("cwd")
        tool_input = event.get("tool_input")
        if kind == "question":
            question = first_question(tool_input)
            if question is None:
                return None
            view, option_index = question_view(question, self._reveal)
        else:
            view = approval_view(event.get("tool_name"), tool_input,
                                 self._reveal)
            option_index = None
        return self._park_normalized({
            "provider": InteractionProvider.CLAUDE.value,
            "kind": kind,
            "project": sanitize_project(cwd),
            "event": event,
            "recommended_index": option_index,
            "view": view,
        }, hold_s, requires_v2=requires_v2)

    def park_normalized(self, normalized: Any,
                        hold_s: float) -> Optional[_Pending]:
        """Park a validated Claude/Codex boundary without publishing raw data."""
        return self._park_normalized(normalized, hold_s, requires_v2=True)

    def _park_normalized(self, normalized: Any, hold_s: float, *,
                         requires_v2: bool) -> Optional[_Pending]:
        if not isinstance(normalized, dict):
            return None
        provider = _provider(normalized.get("provider"))
        kind = normalized.get("kind")
        if provider is None or kind not in KINDS:
            return None
        view = _normalized_view(kind, normalized.get("view"))
        project = normalized.get("project")
        if view is None or (project is not None and (
                not _is_safe_text(project) or
                sanitize_project(project) != project)):
            return None
        recommended = normalized.get("recommended_index")
        if recommended is not None and (
                type(recommended) is not int or recommended < 0):
            return None
        if kind == "question" and recommended is not None and \
                recommended >= view["options_total"]:
            return None
        if kind == "approval" and recommended is not None:
            return None

        event: Optional[Dict[str, Any]] = None
        session_id: Any = None
        if provider is InteractionProvider.CLAUDE:
            if set(normalized) != {
                    "provider", "kind", "project", "event",
                    "recommended_index", "view"}:
                return None
            raw_event = normalized.get("event")
            raw_cwd = raw_event.get("cwd") if isinstance(raw_event, dict) \
                else None
            if not isinstance(raw_event, dict) or \
                    not _claude_event_text_is_safe(kind, raw_event) or \
                    sanitize_project(raw_cwd) != project:
                return None
            tool_input = raw_event.get("tool_input")
            if kind == "question":
                question = first_question(tool_input)
                if question is None:
                    return None
                expected_view, expected_index = question_view(
                    question, self._reveal)
            else:
                expected_view = approval_view(
                    raw_event.get("tool_name"), tool_input, self._reveal)
                expected_index = None
            frozen_expected = _normalized_view(kind, expected_view)
            if frozen_expected is None or \
                    dict(view) != dict(frozen_expected) or \
                    recommended != expected_index:
                return None
            event = copy.deepcopy(raw_event)
            session_id = raw_event.get("session_id")
        else:
            if not isinstance(normalized.get("session_id"), str) or \
                    not normalized["session_id"] or \
                    not isinstance(normalized.get("turn_id"), str) or \
                    not normalized["turn_id"]:
                return None
            if kind == "question":
                if not _codex_question_is_normalized(
                        normalized, view, recommended):
                    return None
            elif not _codex_approval_is_normalized(
                    normalized, recommended, self._reveal):
                return None
            session_id = normalized["session_id"]

        if isinstance(hold_s, bool):
            return None
        try:
            requested_duration = float(hold_s)
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(requested_duration) or \
                requested_duration <= 0 or \
                requested_duration > MAX_HOLD_MS / 1000:
            return None
        duration = max(1.0, requested_duration)
        now = self._now()
        wall_now = self._wall()
        try:
            challenge = self._relay_random_bytes(32)
        except Exception:
            return None
        if not isinstance(challenge, bytes) or len(challenge) != 32 or \
                not isinstance(wall_now, (int, float)) or \
                isinstance(wall_now, bool) or not math.isfinite(wall_now):
            return None
        relay_expiry = math.ceil(wall_now + duration)
        if not 0 < relay_expiry <= 0xFFFFFFFF:
            return None
        hold_ms = int(duration * 1000)
        stable_template = {
            "provider": provider.value,
            "request_id": "A" * 22,
            "project": project,
            "hold_ms": hold_ms,
        }
        stable_template.update(view)
        stable_template = {
            key: value for key, value in stable_template.items()
            if value is not None
        }
        if not 0 < len(view_bytes(stable_template)) <= PENDING_BUDGET_BYTES:
            return None
        with self._lock:
            self._sweep_locked(now)
        self._flush_relay_notifications()
        with self._lock:
            if len(self._pending) >= MAX_PENDING:
                return None
            request_id = self._mint_id_locked()
            if request_id is None:
                return None
            stable = {
                "provider": provider.value,
                "request_id": request_id,
                "project": project,
                "hold_ms": hold_ms,
            }
            stable.update(view)
            stable = {key: value for key, value in stable.items()
                      if value is not None}
            stable_bytes = view_bytes(stable)
            digest = hashlib.sha256(stable_bytes).digest()
            relay_job = RelayPublishJob(
                request_id=request_id,
                challenge=challenge,
                view_bytes=stable_bytes,
                view_sha256=digest,
                expires_at=relay_expiry,
                provider=provider.value,
                can_approve=bool(view.get("can_approve")),
            )
            arrival_index = self._next_arrival_index
            self._next_arrival_index += 1
            entry = _Pending(
                request_id=request_id,
                provider=provider,
                kind=kind,
                event=event,
                view=view,
                recommended_index=recommended,
                view_sha256=digest.hex(),
                requires_v2=requires_v2,
                project=project,
                session_key=_session_key(session_id),
                hold_ms=hold_ms,
                created_at=now,
                arrival_index=arrival_index,
                expires_at=now + duration,
                relay_job=relay_job,
            )
            self._pending[entry.request_id] = entry
            self._protected_ids.add(entry.request_id)
        if requires_v2:
            self._notify_relay_park(entry.relay_job)
        self._log("parked", entry, None)
        return entry

    def await_result(self, entry: _Pending,
                     is_alive: Optional[Callable[[], bool]] = None
                     ) -> Optional[InteractionResult]:
        """Block until answered, abandoned or expired; return a neutral result.

        ``is_alive`` lets the caller say whether the session that asked is
        still there. Without it a hook whose client has gone — Ctrl-C, a
        closed terminal, a killed session — would sit parked until its
        timeout, and because the panel shows the oldest interaction first,
        that ghost would shadow real ones for up to two minutes and eat a
        slot in a queue that only holds a few.
        """
        if is_alive is None:
            remaining = max(0.0, entry.expires_at - self._now())
            entry.done.wait(timeout=remaining)
        else:
            while True:
                remaining = entry.expires_at - self._now()
                if remaining <= 0:
                    break
                if entry.done.wait(timeout=min(ALIVE_POLL_S, remaining)):
                    break
                if not is_alive():
                    with self._lock:
                        removed = self._pending.pop(entry.request_id, None)
                        self._protected_ids.discard(entry.request_id)
                        if removed is not None:
                            self._queue_relay_remove_locked(
                                entry, "abandoned")
                    self._flush_relay_notifications()
                    self._log("abandoned", entry, None)
                    return None
        with self._lock:
            removed = self._pending.pop(entry.request_id, None)
            self._protected_ids.discard(entry.request_id)
            if removed is not None and entry.verdict is None:
                self._queue_relay_remove_locked(entry, "timeout")
        self._flush_relay_notifications()
        if entry.verdict is None:
            self._log("timeout", entry, None)
            return None
        option_index = entry.recommended_index \
            if entry.verdict == "approve" else None
        return InteractionResult(entry.verdict, option_index)

    def await_verdict(self, entry: _Pending,
                      is_alive: Optional[Callable[[], bool]] = None
                      ) -> Optional[Dict[str, Any]]:
        """Claude-only compatibility wrapper returning its exact hook body."""
        if entry.provider is not InteractionProvider.CLAUDE:
            # A wrong adapter must never turn a Codex result into Claude hook
            # authority. Reap a still-live mistake so it cannot shadow the
            # queue as a ghost.
            with self._lock:
                removed = self._pending.pop(entry.request_id, None)
                self._protected_ids.discard(entry.request_id)
                if removed is not None:
                    self._queue_relay_remove_locked(
                        entry, "provider-mismatch")
            entry.done.set()
            self._flush_relay_notifications()
            self._log("provider-mismatch", entry, None)
            return None
        result = self.await_result(entry, is_alive=is_alive)
        if result is None or entry.event is None:
            return None
        return hook_response(
            entry.kind, result.verdict, entry.event,
            result.option_index if result.option_index is not None else 0,
        )

    # -- answering --------------------------------------------------------

    def resolve(self, request_id: Any, verdict: Any, ts: Any,
                mac: Any, provider: Any = None,
                view_sha256: Any = None) -> Tuple[bool, str]:
        """Apply one signed answer. Returns (ok, reason)."""
        if not isinstance(request_id, str) or verdict not in VERDICTS:
            return False, "bad request"
        if not self._secret:
            return False, "device answers are not configured"

        with self._lock:
            self._sweep_locked(self._now())
            entry = self._pending.get(request_id)
        self._flush_relay_notifications()
        uses_v2 = provider is not None or view_sha256 is not None
        if entry is not None and entry.requires_v2 and \
                (provider is None or view_sha256 is None):
            return False, "v2 verdict required"
        if uses_v2:
            if not verify_answer_v2(
                    self._secret, provider, request_id, view_sha256, verdict,
                    ts, mac, self._wall()):
                return False, "signature rejected"
            if entry is not None and (
                    _provider(provider) is not entry.provider or
                    not hmac.compare_digest(view_sha256,
                                            entry.view_sha256)):
                return False, "interaction binding rejected"
        else:
            if entry is not None and entry.requires_v2:
                return False, "v2 verdict required"
            if not verify_answer(self._secret, request_id, verdict, ts, mac,
                                 self._wall()):
                return False, "signature rejected"

        accepted_entry = None
        failure = None
        with self._lock:
            self._sweep_locked(self._now())
            entry = self._pending.get(request_id)
            if entry is None:
                # Already answered, expired, or never existed. Identical
                # response on purpose: a stale tap learns nothing.
                failure = "no such pending interaction"
            elif verdict == "approve" and not entry.view.get("can_approve"):
                failure = "this one has to be approved at the terminal"
            else:
                del self._pending[request_id]
                entry.verdict = verdict
                self._queue_relay_remove_locked(entry, "resolved")
                accepted_entry = entry
        if accepted_entry is not None:
            accepted_entry.done.set()
        self._flush_relay_notifications()
        if failure is not None:
            return False, failure
        self._log("resolved", accepted_entry, verdict)
        return True, "ok"

    def resolve_relay(
            self, result: Any,
            verify: Callable[[RelayPublishJob, RelayResolution], bool]
            ) -> Tuple[bool, str]:
        """Consume one decrypted relay verdict in a single locked transition.

        AEAD decoding happens before this boundary. ``verify`` must be a pure,
        non-blocking HMAC check; it is invoked under the store lock only after
        the live request, deadline, challenge, and view digest match.
        """
        if not isinstance(result, RelayResolution) or \
                not isinstance(result.request_id, str) or \
                not isinstance(result.challenge, bytes) or \
                len(result.challenge) != 32 or \
                not isinstance(result.view_sha256, bytes) or \
                len(result.view_sha256) != 32 or \
                not isinstance(result.mac, bytes) or len(result.mac) != 32 or \
                result.verdict not in ("approve", "deny", "terminal", "panic") or \
                not callable(verify):
            return False, "bad request"

        completed = []
        accepted = False
        reason = "no such pending interaction"
        with self._lock:
            self._sweep_locked(self._now())
            entry = self._pending.get(result.request_id)
            if entry is not None:
                job = entry.relay_job
                if not hmac.compare_digest(result.challenge, job.challenge) or \
                        not hmac.compare_digest(
                            result.view_sha256, job.view_sha256):
                    reason = "interaction binding rejected"
                else:
                    try:
                        authenticated = bool(verify(job, result))
                    except Exception:
                        authenticated = False
                    if not authenticated:
                        reason = "signature rejected"
                    elif result.verdict == "approve" and \
                            not job.can_approve:
                        reason = \
                            "this one has to be approved at the terminal"
                    elif result.verdict == "panic":
                        completed = list(self._pending.values())
                        self._pending.clear()
                        for pending in completed:
                            pending.verdict = "deny"
                            self._queue_relay_remove_locked(pending, "panic")
                        accepted = True
                        reason = "panic"
                    else:
                        del self._pending[result.request_id]
                        entry.verdict = "leave_it" \
                            if result.verdict == "terminal" \
                            else result.verdict
                        self._queue_relay_remove_locked(
                            entry,
                            "terminal" if result.verdict == "terminal"
                            else "resolved",
                        )
                        completed = [entry]
                        accepted = True
                        reason = "ok"
        for entry in completed:
            entry.done.set()
        self._flush_relay_notifications()
        if accepted:
            for entry in completed:
                self._log(
                    "relay-panic" if result.verdict == "panic"
                    else "relay-resolved",
                    entry,
                    entry.verdict,
                )
        return accepted, reason

    def panic(self, ts: Any, mac: Any) -> Tuple[bool, int]:
        """Signed panic stop. Returns (accepted, denied_count).

        Signed over the same primitive as any other answer, with a fixed
        request id, so the device needs no extra crypto and a captured panic
        cannot be replayed as an approval.
        """
        if not self._secret:
            return False, 0
        if not verify_answer(self._secret, "panic", "deny", ts, mac,
                             self._wall()):
            return False, 0
        return True, self.deny_all()

    def deny_all(self) -> int:
        """Panic stop: refuse everything parked. Only ever denies."""
        with self._lock:
            entries = list(self._pending.values())
            self._pending.clear()
            for entry in entries:
                self._queue_relay_remove_locked(entry, "panic")
        for entry in entries:
            entry.verdict = "deny"
            entry.done.set()
            self._log("panic-denied", entry, "deny")
        self._flush_relay_notifications()
        return len(entries)

    # -- publishing -------------------------------------------------------

    def pending_public(self) -> Optional[Dict[str, Any]]:
        """The one interaction the panel should show, or None.

        One at a time: a 480x480 panel that lists decisions is a dashboard,
        and a dashboard is not what you answer from the kitchen.
        """
        now = self._now()
        payload = None
        with self._lock:
            self._sweep_locked(now)
            if self._pending:
                entry = min(
                    self._pending.values(),
                    key=lambda item: (item.created_at, item.arrival_index),
                )
                payload = {
                    "request_id": entry.request_id,
                    "project": entry.project,
                    "expires_in_ms": max(
                        0, int((entry.expires_at - now) * 1000)),
                    # The original hold, so the panel's countdown ring maps to
                    # the REAL terminal-fallback time rather than guessing.
                    "hold_ms": entry.hold_ms,
                }
                if entry.requires_v2:
                    payload["provider"] = entry.provider.value
                payload.update(entry.view)
                if entry.requires_v2:
                    payload["view_sha256"] = entry.view_sha256
        self._flush_relay_notifications()
        if payload is None:
            return None
        payload = {key: value for key, value in payload.items()
                   if value is not None}
        encoded = len(json.dumps(payload).encode())
        if encoded > PENDING_BUDGET_BYTES:  # pragma: no cover - bounds above
            return None
        return payload

    # -- internals --------------------------------------------------------

    def _sweep_locked(self, now: float) -> None:
        expired = [key for key, entry in self._pending.items()
                   if entry.expires_at <= now]
        for key in expired:
            entry = self._pending.pop(key)
            self._queue_relay_remove_locked(entry, "timeout")
            entry.done.set()  # unblock the hook; verdict stays None → no
            #                   decision, so the terminal asks

    def _queue_relay_remove_locked(self, entry: _Pending,
                                   reason: str) -> None:
        if entry.requires_v2:
            self._relay_notifications.append((entry.request_id, reason))

    def _flush_relay_notifications(self) -> None:
        with self._lock:
            listener = self._relay_listener
            notifications = list(self._relay_notifications)
            self._relay_notifications.clear()
        if listener is None:
            return
        for request_id, reason in notifications:
            try:
                listener.on_remove(request_id, reason)
            except Exception:  # noqa: S110 - a listener's failure must not block removal
                pass

    def _notify_relay_park(self, job: RelayPublishJob) -> None:
        with self._lock:
            listener = self._relay_listener
        if listener is None:
            return
        try:
            listener.on_park(job)
        except Exception:  # noqa: S110 - a listener's failure must not block parking
            pass

    def _log(self, action: str, entry: _Pending,
             verdict: Optional[str]) -> None:
        if self._audit is None:
            return
        try:
            self._audit(action, {
                "request_id": entry.request_id,
                "provider": entry.provider.value,
                "kind": entry.kind,
                "tool": entry.event.get("tool_name")
                if entry.event is not None else entry.view.get("tool"),
                "project": entry.project,
                "session": entry.session_key,
                "verdict": verdict,
            })
        except Exception:  # noqa: S110 - the audit trail must never break the decision path
            pass


def read_device_key(repo_root: Optional[Any] = None) -> Optional[str]:
    """The key the paired panel signs its answers with.

    Same shape and search order as the project's other secrets, ending in
    ``secrets.h`` — which is the point: the firmware compiles the identical
    ``TK_VIBEPULSE_DEVICE_KEY`` in, so pairing is one value in one git-ignored
    file rather than a key exchange, and revoking is changing it.

    It authenticates the *device* and nothing else. It grants no access to
    Claude, GitHub or this Mac; the most it can do is answer a prompt this
    machine was already going to ask a human about — and only within the
    limits the user's own deny rules already set.
    """
    import os
    from pathlib import Path

    for name in ("VIBEPULSE_DEVICE_KEY", "TK_VIBEPULSE_DEVICE_KEY"):
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    try:
        key = (Path.home() / ".vibepulse-device-key").read_text(
            encoding="utf-8").strip()
        if key:
            return key
    except OSError:
        pass
    root = Path(repo_root) if repo_root is not None else \
        Path(__file__).resolve().parents[2]
    try:
        header = (root / "secrets.h").read_text(
            encoding="utf-8", errors="ignore")
        match = re.search(
            r'#\s*define\s+TK_VIBEPULSE_DEVICE_KEY\s+"([^"]+)"', header)
        if match and match.group(1).strip():
            return match.group(1).strip()
    except OSError:
        pass
    return None


def _session_key(session_id: Any) -> Optional[str]:
    """Short, stable, non-reversing handle for a session."""
    if not isinstance(session_id, str) or not session_id:
        return None
    return hashlib.sha256(session_id.encode(
        "utf-8", errors="surrogatepass")).hexdigest()[:16]
