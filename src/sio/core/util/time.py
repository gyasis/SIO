"""Timezone-aware timestamp utilities — T007 (004-pipeline-integrity-remediation).

All timestamps stored in SIO databases MUST be UTC ISO-8601 with an explicit
+00:00 offset (never bare 'Z', never naive).  This module provides the two
canonical helpers used throughout the codebase:

  - ``to_utc_iso(s)``: normalise any ISO-8601 string to UTC+00:00
  - ``utc_now_iso()``: current wall-clock time as UTC ISO-8601 string

Decision record: research.md R-7.
"""

from __future__ import annotations

from datetime import datetime, timezone


def to_utc_iso(s: str) -> str:
    """Normalise an ISO-8601 timestamp string to UTC with explicit +00:00 offset.

    Handles three input forms:
      1. UTC with 'Z' suffix   — ``"2026-04-20T14:32:00Z"``
      2. Numeric TZ offset     — ``"2026-04-20T10:32:00-04:00"``
      3. Naive (no TZ info)    — interpreted via local timezone then converted

    Args:
        s: An ISO-8601-ish timestamp string.

    Returns:
        Canonical UTC string ending with ``+00:00``, e.g.
        ``"2026-04-20T14:32:00+00:00"``.

    Raises:
        ValueError: If *s* is empty, whitespace-only, or unparseable.
    """
    if not s or not s.strip():
        raise ValueError(
            f"to_utc_iso() received an empty or blank timestamp: {s!r}. "
            "All SIO timestamps must be non-empty ISO-8601 strings."
        )

    stripped = s.strip()

    # Replace trailing 'Z' with '+00:00' so fromisoformat can parse it
    # (Python <3.11 does not accept 'Z' in fromisoformat).
    if stripped.endswith("Z"):
        stripped = stripped[:-1] + "+00:00"

    try:
        dt = datetime.fromisoformat(stripped)
    except ValueError as exc:
        raise ValueError(
            f"to_utc_iso() cannot parse {s!r} as ISO-8601: {exc}"
        ) from exc

    if dt.tzinfo is None:
        # Naive datetime — interpret in local system timezone, then convert.
        dt = dt.astimezone(timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    # Replace tzinfo to ensure the +00:00 suffix (not 'UTC' label on some platforms).
    dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def utc_now_iso() -> str:
    """Return the current UTC time as a canonical ISO-8601 string.

    The returned string always ends with ``+00:00`` and is parseable by
    ``datetime.fromisoformat()``.

    Consecutive calls return monotonically non-decreasing values (wall-clock
    monotonicity is guaranteed by ``datetime.now(timezone.utc)``).

    Returns:
        Current UTC timestamp, e.g. ``"2026-04-20T14:32:00.123456+00:00"``.
    """
    return datetime.now(timezone.utc).isoformat()
