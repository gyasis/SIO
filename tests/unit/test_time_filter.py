"""Unit tests for sio.mining.time_filter — T0XX.

Tests the time-filtering utilities that decide which session files fall
within a requested look-back window.

Public API under test
---------------------
    filter_files(paths: list[Path], since: str) -> list[Path]
        Return only the paths whose effective timestamp is >= the datetime
        produced by parse_since(since).  The effective timestamp is the
        filename-encoded datetime for SpecStory-style names
        (``YYYY-MM-DD_HH-MM-SSZ-<slug>.md``), or the file's mtime otherwise.

    parse_since(since: str) -> datetime
        Convert a human-readable look-back string such as "3 days",
        "1 week", "2 weeks", or "30 days" into an aware UTC datetime.

Design notes
------------
- os.utime() is used to set deterministic mtimes on tmp files so that
  mtime-based filtering is fully reproducible without sleeping.
- All produced datetimes are UTC-aware; tests freeze the reference point
  via monkeypatch where needed.
- SpecStory filename timestamp tests do NOT set mtime, deliberately
  verifying that the filename path is taken when the name matches.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sio.mining.time_filter import filter_files, parse_since  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UTC = timezone.utc


def _now() -> datetime:
    """Return the current UTC-aware datetime."""
    return datetime.now(_UTC)


def _set_mtime(path: Path, dt: datetime) -> None:
    """Set *path*'s modification time to *dt* (UTC-aware or naive-UTC ok)."""
    ts = dt.timestamp()
    os.utime(path, (ts, ts))


def _make_plain_file(tmp_path: Path, name: str, age: timedelta) -> Path:
    """Create a file whose mtime is ``now - age``.

    The filename does NOT encode a timestamp, so filter_files falls back
    to mtime for these files.
    """
    p = tmp_path / name
    p.write_text("content")
    _set_mtime(p, _now() - age)
    return p


def _make_specstory_file(
    tmp_path: Path,
    filename_dt: datetime,
    *,
    mtime_dt: datetime | None = None,
) -> Path:
    """Create a SpecStory-style file whose name encodes *filename_dt*.

    If *mtime_dt* is given the mtime is set to that value; otherwise the
    mtime is left at "now" (i.e. deliberately different from filename_dt)
    so that tests can assert the filename path is taken.
    """
    slug = filename_dt.strftime("%Y-%m-%d_%H-%M-%SZ-session-name.md")
    p = tmp_path / slug
    p.write_text("content")
    if mtime_dt is not None:
        _set_mtime(p, mtime_dt)
    return p


# ---------------------------------------------------------------------------
# 1. test_filter_by_days
# ---------------------------------------------------------------------------


class TestFilterByDays:
    """filter_files with 'N days' correctly partitions on mtime."""

    def test_recent_files_returned(self, tmp_path: Path) -> None:
        """Files newer than the cut-off must appear in the result."""
        recent = _make_plain_file(tmp_path, "recent.txt", timedelta(days=1))
        old = _make_plain_file(tmp_path, "old.txt", timedelta(days=5))

        result = filter_files([recent, old], "3 days")

        assert recent in result

    def test_old_files_excluded(self, tmp_path: Path) -> None:
        """Files older than the cut-off must NOT appear in the result."""
        recent = _make_plain_file(tmp_path, "recent.txt", timedelta(days=1))
        old = _make_plain_file(tmp_path, "old.txt", timedelta(days=5))

        result = filter_files([recent, old], "3 days")

        assert old not in result

    def test_boundary_file_included(self, tmp_path: Path) -> None:
        """A file whose mtime is exactly at the cut-off boundary is included."""
        # Shift slightly inside the window to avoid sub-second race conditions.
        boundary = _make_plain_file(tmp_path, "boundary.txt", timedelta(days=3) - timedelta(seconds=5))

        result = filter_files([boundary], "3 days")

        assert boundary in result

    def test_multiple_recent_all_returned(self, tmp_path: Path) -> None:
        """When several files are within the window all of them are returned."""
        files = [
            _make_plain_file(tmp_path, f"f{i}.txt", timedelta(hours=i * 6))
            for i in range(5)  # 0h, 6h, 12h, 18h, 24h — all within 3 days
        ]

        result = filter_files(files, "3 days")

        assert set(files) == set(result)

    def test_result_is_list_of_paths(self, tmp_path: Path) -> None:
        """Return type must be list[Path]."""
        f = _make_plain_file(tmp_path, "f.txt", timedelta(days=1))

        result = filter_files([f], "3 days")

        assert isinstance(result, list)
        assert all(isinstance(p, Path) for p in result)


