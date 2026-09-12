"""Shared discipline for the service's small JSON state files.

Three stores keep state on disk -- the Max Tracker (400 days of history),
the quota cache and the usage history -- and each used to answer a corrupt
file by starting empty, silently, and then overwriting the corrupt bytes on
its next save (OBS-11). The bytes are usually 99 % intact; what was lost
was the *option* to look. Two of the three also stopped their atomic write
at the file fsync, one step short of the parent-directory fsync that makes
a rename survive power loss (OBS-21). Both rules now live here, once.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("tokenserver.state")


def state_dir() -> Path:
    """The service's state directory -- the lock, cache, history, tracker.

    ``~/Library/Application Support`` is the macOS convention; the Windows
    counterpart is ``%LOCALAPPDATA%``. Lives here rather than only in
    ``tokenserver.py`` because the statusLine bridge, a short-lived process
    Claude Code spawns on every status-line trigger, must find the same
    directory without importing the whole service.
    """
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        base = (Path(local_app_data) if local_app_data
                else Path.home() / "AppData" / "Local")
        return base / "VibePulse"
    return Path.home() / "Library" / "Application Support" / "VibePulse"


def quarantine_corrupt(path: Path, reason: str) -> Path | None:
    """Move an unreadable state file aside instead of overwriting it.

    The file becomes ``<name>.corrupt-<UTC stamp>`` beside the original
    (a numeric suffix if that name is taken), and one WARNING names the
    file and the reason -- never its contents. Returns the new path, or
    ``None`` when the move itself failed (the caller still starts empty;
    the next save then overwrites, which is the pre-existing behaviour and
    the only remaining option).
    """
    path = Path(path)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = path.with_name(f"{path.name}.corrupt-{stamp}")
    counter = 1
    while target.exists():
        counter += 1
        target = path.with_name(f"{path.name}.corrupt-{stamp}-{counter}")
    try:
        os.replace(path, target)
    except OSError as error:
        log.warning("%s is unreadable (%s) and could not be quarantined "
                    "(%s): starting empty; the next save overwrites it",
                    path.name, reason, type(error).__name__)
        return None
    # The rename is only durable once the directory entry is (OBS-21):
    # without this, power loss before the next state save could drop the
    # quarantined copy the warning below promises is kept.
    try:
        fsync_parent(target)
    except OSError as error:
        log.warning("%s is unreadable (%s): quarantined as %s and starting "
                    "empty, but the directory fsync failed (%s) so the "
                    "copy is not yet durable.",
                    path.name, reason, target.name, type(error).__name__)
        return target
    log.warning("%s is unreadable (%s): quarantined as %s and starting "
                "empty. The bytes are kept for inspection or hand repair.",
                path.name, reason, target.name)
    return target


def fsync_parent(path: Path) -> None:
    """Make a completed ``os.replace`` durable: fsync the directory entry.

    Windows exposes no POSIX directory descriptors, and opening the parent
    as a file there makes every write fail, so it is a no-op on ``nt`` --
    the temporary file itself is still flushed before the replace.
    """
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(Path(path).parent, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
