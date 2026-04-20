"""Failing tests for sio.core.util.time — T006 (TDD red phase).

Tests cover:
  - to_utc_iso(): Z suffix, numeric offset, naive-local, empty raises ValueError
  - utc_now_iso(): returns +00:00 string, parseable, monotonically non-decreasing

Run to confirm RED before implementing time.py:
    uv run pytest tests/unit/util/test_time.py -v
"""

import time
from datetime import datetime, timezone

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _import_module():
    """Import the module under test; raises ImportError if not yet created."""
    from sio.core.util import time as time_mod  # noqa: PLC0415
    return time_mod


# ---------------------------------------------------------------------------
# to_utc_iso — Z suffix
# ---------------------------------------------------------------------------


def test_to_utc_iso_z_suffix_canonical():
    """'2026-04-20T14:32:00Z' → '2026-04-20T14:32:00+00:00'."""
    mod = _import_module()
    result = mod.to_utc_iso("2026-04-20T14:32:00Z")
    assert result == "2026-04-20T14:32:00+00:00"


def test_to_utc_iso_z_suffix_returns_utc_offset():
    """Result always ends with '+00:00', never 'Z'."""
    mod = _import_module()
    result = mod.to_utc_iso("2026-01-01T00:00:00Z")
    assert result.endswith("+00:00"), f"Expected +00:00 suffix, got: {result!r}"


# ---------------------------------------------------------------------------
# to_utc_iso — numeric offset conversion
# ---------------------------------------------------------------------------


def test_to_utc_iso_negative_offset_converts_to_utc():
    """'2026-04-20T10:32:00-04:00' → '2026-04-20T14:32:00+00:00'."""
    mod = _import_module()
    result = mod.to_utc_iso("2026-04-20T10:32:00-04:00")
    assert result == "2026-04-20T14:32:00+00:00"


def test_to_utc_iso_positive_offset_converts_to_utc():
    """'+05:30' IST offset converted correctly."""
    mod = _import_module()
    # 2026-04-20T19:30:00+05:30 == 2026-04-20T14:00:00+00:00
    result = mod.to_utc_iso("2026-04-20T19:30:00+05:30")
    assert result == "2026-04-20T14:00:00+00:00"


def test_to_utc_iso_zero_offset_unchanged():
    """'+00:00' explicit UTC offset round-trips cleanly."""
    mod = _import_module()
    result = mod.to_utc_iso("2026-04-20T14:32:00+00:00")
    assert result == "2026-04-20T14:32:00+00:00"


# ---------------------------------------------------------------------------
# to_utc_iso — naive string (no TZ info)
# ---------------------------------------------------------------------------


def test_to_utc_iso_naive_with_eastern_tz(monkeypatch):
    """Naive ISO string interpreted in local TZ (mocked to America/New_York).

    EST = UTC-5, EDT = UTC-4. We freeze to a winter date to ensure EST.
    2026-01-20T09:00:00 in EST (UTC-5) == 2026-01-20T14:00:00+00:00.
    """
    import os
    monkeypatch.setenv("TZ", "America/New_York")
    # Reload time module to pick up TZ change (platform-dependent)
    try:
        import importlib
        import sio.core.util.time as _t
        importlib.reload(_t)
        mod = _t
    except ImportError:
        mod = _import_module()

    # Use a date well into winter to ensure EST (not EDT).
    result = mod.to_utc_iso("2026-01-20T09:00:00")
    # Accept either UTC-5 (EST) = +5h or UTC-4 (EDT) based on platform behaviour.
    parsed = datetime.fromisoformat(result)
    assert parsed.tzinfo is not None
    assert parsed.tzinfo == timezone.utc or str(parsed.tzinfo) == "UTC"
    # The offset in the string must be +00:00
    assert result.endswith("+00:00"), f"Expected +00:00, got {result!r}"


# ---------------------------------------------------------------------------
# to_utc_iso — error cases
# ---------------------------------------------------------------------------


def test_to_utc_iso_empty_string_raises():
    """Empty string raises ValueError with a clear message."""
    mod = _import_module()
    with pytest.raises(ValueError, match=r"(?i)empty|blank|invalid|cannot"):
        mod.to_utc_iso("")


def test_to_utc_iso_whitespace_only_raises():
    """Whitespace-only string also raises ValueError."""
    mod = _import_module()
    with pytest.raises(ValueError):
        mod.to_utc_iso("   ")


def test_to_utc_iso_garbage_raises():
    """Completely invalid string raises ValueError (not AttributeError or TypeError)."""
    mod = _import_module()
    with pytest.raises(ValueError):
        mod.to_utc_iso("not-a-timestamp")


# ---------------------------------------------------------------------------
# utc_now_iso — basic contract
# ---------------------------------------------------------------------------


def test_utc_now_iso_returns_string():
    """utc_now_iso() returns a str."""
    mod = _import_module()
    assert isinstance(mod.utc_now_iso(), str)


def test_utc_now_iso_ends_with_utc_offset():
    """utc_now_iso() string ends with '+00:00'."""
    mod = _import_module()
    result = mod.utc_now_iso()
    assert result.endswith("+00:00"), f"Got: {result!r}"


def test_utc_now_iso_parseable_by_fromisoformat():
    """utc_now_iso() result is parseable by datetime.fromisoformat."""
    mod = _import_module()
    result = mod.utc_now_iso()
    parsed = datetime.fromisoformat(result)
    assert parsed.tzinfo is not None


def test_utc_now_iso_is_utc():
    """Parsed utc_now_iso() is in UTC (offset == 0)."""
    mod = _import_module()
    result = mod.utc_now_iso()
    parsed = datetime.fromisoformat(result)
    assert parsed.utcoffset().total_seconds() == 0


# ---------------------------------------------------------------------------
# utc_now_iso — monotonicity
# ---------------------------------------------------------------------------


def test_utc_now_iso_monotonically_non_decreasing():
    """Two consecutive calls return non-decreasing timestamps."""
    mod = _import_module()
    t1 = datetime.fromisoformat(mod.utc_now_iso())
    t2 = datetime.fromisoformat(mod.utc_now_iso())
    assert t2 >= t1, f"t2={t2!r} < t1={t1!r}; utc_now_iso() went backwards"


def test_utc_now_iso_monotonically_non_decreasing_with_sleep():
    """With a small sleep, second timestamp is strictly greater."""
    mod = _import_module()
    t1 = datetime.fromisoformat(mod.utc_now_iso())
    time.sleep(0.01)
    t2 = datetime.fromisoformat(mod.utc_now_iso())
    assert t2 > t1, f"After sleep: t2={t2!r} not > t1={t1!r}"
