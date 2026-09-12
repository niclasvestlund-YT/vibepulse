#!/usr/bin/env python3
"""Claude Code statusLine -> VibePulse bridge.

Claude Code runs the command in ``settings.json``'s ``statusLine`` on every
new assistant message (and a few other triggers) and pipes it a JSON
document on stdin.  That document carries the account's session (5 h) and
weekly rate-limit windows as ``rate_limits.five_hour`` / ``seven_day`` --
the same two numbers the tokenserver otherwise has to fetch from an
undocumented OAuth endpoint that rate-limits hard.

This bridge is what ``vibepulse_setup.py statusline install`` points that
command at.  It keeps exactly those two windows and the Claude Code
``version`` string, validates every retained field strictly, and merges
them per window into one file in the tokenserver's state directory,
``claude-statusline-quota.json``.  Then it runs the status line the user
had before (recorded at install time) with the same stdin, passing that
command's stdout and exit status through unchanged.  The bridge itself
prints nothing.

Design: ``docs/superpowers/specs/2026-09-10-vibepulse-statusline-quota-
source-design.md``.  This is the single-account slice of it: the sample is
keyed under one account entry, ``single``, because the install command
makes the operator assert that Claude Code and the tokenserver use the same
Claude account on this computer.  The account-binding machinery the spec
describes for two-account hosts is deliberately not implemented here.

Invariants, in the spirit of ``docs/lessons.md``'s hostile-input entries:

* A window absent from stdin is no observation: the stored window keeps
  its value until its own ``resets_at`` passes.  Nothing is ever written
  as zero and no window is ever invented.
* A present-and-malformed window rejects the whole payload: nothing is
  written, the stored entry stays exactly as it was, the chained command
  still runs.
* Within one reset window usage only accumulates, so a replay with the
  same or a lower percentage never regresses the stored figure; it only
  advances ``seen`` (the bridge is alive).  A newer ``resets_at`` is a new
  window and replaces the old one.
* The read-merge-replace runs under an interprocess lock; a bridge that
  cannot take it within a short bound skips the write rather than putting
  back a stale copy of a window another run just updated.
* Whatever goes wrong, the chained command runs and the exit status is 0
  when there is none -- Claude Code must never lose its status line to
  this bridge.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None
try:
    import msvcrt
except ImportError:  # macOS/Linux
    msvcrt = None

if __package__:
    from .state_files import fsync_parent, quarantine_corrupt, state_dir
else:  # run directly: python3 tools/tokenserver/statusline_bridge.py
    from state_files import fsync_parent, quarantine_corrupt, state_dir

SAMPLE_NAME = "claude-statusline-quota.json"
CONFIG_NAME = "claude-statusline-bridge.json"
LOCK_NAME = "claude-statusline-quota.lock"
SAMPLE_VERSION = 1
CONFIG_VERSION = 1
# v1 keys every sample under one account entry (see the module docstring).
ACCOUNT_KEY = "single"
WINDOWS = ("five_hour", "seven_day")
# How far ahead a window may claim to reset.  Five hours plus slack for the
# API's clock against the host's; eight days for the weekly window.  A
# ``five_hour`` claiming a reset a week out would otherwise win the
# later-reset arbitration and hold the session ring on a bogus window.
RESET_SLACK_S = 15 * 60
WINDOW_HORIZON_S = {
    "five_hour": 5 * 3600 + RESET_SLACK_S,
    "seven_day": 8 * 24 * 3600,
}
STDIN_MAX_BYTES = 256 * 1024
# The chained status line was promised the same stdin: everything is
# forwarded, up to this hard bound, even when the sample parser refuses
# more than STDIN_MAX_BYTES of it.
STDIN_FORWARD_MAX_BYTES = 8 * 1024 * 1024
SAMPLE_MAX_BYTES = 64 * 1024
CONFIG_MAX_BYTES = 64 * 1024
VERSION_MAX_CHARS = 64
LOCK_WAIT_S = 0.5
# A sample whose newest ``seen`` is older than this is "stale": Claude Code
# has not spoken for a while (no session open, or the bridge is broken) and
# the tokenserver's own probe is the better source again.  Shared with the
# doctor and the tokenserver so all three agree on the word.
FRESH_S = 15 * 60
LOCK_RETRY_S = 0.05
CHAINED_TIMEOUT_S = 10.0


class RejectedPayload(ValueError):
    """The stdin document must not touch the file (reason in ``args[0]``)."""


def config_dir_key(config_dir: Path) -> str:
    """The per-config-directory key setup and the bridge share."""
    resolved = str(Path(config_dir).expanduser().resolve())
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:16]


def claude_config_dir(env=None) -> Path:
    """Where this Claude Code session keeps ``settings.json``."""
    env = os.environ if env is None else env
    override = env.get("CLAUDE_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".claude"


# --- validation -----------------------------------------------------------

def _finite_number(value) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:  # an int JSON parsed that no float can hold
        return False


def _validate_window(name: str, value, now: int) -> dict:
    if not isinstance(value, dict):
        raise RejectedPayload(f"{name}: not an object")
    pct = value.get("used_percentage")
    if not _finite_number(pct) or not 0 <= pct <= 100:
        raise RejectedPayload(f"{name}: used_percentage out of range")
    resets_at = value.get("resets_at")
    if (not _finite_number(resets_at) or int(resets_at) != resets_at
            or resets_at <= now
            or resets_at > now + WINDOW_HORIZON_S[name]):
        raise RejectedPayload(f"{name}: resets_at not a plausible epoch")
    return {"pct": round(float(pct), 1), "resets_at": int(resets_at)}


def parse_payload(raw: bytes, now: int) -> dict:
    """Return ``{"windows": {...}, "version": str | None}`` or raise.

    ``windows`` holds only the windows present on stdin, each validated.
    A missing ``rate_limits`` block (the session-start invocation, the free
    tier, an API-key session) is an empty ``windows`` -- no observation --
    not a rejection.
    """
    if len(raw) > STDIN_MAX_BYTES:
        raise RejectedPayload("stdin larger than the bound")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RejectedPayload(
            f"stdin is not JSON ({type(error).__name__})") from None
    if not isinstance(payload, dict):
        raise RejectedPayload("stdin is not a JSON object")
    windows = {}
    limits = payload.get("rate_limits")
    if limits is not None:
        if not isinstance(limits, dict):
            raise RejectedPayload("rate_limits is not an object")
        for name in WINDOWS:
            if name in limits:
                windows[name] = _validate_window(name, limits[name], now)
    version = payload.get("version")
    if (not isinstance(version, str) or not version
            or len(version) > VERSION_MAX_CHARS
            or not version.isprintable()):
        version = None
    return {"windows": windows, "version": version}


# --- the merge (pure) ---------------------------------------------------

def _well_formed_window(value) -> bool:
    """The persisted shape of one window, regardless of whether it has
    reset since: the file-level check, so a malformed record is a corrupt
    file to quarantine, never a value to silently drop and overwrite."""
    return (isinstance(value, dict)
            and _finite_number(value.get("pct"))
            and 0 <= value["pct"] <= 100
            and _finite_number(value.get("resets_at"))
            and int(value["resets_at"]) == value["resets_at"]
            and _finite_number(value.get("at"))
            and _finite_number(value.get("seen")))


def _valid_stored_window(value, now: int, name: str) -> bool:
    """Well-formed AND still running: a reset in the future, within the
    window's horizon."""
    return (_well_formed_window(value)
            and value["resets_at"] > now
            and value["resets_at"] <= now + WINDOW_HORIZON_S[name])


