#!/usr/bin/env python3
"""Install and inspect the optional local VibePulse Codex integration."""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import secrets
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from typing import Callable, Sequence
import urllib.request


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from codex_mcp_timeout import (  # noqa: E402
    TOOL_TIMEOUT_SECONDS as CODEX_MCP_TOOL_TIMEOUT_SECONDS,
)
from tokenserver.vibepulse_config import (  # noqa: E402
    ConfigError,
    VibePulseConfig,
    config_lock,
    load_config,
    save_config,
)
from tokenserver.codex_command import resolve_codex_executable  # noqa: E402
from tokenserver.tokenserver import _read_source_fingerprint  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[1]
MARKETPLACE_NAME = "torget"
TOKEN_SERVER_URL = "http://127.0.0.1:8737/"
MAX_DIAGNOSTIC_BYTES = 16 * 1024
MAX_COMMAND_OUTPUT_BYTES = 16 * 1024
COMMAND_TIMEOUT_SECONDS = 15
NETWORK_TIMEOUT_SECONDS = 2
PIPE_JOIN_TIMEOUT_SECONDS = 1
_AUTO = object()
_RELAY_BLOCK_BEGIN = "/* VIBEPULSE INTERACTION RELAY BEGIN */"
_RELAY_BLOCK_END = "/* VIBEPULSE INTERACTION RELAY END */"
_MAILBOX_RE = re.compile(r"vp_[A-Za-z0-9_-]{16}\Z")
_BEARER_RE = re.compile(r"[A-Za-z0-9_-]{43}\Z")

_KNOWN_ABSENT = {
    ("plugin", "marketplace", "remove", "torget"): re.compile(
        r"\AError: marketplace `torget` is not configured or installed\Z"),
}
_CODEX_VERSION = re.compile(
    r"\A(?:codex|codex-cli) [0-9]+\.[0-9]+\.[0-9]+"
    r"(?:-[0-9A-Za-z.-]+)?\n?\Z")
_PYTHON_PROBE = (
    "import sys; print('vibepulse-python-3.11+'"
    "if sys.version_info >= (3, 11) else 'unsupported')")


@dataclass(frozen=True)
class _CommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class _ExternalState:
    mcp: bool
    plugin: bool
    marketplace: bool


class _ConfigPublishError(ConfigError):
    def __init__(self, message: str, *, irrecoverable: bool) -> None:
        super().__init__(message)
        self.irrecoverable = irrecoverable


def default_config_path() -> Path:
    """Return the tokenserver's existing saved interaction config path."""
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        return base / "VibePulse" / "config.json"
    return (Path.home() / "Library" / "Application Support" / "VibePulse" /
            "config.json")


def plan_codex_install(
        repo_root: Path, python: Path, codex: Path,
        marketplace_name: str = MARKETPLACE_NAME) -> list[list[str]]:
    """Build the exact shell-free Codex installation plan."""
    root = Path(repo_root).resolve()
    python_path = str(Path(python).resolve())
    codex_path = str(Path(codex).resolve())
    marketplace_root = root / ".agents" / "plugins"
    mcp_server = (marketplace_root / "plugins" / "vibepulse" / "scripts" /
                  "mcp_server.py")
    timeout_helper = root / "tools" / "codex_mcp_timeout.py"
    return [
        [codex_path, "plugin", "marketplace", "add",
         str(root)],
        [codex_path, "plugin", "add",
         f"vibepulse@{marketplace_name}"],
        [codex_path, "mcp", "remove", "vibepulse"],
        [codex_path, "mcp", "add", "vibepulse", "--", python_path,
         str(mcp_server)],
        [python_path, str(timeout_helper)],
    ]


def plan_codex_uninstall(
        codex: Path, marketplace_name: str = MARKETPLACE_NAME,
        ) -> list[list[str]]:
    codex_path = str(Path(codex).resolve())
    return [
        [codex_path, "mcp", "remove", "vibepulse"],
        [codex_path, "plugin", "remove",
         f"vibepulse@{marketplace_name}"],
        [codex_path, "plugin", "marketplace", "remove", marketplace_name],
    ]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    install = commands.add_parser(
        "install", help="install the package and save explicit providers")
    install.add_argument(
        "--providers", choices=("off", "claude", "codex", "both"),
        help="providers to enable; omitted non-interactively means off")
    detail = install.add_mutually_exclusive_group()
    detail.add_argument(
        "--detail", dest="detail", action="store_true",
        help="send bounded question/command detail to the local panel")
    detail.add_argument(
        "--no-detail", dest="detail", action="store_false",
        help="keep question/command detail on this computer")
    install.set_defaults(detail=None)
    install.add_argument(
        "--legacy-claude-panel-v1",
        action=argparse.BooleanOptionalAction, default=None,
        help="enable or disable INSECURE compatibility for old Claude panel "
             "firmware; default off and never applies to Codex")

    commands.add_parser("status", help="show saved provider switches")
    commands.add_parser("doctor", help="run read-only local diagnostics")

    disable = commands.add_parser(
        "disable", help="disable one or all provider routes")
    disable.add_argument("target", choices=("codex", "claude", "all"))

    uninstall = commands.add_parser(
        "uninstall", help="remove only the selected Codex integration")
    uninstall.add_argument("target", choices=("codex",))

    relay = commands.add_parser(
        "relay", help="manage the optional encrypted interaction relay")
    relay_commands = relay.add_subparsers(
        dest="relay_command", required=True)
    relay_install = relay_commands.add_parser(
        "install", help="deploy and opt in to the E2E cloud mailbox")
    relay_install.add_argument(
        "--url", required=True, metavar="HTTPS_ORIGIN",
        help="public HTTPS origin of this user-owned Worker")
    relay_install.add_argument(
        "--yes-e2e-cloud", action="store_true",
        help="confirm that bounded encrypted panel views and verdicts may "
             "cross the user-owned cloud mailbox")
    relay_commands.add_parser(
        "status", help="show relay switch and local credential readiness")
    relay_commands.add_parser(
        "doctor", help="validate relay setup without changing anything")
    relay_commands.add_parser(
        "disable", help="turn off relay traffic but keep its credentials")
    relay_status_enable = relay_commands.add_parser(
        "enable-status",
        help="opt in to E2E-encrypted live agent status through the relay")
    relay_status_enable.add_argument(
        "--yes-e2e-cloud", action="store_true",
        help="confirm that minimized E2E-encrypted live status may cross "
             "the user-owned cloud mailbox")
    relay_commands.add_parser(
        "disable-status",
        help="turn off live status relay traffic but keep credentials")
    relay_uninstall = relay_commands.add_parser(
        "uninstall", help="remove only encrypted-relay settings")
    worker = relay_uninstall.add_mutually_exclusive_group()
    worker.add_argument(
        "--delete-worker", action="store_true", dest="delete_worker",
        help="also delete the user-owned Cloudflare Worker")
    worker.add_argument(
        "--keep-worker", action="store_false", dest="delete_worker",
        help="leave the Cloudflare Worker deployed")
    relay_uninstall.set_defaults(delete_worker=None)
    return parser


def _interactive_providers(input_fn: Callable[[str], str]) -> str:
    prompt = (
        "Choose VibePulse panel providers "
        "[off/claude/codex/both] (default off): ")
    while True:
        choice = input_fn(prompt).strip().lower() or "off"
        if choice in {"off", "claude", "codex", "both"}:
            return choice


def _interactive_detail(input_fn: Callable[[str], str]) -> bool:
    prompt = (
        "Send bounded question and command detail to the local panel? "
        "[y/N]: ")
    while True:
        choice = input_fn(prompt).strip().lower()
        if choice in {"", "n", "no"}:
            return False
        if choice in {"y", "yes"}:
            return True


def _chosen_config(providers: str, detail: bool,
                   legacy_claude_panel_v1: bool = False,
                   saved: VibePulseConfig | None = None) -> VibePulseConfig:
    saved = VibePulseConfig() if saved is None else saved
    return VibePulseConfig(
        claude_interactions=providers in {"claude", "both"},
        codex_interactions=providers in {"codex", "both"},
        interaction_detail=detail,
        legacy_claude_panel_v1=(legacy_claude_panel_v1 and
                                providers in {"claude", "both"}),
        interaction_relay=saved.interaction_relay,
        agent_status_relay=saved.agent_status_relay,
        interaction_relay_url=saved.interaction_relay_url,
        interaction_mailbox=saved.interaction_mailbox,
    )


def _disabled_config(saved: VibePulseConfig, target: str) -> VibePulseConfig:
    return VibePulseConfig(
        claude_interactions=(saved.claude_interactions
                             if target == "codex" else False),
        codex_interactions=(saved.codex_interactions
                            if target == "claude" else False),
        interaction_detail=saved.interaction_detail,
        legacy_claude_panel_v1=(saved.legacy_claude_panel_v1
                                if target == "codex" else False),
        interaction_relay=saved.interaction_relay,
        agent_status_relay=saved.agent_status_relay,
        interaction_relay_url=saved.interaction_relay_url,
        interaction_mailbox=saved.interaction_mailbox,
    )


