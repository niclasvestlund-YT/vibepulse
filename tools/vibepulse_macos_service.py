#!/usr/bin/env python3
"""Install or validate VibePulse's per-user macOS launchd service."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile


LABEL = "se.torget.tokenserver"
PLIST_NAME = f"{LABEL}.plist"
PYTHON_PROBE = (
    "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)")


class ServiceConfigError(ValueError):
    pass


def _clean_text(value, *, maximum=4096):
    return (isinstance(value, str) and 0 < len(value) <= maximum and
            "\x00" not in value and "\n" not in value and "\r" not in value)


def _read_preserved(path: Path):
    """Keep explicit runtime options without ever printing their values."""
    if not path.exists():
        return [], None
    if path.is_symlink() or not path.is_file():
        raise ServiceConfigError("existing LaunchAgent must be a regular file")
    try:
        with path.open("rb") as handle:
            value = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException) as exc:
        raise ServiceConfigError("existing LaunchAgent is invalid") from exc
    if not isinstance(value, dict) or value.get("Label") != LABEL:
        raise ServiceConfigError("existing LaunchAgent is not owned by VibePulse")
    argv = value.get("ProgramArguments")
    if (not isinstance(argv, list) or len(argv) < 3 or
            argv[1] != "-u" or Path(argv[2]).name != "tokenserver.py" or
            any(not _clean_text(item) for item in argv)):
        raise ServiceConfigError("existing VibePulse command is not recognized")
    environment = value.get("EnvironmentVariables")
    if environment is not None:
        if (not isinstance(environment, dict) or
                any(not _clean_text(key, maximum=128) or
                    not _clean_text(item)
                    for key, item in environment.items())):
            raise ServiceConfigError(
                "existing VibePulse environment is not safely preservable")
        environment = dict(environment)
    return list(argv[3:]), environment


def _read_owned_plist(path: Path):
    if not path.exists() or path.is_symlink() or not path.is_file():
        raise ServiceConfigError("installed LaunchAgent is missing or unsafe")
    try:
        with path.open("rb") as handle:
            value = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException) as exc:
        raise ServiceConfigError("installed LaunchAgent is invalid") from exc
    if not isinstance(value, dict) or value.get("Label") != LABEL:
        raise ServiceConfigError("installed LaunchAgent is not owned by VibePulse")
    return value


def build_plist(repo_root: Path, python: Path, home: Path, *,
                extra_arguments=(), environment=None):
    root = repo_root.resolve(strict=True)
    # Keep the lexical .venv path. Resolving its interpreter symlink to the
    # Homebrew/base Python would silently discard the environment containing
    # discovery and encrypted-relay dependencies.
    python_path = Path(os.path.abspath(python))
    if not python_path.is_file():
        raise ServiceConfigError("configured Python executable does not exist")
    server = (root / "tools" / "tokenserver" / "tokenserver.py").resolve(
        strict=True)
    workdir = server.parent
    values = [str(python_path), "-u", str(server), *extra_arguments]
    if any(not _clean_text(item) for item in values):
        raise ServiceConfigError("service command contains an invalid value")
    payload = {
        "Label": LABEL,
        "ProgramArguments": values,
        "WorkingDirectory": str(workdir),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 30,
        "StandardOutPath": str(home / "Library" / "Logs" /
                               "torget-tokenserver.log"),
        "StandardErrorPath": str(home / "Library" / "Logs" /
                                 "torget-tokenserver.log"),
    }
    if environment:
        payload["EnvironmentVariables"] = dict(environment)
    return payload


def _python_supported(path: Path, run=subprocess.run):
    try:
        result = run(
            [str(path), "-c", PYTHON_PROBE], capture_output=True,
            text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _atomic_write(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=False)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="wb", dir=path.parent, prefix=f".{path.name}.",
                delete=False) as handle:
            temp_name = handle.name
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o644)
        os.replace(temp_name, path)
        temp_name = None
    finally:
        if temp_name is not None:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass


def _launchd_domain():
    if sys.platform != "darwin" or not hasattr(os, "getuid"):
        raise ServiceConfigError("LaunchAgent installation requires macOS")
    return f"gui/{os.getuid()}"


def install(*, repo_root: Path, python: Path, plist_path: Path, home: Path,
            validate_only=False, run=subprocess.run):
    previous_payload = (_read_owned_plist(plist_path)
                        if plist_path.exists() else None)
    extras, environment = _read_preserved(plist_path)
    payload = build_plist(
        repo_root, python, home, extra_arguments=extras,
        environment=environment)
    if not _python_supported(Path(payload["ProgramArguments"][0]), run=run):
        raise ServiceConfigError("Python 3.11 or newer is required")
    if validate_only:
        if _read_owned_plist(plist_path) != payload:
            raise ServiceConfigError(
                "installed LaunchAgent does not match this checkout")
        return payload
    domain = _launchd_domain()
    _atomic_write(plist_path, payload)
    run(["launchctl", "bootout", f"{domain}/{LABEL}"],
        capture_output=True, text=True, timeout=15, check=False)
    started = run(
        ["launchctl", "bootstrap", domain, str(plist_path)],
        capture_output=True, text=True, timeout=15, check=False)
    if started.returncode != 0:
        if previous_payload is None:
            try:
                plist_path.unlink()
            except FileNotFoundError:
                pass
            raise ServiceConfigError(
                "launchctl bootstrap failed; new service file was removed")
        _atomic_write(plist_path, previous_payload)
        restored = run(
            ["launchctl", "bootstrap", domain, str(plist_path)],
            capture_output=True, text=True, timeout=15, check=False)
        if restored.returncode != 0:
            raise ServiceConfigError(
                "launchctl bootstrap failed and previous service could not "
                "be reloaded")
        raise ServiceConfigError(
            "launchctl bootstrap failed; previous service was restored")
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("install", "validate"))
    parser.add_argument("--repo-root", type=Path,
                        default=Path(__file__).resolve().parents[1])
    parser.add_argument("--python", type=Path)
    parser.add_argument("--plist", type=Path)
    args = parser.parse_args(argv)
    home = Path.home()
    python = args.python or args.repo_root / ".venv" / "bin" / "python"
    plist_path = args.plist or home / "Library" / "LaunchAgents" / PLIST_NAME
    try:
        install(repo_root=args.repo_root, python=python,
                plist_path=plist_path, home=home,
                validate_only=args.command == "validate")
    except (OSError, ServiceConfigError) as exc:
        print(f"FIX VibePulse LaunchAgent: {exc}")
        return 1
    verb = "valid" if args.command == "validate" else "installed and reloaded"
    print(f"PASS VibePulse LaunchAgent: {verb}; source is the requested durable checkout")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