# ---------------------------------------------------------------------------
# 2. test_filter_by_weeks
# ---------------------------------------------------------------------------


class TestFilterByWeeks:
    """filter_files with '1 week' (7 days) look-back window."""

    def test_within_week_returned(self, tmp_path: Path) -> None:
        """A file 3 days old is within a 1-week window."""
        recent = _make_plain_file(tmp_path, "recent.txt", timedelta(days=3))

        result = filter_files([recent], "1 week")

        assert recent in result

    def test_older_than_week_excluded(self, tmp_path: Path) -> None:
        """A file 10 days old exceeds a 1-week window."""
        old = _make_plain_file(tmp_path, "old.txt", timedelta(days=10))

        result = filter_files([old], "1 week")

        assert old not in result

    def test_mixed_files_correctly_partitioned(self, tmp_path: Path) -> None:
        """The correct subset is returned when files span the boundary."""
        inside = [
            _make_plain_file(tmp_path, f"in{i}.txt", timedelta(days=i + 1))
            for i in range(6)  # 1–6 days old, all inside 1 week
        ]
        outside = [
            _make_plain_file(tmp_path, f"out{i}.txt", timedelta(days=8 + i))
            for i in range(3)  # 8–10 days old, all outside
        ]

        result = filter_files(inside + outside, "1 week")

        assert set(inside) == set(result)
        for f in outside:
            assert f not in result


# ---------------------------------------------------------------------------
# 3. test_filter_by_custom_range
# ---------------------------------------------------------------------------


class TestFilterByCustomRange:
    """filter_files with '30 days' look-back window."""

    def test_within_30_days_returned(self, tmp_path: Path) -> None:
        """A file 20 days old is within a 30-day window."""
        f = _make_plain_file(tmp_path, "mid.txt", timedelta(days=20))

        result = filter_files([f], "30 days")

        assert f in result

    def test_older_than_30_days_excluded(self, tmp_path: Path) -> None:
        """A file 35 days old exceeds a 30-day window."""
        f = _make_plain_file(tmp_path, "old.txt", timedelta(days=35))

        result = filter_files([f], "30 days")

        assert f not in result

    def test_exactly_30_days_boundary(self, tmp_path: Path) -> None:
        """A file 30 days - 5 seconds old is included (at boundary)."""
        f = _make_plain_file(
            tmp_path, "boundary.txt", timedelta(days=30) - timedelta(seconds=5)
        )

        result = filter_files([f], "30 days")

        assert f in result

    def test_large_mixed_set(self, tmp_path: Path) -> None:
        """Stress test: 40 files, half inside the window, half outside."""
        inside = [
            _make_plain_file(tmp_path, f"in{i}.txt", timedelta(days=i + 1))
            for i in range(20)  # 1–20 days old
        ]
        outside = [
            _make_plain_file(tmp_path, f"out{i}.txt", timedelta(days=31 + i))
            for i in range(20)  # 31–50 days old
        ]

        result = filter_files(inside + outside, "30 days")

        assert set(inside) == set(result)


# ---------------------------------------------------------------------------
# 4. test_empty_result
# ---------------------------------------------------------------------------


class TestEmptyResult:
    """When all files fall outside the window, an empty list is returned."""

    def test_all_old_returns_empty(self, tmp_path: Path) -> None:
        """All files older than window → empty result."""
        files = [
            _make_plain_file(tmp_path, f"f{i}.txt", timedelta(days=10 + i))
            for i in range(5)
        ]

        result = filter_files(files, "3 days")

        assert result == []

    def test_return_type_is_list_when_empty(self, tmp_path: Path) -> None:
        """Return type must be list even when no files match."""
        f = _make_plain_file(tmp_path, "old.txt", timedelta(days=100))

        result = filter_files([f], "1 week")

        assert isinstance(result, list)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# 5. test_all_files_match
