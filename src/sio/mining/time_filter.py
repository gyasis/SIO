"""Time-based filtering utilities for session/history files.

Public API
----------
    parse_since(since: str) -> datetime
        Convert a human-readable look-back string ("3 days", "1 week", etc.)
        into a UTC-aware datetime representing the cutoff point.

    filter_files(paths: list[Path], since: str) -> list[Path]
        Return only those paths whose effective timestamp is >= parse_since(since).
        The effective timestamp is taken from the filename when the file follows
        the SpecStory naming convention (``YYYY-MM-DD_HH-MM-SSZ-<slug>.md``);
        otherwise the file's mtime is used.

SpecStory filename format
-------------------------
    ``2026-02-25_10-00-00Z-session-name.md``
    Regex group names: ``year``, ``month``, ``day``, ``hour``, ``minute``, ``second``.
    The trailing ``Z`` denotes UTC.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_UTC = timezone.utc

# Matches: 2026-02-25_10-00-00Z-anything.md
# Groups: year, month, day, hour, minute, second
_SPECSTORY_RE = re.compile(
    r"^(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})"
    r"_(?P<hour>\d{2})-(?P<minute>\d{2})-(?P<second>\d{2})Z"
    r"-.+\.md$"
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_since(since: str) -> datetime:
    """Convert a human-readable look-back string into a UTC-aware cutoff datetime.

    Supported formats
    -----------------
    - "N day"  / "N days"  → now - N days
    - "N week" / "N weeks" → now - N*7 days

    Parameters
    ----------
    since:
        A string such as ``"3 days"``, ``"1 week"``, or ``"2 weeks"``.

    Returns
    -------
    datetime
        A UTC-aware datetime representing the start of the look-back window.

    Raises
    ------
    ValueError
        If *since* does not match an understood pattern.

    Examples
    --------
    >>> from datetime import timezone
    >>> result = parse_since("3 days")
    >>> result.tzinfo == timezone.utc
    True
    """
    since = since.strip().lower()

    # "N day(s)"
    match = re.fullmatch(r"(\d+)\s+days?", since)
    if match:
        n = int(match.group(1))
        return datetime.now(_UTC) - timedelta(days=n)

    # "N week(s)"
    match = re.fullmatch(r"(\d+)\s+weeks?", since)
    if match:
        n = int(match.group(1))
        return datetime.now(_UTC) - timedelta(weeks=n)

    raise ValueError(
        f"Unrecognised since format: {since!r}. "
        "Expected formats: 'N days', 'N day', 'N weeks', 'N week'."
    )


def filter_files(paths: list[Path], since: str) -> list[Path]:
    """Return paths whose effective timestamp is >= parse_since(since).

    Effective timestamp rules
    -------------------------
    1. If the filename matches the SpecStory format
       ``YYYY-MM-DD_HH-MM-SSZ-<slug>.md``, the timestamp encoded in the
       filename is used regardless of the file's mtime.
    2. Otherwise, ``os.path.getmtime`` is used and interpreted as UTC.

    Parameters
    ----------
    paths:
        Sequence of :class:`~pathlib.Path` objects to evaluate.
    since:
        Human-readable look-back string accepted by :func:`parse_since`.

    Returns
    -------
    list[Path]
        A new list containing only the paths whose effective timestamp falls
        within the look-back window (i.e. >= cutoff).  Order follows the
        order of the input list.

    Examples
    --------
    >>> filter_files([], "3 days")
    []
    """
    if not paths:
        return []

    cutoff = parse_since(since)
    result: list[Path] = []

    for path in paths:
        effective_ts = _effective_timestamp(path)
        if effective_ts >= cutoff:
            result.append(path)

    return result


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _parse_specstory_filename(name: str) -> datetime | None:
    """Return a UTC-aware datetime if *name* matches the SpecStory format.

    Parameters
    ----------
    name:
        Bare filename (no directory components), e.g.
        ``"2026-02-25_10-00-00Z-session-name.md"``.

    Returns
    -------
    datetime | None
        UTC-aware datetime if the name matches; ``None`` otherwise.
    """
    match = _SPECSTORY_RE.match(name)
    if match is None:
        return None

    return datetime(
        year=int(match.group("year")),
        month=int(match.group("month")),
        day=int(match.group("day")),
        hour=int(match.group("hour")),
        minute=int(match.group("minute")),
        second=int(match.group("second")),
        tzinfo=_UTC,
    )


def _effective_timestamp(path: Path) -> datetime:
    """Return the effective UTC-aware datetime for *path*.

    Uses the filename-encoded timestamp when available; falls back to mtime.

    Parameters
    ----------
    path:
        Path to the file being evaluated.

    Returns
    -------
    datetime
        UTC-aware effective timestamp for the file.
    """
    filename_dt = _parse_specstory_filename(path.name)
    if filename_dt is not None:
        return filename_dt

    # Fall back to mtime, interpreted as UTC.
    mtime_unix = os.path.getmtime(path)
    return datetime.fromtimestamp(mtime_unix, tz=_UTC)
