"""Time-based filtering utilities for session/history files.

Uses ``python-dateutil`` for flexible date parsing.  Supports relative
durations ("3 days", "2 months", "6 hours"), natural language ("yesterday",
"last week", "2 days ago"), and absolute dates ("2026-01-15", "Jan 15 2026",
ISO 8601).

Public API
----------
    parse_since(since: str) -> datetime
        Convert any human-readable time expression into a UTC-aware cutoff.

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

from dateutil import parser as dateutil_parser
from dateutil.relativedelta import relativedelta

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
    """Convert a human-readable time expression into a UTC-aware cutoff datetime.

    Supported formats (all case-insensitive)
    -----------------------------------------
    Relative durations:
        - ``"N day(s)"``   / ``"N d"``       → now - N days
        - ``"N week(s)"``  / ``"N w"``       → now - N weeks
        - ``"N month(s)"`` / ``"N mo"``      → now - N months (calendar months)
        - ``"N hour(s)"``  / ``"N h"``       → now - N hours
        - ``"N minute(s)"``/ ``"N min"``     → now - N minutes
        - ``"N year(s)"``  / ``"N y"``       → now - N years

    Natural language (via dateutil):
        - ``"yesterday"``                    → start of yesterday (midnight UTC)
        - ``"last monday"`` / ``"last week"``→ parsed by dateutil
        - ``"2 days ago"``                   → parsed by dateutil
        - ``"last month"``                   → start of previous month

    Absolute dates (via dateutil):
        - ``"2026-01-15"``                   → that date at midnight UTC
        - ``"Jan 15, 2026"``                 → parsed by dateutil
        - ``"2026-01-15T10:30:00Z"``         → ISO 8601
        - ``"15/01/2026"``                   → various international formats

    Parameters
    ----------
    since:
        A human-readable time expression.

    Returns
    -------
    datetime
        A UTC-aware datetime representing the start of the look-back window.

    Raises
    ------
    ValueError
        If *since* cannot be interpreted as any known format.

    Examples
    --------
    >>> from datetime import timezone
    >>> result = parse_since("3 days")
    >>> result.tzinfo == timezone.utc
    True
    >>> result = parse_since("2 months")
    >>> result.tzinfo == timezone.utc
    True
    """
    raw = since.strip()
    text = raw.lower()

    now = datetime.now(_UTC)

    # --- 1. Relative duration patterns: "N <unit>" --------------------------

    m = re.fullmatch(r"(\d+)\s*(days?|d)", text)
    if m:
        return now - timedelta(days=int(m.group(1)))

    m = re.fullmatch(r"(\d+)\s*(weeks?|w)", text)
    if m:
        return now - timedelta(weeks=int(m.group(1)))

    m = re.fullmatch(r"(\d+)\s*(months?|mo)", text)
    if m:
        return now - relativedelta(months=int(m.group(1)))

    m = re.fullmatch(r"(\d+)\s*(hours?|h)", text)
    if m:
        return now - timedelta(hours=int(m.group(1)))

    m = re.fullmatch(r"(\d+)\s*(minutes?|mins?)", text)
    if m:
        return now - timedelta(minutes=int(m.group(1)))

    m = re.fullmatch(r"(\d+)\s*(years?|y)", text)
    if m:
        return now - relativedelta(years=int(m.group(1)))

    # --- 2. Natural language shortcuts ---------------------------------------

    if text == "yesterday":
        return (now - timedelta(days=1)).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )

    if text == "last week":
        return now - timedelta(weeks=1)

    if text == "last month":
        return now - relativedelta(months=1)

    if text == "last year":
        return now - relativedelta(years=1)

    # "N <unit> ago" — strip "ago" and re-parse
    m = re.fullmatch(r"(\d+\s+\w+)\s+ago", text)
    if m:
        return parse_since(m.group(1))

    # --- 3. Absolute date/datetime via dateutil ------------------------------

    try:
        parsed = dateutil_parser.parse(raw, fuzzy=True)
        # Ensure UTC-aware
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_UTC)
        return parsed
    except (ValueError, OverflowError) as e:
        from sio.core.observability import log_failure  # noqa: PLC0415
        log_failure(
            "parse_errors", f"since={raw!r}", e,
            stage="dateutil_parse", severity="debug",
        )

    raise ValueError(
        f"Unrecognised since format: {since!r}. "
        "Supported: 'N days', 'N weeks', 'N months', 'N hours', "
        "'yesterday', 'last week', '2 days ago', 'Jan 15 2026', "
        "'2026-01-15', or any dateutil-parseable string."
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
