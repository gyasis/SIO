"""Unit tests for sio.core.metrics.velocity — T029 [US3].

Tests learning velocity tracking: error frequency computation over rolling
windows, correction decay rate after rule application, and adaptation speed.

Acceptance criteria: FR-014, FR-015, FR-016.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sio.core.metrics.velocity import compute_velocity_snapshot, get_velocity_trends

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(timezone.utc)


def _ts(days_ago: float = 0, hours_ago: float = 0) -> str:
    """Return ISO timestamp offset from now."""
    delta = timedelta(days=days_ago, hours=hours_ago)
    return (_NOW - delta).isoformat()


def _insert_error(
    db,
    error_type: str = "unused_import",
    session_id: str = "sess-001",
    days_ago: float = 0,
    hours_ago: float = 0,
    tool_name: str = "Bash",
) -> int:
    """Insert a single error_record and return its id."""
    ts = _ts(days_ago=days_ago, hours_ago=hours_ago)
    cur = db.execute(
        "INSERT INTO error_records "
        "(session_id, timestamp, source_type, source_file, tool_name, "
        "error_text, error_type, mined_at) "
        "VALUES (?, ?, 'jsonl', 'test.jsonl', ?, 'test error', ?, ?)",
        (session_id, ts, tool_name, error_type, _NOW.isoformat()),
    )
    db.commit()
    return cur.lastrowid


def _insert_suggestion(
    db,
    error_type: str = "unused_import",
    status: str = "approved",
) -> int:
    """Insert a suggestion that references the given error_type."""
    cur = db.execute(
        "INSERT INTO suggestions "
        "(description, confidence, proposed_change, target_file, "
        "change_type, status, created_at) "
        "VALUES (?, 0.9, ?, 'CLAUDE.md', 'append', ?, ?)",
        (
            f"Fix {error_type} errors",
            f"Add rule to prevent {error_type}",
            status,
            _NOW.isoformat(),
        ),
    )
    db.commit()
    return cur.lastrowid


def _apply_suggestion(
    db,
    suggestion_id: int,
    days_ago: float = 3,
) -> int:
    """Insert an applied_change for a suggestion."""
    applied_at = _ts(days_ago=days_ago)
    cur = db.execute(
        "INSERT INTO applied_changes "
        "(suggestion_id, target_file, diff_before, diff_after, applied_at) "
        "VALUES (?, 'CLAUDE.md', 'before', 'after', ?)",
        (suggestion_id, applied_at),
    )
    db.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Tests: compute_velocity_snapshot — basic error rate (FR-014)
# ---------------------------------------------------------------------------


class TestComputeVelocitySnapshotBasic:
    """Test basic error rate computation over rolling window."""

    def test_returns_dict_with_required_keys(self, tmp_db):
        """Snapshot dict contains all documented keys."""
        _insert_error(tmp_db, error_type="unused_import", days_ago=1)
        result = compute_velocity_snapshot(tmp_db, "unused_import", window_days=7)

        required_keys = {
            "error_type",
            "error_rate",
            "error_count_in_window",
            "correction_decay_rate",
            "adaptation_speed",
            "rule_applied",
            "rule_suggestion_id",
            "window_start",
            "window_end",
            "created_at",
        }
        assert required_keys.issubset(result.keys())

    def test_error_rate_ten_of_ten(self, tmp_db):
        """10 errors of type 'unused_import' out of 10 total -> rate = 1.0."""
        for i in range(10):
            _insert_error(
                tmp_db,
                error_type="unused_import",
                session_id=f"sess-{i:03d}",
                days_ago=i * 0.5,
            )
        result = compute_velocity_snapshot(tmp_db, "unused_import", window_days=7)

        assert result["error_count_in_window"] == 10
        assert result["error_rate"] == pytest.approx(1.0)

    def test_error_rate_partial(self, tmp_db):
        """10 unused_import + 10 other_error -> rate = 0.5."""
        for i in range(10):
            _insert_error(
                tmp_db,
                error_type="unused_import",
                session_id=f"sess-{i:03d}",
                hours_ago=i,
            )
        for i in range(10):
            _insert_error(
                tmp_db,
                error_type="other_error",
                session_id=f"sess-other-{i:03d}",
                hours_ago=i,
            )

        result = compute_velocity_snapshot(tmp_db, "unused_import", window_days=7)

        assert result["error_count_in_window"] == 10
        assert result["error_rate"] == pytest.approx(0.5)

    def test_errors_outside_window_excluded(self, tmp_db):
        """Errors older than window_days are not counted."""
        # 5 errors within window
        for i in range(5):
            _insert_error(tmp_db, days_ago=i, session_id=f"sess-in-{i}")
        # 5 errors outside window (8+ days ago for a 7-day window)
        for i in range(5):
            _insert_error(tmp_db, days_ago=8 + i, session_id=f"sess-out-{i}")

        result = compute_velocity_snapshot(tmp_db, "unused_import", window_days=7)

        assert result["error_count_in_window"] == 5

    def test_persists_to_velocity_snapshots_table(self, tmp_db):
        """Snapshot is inserted into velocity_snapshots table."""
        _insert_error(tmp_db, days_ago=1)
        compute_velocity_snapshot(tmp_db, "unused_import", window_days=7)

        rows = tmp_db.execute(
            "SELECT * FROM velocity_snapshots WHERE error_type = 'unused_import'"
        ).fetchall()
        assert len(rows) == 1
        assert dict(rows[0])["error_type"] == "unused_import"


# ---------------------------------------------------------------------------
# Tests: pre-rule baseline (FR-014)
# ---------------------------------------------------------------------------


class TestPreRuleBaseline:
    """Before any rule is applied, baseline metrics."""

    def test_no_rule_applied(self, tmp_db):
        """When no suggestion is applied, rule_applied is False."""
        for i in range(10):
            _insert_error(tmp_db, days_ago=i * 0.5, session_id=f"sess-{i}")

        result = compute_velocity_snapshot(tmp_db, "unused_import", window_days=7)

        assert result["rule_applied"] is False
        assert result["rule_suggestion_id"] is None
        assert result["correction_decay_rate"] is None
        assert result["adaptation_speed"] is None

    def test_baseline_rate_calculation(self, tmp_db):
        """10 unused_import errors in 7-day window, total 20 -> rate = 0.5."""
        for i in range(10):
            _insert_error(
                tmp_db,
                error_type="unused_import",
                session_id=f"sess-ui-{i}",
                hours_ago=i * 2,
            )
        for i in range(10):
            _insert_error(
                tmp_db,
                error_type="select_star",
                session_id=f"sess-ss-{i}",
                hours_ago=i * 2,
            )

        result = compute_velocity_snapshot(tmp_db, "unused_import", window_days=7)

        assert result["error_rate"] == pytest.approx(0.5)
        assert result["error_count_in_window"] == 10


# ---------------------------------------------------------------------------
# Tests: post-rule improvement — correction_decay_rate (FR-015)
# ---------------------------------------------------------------------------


class TestCorrectionDecayRate:
    """Measure how quickly errors decrease after rule applied."""

    def test_improvement_detected(self, tmp_db):
        """After rule applied, fewer errors -> positive correction_decay_rate."""
        # Pre-rule: 10 errors of type before rule applied (4 days ago)
        for i in range(10):
            _insert_error(
                tmp_db,
                error_type="unused_import",
                session_id=f"sess-pre-{i}",
                days_ago=5 + i * 0.1,
            )
        # Also have some total context
        for i in range(10):
            _insert_error(
                tmp_db,
                error_type="other",
                session_id=f"sess-pre-other-{i}",
                days_ago=5 + i * 0.1,
            )

        # Apply a rule 3 days ago
        sug_id = _insert_suggestion(tmp_db, error_type="unused_import")
        _apply_suggestion(tmp_db, sug_id, days_ago=3)

        # Post-rule: only 3 errors of type in recent window (improvement)
        for i in range(3):
            _insert_error(
                tmp_db,
                error_type="unused_import",
                session_id=f"sess-post-{i}",
                days_ago=i * 0.5,
            )
        # And 10 other errors to maintain total context
        for i in range(10):
            _insert_error(
                tmp_db,
                error_type="other",
                session_id=f"sess-post-other-{i}",
                days_ago=i * 0.5,
            )

        result = compute_velocity_snapshot(tmp_db, "unused_import", window_days=7)

        assert result["rule_applied"] is True
        assert result["rule_suggestion_id"] == sug_id
        # correction_decay_rate should be positive (improvement)
        assert result["correction_decay_rate"] is not None
        assert result["correction_decay_rate"] > 0

    def test_rolled_back_rule_not_counted(self, tmp_db):
        """A rolled-back rule should not be considered as applied."""
        _insert_error(tmp_db, days_ago=1)
        sug_id = _insert_suggestion(tmp_db, error_type="unused_import")
        ac_id = _apply_suggestion(tmp_db, sug_id, days_ago=2)

        # Mark as rolled back
        tmp_db.execute(
            "UPDATE applied_changes SET rolled_back_at = ? WHERE id = ?",
            (_NOW.isoformat(), ac_id),
        )
        tmp_db.commit()

        result = compute_velocity_snapshot(tmp_db, "unused_import", window_days=7)

        assert result["rule_applied"] is False
        assert result["correction_decay_rate"] is None


# ---------------------------------------------------------------------------
# Tests: adaptation_speed (FR-016)
# ---------------------------------------------------------------------------


class TestAdaptationSpeed:
    """Number of sessions until error rate drops below threshold."""

    def test_fast_adaptation(self, tmp_db):
        """Error rate drops quickly -> low adaptation_speed."""
        # Pre-rule: 10 errors across 2 sessions
        for i in range(5):
            _insert_error(
                tmp_db,
                error_type="unused_import",
                session_id="sess-pre-a",
                days_ago=5 + i * 0.01,
            )
        for i in range(5):
            _insert_error(
                tmp_db,
                error_type="unused_import",
                session_id="sess-pre-b",
                days_ago=5.1 + i * 0.01,
            )
        # Need total errors for rate denominator
        for i in range(10):
            _insert_error(
                tmp_db,
                error_type="other",
                session_id=f"sess-pre-other-{i}",
                days_ago=5 + i * 0.05,
            )

        # Apply rule 3 days ago
        sug_id = _insert_suggestion(tmp_db, error_type="unused_import")
        _apply_suggestion(tmp_db, sug_id, days_ago=3)

        # Post-rule: 3 sessions with decreasing errors
        # Session 1: 2 errors of type + 10 other
        for i in range(2):
            _insert_error(
                tmp_db,
                error_type="unused_import",
                session_id="sess-post-1",
                days_ago=2 + i * 0.01,
            )
        for i in range(10):
            _insert_error(
                tmp_db,
                error_type="other",
                session_id="sess-post-1",
                days_ago=2 + i * 0.01,
            )

        # Session 2: 0 errors of type + 10 other
        for i in range(10):
            _insert_error(
                tmp_db,
                error_type="other",
                session_id="sess-post-2",
                days_ago=1 + i * 0.01,
            )

        result = compute_velocity_snapshot(tmp_db, "unused_import", window_days=7)

        assert result["rule_applied"] is True
        assert result["adaptation_speed"] is not None
        # Should have adapted within a small number of sessions
        assert result["adaptation_speed"] <= 3


# ---------------------------------------------------------------------------
# Tests: edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases: empty DB, no errors of type, no rules applied."""

    def test_no_errors_at_all(self, tmp_db):
        """Empty DB -> rate 0, no rule applied."""
        result = compute_velocity_snapshot(tmp_db, "unused_import", window_days=7)

        assert result["error_rate"] == 0.0
        assert result["error_count_in_window"] == 0
        assert result["rule_applied"] is False
        assert result["correction_decay_rate"] is None
        assert result["adaptation_speed"] is None

    def test_no_errors_of_type(self, tmp_db):
        """Errors exist but none of the requested type."""
        for i in range(5):
            _insert_error(
                tmp_db,
                error_type="select_star",
                session_id=f"sess-{i}",
                days_ago=i,
            )

        result = compute_velocity_snapshot(tmp_db, "unused_import", window_days=7)

        assert result["error_rate"] == 0.0
        assert result["error_count_in_window"] == 0

    def test_suggestion_exists_but_not_applied(self, tmp_db):
        """A pending suggestion (not applied) is not counted."""
        _insert_error(tmp_db, days_ago=1)
        _insert_suggestion(tmp_db, error_type="unused_import", status="pending")

        result = compute_velocity_snapshot(tmp_db, "unused_import", window_days=7)

        assert result["rule_applied"] is False

    def test_custom_window_days(self, tmp_db):
        """Window of 1 day excludes errors from 2 days ago."""
        _insert_error(tmp_db, days_ago=0.5, session_id="sess-recent")
        _insert_error(tmp_db, days_ago=2, session_id="sess-old")

        result = compute_velocity_snapshot(tmp_db, "unused_import", window_days=1)

        assert result["error_count_in_window"] == 1