# ---------------------------------------------------------------------------


class TestAllFilesMatch:
    """When every file falls within the window, the full list is returned."""

    def test_all_recent_files_returned(self, tmp_path: Path) -> None:
        """Every file within the window appears in the result."""
        files = [
            _make_plain_file(tmp_path, f"f{i}.txt", timedelta(hours=i * 2))
            for i in range(6)  # 0h, 2h, 4h, 6h, 8h, 10h — all < 3 days
        ]

        result = filter_files(files, "3 days")

        assert set(files) == set(result)

    def test_result_length_matches_input(self, tmp_path: Path) -> None:
        """len(result) equals len(input) when all files match."""
        files = [
            _make_plain_file(tmp_path, f"f{i}.txt", timedelta(days=i))
            for i in range(5)  # 0–4 days, all within 1 week
        ]

        result = filter_files(files, "1 week")

        assert len(result) == len(files)


# ---------------------------------------------------------------------------
# 6. test_parse_since_days
# ---------------------------------------------------------------------------


class TestParseSinceDays:
    """parse_since correctly converts 'N days' into a UTC-aware datetime."""

    def test_3_days_roughly_correct(self) -> None:
        """'3 days' produces a datetime approximately 3 days before now."""
        before = _now()
        result = parse_since("3 days")
        after = _now()

        expected_low = before - timedelta(days=3) - timedelta(seconds=5)
        expected_high = after - timedelta(days=3) + timedelta(seconds=5)

        assert expected_low <= result <= expected_high

    def test_1_day(self) -> None:
        """'1 day' produces a datetime approximately 1 day before now."""
        before = _now()
        result = parse_since("1 day")
        after = _now()

        expected_low = before - timedelta(days=1) - timedelta(seconds=5)
        expected_high = after - timedelta(days=1) + timedelta(seconds=5)

        assert expected_low <= result <= expected_high

    def test_result_is_utc_aware(self) -> None:
        """parse_since must return a timezone-aware datetime."""
        result = parse_since("3 days")
        assert result.tzinfo is not None

    def test_30_days(self) -> None:
        """'30 days' produces a datetime approximately 30 days before now."""
        before = _now()
        result = parse_since("30 days")
        after = _now()

        expected_low = before - timedelta(days=30) - timedelta(seconds=5)
        expected_high = after - timedelta(days=30) + timedelta(seconds=5)

        assert expected_low <= result <= expected_high


# ---------------------------------------------------------------------------
# 7. test_parse_since_weeks
# ---------------------------------------------------------------------------


class TestParseSinceWeeks:
    """parse_since correctly converts 'N weeks' into a UTC-aware datetime."""

    def test_1_week(self) -> None:
        """'1 week' produces a datetime approximately 7 days before now."""
        before = _now()
        result = parse_since("1 week")
        after = _now()

        expected_low = before - timedelta(weeks=1) - timedelta(seconds=5)
        expected_high = after - timedelta(weeks=1) + timedelta(seconds=5)

        assert expected_low <= result <= expected_high

    def test_2_weeks(self) -> None:
        """'2 weeks' produces a datetime approximately 14 days before now."""
        before = _now()
        result = parse_since("2 weeks")
        after = _now()

        expected_low = before - timedelta(weeks=2) - timedelta(seconds=5)
        expected_high = after - timedelta(weeks=2) + timedelta(seconds=5)

        assert expected_low <= result <= expected_high

    def test_result_is_utc_aware(self) -> None:
        """parse_since('2 weeks') must return a timezone-aware datetime."""
        result = parse_since("2 weeks")
        assert result.tzinfo is not None

    def test_1_week_equals_7_days(self) -> None:
        """'1 week' and '7 days' must produce equivalent datetimes (±1 s)."""
        r_week = parse_since("1 week")
        r_days = parse_since("7 days")

        diff = abs((r_week - r_days).total_seconds())
        assert diff < 1.0, f"'1 week' and '7 days' diverged by {diff}s"


# ---------------------------------------------------------------------------
# 8. test_filename_timestamp
# ---------------------------------------------------------------------------


