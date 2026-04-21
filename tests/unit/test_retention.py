"""Unit tests for retention purge logic."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sio.core.db.retention import purge


def _insert_invocation(conn, record):
    cols = [
        "session_id",
        "timestamp",
        "platform",
        "user_message",
        "behavior_type",
        "actual_action",
        "expected_action",
        "activated",
        "correct_action",
        "correct_outcome",
        "user_satisfied",
        "user_note",
        "passive_signal",
        "history_file",
        "line_start",
        "line_end",
        "token_count",
        "latency_ms",
        "labeled_by",
        "labeled_at",
    ]
    placeholders = ", ".join(["?"] * len(cols))
    col_names = ", ".join(cols)
    values = [record.get(c) for c in cols]
    cur = conn.execute(
        f"INSERT INTO behavior_invocations ({col_names}) VALUES ({placeholders})", values
    )
    conn.commit()
    return cur.lastrowid


def _insert_gold_standard(conn, invocation_id):
    conn.execute(
        "INSERT INTO gold_standards (invocation_id, platform, skill_name, user_message, expected_action, created_at, exempt_from_purge) "
        "VALUES (?, 'claude-code', 'Read', 'test', 'Read', ?, 1)",
        (invocation_id, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def _row_count(conn):
    return conn.execute("SELECT COUNT(*) FROM behavior_invocations").fetchone()[0]


def _days_ago(n):
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat()


class TestPurge:
    def test_purge_old_records(self, tmp_db, sample_invocation):
        _insert_invocation(tmp_db, sample_invocation(timestamp=_days_ago(100)))
        assert purge(tmp_db, older_than_days=90) == 1
        assert _row_count(tmp_db) == 0

    def test_purge_keeps_recent(self, tmp_db, sample_invocation):
        _insert_invocation(tmp_db, sample_invocation(timestamp=_days_ago(10)))
        assert purge(tmp_db, older_than_days=90) == 0
        assert _row_count(tmp_db) == 1

    def test_purge_gold_standard_exempt(self, tmp_db, sample_invocation):
        row_id = _insert_invocation(tmp_db, sample_invocation(timestamp=_days_ago(100)))
        _insert_gold_standard(tmp_db, row_id)
        assert purge(tmp_db, older_than_days=90) == 0
        assert _row_count(tmp_db) == 1

    def test_purge_custom_days(self, tmp_db, sample_invocation):
        _insert_invocation(tmp_db, sample_invocation(timestamp=_days_ago(40)))
        _insert_invocation(
            tmp_db,
            sample_invocation(timestamp=_days_ago(20), tool_name="Glob", actual_action="Glob"),
        )
        assert purge(tmp_db, older_than_days=30) == 1
        assert _row_count(tmp_db) == 1

    def test_dry_run_returns_count(self, tmp_db, sample_invocation):
        for i in range(5):
            _insert_invocation(
                tmp_db, sample_invocation(timestamp=_days_ago(100 + i), session_id=f"s-{i}")
            )
        assert purge(tmp_db, older_than_days=90, dry_run=True) == 5

    def test_dry_run_no_deletion(self, tmp_db, sample_invocation):
        for i in range(3):
            _insert_invocation(
                tmp_db, sample_invocation(timestamp=_days_ago(100 + i), session_id=f"s-{i}")
            )
        purge(tmp_db, older_than_days=90, dry_run=True)
        assert _row_count(tmp_db) == 3

    def test_purge_empty_table(self, tmp_db):
        assert purge(tmp_db, older_than_days=90) == 0

    def test_purge_returns_deleted_count(self, tmp_db, sample_invocation):
        for i in range(4):
            _insert_invocation(
                tmp_db, sample_invocation(timestamp=_days_ago(100 + i), session_id=f"old-{i}")
            )
        for i in range(2):
            _insert_invocation(
                tmp_db, sample_invocation(timestamp=_days_ago(10 + i), session_id=f"recent-{i}")
            )
        assert purge(tmp_db, older_than_days=90) == 4
        assert _row_count(tmp_db) == 2