# ---------------------------------------------------------------------------
# Tests: get_velocity_trends
# ---------------------------------------------------------------------------


class TestGetVelocityTrends:
    """Query velocity_snapshots ordered by time."""

    def test_returns_empty_for_no_snapshots(self, tmp_db):
        """No snapshots -> empty list."""
        result = get_velocity_trends(tmp_db)
        assert result == []

    def test_returns_snapshots_ordered_by_time(self, tmp_db):
        """Multiple snapshots returned in chronological order."""
        # Insert errors and compute two snapshots
        _insert_error(tmp_db, error_type="unused_import", days_ago=1)
        compute_velocity_snapshot(tmp_db, "unused_import", window_days=7)

        _insert_error(tmp_db, error_type="select_star", days_ago=1)
        compute_velocity_snapshot(tmp_db, "select_star", window_days=7)

        trends = get_velocity_trends(tmp_db)
        assert len(trends) == 2
        # First snapshot was created before the second
        assert trends[0]["created_at"] <= trends[1]["created_at"]

    def test_filter_by_error_type(self, tmp_db):
        """Filtering by error_type returns only matching snapshots."""
        _insert_error(tmp_db, error_type="unused_import", days_ago=1)
        compute_velocity_snapshot(tmp_db, "unused_import", window_days=7)

        _insert_error(tmp_db, error_type="select_star", days_ago=1)
        compute_velocity_snapshot(tmp_db, "select_star", window_days=7)

        trends = get_velocity_trends(tmp_db, error_type="unused_import")
        assert len(trends) == 1
        assert trends[0]["error_type"] == "unused_import"

    def test_filter_none_returns_all(self, tmp_db):
        """Passing error_type=None returns all snapshots."""
        _insert_error(tmp_db, error_type="unused_import", days_ago=1)
        compute_velocity_snapshot(tmp_db, "unused_import", window_days=7)

        _insert_error(tmp_db, error_type="select_star", days_ago=1)
        compute_velocity_snapshot(tmp_db, "select_star", window_days=7)

        trends = get_velocity_trends(tmp_db, error_type=None)
        assert len(trends) == 2

    def test_snapshot_dict_has_all_columns(self, tmp_db):
        """Each snapshot dict contains all velocity_snapshots columns."""
        _insert_error(tmp_db, error_type="unused_import", days_ago=1)
        compute_velocity_snapshot(tmp_db, "unused_import", window_days=7)

        trends = get_velocity_trends(tmp_db, error_type="unused_import")
        assert len(trends) == 1

        snapshot = trends[0]
        expected_keys = {
            "id",
            "error_type",
            "session_id",
            "error_rate",
            "error_count_in_window",
            "window_start",
            "window_end",
            "rule_applied",
            "rule_suggestion_id",
            "created_at",
        }
        assert expected_keys.issubset(snapshot.keys())