def _disable(path: Path, target: str) -> None:
    with config_lock(path):
        saved = load_config(path)
        save_config(path, _disabled_config(saved, target))


def _relay_config(saved: VibePulseConfig, *, enabled: bool,
                  url=None, mailbox=None, clear=False) -> VibePulseConfig:
    return VibePulseConfig(
        claude_interactions=saved.claude_interactions,
        codex_interactions=saved.codex_interactions,
        interaction_detail=saved.interaction_detail,
        legacy_claude_panel_v1=saved.legacy_claude_panel_v1,
        interaction_relay=enabled,
        agent_status_relay=saved.agent_status_relay,
        interaction_relay_url=(None if clear else
                               (saved.interaction_relay_url
                                if url is None else url)),
        interaction_mailbox=(None if clear else
                             (saved.interaction_mailbox
                              if mailbox is None else mailbox)),
    )


def _agent_status_relay_config(
        saved: VibePulseConfig, *, enabled: bool) -> VibePulseConfig:
    return VibePulseConfig(
        claude_interactions=saved.claude_interactions,
        codex_interactions=saved.codex_interactions,
        interaction_detail=saved.interaction_detail,
        legacy_claude_panel_v1=saved.legacy_claude_panel_v1,
        interaction_relay=saved.interaction_relay,
        agent_status_relay=enabled,
        interaction_relay_url=saved.interaction_relay_url,
        interaction_mailbox=saved.interaction_mailbox,
    )


def _valid_bearer(value: str) -> bool:
    if not isinstance(value, str) or _BEARER_RE.fullmatch(value) is None:
        return False
    try:
        decoded = base64.b64decode(
            value + "=", altchars=b"-_", validate=True)
    except (ValueError, TypeError):
        return False
    return (len(decoded) == 32 and
            base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii") ==
            value)


def _relay_secrets_block(url: str, mailbox: str, panel_token: str) -> str:
    # VibePulseConfig validates the public values; the token validator keeps
    # quote/backslash/newline characters out of this C-string boundary.
    VibePulseConfig(
        interaction_relay_url=url, interaction_mailbox=mailbox)
    if not _valid_bearer(panel_token):
        raise ConfigError("invalid panel relay token")
    return (
        f"{_RELAY_BLOCK_BEGIN}\n"
        f'#define TK_VIBEPULSE_INTERACTION_RELAY_URL "{url}"\n'
        f'#define TK_VIBEPULSE_INTERACTION_MAILBOX "{mailbox}"\n'
        f'#define TK_VIBEPULSE_INTERACTION_PANEL_TOKEN "{panel_token}"\n'
        f"{_RELAY_BLOCK_END}\n"
    )


def _without_relay_block(text: str) -> str:
    if not isinstance(text, str):
        raise ConfigError("secrets.h must be UTF-8 text")
    begin_count = text.count(_RELAY_BLOCK_BEGIN)
    end_count = text.count(_RELAY_BLOCK_END)
    if begin_count == 0 and end_count == 0:
        return text
    if begin_count != 1 or end_count != 1:
        raise ConfigError("secrets.h has a malformed relay block")
    start = text.index(_RELAY_BLOCK_BEGIN)
    end_start = text.index(_RELAY_BLOCK_END)
    if end_start < start:
        raise ConfigError("secrets.h has a malformed relay block")
    end = end_start + len(_RELAY_BLOCK_END)
    if end < len(text) and text[end] == "\n":
        end += 1
    return text[:start] + text[end:]


def _with_relay_block(text: str, block: str) -> str:
    base = _without_relay_block(text)
    if base and not base.endswith("\n"):
        base += "\n"
    return base + block


def _read_small_regular(path: Path, limit: int = 64 * 1024) -> bytes:
    path = Path(path)
    try:
        before = os.lstat(path)
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise ConfigError("path must be a regular non-symlink file")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | \
            getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(path, flags)
        try:
            descriptor = os.fstat(fd)
            after = os.lstat(path)
            if (not stat.S_ISREG(descriptor.st_mode) or
                    stat.S_ISLNK(after.st_mode) or
                    not os.path.samestat(before, descriptor) or
                    not os.path.samestat(descriptor, after)):
                raise ConfigError("path changed while opening")
            value = os.read(fd, limit + 1)
        finally:
            os.close(fd)
    except FileNotFoundError:
        raise
    except ConfigError:
        raise
    except OSError as exc:
        raise ConfigError("cannot read private setup file") from exc
    if len(value) > limit:
        raise ConfigError("private setup file is too large")
    return value


def _atomic_private_write(path: Path, payload: bytes) -> None:
    path = Path(path)
    if not isinstance(payload, bytes):
        raise ConfigError("private payload must be bytes")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(path.parent, 0o700)
    try:
        existing = os.lstat(path)
    except FileNotFoundError:
        existing = None
    if existing is not None and stat.S_ISLNK(existing.st_mode):
        raise ConfigError("private destination must not be a symlink")
    temporary = None
    try:
        fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(name)
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            raise
        os.replace(temporary, path)
        temporary = None
        if os.name == "posix":
            os.chmod(path, 0o600)
    except OSError as exc:
        raise ConfigError("cannot save private setup file") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def _device_key_from_secrets(text: str) -> str | None:
    values = re.findall(
        r'^\s*#\s*define\s+TK_VIBEPULSE_DEVICE_KEY\s+"([^"]*)"\s*$',
        text, flags=re.MULTILINE)
    if len(values) != 1 or re.fullmatch(
            r"[0-9A-Fa-f]{64}", values[0]) is None:
        return None
    return values[0]


def _wrangler_path(service_dir: Path) -> Path | None:
    service = Path(service_dir)
    candidate = service / "node_modules" / ".bin" / (
        "wrangler.cmd" if os.name == "nt" else "wrangler")
    try:
        service_root = service.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(service_root / "node_modules")
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            return None
    except (OSError, RuntimeError, ValueError):
        return None
    return candidate


def _known_absent(argv: Sequence[str], stderr: str) -> bool:
    command = tuple(argv[1:])
    pattern = _KNOWN_ABSENT.get(command)
    if pattern is None:
        return False
    normalized = stderr.replace("\r\n", "\n")
    if normalized.endswith("\n"):
        normalized = normalized[:-1]
    return pattern.fullmatch(normalized) is not None


def _close_process_pipes(process, *, only_if_idle: bool = False,
                         threads: Sequence[threading.Thread] = ()) -> None:
    if only_if_idle and any(thread.is_alive() for thread in threads):
        return
    for pipe in (getattr(process, "stdout", None),
                 getattr(process, "stderr", None)):
        if pipe is not None:
            try:
                pipe.close()
            except OSError:
                pass


def _terminate_process_tree(process) -> None:
    """Terminate a probe and descendants without invoking a shell."""
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            if process.poll() is None:
                try:
                    process.kill()
                except OSError:
                    pass
    else:
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=PIPE_JOIN_TIMEOUT_SECONDS,
                check=False, shell=False)
        except (OSError, subprocess.SubprocessError):
            if process.poll() is None:
                try:
                    process.kill()
                except OSError:
                    pass
    try:
        process.wait(timeout=PIPE_JOIN_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError):
        if process.poll() is None:
            try:
                process.kill()
                process.wait(timeout=PIPE_JOIN_TIMEOUT_SECONDS)
            except (OSError, subprocess.SubprocessError):
                pass


def _decode_command_result(
        returncode: int, captured: dict[str, bytearray],
        reader_errors: Sequence[BaseException] = (),
        ) -> _CommandResult | None:
    if (reader_errors or
            len(captured["stdout"]) > MAX_COMMAND_OUTPUT_BYTES or
            len(captured["stderr"]) > MAX_COMMAND_OUTPUT_BYTES):
        return None
    try:
        stdout = bytes(captured["stdout"]).decode("utf-8", errors="strict")
        stderr = bytes(captured["stderr"]).decode("utf-8", errors="strict")
    except UnicodeError:
        return None
    return _CommandResult(returncode, stdout, stderr)


def _capture_chunk(captured: dict[str, bytearray], label: str,
                   chunk: bytes) -> None:
    remaining = MAX_COMMAND_OUTPUT_BYTES + 1 - len(captured[label])
    if remaining > 0:
        captured[label].extend(chunk[:remaining])


