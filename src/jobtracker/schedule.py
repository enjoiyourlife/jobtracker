"""
Scheduling — jobtracker manages its own cron entry.

Delegates the actual periodic execution to cron rather than running as
a background daemon: a daemon has to solve "stay alive across reboots
and sleep/wake cycles" itself, and cron already does that correctly at
the OS level. This module just makes crontab's edit-a-text-file dance
a `jobtracker schedule` command instead of a manual step you'd have to
remember and redo on every machine this repo ends up on.

Idempotent by design: install() replaces jobtracker's own line rather
than appending a duplicate, found via a marker comment rather than by
matching the full command — so changing the poll time doesn't leave a
stale entry running alongside the new one.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_MARKER = "# jobtracker: daily poll"
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_PYTHON = _PROJECT_ROOT / ".venv" / "bin" / "python"
_LOG_PATH = _PROJECT_ROOT / "data" / "poller.log"


class ScheduleError(RuntimeError):
    """Raised when crontab can't be read or written."""


def _current_crontab() -> list[str]:
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if result.returncode != 0:
        # No crontab installed yet — an empty one, not an error.
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


def _write_crontab(lines: list[str]) -> None:
    content = "\n".join(lines) + ("\n" if lines else "")
    result = subprocess.run(["crontab", "-"], input=content, capture_output=True, text=True)
    if result.returncode != 0:
        raise ScheduleError(f"Failed to update crontab: {result.stderr.strip()}")


def cron_line(hour: int, minute: int) -> str:
    command = f"{_PYTHON} -m jobtracker.poller >> {_LOG_PATH} 2>&1"
    return f"{minute} {hour} * * * {command}  {_MARKER}"


def status() -> str | None:
    """jobtracker's installed cron line, or None if not scheduled."""
    for line in _current_crontab():
        if _MARKER in line:
            return line
    return None


def install(hour: int = 7, minute: int = 0) -> None:
    """Install or replace jobtracker's daily poll entry, leaving everything else in crontab untouched."""
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Invalid time {hour:02d}:{minute:02d}")
    lines = [line for line in _current_crontab() if _MARKER not in line]
    lines.append(cron_line(hour, minute))
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _write_crontab(lines)


def uninstall() -> bool:
    """Remove jobtracker's cron entry, if present. Returns whether one was removed."""
    lines = _current_crontab()
    kept = [line for line in lines if _MARKER not in line]
    if len(kept) == len(lines):
        return False
    _write_crontab(kept)
    return True
