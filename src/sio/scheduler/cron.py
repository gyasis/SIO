"""sio.scheduler.cron — crontab management for passive background analysis.

Public API
----------
    install_schedule() -> dict
        Write daily (midnight) and weekly (Sunday midnight) cron entries.
        Returns: {installed, daily_enabled, weekly_enabled, entries}

    uninstall_schedule() -> dict
        Remove all SIO-managed cron entries.
        Returns: {installed, removed_count}

    get_status() -> dict
        Report whether SIO cron entries are present.
        Returns: {installed, daily_enabled, weekly_enabled}

SIO cron entries are identified by the inline comment ``# SIO passive analysis``.
The comment is placed at the end of each cron line so that standard crontab
parsers ignore it and the tests' regex checks match the schedule fields.
"""

from __future__ import annotations

import re
import subprocess
import sys
from typing import Any

# Marker used to identify SIO-owned cron lines.
_SIO_MARKER = "# SIO passive analysis"

# Regex patterns that match daily and weekly schedule fields.
_MIDNIGHT_RE = re.compile(r"^(0\s+0\s+\*\s+\*\s+\*|@daily|@midnight)\b")
_SUNDAY_RE = re.compile(r"^(0\s+0\s+\*\s+\*\s+0|@weekly)\b")

# The command invoked by the cron job.
_SIO_CMD = f"{sys.executable} -m sio schedule run"

# The two cron entries SIO manages.
# @daily and @weekly shorthands are used so that regex-based schedule parsers
# can unambiguously match the schedule field with a word-boundary anchor.
_DAILY_ENTRY = f"@daily {_SIO_CMD} --mode daily {_SIO_MARKER}"
_WEEKLY_ENTRY = f"@weekly {_SIO_CMD} --mode weekly {_SIO_MARKER}"


# ---------------------------------------------------------------------------
# Low-level crontab I/O
# ---------------------------------------------------------------------------


def _read_crontab() -> str:
    """Return the current user crontab as a string.

    Returns an empty string when no crontab exists yet (``crontab -l`` exits
    with a non-zero code and prints a "no crontab" message).
    """
    result = subprocess.run(
        ["crontab", "-l"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # No existing crontab — treat as empty.
        return ""
    return result.stdout or ""


def _write_crontab(content: str) -> None:
    """Overwrite the user crontab with *content*."""
    subprocess.run(
        ["crontab", "-"],
        input=content.encode("utf-8"),
        capture_output=True,
        check=True,
    )


def _is_sio_line(line: str) -> bool:
    """Return True when *line* is an SIO-managed cron entry."""
    return _SIO_MARKER in line


def _strip_sio_lines(crontab_text: str) -> str:
    """Remove all SIO-managed lines from *crontab_text*."""
    kept = [line for line in crontab_text.splitlines() if not _is_sio_line(line)]
    text = "\n".join(kept)
    # Preserve a trailing newline when the original had one.
    if crontab_text.endswith("\n"):
        text += "\n"
    return text


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def install_schedule() -> dict[str, Any]:
    """Install daily and weekly SIO cron entries (idempotent).

    Reads the current crontab, strips any existing SIO entries, then appends
    the canonical daily and weekly entries.  Calling this function multiple
    times produces exactly one daily entry and one weekly entry.

    Returns
    -------
    dict
        ``installed``     (bool) — always True on success.
        ``daily_enabled`` (bool) — True when the daily entry was written.
        ``weekly_enabled`` (bool) — True when the weekly entry was written.
        ``entries``       (list[str]) — the cron lines that were written.

    Raises
    ------
    RuntimeError
        If the platform does not support crontab (e.g. Windows).
    """
    if sys.platform == "win32":
        raise RuntimeError(
            "Cron scheduling is not supported on Windows. "
            "Use Task Scheduler (schtasks) instead."
        )
    current = _read_crontab()
    # Remove any pre-existing SIO lines to prevent duplicates.
    base = _strip_sio_lines(current)

    # Ensure the base ends with a newline so entries are on their own line.
    if base and not base.endswith("\n"):
        base += "\n"

    new_crontab = base + _DAILY_ENTRY + "\n" + _WEEKLY_ENTRY + "\n"
    _write_crontab(new_crontab)

    return {
        "installed": True,
        "daily_enabled": True,
        "weekly_enabled": True,
        "entries": [_DAILY_ENTRY, _WEEKLY_ENTRY],
    }


def uninstall_schedule() -> dict[str, Any]:
    """Remove all SIO-managed cron entries.

    Returns
    -------
    dict
        ``installed``    (bool)  — False after successful removal.
        ``removed_count`` (int)  — number of lines removed.
    """
    current = _read_crontab()
    sio_lines = [line for line in current.splitlines() if _is_sio_line(line)]
    cleaned = _strip_sio_lines(current)
    _write_crontab(cleaned)
    return {
        "installed": False,
        "removed_count": len(sio_lines),
    }


def get_status() -> dict[str, Any]:
    """Check whether SIO cron entries are currently installed.

    Returns
    -------
    dict
        ``installed``     (bool) — True when at least one SIO entry exists.
        ``daily_enabled`` (bool) — True when a midnight/daily entry is present.
        ``weekly_enabled`` (bool) — True when a Sunday/weekly entry is present.
    """
    current = _read_crontab()
    sio_lines = [line.strip() for line in current.splitlines() if _is_sio_line(line)]

    daily_enabled = any(_MIDNIGHT_RE.match(line) for line in sio_lines)
    weekly_enabled = any(_SUNDAY_RE.match(line) for line in sio_lines)
    installed = bool(sio_lines)

    return {
        "installed": installed,
        "daily_enabled": daily_enabled,
        "weekly_enabled": weekly_enabled,
    }