def _bounded_posix_process(argv: Sequence[str]) -> _CommandResult | None:
    """Capture raw pipes without ever waiting for inherited-writer EOF."""
    captured = {"stdout": bytearray(), "stderr": bytearray()}
    reads: dict[int, str] = {}
    writes: list[int] = []
    process = None
    selector = selectors.DefaultSelector()
    timed_out = False
    returncode = None
    drain_deadline = None

    try:
        for label in ("stdout", "stderr"):
            read_fd, write_fd = os.pipe()
            reads[read_fd] = label
            writes.append(write_fd)
            os.set_blocking(read_fd, False)
            selector.register(read_fd, selectors.EVENT_READ, label)
        process = subprocess.Popen(
            [str(value) for value in argv], stdin=subprocess.DEVNULL,
            stdout=writes[0], stderr=writes[1], shell=False,
            close_fds=True, start_new_session=True)
    except (OSError, subprocess.SubprocessError):
        selector.close()
        for read_fd in tuple(reads):
            try:
                os.close(read_fd)
            except OSError:
                pass
        return None
    finally:
        for write_fd in writes:
            try:
                os.close(write_fd)
            except OSError:
                pass

    deadline = time.monotonic() + COMMAND_TIMEOUT_SECONDS
    try:
        while True:
            now = time.monotonic()
            polled = process.poll()
            if polled is not None and returncode is None:
                returncode = polled
                drain_deadline = now + PIPE_JOIN_TIMEOUT_SECONDS
            if polled is None and not timed_out and now >= deadline:
                timed_out = True
                _terminate_process_tree(process)
                returncode = process.poll()
                drain_deadline = time.monotonic() + PIPE_JOIN_TIMEOUT_SECONDS

            if timed_out or returncode is not None:
                assert drain_deadline is not None
                if not reads:
                    break
                remaining = drain_deadline - time.monotonic()
                if remaining <= 0:
                    break
            else:
                remaining = deadline - time.monotonic()
            wait = max(0.0, min(0.05, remaining))
            events = selector.select(wait) if reads else ()
            if not reads and wait:
                time.sleep(wait)
            for key, _mask in events:
                fd = key.fd
                while True:
                    try:
                        chunk = os.read(fd, 8192)
                    except BlockingIOError:
                        break
                    except OSError:
                        chunk = b""
                    if not chunk:
                        try:
                            selector.unregister(fd)
                        except (KeyError, OSError):
                            pass
                        try:
                            os.close(fd)
                        except OSError:
                            pass
                        reads.pop(fd, None)
                        break
                    _capture_chunk(captured, key.data, chunk)

            if not timed_out and returncode is None:
                returncode = process.poll()
                if returncode is not None:
                    drain_deadline = (time.monotonic() +
                                      PIPE_JOIN_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError):
        _terminate_process_tree(process)
        return None
    except BaseException:
        _terminate_process_tree(process)
        raise
    finally:
        selector.close()
        for read_fd in tuple(reads):
            try:
                os.close(read_fd)
            except OSError:
                pass

    if timed_out:
        return None
    if returncode is None:
        returncode = process.poll()
    if returncode is None:
        _terminate_process_tree(process)
        return None
    return _decode_command_result(returncode, captured)