def _well_formed_document(document) -> bool:
    """The whole v1 sample shape, down to each window: the envelope, every
    account entry an object, every present window well-formed, the version
    a string when present."""
    if (not isinstance(document, dict)
            or document.get("v") != SAMPLE_VERSION
            or not isinstance(document.get("accounts"), dict)):
        return False
    for entry in document["accounts"].values():
        if not isinstance(entry, dict):
            return False
        for name in WINDOWS:
            if name in entry and not _well_formed_window(entry[name]):
                return False
        version = entry.get("claude_code_version")
        if version is not None and not isinstance(version, str):
            return False
    return True


def merge_entry(stored, observed: dict, now: int) -> dict:
    """Merge one payload's windows into one account entry.

    ``stored`` is the entry as read from the file (anything; malformed
    windows are treated as absent), ``observed`` is ``parse_payload``'s
    result.  Returns the new entry; the caller decides whether it changed.
    """
    entry = {}
    stored = stored if isinstance(stored, dict) else {}
    for name in WINDOWS:
        old = stored.get(name)
        old = old if _valid_stored_window(old, now, name) else None
        new = observed["windows"].get(name)
        if new is None:
            if old is not None:
                entry[name] = dict(old)
            continue
        if (old is None or new["resets_at"] > old["resets_at"]
                or (new["resets_at"] == old["resets_at"]
                    and new["pct"] > old["pct"])):
            entry[name] = {"pct": new["pct"], "resets_at": new["resets_at"],
                           "at": now, "seen": now}
        else:
            # Same window, same or lower figure (a replay of Claude Code's
            # cached values on a non-API trigger), or an older window than
            # the one already stored: the value and its ``at`` stand, the
            # bridge is nonetheless alive.
            entry[name] = dict(old, seen=now)
    version = observed.get("version")
    if version is None:
        version = stored.get("claude_code_version")
    if isinstance(version, str) and version:
        entry["claude_code_version"] = version[:VERSION_MAX_CHARS]
    return entry


