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

import hashlib
import hmac
import json
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple

if __package__:
    from .agent_status import sanitize_project
else:  # direct execution, same convention as tokenserver.py
    from agent_status import sanitize_project


KINDS = ("question", "approval")
VERDICTS = ("approve", "deny", "leave_it")

# At most this many interactions may be parked at once. Concurrent sessions are
# expected (that is the multi-agent case); an unbounded queue is not.
MAX_PENDING = 8

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
RESPONSE_CEILING_BYTES = 3072

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


def _clean_text(value: Any, limit: int) -> Optional[str]:
    """Control-free, length-bounded display text, or None."""
    if not isinstance(value, str):
        return None
    collapsed = " ".join(value.split())
    if not collapsed:
        return None
    if len(collapsed) > limit:
        collapsed = collapsed[:limit - 1].rstrip() + "…"
    return collapsed


def _is_truncated(value: Any, limit: int) -> bool:
    if not isinstance(value, str):
        return False
    return len(" ".join(value.split())) > limit


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
    if not isinstance(options, list) or not options:
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
        view["can_approve"] = marked and bool(view["title"]) and \
            not _is_truncated(label, TITLE_MAX)
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
        view["can_approve"] = readable and approvable_tool(tool_name,
                                                           tool_input)
    else:
        view["can_approve"] = False
    return view


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


@dataclass
class _Pending:
    request_id: str
    kind: str
    event: Dict[str, Any]
    view: Dict[str, Any]
    option_index: int
    project: Optional[str]
    session_key: Optional[str]
    created_at: float
    expires_at: float
    # Arrival order, independent of the clock. ``created_at`` alone cannot
    # order two hooks that land in the same tick, and a tick is not small
    # everywhere: it is under a microsecond on macOS and Linux but about
    # 15.6 ms on Windows, which two agents answering at once will share.
    # Falling back to request_id there ordered the queue by a random mint,
    # so "the panel shows the oldest first" quietly became "shows either".
    arrival: int = 0
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
                 audit: Optional[Callable[[str, Dict[str, Any]], None]] = None):
        self._secret = secret
        self._reveal = bool(reveal_detail)
        self._now = now
        self._wall = wall
        self._audit = audit
        self._lock = threading.Lock()
        self._pending: Dict[str, _Pending] = {}
        self._counter = 0
        self._arrivals = 0

    # -- creation ---------------------------------------------------------

    def _mint_id(self) -> str:
        self._counter += 1
        raw = f"{self._wall():.6f}|{self._counter}|{id(self):x}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def park(self, kind: str, event: Dict[str, Any],
             timeout_s: float) -> Optional[_Pending]:
        """Register an interaction. None means "we cannot show this" — the
        caller returns no decision and the terminal handles it."""
        if kind not in KINDS or not isinstance(event, dict):
            return None
        tool_input = event.get("tool_input")
        if kind == "question":
            question = first_question(tool_input)
            if question is None:
                return None
            view, option_index = question_view(question, self._reveal)
        else:
            view = approval_view(event.get("tool_name"), tool_input,
                                 self._reveal)
            option_index = 0

        now = self._now()
        entry = _Pending(
            request_id=self._mint_id(),
            kind=kind,
            event=event,
            view=view,
            option_index=option_index,
            project=sanitize_project(event.get("cwd")),
            session_key=_session_key(event.get("session_id")),
            created_at=now,
            expires_at=now + max(1.0, float(timeout_s)),
        )
        with self._lock:
            self._sweep_locked(now)
            if len(self._pending) >= MAX_PENDING:
                return None
            # Under the lock, so two hooks arriving together get distinct,
            # correctly ordered numbers rather than racing for one.
            self._arrivals += 1
            entry.arrival = self._arrivals
            self._pending[entry.request_id] = entry
        self._log("parked", entry, None)
        return entry

    def await_verdict(self, entry: _Pending,
                      is_alive: Optional[Callable[[], bool]] = None
                      ) -> Optional[Dict[str, Any]]:
        """Block until answered, abandoned or expired; return the hook body.

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
                        self._pending.pop(entry.request_id, None)
                    self._log("abandoned", entry, None)
                    return None
        with self._lock:
            self._pending.pop(entry.request_id, None)
        verdict = entry.verdict or "leave_it"
        if entry.verdict is None:
            self._log("timeout", entry, None)
        return hook_response(entry.kind, verdict, entry.event,
                             entry.option_index)

    # -- answering --------------------------------------------------------

    def resolve(self, request_id: Any, verdict: Any, ts: Any,
                mac: Any) -> Tuple[bool, str]:
        """Apply one signed answer. Returns (ok, reason)."""
        if not isinstance(request_id, str) or verdict not in VERDICTS:
            return False, "bad request"
        if not self._secret:
            return False, "device answers are not configured"
        if not verify_answer(self._secret, request_id, verdict, ts, mac,
                             self._wall()):
            return False, "signature rejected"
        with self._lock:
            self._sweep_locked(self._now())
            entry = self._pending.get(request_id)
            if entry is None:
                # Already answered, expired, or never existed. Identical
                # response on purpose: a stale tap learns nothing.
                return False, "no such pending interaction"
            if verdict == "approve" and not entry.view.get("can_approve"):
                return False, "this one has to be approved at the terminal"
            del self._pending[request_id]
            entry.verdict = verdict
        entry.done.set()
        self._log("resolved", entry, verdict)
        return True, "ok"

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
            entry.verdict = "deny"
            entry.done.set()
            self._log("panic-denied", entry, "deny")
        return len(entries)

    # -- publishing -------------------------------------------------------

    def pending_public(self) -> Optional[Dict[str, Any]]:
        """The one interaction the panel should show, or None.

        One at a time: a 480x480 panel that lists decisions is a dashboard,
        and a dashboard is not what you answer from the kitchen.
        """
        now = self._now()
        with self._lock:
            self._sweep_locked(now)
            if not self._pending:
                return None
            entry = min(self._pending.values(),
                        key=lambda item: (item.created_at, item.arrival))
            payload = {
                "request_id": entry.request_id,
                "project": entry.project,
                "expires_in_ms": max(
                    0, int((entry.expires_at - now) * 1000)),
                # The original hold, so the panel's countdown ring maps to the
                # REAL terminal-fallback time rather than guessing a duration.
                "hold_ms": max(1, int((entry.expires_at - entry.created_at)
                                      * 1000)),
            }
            payload.update(entry.view)
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
            entry.done.set()  # unblock the hook; verdict stays None → no
            #                   decision, so the terminal asks

    def _log(self, action: str, entry: _Pending,
             verdict: Optional[str]) -> None:
        if self._audit is None:
            return
        try:
            self._audit(action, {
                "request_id": entry.request_id,
                "kind": entry.kind,
                "tool": entry.event.get("tool_name"),
                "project": entry.project,
                "session": entry.session_key,
                "verdict": verdict,
            })
        except Exception:
            pass  # the audit trail must never break the decision path


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