def _bounded_thread_process(argv: Sequence[str]) -> _CommandResult | None:
    """Windows fallback: bounded daemon readers; never close under a reader."""
    process = None
    threads: list[threading.Thread] = []
    captured = {"stdout": bytearray(), "stderr": bytearray()}
    reader_errors: list[BaseException] = []

    def drain(label: str, pipe) -> None:
        try:
            while True:
                chunk = pipe.read(8192)
                if not chunk:
                    return
                _capture_chunk(captured, label, chunk)
        except BaseException as exc:
            reader_errors.append(exc)
        finally:
            try:
                pipe.close()
            except OSError:
                pass

    try:
        process_kwargs = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "shell": False,
            "close_fds": True,
        }
        process_kwargs["creationflags"] = getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        process = subprocess.Popen(
            [str(value) for value in argv], **process_kwargs)
        assert process.stdout is not None and process.stderr is not None
        for label, pipe in (("stdout", process.stdout),
                            ("stderr", process.stderr)):
            thread = threading.Thread(
                target=drain, args=(label, pipe),
                name=f"vibepulse-drain-{label}", daemon=True)
            thread.start()
            threads.append(thread)
        try:
            returncode = process.wait(timeout=COMMAND_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            _terminate_process_tree(process)
            return None
    except (OSError, subprocess.SubprocessError):
        return None
    except BaseException:
        if process is not None:
            _terminate_process_tree(process)
        raise
    finally:
        for thread in threads:
            thread.join(PIPE_JOIN_TIMEOUT_SECONDS)
        if process is not None:
            if any(thread.is_alive() for thread in threads):
                _terminate_process_tree(process)
            _close_process_pipes(
                process, only_if_idle=True, threads=threads)
        for thread in threads:
            if thread.is_alive():
                thread.join(PIPE_JOIN_TIMEOUT_SECONDS)

    if any(thread.is_alive() for thread in threads):
        return None

    return _decode_command_result(returncode, captured, reader_errors)


def _bounded_process(argv: Sequence[str]) -> _CommandResult | None:
    """Run one argv with bounded time and retained output."""
    if os.name == "posix":
        return _bounded_posix_process(argv)
    return _bounded_thread_process(argv)


def _invoke(argv: Sequence[str], run) -> _CommandResult | None:
    if run is _AUTO:
        return _bounded_process(argv)
    try:
        completed = run(
            [str(value) for value in argv], capture_output=True, text=True,
            timeout=COMMAND_TIMEOUT_SECONDS, check=False, shell=False)
    except (OSError, subprocess.SubprocessError):
        return None
    returncode = getattr(completed, "returncode", None)
    stdout = getattr(completed, "stdout", None)
    stderr = getattr(completed, "stderr", None)
    if (type(returncode) is not int or not isinstance(stdout, str) or
            not isinstance(stderr, str)):
        return None
    try:
        if (len(stdout.encode("utf-8")) > MAX_COMMAND_OUTPUT_BYTES or
                len(stderr.encode("utf-8")) > MAX_COMMAND_OUTPUT_BYTES):
            return None
    except UnicodeError:
        return None
    return _CommandResult(returncode, stdout, stderr)


def _private_relay_token(path: Path) -> str | None:
    try:
        before = os.lstat(path)
        if os.name == "posix" and stat.S_IMODE(before.st_mode) != 0o600:
            return None
        raw = _read_small_regular(path, 256)
    except (FileNotFoundError, ConfigError, OSError):
        return None
    if raw.endswith(b"\n"):
        raw = raw[:-1]
    try:
        value = raw.decode("ascii", "strict")
    except UnicodeError:
        return None
    return value if _valid_bearer(value) else None


def _relay_block_matches(text: str, config: VibePulseConfig) -> bool:
    try:
        start = text.index(_RELAY_BLOCK_BEGIN)
        end = text.index(_RELAY_BLOCK_END, start) + len(_RELAY_BLOCK_END)
    except ValueError:
        return False
    if text.count(_RELAY_BLOCK_BEGIN) != 1 or \
            text.count(_RELAY_BLOCK_END) != 1:
        return False
    block = text[start:end]
    values = {}
    for name in (
            "TK_VIBEPULSE_INTERACTION_RELAY_URL",
            "TK_VIBEPULSE_INTERACTION_MAILBOX",
            "TK_VIBEPULSE_INTERACTION_PANEL_TOKEN"):
        matches = re.findall(
            rf'^\s*#\s*define\s+{name}\s+"([^"]*)"\s*$',
            block, flags=re.MULTILINE)
        if len(matches) != 1:
            return False
        values[name] = matches[0]
    return (
        values["TK_VIBEPULSE_INTERACTION_RELAY_URL"] ==
        config.interaction_relay_url and
        values["TK_VIBEPULSE_INTERACTION_MAILBOX"] ==
        config.interaction_mailbox and
        _valid_bearer(
            values["TK_VIBEPULSE_INTERACTION_PANEL_TOKEN"]) and
        _device_key_from_secrets(text) is not None)


def _relay_install(
        *, path: Path, url: str, consent: bool, token_path: Path,
        secrets_path: Path, service_dir: Path, token_urlsafe, run,
        stdout) -> bool:
    with config_lock(_setup_transaction_path(path)):
        with config_lock(path):
            snapshot = load_config(path)
        if snapshot.interaction_relay:
            print("FIX Relay is already installed; use relay status or "
                  "uninstall before rotating credentials", file=stdout)
            return False
        if not consent:
            print("FIX E2E cloud consent is required before relay install",
                  file=stdout)
            return False
        if not (snapshot.claude_interactions or
                snapshot.codex_interactions):
            print("FIX Enable at least one provider before relay install",
                  file=stdout)
            return False
        if not snapshot.interaction_detail:
            print("FIX Enable detail before relay install; this is the "
                  "bounded view that will be encrypted", file=stdout)
            return False
        try:
            # Construction is the shared strict HTTPS-origin validator.
            VibePulseConfig(interaction_relay_url=url)
            secrets_raw = _read_small_regular(secrets_path)
            secrets_text = secrets_raw.decode("utf-8", "strict")
        except (ConfigError, FileNotFoundError, UnicodeError):
            print("FIX secrets.h and the HTTPS relay URL must be valid",
                  file=stdout)
            return False
        if _device_key_from_secrets(secrets_text) is None:
            print("FIX A valid 64-hex device key is required in secrets.h",
                  file=stdout)
            return False
        if (Path(token_path).exists() or Path(token_path).is_symlink() or
                _RELAY_BLOCK_BEGIN in secrets_text or
                _RELAY_BLOCK_END in secrets_text):
            print("FIX Existing relay credentials require relay status or "
                  "uninstall before install", file=stdout)
            return False
        wrangler = _wrangler_path(service_dir)
        if wrangler is None:
            print("FIX Pinned local Wrangler is missing; run npm ci in "
                  "tools/interaction-relay", file=stdout)
            return False
        try:
            mailbox = "vp_" + token_urlsafe(12)
            mac_token = token_urlsafe(32)
            panel_token = token_urlsafe(32)
        except Exception:
            print("FIX Secure credential generation failed", file=stdout)
            return False
        if (_MAILBOX_RE.fullmatch(mailbox) is None or
                not _valid_bearer(mac_token) or
                not _valid_bearer(panel_token) or
                mac_token == panel_token):
            print("FIX Secure credential generation failed", file=stdout)
            return False
        target = _relay_config(
            snapshot, enabled=True, url=url, mailbox=mailbox)
        block = _relay_secrets_block(url, mailbox, panel_token)

        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        secret_temp = None
        cloud_touched = False
        try:
            fd, name = tempfile.mkstemp(
                prefix=".interaction-relay-secrets.", dir=path.parent)
            secret_temp = Path(name)
            if hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(json.dumps(
                    {"MAC_TOKEN": mac_token, "PANEL_TOKEN": panel_token},
                    sort_keys=True, separators=(",", ":"),
                ).encode("ascii") + b"\n")
                handle.flush()
                os.fsync(handle.fileno())
            commands = (
                [str(wrangler), "--cwd", str(service_dir),
                 "secret", "bulk", str(secret_temp)],
                [str(wrangler), "--cwd", str(service_dir),
                 "deploy", "--var", f"MAILBOX_ID:{mailbox}"],
            )
            for command in commands:
                cloud_touched = True
                if not _command_ok(_invoke(command, run), command):
                    raise ConfigError("Cloudflare relay deployment failed")

            backup = path.parent / "secrets.h.before-interaction-relay"
            _atomic_private_write(backup, secrets_raw)
            _atomic_private_write(
                secrets_path,
                _with_relay_block(secrets_text, block).encode("utf-8"))
            _atomic_private_write(
                token_path, (mac_token + "\n").encode("ascii"))
            _publish_config(path, snapshot, target)
        except BaseException:
            try:
                _atomic_private_write(secrets_path, secrets_raw)
            except BaseException:
                pass
            try:
                if Path(token_path).exists() or Path(token_path).is_symlink():
                    Path(token_path).unlink()
            except OSError:
                pass
            if cloud_touched:
                _invoke([str(wrangler), "--cwd", str(service_dir),
                         "delete", "--force"], run)
            print("FIX Relay install failed; local secrets and routing were "
                  "not enabled", file=stdout)
            return False
        finally:
            if secret_temp is not None:
                try:
                    secret_temp.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    pass
        print("PASS Encrypted interaction relay installed and enabled",
              file=stdout)
        return True


def _relay_disable(path: Path, stdout) -> bool:
    with config_lock(path):
        saved = load_config(path)
        save_config(path, _relay_config(saved, enabled=False))
    print("PASS Encrypted interaction relay disabled; credentials kept",
          file=stdout)
    return True


def _relay_ownership_probe(
        config: VibePulseConfig, token: str, urlopen) -> bool:
    if (config.interaction_relay_url is None or
            config.interaction_mailbox is None or
            not _valid_bearer(token)):
        return False
    endpoint = (
        config.interaction_relay_url.rstrip("/") +
        f"/v1/mailboxes/{config.interaction_mailbox}/verdicts")
    request = urllib.request.Request(
        endpoint,
        headers={
            "Accept": "application/json",
            "Authorization": "Bearer " + token,
        },
        method="GET")
    open_url = _default_urlopen if urlopen is _AUTO else urlopen
    try:
        with open_url(
                request, timeout=NETWORK_TIMEOUT_SECONDS) as response:
            status_code = getattr(response, "status", None)
            if status_code == 204:
                return response.read(1) == b""
            if status_code != 200 or not _response_content_type(response):
                return False
            raw = response.read(MAX_DIAGNOSTIC_BYTES + 1)
        if not isinstance(raw, bytes) or len(raw) > MAX_DIAGNOSTIC_BYTES:
            return False
        payload = _strict_json(raw.decode("utf-8", errors="strict"))
        return (isinstance(payload, dict) and set(payload) == {"verdicts"} and
                isinstance(payload["verdicts"], list))
    except (OSError, UnicodeError, ValueError, RecursionError, TimeoutError):
        return False


def _relay_enable_status(
        *, path: Path, consent: bool, token_path: Path,
        secrets_path: Path, urlopen, stdout) -> bool:
    if not consent:
        print("FIX E2E cloud consent is required before live status is "
              "enabled", file=stdout)
        return False
    with config_lock(_setup_transaction_path(path)):
        with config_lock(path):
            snapshot = load_config(path)
        token = _private_relay_token(token_path)
        try:
            text = _read_small_regular(
                secrets_path).decode("utf-8", "strict")
            panel_ready = _relay_block_matches(text, snapshot)
        except (FileNotFoundError, ConfigError, UnicodeError):
            panel_ready = False
        if token is None or not panel_ready:
            print("FIX Existing private relay credentials are missing or "
                  "unsafe", file=stdout)
            return False
        if not _relay_ownership_probe(snapshot, token, urlopen):
            print("FIX Relay mailbox ownership could not be verified; live "
                  "status remains off", file=stdout)
            return False
        try:
            _publish_config(
                path, snapshot,
                _agent_status_relay_config(snapshot, enabled=True))
        except BaseException:
            print("FIX Live status consent could not be saved safely",
                  file=stdout)
            return False
    print("PASS E2E-encrypted live agent status enabled", file=stdout)
    return True


def _relay_disable_status(path: Path, stdout) -> bool:
    with config_lock(path):
        saved = load_config(path)
        save_config(path, _agent_status_relay_config(saved, enabled=False))
    print("PASS Live agent status relay disabled; credentials kept",
          file=stdout)
    return True


def _relay_uninstall(
        *, path: Path, delete_worker: bool, token_path: Path,
        secrets_path: Path, service_dir: Path, run, stdout) -> bool:
    with config_lock(_setup_transaction_path(path)):
        with config_lock(path):
            snapshot = load_config(path)
        wrangler = _wrangler_path(service_dir)
        if delete_worker:
            if wrangler is None:
                print("FIX Pinned local Wrangler is missing; Worker was not "
                      "deleted and local credentials were kept", file=stdout)
                return False
            command = [str(wrangler), "--cwd", str(service_dir),
                       "delete", "--force"]
            if not _command_ok(_invoke(command, run), command):
                print("FIX Worker deletion failed; local credentials were "
                      "kept so it can be retried", file=stdout)
                return False

        try:
            try:
                secrets_raw = _read_small_regular(secrets_path)
                secrets_text = secrets_raw.decode("utf-8", "strict")
            except FileNotFoundError:
                secrets_raw = None
                secrets_text = None
            token_raw = None
            if Path(token_path).exists() or Path(token_path).is_symlink():
                token_raw = _read_small_regular(token_path, 256)
            target = _relay_config(
                snapshot, enabled=False, clear=True)
            target = _agent_status_relay_config(target, enabled=False)
            if secrets_raw is not None:
                backup = path.parent / "secrets.h.before-interaction-relay"
                _atomic_private_write(backup, secrets_raw)
                _atomic_private_write(
                    secrets_path,
                    _without_relay_block(secrets_text).encode("utf-8"))
            if token_raw is not None:
                Path(token_path).unlink()
            _publish_config(path, snapshot, target)
        except BaseException:
            try:
                if secrets_raw is not None:
                    _atomic_private_write(secrets_path, secrets_raw)
                if token_raw is not None:
                    _atomic_private_write(token_path, token_raw)
            except BaseException:
                pass
            print("FIX Relay uninstall could not safely update local files",
                  file=stdout)
            return False
        print("PASS Removed only encrypted interaction relay settings",
              file=stdout)
        return True


def _relay_status(config: VibePulseConfig, *, token_path: Path,
                  secrets_path: Path, stdout) -> None:
    print(f"Interaction relay: {'ON' if config.interaction_relay else 'OFF'}",
          file=stdout)
    print(f"Agent status relay: "
          f"{'ON' if config.agent_status_relay else 'OFF'}", file=stdout)
    print("Relay endpoint: " + (
        "CONFIGURED" if config.interaction_relay_url else "MISSING"),
        file=stdout)
    print("Relay mailbox: " + (
        "CONFIGURED" if config.interaction_mailbox else "MISSING"),
        file=stdout)
    print("Mac relay token: " + (
        "READY" if _private_relay_token(token_path) else "MISSING/UNSAFE"),
        file=stdout)
    try:
        text = _read_small_regular(secrets_path).decode("utf-8", "strict")
        panel_ready = _relay_block_matches(text, config)
    except (FileNotFoundError, ConfigError, UnicodeError):
        panel_ready = False
    print("Panel relay block: " + ("READY" if panel_ready else "MISSING"),
          file=stdout)


def _relay_doctor(config: VibePulseConfig, *, token_path: Path,
                  secrets_path: Path, service_dir: Path, stdout) -> bool:
    if not (config.interaction_relay or config.agent_status_relay):
        print("OFF Encrypted relay traffic intentionally disabled",
              file=stdout)
        return True
    checks = []
    if config.interaction_relay:
        checks.append((bool(config.claude_interactions or
                            config.codex_interactions), "provider"))
        checks.append((config.interaction_detail,
                       "bounded interaction detail"))
    checks.append((_private_relay_token(token_path) is not None,
                   "private Mac relay token"))
    try:
        text = _read_small_regular(secrets_path).decode("utf-8", "strict")
        panel_ok = _relay_block_matches(text, config)
    except (FileNotFoundError, ConfigError, UnicodeError):
        panel_ok = False
    checks.append((panel_ok, "matching panel relay block and device key"))
    checks.append((_wrangler_path(service_dir) is not None,
                   "pinned local Wrangler"))
    for passed, label in checks:
        print(f"{'PASS' if passed else 'FIX'} Relay {label}", file=stdout)
    return all(passed for passed, _label in checks)


def _print_status(config: VibePulseConfig, stdout) -> None:
    def switch(value: bool) -> str:
        return "ON" if value else "OFF"

    print(f"Claude: {switch(config.claude_interactions)}", file=stdout)
    print(f"Codex: {switch(config.codex_interactions)}", file=stdout)
    print(f"Detail: {switch(config.interaction_detail)}", file=stdout)
    legacy = switch(config.legacy_claude_panel_v1)
    suffix = " (INSECURE COMPATIBILITY MODE)" \
        if config.legacy_claude_panel_v1 else ""
    print(f"Legacy Claude panel v1: {legacy}{suffix}", file=stdout)
    print(f"Interaction relay: {switch(config.interaction_relay)}",
          file=stdout)
    print(f"Agent status relay: {switch(config.agent_status_relay)}",
          file=stdout)


def _reject_json_constant(value):
    raise ValueError(f"non-standard JSON constant: {value}")


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _strict_json(text: str):
    if not isinstance(text, str):
        raise ValueError("JSON input must be text")
    try:
        encoded_size = len(text.encode("utf-8"))
    except UnicodeError as exc:
        raise ValueError("JSON input is not valid UTF-8") from exc
    if encoded_size > MAX_COMMAND_OUTPUT_BYTES:
        raise ValueError("JSON output is too large")
    try:
        return json.loads(
            text, parse_constant=_reject_json_constant,
            object_pairs_hook=_unique_json_object)
    except (RecursionError, UnicodeError, ValueError) as exc:
        raise ValueError("invalid strict JSON") from exc


def _expected_mcp_item(
        repo_root: Path, python: Path, *, legacy_timeout: bool = False) -> dict:
    script = (Path(repo_root).resolve() / ".agents" / "plugins" / "plugins" /
              "vibepulse" / "scripts" / "mcp_server.py")
    return {
        "name": "vibepulse",
        "enabled": True,
        "disabled_reason": None,
        "transport": {
            "type": "stdio",
            "command": str(Path(python).resolve()),
            "args": [str(script)],
            "env": None,
            "env_vars": [],
            "cwd": None,
        },
        "startup_timeout_sec": None,
        "tool_timeout_sec": (None if legacy_timeout else
                             CODEX_MCP_TOOL_TIMEOUT_SECONDS),
        "auth_status": "unsupported",
    }


def _mcp_listing(text: str) -> list[dict] | None:
    try:
        value = _strict_json(text)
    except (TypeError, ValueError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(value, list) or any(not isinstance(item, dict)
                                          for item in value):
        return None
    return value


def _owned_mcp_state(
        text: str, repo_root: Path, python: Path, *,
        allow_legacy_timeout: bool = False) -> bool | None:
    listing = _mcp_listing(text)
    if listing is None:
        return None
    named = [item for item in listing if item.get("name") == "vibepulse"]
    if not named:
        return False
    expected = _expected_mcp_item(repo_root, python)
    legacy = _expected_mcp_item(repo_root, python, legacy_timeout=True)
    if (len(named) != 1 or
            (named[0] != expected and
             not (allow_legacy_timeout and named[0] == legacy))):
        return None
    return True


def _has_symlink_component(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            return True
    return False


def _is_exact_existing_directory(value, expected: Path) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        candidate = Path(value)
        canonical_expected = Path(expected).resolve(strict=True)
        return all((
            candidate.is_absolute(),
            value == str(candidate),
            value == str(canonical_expected),
            candidate.is_dir(),
            not _has_symlink_component(candidate),
            candidate.resolve(strict=True) == canonical_expected,
        ))
    except (OSError, RuntimeError, ValueError):
        return False


def _is_canonical_existing_directory(value) -> bool:
    """Accept only an absolute, unaliased spelling of an existing directory."""
    if not isinstance(value, str) or not value:
        return False
    try:
        candidate = Path(value)
        canonical = candidate.resolve(strict=True)
        return all((
            candidate.is_absolute(),
            value == str(candidate),
            value == str(canonical),
            candidate.is_dir(),
            not _has_symlink_component(candidate),
        ))
    except (OSError, RuntimeError, ValueError):
        return False


def _owned_plugin_item(item, repo_root: Path) -> bool:
    if not isinstance(item, dict):
        return False
    legacy_keys = {
        "pluginId", "name", "marketplaceName", "installed", "enabled",
        "source", "marketplaceSource",
    }
    cached_marketplace_keys = legacy_keys | {
        "version", "installPolicy", "authPolicy",
    }
    item_keys = set(item)
    cached_marketplace = item_keys == cached_marketplace_keys
    source = item.get("source")
    marketplace_source = item.get("marketplaceSource")
    if (item_keys not in (legacy_keys, cached_marketplace_keys) or
            item.get("pluginId") != "vibepulse@torget" or
            item.get("name") != "vibepulse" or
            item.get("marketplaceName") != "torget" or
            item.get("installed") is not True or
            item.get("enabled") is not True or
            (cached_marketplace and any(
                not isinstance(item.get(key), str) or not item.get(key)
                for key in ("version", "installPolicy", "authPolicy"))) or
            not isinstance(source, dict) or
            set(source) != {"source", "path"} or
            source.get("source") != "local" or
            not isinstance(marketplace_source, dict) or
            set(marketplace_source) != {"sourceType", "source"} or
            marketplace_source.get("sourceType") != "local"):
        return False
    try:
        expected_root = Path(repo_root).resolve(strict=True)
        expected_plugin = (expected_root / ".agents" / "plugins" / "plugins" /
                           "vibepulse")
        if expected_plugin.resolve(strict=True) != expected_plugin:
            return False
    except (OSError, RuntimeError, ValueError):
        return False
    return all((
        expected_root.is_dir(),
        expected_plugin.is_dir(),
        (_is_canonical_existing_directory(marketplace_source.get("source"))
         if cached_marketplace else
         _is_exact_existing_directory(
             marketplace_source.get("source"), expected_root)),
        _is_exact_existing_directory(source.get("path"), expected_plugin),
    ))


def _plugin_state(text: str, repo_root: Path) -> bool | None:
    try:
        value = _strict_json(text)
    except (TypeError, ValueError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(value, dict) or set(value) != {"installed", "available"}:
        return None
    installed = value.get("installed")
    available = value.get("available")
    if (not isinstance(installed, list) or
            not isinstance(available, list) or
            any(not isinstance(item, dict) for item in installed) or
            any(not isinstance(item, dict) for item in available)):
        return None
    matches = [item for item in installed
               if (item.get("pluginId") == "vibepulse@torget" or
                   item.get("name") == "vibepulse")]
    if not matches:
        return False
    if len(matches) != 1 or not _owned_plugin_item(matches[0], repo_root):
        return None
    return True


def _plugin_installed(text: str, repo_root: Path) -> bool:
    return _plugin_state(text, repo_root) is True


def _marketplace_state(text: str, repo_root: Path) -> bool | None:
    try:
        value = _strict_json(text)
    except (TypeError, ValueError, json.JSONDecodeError, UnicodeError):
        return None
    if (not isinstance(value, dict) or set(value) != {"marketplaces"} or
            not isinstance(value.get("marketplaces"), list) or
            any(not isinstance(item, dict)
                for item in value.get("marketplaces", []))):
        return None
    matches = [item for item in value["marketplaces"]
               if item.get("name") == MARKETPLACE_NAME]
    if not matches:
        return False
    if len(matches) != 1:
        return None
    item = matches[0]
    if set(item) == {"name", "root"}:
        try:
            expected = Path(repo_root).resolve(strict=True)
        except (OSError, RuntimeError, ValueError):
            return None
        return _is_exact_existing_directory(item.get("root"), expected)
    source = item.get("marketplaceSource")
    if (set(item) != {"name", "root", "marketplaceSource"} or
            not isinstance(source, dict) or
            set(source) != {"sourceType", "source"} or
            source.get("sourceType") != "local"):
        return None
    try:
        expected = Path(repo_root).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None
    if (not _is_exact_existing_directory(item.get("root"), expected) or
            not _is_exact_existing_directory(source.get("source"), expected)):
        return None
    return True


def _python_probe_ok(python: Path | None, run) -> bool:
    if python is None:
        return False
    probe = _invoke([str(Path(python).resolve()), "-c", _PYTHON_PROBE], run)
    sentinel = "vibepulse-python-3.11+"
    return (probe is not None and probe.returncode == 0 and
            probe.stdout in (sentinel + "\n", sentinel + "\r\n") and
            probe.stderr == "")


def _codex_probe_ok(codex: Path | None, run) -> bool:
    if codex is None:
        return False
    probe = _invoke([str(Path(codex).resolve()), "--version"], run)
    return (probe is not None and probe.returncode == 0 and
            probe.stderr == "" and
            _CODEX_VERSION.fullmatch(probe.stdout) is not None)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _default_urlopen(request, *, timeout):
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _NoRedirect())
    return opener.open(request, timeout=timeout)


def _response_content_type(response) -> bool:
    headers = getattr(response, "headers", None)
    get_all = getattr(headers, "get_all", None)
    if not callable(get_all):
        return False
    values = get_all("Content-Type", [])
    if not isinstance(values, list) or len(values) != 1:
        return False
    value = values[0]
    if not isinstance(value, str):
        return False
    return re.fullmatch(
        r"application/json(?:;[ \t]*charset=[Uu][Tt][Ff]-8)?", value
    ) is not None


def _doctor(
        config: VibePulseConfig, *, python: Path | None, codex: Path | None,
        repo_root: Path, run: Callable[..., object],
        urlopen: Callable[..., object], stdout,
        ) -> bool:
    fixes = False

    if config.legacy_claude_panel_v1:
        print("FIX Legacy Claude panel v1: insecure compatibility mode is "
              "enabled; reinstall with --no-legacy-claude-panel-v1",
              file=stdout)
        fixes = True
    else:
        print("PASS Legacy Claude panel v1: secure v2 mode", file=stdout)

    python_ok = _python_probe_ok(python, run)
    if not python_ok:
        print("FIX Python executable: install Python 3.11 or newer", file=stdout)
        fixes = True
    else:
        print("PASS Python executable", file=stdout)

    codex_ok = False
    if not config.codex_interactions:
        print("OFF Codex executable: provider intentionally disabled",
              file=stdout)
    elif codex is None:
        if sys.platform == "win32":
            print("FIX Codex executable: install the standalone Codex CLI; "
                  "the Windows desktop app alias is not background-safe",
                  file=stdout)
        else:
            print("FIX Codex executable: install or expose codex on PATH",
                  file=stdout)
        fixes = True
    else:
        codex_ok = _codex_probe_ok(codex, run)
        if codex_ok:
            print("PASS Codex executable", file=stdout)
        else:
            print("FIX Codex executable: candidate is not Codex", file=stdout)
            fixes = True

    if not config.codex_interactions:
        print("OFF Codex plugin: provider intentionally disabled", file=stdout)
        print("OFF Codex MCP: provider intentionally disabled", file=stdout)
    elif not codex_ok or codex is None or python is None:
        print("FIX Codex plugin: cannot inspect without Codex", file=stdout)
        print("FIX Codex MCP: cannot inspect without Codex", file=stdout)
        fixes = True
    else:
        codex_text = str(Path(codex).resolve())
        plugin_result = _invoke(
            [codex_text, "plugin", "list", "--json"], run)
        plugin_ok = (plugin_result is not None and
                     plugin_result.returncode == 0 and
                     _plugin_installed(plugin_result.stdout, repo_root))
        if plugin_ok:
            print("PASS Codex plugin", file=stdout)
        else:
            print("FIX Codex plugin: install vibepulse@torget", file=stdout)
            fixes = True

        mcp_result = _invoke([codex_text, "mcp", "list", "--json"], run)
        mcp_ok = (mcp_result is not None and mcp_result.returncode == 0 and
                  _owned_mcp_state(
                      mcp_result.stdout, repo_root, python) is True)
        if mcp_ok:
            print("PASS Codex MCP", file=stdout)
        else:
            print("FIX Codex MCP: register the local bridge with its "
                  "130-second tool timeout", file=stdout)
            fixes = True

    if config.codex_interactions:
        print("FIX hooks: open /hooks and review VibePulse; trust status is "
              "not machine-readable", file=stdout)
        fixes = True
    else:
        print("OFF Hook review: provider intentionally disabled", file=stdout)

    if not (config.claude_interactions or config.codex_interactions):
        print("OFF Tokenserver: all providers intentionally disabled",
              file=stdout)
    else:
        try:
            request = urllib.request.Request(
                TOKEN_SERVER_URL, headers={"Accept": "application/json"})
            open_url = (_default_urlopen if urlopen is _AUTO else urlopen)
            with open_url(request, timeout=NETWORK_TIMEOUT_SECONDS) as response:
                if (getattr(response, "status", None) != 200 or
                        not _response_content_type(response)):
                    raise ValueError("untrusted diagnostic response")
                raw = response.read(MAX_DIAGNOSTIC_BYTES + 1)
            if (not isinstance(raw, bytes) or
                    len(raw) > MAX_DIAGNOSTIC_BYTES):
                raise ValueError("oversized diagnostics")
            payload = _strict_json(raw.decode("utf-8", errors="strict"))
            if not isinstance(payload, dict):
                raise ValueError("diagnostics must be an object")
            interactions = payload.get("interactions")
            expected = {
                "claude": config.claude_interactions,
                "codex": config.codex_interactions,
                "detail": config.interaction_detail,
                "legacyClaudePanelV1": config.legacy_claude_panel_v1,
            }
            expected_relay = {
                "status": "ready" if config.interaction_relay else "off"}
            expected_agent_status_relay = {
                "status": "ready" if config.agent_status_relay else "off"}
            expected_transport = (
                "lan+encrypted-relay"
                if (config.interaction_relay or config.agent_status_relay)
                else "lan")
            if (payload.get("service") != "torget-tokenserver" or
                    not isinstance(interactions, dict) or
                    any(interactions.get(key) is not value
                        for key, value in expected.items()) or
                    interactions.get("relay") != expected_relay or
                    interactions.get("agentStatusRelay") !=
                    expected_agent_status_relay or
                    interactions.get("transport") != expected_transport):
                raise ValueError("diagnostics mismatch")
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError,
                RecursionError, TimeoutError):
            print("FIX Tokenserver: local diagnostics unavailable or stale",
                  file=stdout)
            fixes = True
        else:
            print("PASS Tokenserver", file=stdout)
            if payload.get("srcFingerprint") != _read_source_fingerprint():
                print("FIX Tokenserver source: the live service does not "
                      "match this checkout; repair the launchd/Task Scheduler "
                      "registration from one durable checkout", file=stdout)
                fixes = True
            else:
                print("PASS Tokenserver source: live fingerprint matches "
                      "this checkout", file=stdout)
            _doctor_panel_lan_contact(interactions, stdout)
            if config.claude_interactions and not _doctor_claude_quota(
                    payload, stdout):
                fixes = True
    return not fixes


def _doctor_panel_lan_contact(interactions: dict, stdout) -> None:
    """Describe direct panel evidence without misclassifying relay-only use."""
    panel = interactions.get("panel")
    if not isinstance(panel, dict):
        return
    status = panel.get("status")
    if status == "ready":
        route = panel.get("route")
        suffix = f" via {route}" if isinstance(route, str) else ""
        print(f"PASS Panel LAN contact: recent confirmed poll{suffix}",
              file=stdout)
        if panel.get("httpStallRecoveryBoot") is True:
            print("PASS Panel self-recovery: this boot followed the bounded "
                  "HTTP-stall restart", file=stdout)
        return
    if status == "stale":
        print("WAIT Panel LAN contact: the last confirmed direct poll is "
              "stale; relay-only use may still be healthy. If the glass "
              "shows STALE, use a dedicated power supply and verify current "
              "discovery-capable firmware", file=stdout)
        return
    if status == "waiting":
        print("WAIT Panel LAN contact: no confirmed direct poll since service "
              "start; relay-only use may still be healthy. If the glass "
              "shows STALE, use a dedicated power supply and verify current "
              "discovery-capable firmware", file=stdout)


def _doctor_claude_quota(payload: dict, stdout) -> bool:
    """Report the safe credential/probe guard published by tokenserver."""
    credential = payload.get("claudeCredential")
    probe = payload.get("claudeProbe")
    if not isinstance(credential, dict):
        print("FIX Claude quota credential: diagnostics are too old; "
              "restart the VibePulse tokenserver", file=stdout)
        return False

    status = credential.get("status")
    remaining = credential.get("expiresInMin")
    source_live = probe == "usage_http_200 + ok"
    if status == "ready" and isinstance(remaining, int) and remaining >= 0:
        if source_live:
            print(f"PASS Claude quota credential: ready ({remaining} min "
                  "remaining)", file=stdout)
            return True
        if probe == "not_run":
            print("WAIT Claude quota probe: credential is ready but the "
                  "first probe has not completed", file=stdout)
            return False
        print("FIX Claude quota probe: credential is ready but the usage "
              "source is unavailable; see docs/agent-setup.md", file=stdout)
        return False

    if status == "expiring" and isinstance(remaining, int) and remaining >= 0:
        live = ("; the current quota source is live, but "
                if source_live else "; ")
        print(f"FIX Saved Claude quota credential: expires in {remaining} "
              f"min{live}start a new Claude Code CLI turn before then so its "
              "supported client refreshes Keychain", file=stdout)
        return False
    if status == "expired":
        if source_live:
            print("FIX Saved Claude quota credential: expired; the current "
                  "quota source is live via a current Claude client, but the "
                  "next client gap can make Fable stale—start a new Claude "
                  "Code CLI turn; VibePulse rechecks automatically and does "
                  "not need a restart", file=stdout)
        else:
            print("FIX Saved Claude quota credential: expired; login status "
                  "alone is not enough—start a new Claude Code CLI turn; "
                  "VibePulse rechecks automatically and does not need a "
                  "restart", file=stdout)
        return False
    if status == "unavailable":
        print("FIX Claude quota credential: no supported Claude credential "
              "was found on this computer", file=stdout)
        return False
    if status == "unknown":
        live = (" The current quota source is live, but its" if source_live
                else " Its")
        print("FIX Claude quota credential:" + live + " process-token expiry "
              "cannot be guarded; sign in with Claude Code so the supported "
              "credential store is populated", file=stdout)
        return False

    print("FIX Claude quota credential: invalid diagnostics", file=stdout)
    return False


def _resolve_executables(python, codex):
    python_path = (Path(sys.executable) if python is _AUTO else
                   (None if python is None else Path(python)))
    if codex is _AUTO:
        found = resolve_codex_executable()
        codex_path = Path(found) if found else None
    else:
        codex_path = None if codex is None else Path(codex)
    return python_path, codex_path


def _setup_transaction_path(path: Path) -> Path:
    path = Path(path)
    return path.with_name(f".{path.name}.vibepulse-setup-transaction")


def _inspect_external(
        *, repo_root: Path, python: Path, codex: Path, run,
        stdout, report_failure: bool = True,
        allow_legacy_mcp_timeout: bool = False) -> _ExternalState | None:
    codex_text = str(Path(codex).resolve())
    commands = [
        [codex_text, "mcp", "list", "--json"],
        [codex_text, "plugin", "list", "--json"],
        [codex_text, "plugin", "marketplace", "list", "--json"],
    ]
    results = [_invoke(argv, run) for argv in commands]
    states: list[bool | None] = [None, None, None]
    if results[0] is not None and results[0].returncode == 0:
        states[0] = _owned_mcp_state(
            results[0].stdout, repo_root, python,
            allow_legacy_timeout=allow_legacy_mcp_timeout)
    if results[1] is not None and results[1].returncode == 0:
        states[1] = _plugin_state(results[1].stdout, repo_root)
    if results[2] is not None and results[2].returncode == 0:
        states[2] = _marketplace_state(results[2].stdout, repo_root)
    if any(state is None for state in states):
        if report_failure:
            print("FIX Codex resources are foreign or unreadable; leaving all "
                  "external state unchanged", file=stdout)
        return None
    return _ExternalState(
        mcp=states[0], plugin=states[1], marketplace=states[2])


def _command_ok(
        completed: _CommandResult | None, argv: Sequence[str], *,
        absent_allowed: bool = False) -> bool:
    return (completed is not None and
            (completed.returncode == 0 or
             (absent_allowed and
              _known_absent(argv, completed.stderr))))


def _state_reconciliation_commands(
        current: _ExternalState, target: _ExternalState, *,
        repo_root: Path, python: Path, codex: Path) -> list[list[str]]:
    install = plan_codex_install(repo_root, python, codex)
    uninstall = plan_codex_uninstall(codex)
    commands = []
    if current.mcp and not target.mcp:
        commands.append(uninstall[0])
    if current.plugin and not target.plugin:
        commands.append(uninstall[1])
    if current.marketplace and not target.marketplace:
        commands.append(uninstall[2])
    if not current.marketplace and target.marketplace:
        commands.append(install[0])
    if not current.plugin and target.plugin:
        commands.append(install[1])
    if not current.mcp and target.mcp:
        commands.extend(install[3:5])
    return commands


def _reconcile_external(
        target: _ExternalState, *, repo_root: Path, python: Path,
        codex: Path, run, stdout) -> bool:
    """Observe, reconcile twice only on progress, then verify exact state."""
    try:
        current = _inspect_external(
            repo_root=repo_root, python=python, codex=codex, run=run,
            stdout=stdout, report_failure=False,
            allow_legacy_mcp_timeout=True)
    except BaseException:
        return False
    if current is None:
        return False
    if current == target:
        return True

    for _attempt in range(2):
        for argv in _state_reconciliation_commands(
                current, target, repo_root=repo_root, python=python,
                codex=codex):
            try:
                _invoke(argv, run)
            except BaseException:
                pass
        try:
            observed = _inspect_external(
                repo_root=repo_root, python=python, codex=codex, run=run,
                stdout=stdout, report_failure=False,
                allow_legacy_mcp_timeout=True)
        except BaseException:
            return False
        if observed is None:
            return False
        if observed == target:
            return True
        if observed == current:
            return False
        current = observed
    return False


def _fail_after_external_mutation(
        target: _ExternalState, *, failed_step: str, repo_root: Path,
        python: Path, codex: Path, run, stdout) -> None:
    restored = _reconcile_external(
        target, repo_root=repo_root, python=python, codex=codex, run=run,
        stdout=stdout)
    config_diverged = "irrecoverable configuration divergence" in failed_step
    if restored and not config_diverged:
        print(f"FIX Setup failed at {failed_step}; external state restored",
              file=stdout)
    elif restored:
        print(f"FIX irrecoverable divergence after {failed_step}; external "
              "state restored", file=stdout)
    else:
        print(f"FIX irrecoverable divergence after {failed_step}; external "
              "state could not be verified or restored", file=stdout)


def _publish_config(
        path: Path, snapshot: VibePulseConfig,
        target: VibePulseConfig) -> None:
    with config_lock(path):
        if load_config(path) != snapshot:
            raise _ConfigPublishError(
                "configuration changed during setup", irrecoverable=False)
        try:
            save_config(path, target)
        except BaseException as exc:
            restored = False
            try:
                current = load_config(path)
                if current == snapshot:
                    restored = True
                elif current == target:
                    try:
                        save_config(path, snapshot)
                    except BaseException:
                        pass
                    restored = load_config(path) == snapshot
            except BaseException:
                restored = False
            raise _ConfigPublishError(
                "configuration publish failed",
                irrecoverable=not restored) from exc


def _failed_step_with_publish_state(
        failed_step: str, exc: BaseException) -> str:
    if isinstance(exc, _ConfigPublishError) and exc.irrecoverable:
        return failed_step + " (irrecoverable configuration divergence)"
    return failed_step


def _install_transaction(
        *, path: Path, providers: str, detail: bool,
        legacy_claude_panel_v1: bool, repo_root: Path,
        python: Path, codex: Path, run, stdout) -> bool:
    """Mutate owned Codex resources and publish routing transactionally."""
    with config_lock(_setup_transaction_path(path)):
        with config_lock(path):
            snapshot = load_config(path)
        target = _chosen_config(
            providers, detail, legacy_claude_panel_v1, snapshot)
        before = None
        failed_step = "runtime preflight"
        try:
            if not _python_probe_ok(python, run):
                print("FIX Python executable: install Python 3.11 or newer",
                      file=stdout)
                return False
            if not _codex_probe_ok(codex, run):
                print("FIX Codex executable: candidate is not Codex",
                      file=stdout)
                return False
            failed_step = "resource ownership preflight"
            before = _inspect_external(
                repo_root=repo_root, python=python, codex=codex, run=run,
                stdout=stdout, allow_legacy_mcp_timeout=True)
            if before is None:
                return False

            install = plan_codex_install(repo_root, python, codex)
            steps = (
                ("marketplace add", install[0]),
                ("plugin add", install[1]),
                ("MCP remove", install[2]),
                ("MCP add", install[3]),
                ("MCP timeout", install[4]),
            )
            for failed_step, argv in steps:
                completed = _invoke(argv, run)
                if not _command_ok(completed, argv):
                    _fail_after_external_mutation(
                        before, failed_step=failed_step, repo_root=repo_root,
                        python=python, codex=codex, run=run, stdout=stdout)
                    return False

            failed_step = "desired external state verification"
            desired = _inspect_external(
                repo_root=repo_root, python=python, codex=codex, run=run,
                stdout=stdout, report_failure=False)
            if desired != _ExternalState(
                    mcp=True, plugin=True, marketplace=True):
                _fail_after_external_mutation(
                    before, failed_step=failed_step, repo_root=repo_root,
                    python=python, codex=codex, run=run, stdout=stdout)
                return False

            failed_step = "configuration publish"
            _publish_config(path, snapshot, target)
            return True
        except BaseException as exc:
            if before is None:
                print(f"FIX Setup failed at {failed_step}", file=stdout)
            else:
                _fail_after_external_mutation(
                    before,
                    failed_step=_failed_step_with_publish_state(
                        failed_step, exc),
                    repo_root=repo_root, python=python, codex=codex, run=run,
                    stdout=stdout)
            return False


def _uninstall_transaction(
        *, path: Path, repo_root: Path, python: Path, codex: Path,
        run, stdout) -> bool:
    """Remove only proven-owned resources and compensate every failure."""
    with config_lock(_setup_transaction_path(path)):
        with config_lock(path):
            snapshot = load_config(path)
        target = _disabled_config(snapshot, "codex")
        before = None
        failed_step = "resource ownership preflight"
        try:
            before = _inspect_external(
                repo_root=repo_root, python=python, codex=codex, run=run,
                stdout=stdout, allow_legacy_mcp_timeout=True)
            if before is None:
                return False
            uninstall = plan_codex_uninstall(codex)
            steps = (
                ("MCP remove", uninstall[0], before.mcp),
                ("plugin remove", uninstall[1], before.plugin),
                ("marketplace remove", uninstall[2], before.marketplace),
            )
            for failed_step, argv, owned_before in steps:
                completed = _invoke(argv, run)
                if not _command_ok(
                        completed, argv, absent_allowed=not owned_before):
                    _fail_after_external_mutation(
                        before, failed_step=failed_step, repo_root=repo_root,
                        python=python, codex=codex, run=run, stdout=stdout)
                    return False

            failed_step = "desired external state verification"
            desired = _inspect_external(
                repo_root=repo_root, python=python, codex=codex, run=run,
                stdout=stdout, report_failure=False)
            if desired != _ExternalState(
                    mcp=False, plugin=False, marketplace=False):
                _fail_after_external_mutation(
                    before, failed_step=failed_step, repo_root=repo_root,
                    python=python, codex=codex, run=run, stdout=stdout)
                return False
            failed_step = "configuration publish"
            _publish_config(path, snapshot, target)
            return True
        except BaseException as exc:
            if before is None:
                print(f"FIX Setup failed at {failed_step}", file=stdout)
            else:
                _fail_after_external_mutation(
                    before,
                    failed_step=_failed_step_with_publish_state(
                        failed_step, exc),
                    repo_root=repo_root, python=python, codex=codex, run=run,
                    stdout=stdout)
            return False


def main(
        argv: Sequence[str] | None = None, *, repo_root: Path = REPO_ROOT,
        config_path: Path | None = None, python=_AUTO, codex=_AUTO,
        run=_AUTO, urlopen=_AUTO,
        input_fn: Callable[[str], str] = input, stdout=None,
        stdin_isatty: bool | None = None,
        relay_token_path: Path | None = None,
        secrets_path: Path | None = None,
        interaction_relay_dir: Path | None = None,
        token_urlsafe=secrets.token_urlsafe) -> int:
    """Run the strict CLI with injectable process and network boundaries."""
    args = _parser().parse_args(argv)
    output = sys.stdout if stdout is None else stdout
    path = default_config_path() if config_path is None else Path(config_path)
    python_path, codex_path = _resolve_executables(python, codex)
    interactive = (sys.stdin.isatty() if stdin_isatty is None
                   else stdin_isatty)
    relay_token = (Path.home() / ".vibepulse-interaction-relay-token"
                   if relay_token_path is None else Path(relay_token_path))
    secrets_header = (Path(repo_root) / "secrets.h"
                      if secrets_path is None else Path(secrets_path))
    relay_service = (Path(repo_root) / "tools" / "interaction-relay"
                     if interaction_relay_dir is None
                     else Path(interaction_relay_dir))

    try:
        if args.command == "relay":
            if args.relay_command == "status":
                _relay_status(
                    load_config(path), token_path=relay_token,
                    secrets_path=secrets_header, stdout=output)
                return 0
            if args.relay_command == "doctor":
                return 0 if _relay_doctor(
                    load_config(path), token_path=relay_token,
                    secrets_path=secrets_header,
                    service_dir=relay_service, stdout=output) else 1
            if args.relay_command == "disable":
                return 0 if _relay_disable(path, output) else 1
            if args.relay_command == "disable-status":
                return 0 if _relay_disable_status(path, output) else 1
            if args.relay_command == "enable-status":
                consent = bool(args.yes_e2e_cloud)
                if not consent and interactive:
                    consent = input_fn(
                        "This sends minimized E2E-encrypted live agent "
                        "status through your Cloudflare Worker. Type YES to "
                        "continue: ").strip() == "YES"
                return 0 if _relay_enable_status(
                    path=path, consent=consent, token_path=relay_token,
                    secrets_path=secrets_header, urlopen=urlopen,
                    stdout=output) else 1
            if args.relay_command == "install":
                consent = bool(args.yes_e2e_cloud)
                if not consent and interactive:
                    consent = input_fn(
                        "This sends bounded E2E-encrypted panel views and "
                        "verdicts through your Cloudflare Worker. Type YES "
                        "to continue: ").strip() == "YES"
                return 0 if _relay_install(
                    path=path, url=args.url, consent=consent,
                    token_path=relay_token, secrets_path=secrets_header,
                    service_dir=relay_service,
                    token_urlsafe=token_urlsafe, run=run,
                    stdout=output) else 1
            delete_worker = args.delete_worker
            if delete_worker is None:
                if interactive:
                    delete_worker = input_fn(
                        "Delete the Cloudflare Worker too? [y/N]: ").strip(
                        ).lower() in {"y", "yes"}
                else:
                    delete_worker = False
            return 0 if _relay_uninstall(
                path=path, delete_worker=delete_worker,
                token_path=relay_token, secrets_path=secrets_header,
                service_dir=relay_service, run=run,
                stdout=output) else 1

        if args.command == "status":
            _print_status(load_config(path), output)
            return 0

        if args.command == "doctor":
            config = load_config(path)
            return 0 if _doctor(
                config, python=python_path, codex=codex_path,
                repo_root=Path(repo_root), run=run, urlopen=urlopen,
                stdout=output) else 1

        if args.command == "disable":
            _disable(path, args.target)
            print(f"PASS Disabled {args.target} VibePulse interactions",
                  file=output)
            return 0

        if args.command == "uninstall":
            if codex_path is None or python_path is None:
                print("FIX Python or Codex executable not found", file=output)
                return 1
            if not _uninstall_transaction(
                    path=path, repo_root=Path(repo_root), python=python_path,
                    codex=codex_path, run=run, stdout=output):
                return 1
            print("PASS Removed only VibePulse Codex registration", file=output)
            return 0

        providers = args.providers
        if providers is None:
            providers = (_interactive_providers(input_fn)
                         if interactive else "off")
        detail = args.detail
        if detail is None:
            detail = (_interactive_detail(input_fn) if interactive else False)
        legacy_claude_panel_v1 = bool(args.legacy_claude_panel_v1)
        if (legacy_claude_panel_v1 and
                providers not in {"claude", "both"}):
            print("FIX --legacy-claude-panel-v1 requires Claude in "
                  "--providers", file=output)
            return 1
        if codex_path is None or python_path is None:
            print("FIX Python or Codex executable not found", file=output)
            return 1
        if not _install_transaction(
                path=path, providers=providers, detail=detail,
                legacy_claude_panel_v1=legacy_claude_panel_v1,
                repo_root=Path(repo_root), python=python_path,
                codex=codex_path, run=run, stdout=output):
            return 1
        print("PASS Installed the local VibePulse package", file=output)
        print("Review and trust the exact VibePulse commands in Codex /hooks.",
              file=output)
        print("If the panel is unavailable, Codex uses computer fallback.",
              file=output)
        return 0
    except ConfigError as exc:
        print(f"FIX VibePulse configuration: {exc}", file=output)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