class TestFilenameTimestamp:
    """Files with SpecStory-style names use the filename timestamp, not mtime."""

    def test_recent_filename_with_old_mtime_is_included(self, tmp_path: Path) -> None:
        """filename encodes a recent date; mtime is very old → file IS returned."""
        # Filename says 1 day ago — within a 3-day window.
        filename_dt = _now() - timedelta(days=1)
        # Mtime says 30 days ago — would be excluded if mtime were used.
        mtime_dt = _now() - timedelta(days=30)

        p = _make_specstory_file(tmp_path, filename_dt, mtime_dt=mtime_dt)

        result = filter_files([p], "3 days")

        assert p in result, (
            "Expected file to be included based on filename timestamp, "
            "but it was filtered out (mtime was incorrectly used)."
        )

    def test_old_filename_with_recent_mtime_is_excluded(self, tmp_path: Path) -> None:
        """filename encodes an old date; mtime is fresh → file is NOT returned."""
        # Filename says 10 days ago — outside a 3-day window.
        filename_dt = _now() - timedelta(days=10)
        # Mtime says 1 hour ago — would be included if mtime were used.
        mtime_dt = _now() - timedelta(hours=1)

        p = _make_specstory_file(tmp_path, filename_dt, mtime_dt=mtime_dt)

        result = filter_files([p], "3 days")

        assert p not in result, (
            "Expected file to be excluded based on filename timestamp, "
            "but it was included (mtime was incorrectly used)."
        )

    def test_specstory_filename_within_window(self, tmp_path: Path) -> None:
        """A SpecStory file within the window is included when only mtime not set."""
        filename_dt = _now() - timedelta(days=2)
        p = _make_specstory_file(tmp_path, filename_dt)

        result = filter_files([p], "1 week")

        assert p in result

    def test_specstory_filename_outside_window(self, tmp_path: Path) -> None:
        """A SpecStory file outside the window is excluded."""
        filename_dt = _now() - timedelta(days=14)
        p = _make_specstory_file(tmp_path, filename_dt)

        result = filter_files([p], "1 week")

        assert p not in result

    def test_non_specstory_filename_uses_mtime(self, tmp_path: Path) -> None:
        """A file without a timestamp-encoded name falls back to mtime."""
        # Plain name — no SpecStory pattern.
        p = tmp_path / "plain_session.md"
        p.write_text("content")
        # Set mtime to 1 day ago (within 3-day window).
        _set_mtime(p, _now() - timedelta(days=1))

        result = filter_files([p], "3 days")

        assert p in result

    @pytest.mark.parametrize(
        "filename",
        [
            "2026-02-25_10-00-00Z-session-name.md",
            "2025-12-01_00-00-00Z-first-session.md",
            "2026-01-15_23-59-59Z-edge-case.md",
        ],
    )
    def test_various_valid_specstory_filenames_parsed(
        self, tmp_path: Path, filename: str
    ) -> None:
        """Any SpecStory-formatted filename is correctly parsed without error."""
        p = tmp_path / filename
        p.write_text("content")
        # Set mtime to "now" so any test failure isolates the filename path.
        _set_mtime(p, _now())

        # We only verify that parse does not raise; result value depends on age.
        try:
            filter_files([p], "30 days")
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"filter_files raised on valid SpecStory filename: {exc}")


# ---------------------------------------------------------------------------
# 9. test_empty_input
# ---------------------------------------------------------------------------


class TestEmptyInput:
    """Edge cases around an empty paths list."""

    def test_empty_list_returns_empty_list(self) -> None:
        """filter_files([]) must return an empty list."""
        result = filter_files([], "3 days")
        assert result == []

    def test_return_type_is_list_for_empty_input(self) -> None:
        """Return type is list even for empty input."""
        result = filter_files([], "1 week")
        assert isinstance(result, list)

    def test_empty_input_with_various_since_strings(self) -> None:
        """Empty input is handled consistently regardless of the since string."""
        for since in ("1 day", "3 days", "1 week", "2 weeks", "30 days"):
            result = filter_files([], since)
            assert result == [], f"Expected empty list for since={since!r}, got {result}"