# --- files --------------------------------------------------------------

def sample_path(directory: Path | None = None) -> Path:
    return (state_dir() if directory is None else Path(directory)) / SAMPLE_NAME


def config_path(directory: Path | None = None) -> Path:
    return (state_dir() if directory is None else Path(directory)) / CONFIG_NAME


def _read_bounded_json(path: Path, limit: int):
    """Return the parsed document, or raise: ``FileNotFoundError``,
    ``OSError`` (unreadable) or ``ValueError`` (corrupt)."""
    size = path.stat().st_size
    if size > limit:
        raise ValueError("larger than the bound")
    raw = path.read_bytes()
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(type(error).__name__) from None


def load_sample(path: Path):
    """The stored document, or ``{}`` to start empty, or ``None`` when the
    file exists but cannot be read (then nothing must be written back)."""
    try:
        document = _read_bounded_json(path, SAMPLE_MAX_BYTES)
    except FileNotFoundError:
        return {}
    except ValueError as error:
        quarantine_corrupt(path, str(error))
        return {}
    except OSError:
        return None
    if not _well_formed_document(document):
        quarantine_corrupt(path, "not the v1 sample shape")
        return {}
    return document


def peek_sample(path: Path) -> tuple[str, dict | None]:
    """Read the sample without side effects, for the tokenserver and the
    doctor: ``("missing", None)``, ``("unreadable", None)``,
    ``("invalid", None)`` or ``("ok", document)``.  Never quarantines --
    the bridge owns the file and does that on its next run."""
    try:
        document = _read_bounded_json(Path(path), SAMPLE_MAX_BYTES)
    except FileNotFoundError:
        return "missing", None
    except ValueError:
        return "invalid", None
    except OSError:
        return "unreadable", None
    if not _well_formed_document(document):
        return "invalid", None
    return "ok", document


def summarize_sample(document: dict | None, now: int) -> dict:
    """The one account entry, validated and dated, for consumers.

    Returns ``{"status": "empty" | "stale" | "fresh", "ageS": int | None,
    "claudeCodeVersion": str | None, "windows": {name: {...}}}`` where
    ``windows`` holds only the windows that are well-formed and have not
    reset, each carrying its own ``age_s`` (since its ``seen``) and
    ``fresh`` flag -- freshness is per window, because a payload that
    keeps reporting only one window leaves the other's ``seen`` behind.
    ``ageS`` and ``status`` describe the newest window.
    """
    entry = None
    if isinstance(document, dict) and isinstance(document.get("accounts"), dict):
        entry = document["accounts"].get(ACCOUNT_KEY)
    entry = entry if isinstance(entry, dict) else {}
    windows = {}
    for name in WINDOWS:
        window = entry.get(name)
        if not _valid_stored_window(window, now, name):
            continue
        window = dict(window)
        window["age_s"] = max(0, int(now) - int(window["seen"]))
        window["fresh"] = window["age_s"] <= FRESH_S
        windows[name] = window
    version = entry.get("claude_code_version")
    if not (isinstance(version, str) and version and version.isprintable()):
        version = None
    else:
        version = version[:VERSION_MAX_CHARS]
    if not windows:
        return {"status": "empty", "ageS": None,
                "claudeCodeVersion": version, "windows": {}}
    age = min(window["age_s"] for window in windows.values())
    return {"status": "fresh" if age <= FRESH_S else "stale", "ageS": age,
            "claudeCodeVersion": version, "windows": windows}


def chained_command(config_dir: Path, path: Path | None = None):
    """The status line recorded for this config directory, or ``None``."""
    try:
        document = _read_bounded_json(
            config_path() if path is None else path, CONFIG_MAX_BYTES)
    except (OSError, ValueError):
        return None
    if not isinstance(document, dict) or document.get("v") != CONFIG_VERSION:
        return None
    dirs = document.get("dirs")
    if not isinstance(dirs, dict):
        return None
    record = dirs.get(config_dir_key(config_dir))
    if not isinstance(record, dict):
        return None
    command = record.get("chained_command")
    if isinstance(command, str) and command and "\n" not in command:
        return command
    return None


