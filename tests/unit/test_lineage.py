"""T029 [US3] Unit tests for sio.datasets.lineage — dataset provenance tracking.

Tests cover:
- track_lineage(dataset_id, sessions, time_window, db_conn) -> None
    Records which sessions and what time window contributed to a dataset.
- get_lineage(dataset_id, db_conn) -> dict
    Retrieves the persisted lineage record for a given dataset.

These tests are intentionally RED until the implementation is written.
"""

from __future__ import annotations

import sqlite3

from sio.datasets.lineage import get_lineage, track_lineage

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


_NOW = "2026-02-25T10:00:00Z"


def _insert_pattern(
    conn: sqlite3.Connection,
    *,
    pattern_id: str = "p-lineage-001",
) -> int:
    """Insert a minimal patterns row and return its rowid."""
    cursor = conn.execute(
        """
        INSERT INTO patterns
            (pattern_id, description, error_count, session_count,
             first_seen, last_seen, rank_score, created_at, updated_at)
        VALUES (?, 'lineage test pattern', 1, 1, ?, ?, 0.5, ?, ?)
        """,
        (pattern_id, _NOW, _NOW, _NOW, _NOW),
    )
    conn.commit()
    return cursor.lastrowid


def _insert_dataset(
    conn: sqlite3.Connection,
    pattern_row_id: int,
    *,
    file_path: str = "/tmp/test_dataset.json",
    positive_count: int = 5,
    negative_count: int = 5,
) -> int:
    """Insert a minimal datasets row and return its rowid."""
    cursor = conn.execute(
        """
        INSERT INTO datasets
            (pattern_id, file_path, positive_count, negative_count,
             created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (pattern_row_id, file_path, positive_count, negative_count, _NOW, _NOW),
    )
    conn.commit()
    return cursor.lastrowid


# ---------------------------------------------------------------------------
# T029-1: track_lineage stores the contributing sessions
# ---------------------------------------------------------------------------


class TestTracksContributingSessions:
    """track_lineage must persist the contributing sessions list."""

    def test_tracks_contributing_sessions(
        self, v2_db: sqlite3.Connection
    ) -> None:
        pattern_row_id = _insert_pattern(v2_db, pattern_id="p-lin-sess-001")
        dataset_id = _insert_dataset(v2_db, pattern_row_id)

        sessions = ["session-a", "session-b", "session-c"]
        track_lineage(dataset_id, sessions, "2 weeks", v2_db)

        lineage = get_lineage(dataset_id, v2_db)

        assert "sessions" in lineage, "get_lineage must return a dict with 'sessions'"
        assert set(lineage["sessions"]) == set(sessions), (
            f"Expected sessions {sessions!r}, got {lineage['sessions']!r}"
        )

    def test_single_session_tracked(
        self, v2_db: sqlite3.Connection
    ) -> None:
        pattern_row_id = _insert_pattern(v2_db, pattern_id="p-lin-single-001")
        dataset_id = _insert_dataset(v2_db, pattern_row_id, file_path="/tmp/ds2.json")

        track_lineage(dataset_id, ["only-session"], "1 day", v2_db)

        lineage = get_lineage(dataset_id, v2_db)

        assert lineage["sessions"] == ["only-session"], (
            f"Expected ['only-session'], got {lineage['sessions']!r}"
        )

    def test_empty_sessions_list_tracked(
        self, v2_db: sqlite3.Connection
    ) -> None:
        """track_lineage with an empty sessions list must not raise."""
        pattern_row_id = _insert_pattern(v2_db, pattern_id="p-lin-empty-001")
        dataset_id = _insert_dataset(
            v2_db, pattern_row_id, file_path="/tmp/ds_empty.json"
        )

        # Must not raise even with an empty list.
        track_lineage(dataset_id, [], "1 week", v2_db)

        lineage = get_lineage(dataset_id, v2_db)
        assert lineage["sessions"] == [] or lineage["sessions"] is not None


# ---------------------------------------------------------------------------
# T029-2: track_lineage records the time window
# ---------------------------------------------------------------------------


class TestTracksTimeWindow:
    """track_lineage must persist the time_window string."""

    def test_tracks_time_window(
        self, v2_db: sqlite3.Connection
    ) -> None:
        pattern_row_id = _insert_pattern(v2_db, pattern_id="p-lin-tw-001")
        dataset_id = _insert_dataset(
            v2_db, pattern_row_id, file_path="/tmp/ds_tw.json"
        )

        track_lineage(dataset_id, ["sess-x"], "2 weeks", v2_db)

        lineage = get_lineage(dataset_id, v2_db)

        assert "time_window" in lineage, (
            "get_lineage must return a dict with 'time_window'"
        )
        assert lineage["time_window"] == "2 weeks", (
            f"Expected time_window='2 weeks', got {lineage['time_window']!r}"
        )

    def test_varied_time_window_strings(
        self, v2_db: sqlite3.Connection
    ) -> None:
        """Arbitrary time_window strings must round-trip without modification."""
        _insert_pattern(v2_db, pattern_id="p-lin-twv-001")

        for window in ("30 days", "3 months", "since last deploy"):
            # Re-insert a fresh dataset for each variant to avoid state leakage.
            p_id = _insert_pattern(
                v2_db,
                pattern_id=f"p-lin-twv-{window.replace(' ', '-')}",
            )
            ds_id = _insert_dataset(
                v2_db, p_id, file_path=f"/tmp/ds_{window.replace(' ', '_')}.json"
            )
            track_lineage(ds_id, ["s1"], window, v2_db)
            result = get_lineage(ds_id, v2_db)
            assert result["time_window"] == window, (
                f"time_window '{window}' did not round-trip; got {result['time_window']!r}"
            )

    def test_get_lineage_includes_dataset_id(
        self, v2_db: sqlite3.Connection
    ) -> None:
        pattern_row_id = _insert_pattern(v2_db, pattern_id="p-lin-dsid-001")
        dataset_id = _insert_dataset(
            v2_db, pattern_row_id, file_path="/tmp/ds_dsid.json"
        )

        track_lineage(dataset_id, ["sess-1"], "1 month", v2_db)

        lineage = get_lineage(dataset_id, v2_db)

        assert "dataset_id" in lineage, (
            "get_lineage result must include 'dataset_id'"
        )
        assert lineage["dataset_id"] == dataset_id


# ---------------------------------------------------------------------------
# T029-3: lineage persists across updates — all sessions present after re-track
# ---------------------------------------------------------------------------


class TestLineagePersistsAcrossUpdates:
    """Calling track_lineage twice must accumulate all sessions (no overwrite)."""

    def test_lineage_persists_across_updates(
        self, v2_db: sqlite3.Connection
    ) -> None:
        pattern_row_id = _insert_pattern(v2_db, pattern_id="p-lin-accum-001")
        dataset_id = _insert_dataset(
            v2_db, pattern_row_id, file_path="/tmp/ds_accum.json"
        )

        initial_sessions = ["alpha", "beta"]
        track_lineage(dataset_id, initial_sessions, "1 week", v2_db)

        additional_sessions = ["gamma", "delta"]
        track_lineage(dataset_id, additional_sessions, "1 week", v2_db)

        lineage = get_lineage(dataset_id, v2_db)

        all_expected = set(initial_sessions) | set(additional_sessions)
        assert set(lineage["sessions"]) == all_expected, (
            f"Expected all sessions {all_expected!r}; "
            f"got {set(lineage['sessions'])!r}"
        )

    def test_no_duplicate_sessions_after_re_track(
        self, v2_db: sqlite3.Connection
    ) -> None:
        """Tracking the same sessions twice must not produce duplicates."""
        pattern_row_id = _insert_pattern(v2_db, pattern_id="p-lin-dedup-001")
        dataset_id = _insert_dataset(
            v2_db, pattern_row_id, file_path="/tmp/ds_dedup.json"
        )

        sessions = ["alpha", "beta"]
        track_lineage(dataset_id, sessions, "2 weeks", v2_db)
        # Track the exact same sessions again.
        track_lineage(dataset_id, sessions, "2 weeks", v2_db)

        lineage = get_lineage(dataset_id, v2_db)

        assert len(lineage["sessions"]) == len(set(lineage["sessions"])), (
            "Duplicate session IDs must not appear in lineage"
        )

    def test_track_twice_time_window_updated(
        self, v2_db: sqlite3.Connection
    ) -> None:
        """The most recent time_window must be reflected after re-tracking."""
        pattern_row_id = _insert_pattern(v2_db, pattern_id="p-lin-tw-update-001")
        dataset_id = _insert_dataset(
            v2_db, pattern_row_id, file_path="/tmp/ds_twupdate.json"
        )

        track_lineage(dataset_id, ["s1"], "1 week", v2_db)
        track_lineage(dataset_id, ["s2"], "3 weeks", v2_db)

        lineage = get_lineage(dataset_id, v2_db)

        # After the second call the time_window should reflect the latest value.
        assert lineage["time_window"] == "3 weeks", (
            f"Expected updated time_window '3 weeks', got {lineage['time_window']!r}"
        )

    def test_get_lineage_unknown_dataset_returns_none(
        self, v2_db: sqlite3.Connection
    ) -> None:
        """get_lineage on a dataset_id that was never tracked must return None."""
        result = get_lineage(999_999, v2_db)
        assert result is None, (
            f"Expected None for unknown dataset_id; got {result!r}"
        )