def atomic_write_private(path: Path, payload: bytes) -> None:
    """Write ``payload`` to ``path`` atomically, 0600, durable."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        fsync_parent(path)
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


class _Lock:
    """Non-blocking interprocess lock with a bounded wait."""

    def __init__(self, path: Path, wait_s: float = LOCK_WAIT_S,
                 sleep=time.sleep, clock=time.monotonic):
        self.path = Path(path)
        self.wait_s = wait_s
        self.sleep = sleep
        self.clock = clock
        self.handle = None

    def _try(self) -> bool:
        if fcntl is not None:
            try:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except OSError:
                return False
        if msvcrt is not None:
            try:
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
                return True
            except OSError:
                return False
        return True  # no locking primitive: behave as before locks existed

    def __enter__(self) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.handle = open(self.path, "a+")
        except OSError:
            return False
        deadline = self.clock() + self.wait_s
        while True:
            if self._try():
                return True
            if self.clock() >= deadline:
                self.handle.close()
                self.handle = None
                return False
            self.sleep(LOCK_RETRY_S)

    def __exit__(self, *exc) -> None:
        if self.handle is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            elif msvcrt is not None:
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        self.handle.close()
        self.handle = None


def record_sample(raw: bytes, *, now: int | None = None,
                  directory: Path | None = None,
                  lock_wait_s: float = LOCK_WAIT_S) -> str:
    """Merge one stdin document into the sample file.

    Returns a short outcome word for tests and the doctor: ``written``,
    ``unchanged``, ``rejected``, ``locked`` (another bridge held the lock
    for the whole bound), ``unreadable`` (the file exists but could not be
    read, so nothing was replaced).  Never raises.
    """
    now = int(time.time()) if now is None else int(now)
    try:
        observed = parse_payload(raw, now)
    except RejectedPayload:
        return "rejected"
    target = sample_path(directory)
    lock = _Lock(target.with_name(LOCK_NAME), wait_s=lock_wait_s)
    with lock as held:
        if not held:
            return "locked"
        document = load_sample(target)
        if document is None:
            return "unreadable"
        accounts = document.get("accounts") or {}
        before = accounts.get(ACCOUNT_KEY)
        after = merge_entry(before, observed, now)
        if not any(name in after for name in WINDOWS):
            after = None
        if after == before:
            return "unchanged"
        accounts = dict(accounts)
        if after is None:
            accounts.pop(ACCOUNT_KEY, None)
        else:
            accounts[ACCOUNT_KEY] = after
        payload = json.dumps({"v": SAMPLE_VERSION, "accounts": accounts},
                             separators=(",", ":"), sort_keys=True)
        try:
            atomic_write_private(target, payload.encode("utf-8"))
        except OSError:
            return "unreadable"
    return "written"


def run_chained(command: str | None, stdin_bytes: bytes,
                stdout=None, run=subprocess.run) -> int:
    """Run the user's own status line with the same stdin; its stdout and
    exit status pass through.  No command means an empty status line."""
    if not command:
        return 0
    if sys.platform == "win32":
        argv = ["cmd.exe", "/d", "/c", command]
    else:
        argv = ["/bin/sh", "-c", command]
    try:
        completed = run(argv, input=stdin_bytes, stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL, timeout=CHAINED_TIMEOUT_S,
                        check=False)
    except (OSError, subprocess.SubprocessError):
        return 0
    out = stdout if stdout is not None else sys.stdout.buffer
    try:
        out.write(completed.stdout or b"")
        out.flush()
    except OSError:
        pass
    return int(completed.returncode or 0)


def parse_argv(argv) -> dict:
    """``--state-dir PATH`` and ``--chained COMMAND``, both baked into the
    launcher at install time: the directory so the bridge and the record
    it reads never disagree, the previous status line so a record that
    cannot be read (corrupt, mid-rewrite, permissions) still leaves the
    user their own status line.  Anything else is ignored."""
    argv = list(argv or [])
    found = {"directory": None, "chained": None}
    index = 0
    while index + 1 < len(argv):
        flag, value = argv[index], argv[index + 1]
        if flag == "--state-dir" and value:
            found["directory"] = Path(value)
        elif flag == "--chained" and value and "\n" not in value:
            found["chained"] = value
        index += 2
    return found


def _directory_from_argv(argv) -> Path | None:
    return parse_argv(argv)["directory"]


def read_stdin(stdin) -> bytes:
    """Everything Claude Code wrote, up to the forward bound."""
    try:
        return stdin.read(STDIN_FORWARD_MAX_BYTES) or b""
    except OSError:
        return b""


def main(argv=None, *, stdin=None, stdout=None, env=None,
         now=None, directory=None) -> int:
    options = parse_argv(argv)
    if directory is None:
        directory = options["directory"]
    stdin = sys.stdin.buffer if stdin is None else stdin
    raw = read_stdin(stdin)
    try:
        record_sample(raw, now=now, directory=directory)
    except Exception:  # noqa: S110 - a bridge bug must not take the status line down
        pass
    config_dir = claude_config_dir(env)
    command = chained_command(
        config_dir, None if directory is None else config_path(directory))
    if command is None:
        # No readable record for this config directory: the launcher's
        # baked-in copy of the previous status line stands in.
        command = options["chained"]
    return run_chained(command, raw, stdout=stdout)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
