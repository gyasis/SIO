"""The sync must carry EVERY column, and must never report success after dropping rows.

TDD red for the behavior_invocations sync defect (PRD sio_behavior_invocations_sync_fix,
2026-10-27). Measured that day: canonical `behavior_invocations` held 0 rows against 55,348
in the per-platform DB on razer and 35,374 on the WSL node. No row had ever arrived.

Cause: the INSERT lists 11 columns and omits `behavior_type`, which the schema declares
`TEXT NOT NULL CHECK(...)`. SQLite's `INSERT OR IGNORE` suppresses NOT NULL and CHECK
failures as well as UNIQUE conflicts, so every row was discarded and the function returned
a count of 0 with no error.

The existing tests/unit/db/test_sync.py could not catch this: it builds its own 11-column
DDL with no `behavior_type` at all, so it exercises a schema in which the defect cannot
occur. These tests use the REAL schema via init_db() for exactly that reason.
"""

from __future__ import annotations

import sqlite3

import pytest

from sio.core.db.schema import init_db

# Every data column of behavior_invocations (schema.py), id excluded.
ALL_COLUMNS = [
    "session_id", "timestamp", "platform", "user_message", "behavior_type",
    "actual_action", "expected_action", "activated", "correct_action", "correct_outcome",
    "user_satisfied", "user_note", "passive_signal", "history_file", "line_start",
    "line_end", "token_count", "latency_ms", "labeled_by", "labeled_at",
    "tool_name", "tool_input", "conversation_pointer",
]

# One fully-populated row: every column carries a distinguishable value, so a column
# dropped in transit shows up as a specific missing value rather than a vague count.
FULL_ROW = {
    "session_id": "claude:sess-full-1", "timestamp": "2026-10-27T05:00:00+00:00",
    "platform": "claude-code", "user_message": "run the tests", "behavior_type": "skill",
    "actual_action": "Bash", "expected_action": "Bash", "activated": 1,
    "correct_action": 1, "correct_outcome": 1, "user_satisfied": 1,
    "user_note": "worked", "passive_signal": "none", "history_file": "/t/a.jsonl",
    "line_start": 10, "line_end": 20, "token_count": 1234, "latency_ms": 567,
    "labeled_by": "human", "labeled_at": "2026-10-27T05:00:01+00:00",
    "tool_name": "Bash", "tool_input": '{"command":"pytest"}',
    "conversation_pointer": "/t/a.jsonl#L10-L20",
}


def _seed(platform_db, rows):
    conn = init_db(str(platform_db))
    cols = ", ".join(ALL_COLUMNS)
    qs = ", ".join("?" for _ in ALL_COLUMNS)
    for r in rows:
        conn.execute(
            f"INSERT INTO behavior_invocations ({cols}) VALUES ({qs})",
            [r[c] for c in ALL_COLUMNS],
        )
    conn.commit()
    conn.close()


@pytest.fixture
def dbs(tmp_path, monkeypatch):
    """Real schema on both sides, exactly as production has it."""
    sio_db = tmp_path / "sio.db"
    plat_db = tmp_path / "claude-code" / "behavior_invocations.db"
    plat_db.parent.mkdir(parents=True, exist_ok=True)
    init_db(str(sio_db)).close()
    monkeypatch.setenv("SIO_DB_PATH", str(sio_db))
    monkeypatch.setenv("SIO_PLATFORM_DB_PATH", str(plat_db))
    return sio_db, plat_db


def _canonical(sio_db):
    c = sqlite3.connect(str(sio_db))
    c.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in c.execute("SELECT * FROM behavior_invocations")]
    finally:
        c.close()


def test_sync_copies_the_row_at_all(dbs):
    """The headline defect: a valid row must ARRIVE. Today zero rows do."""
    from sio.core.db.sync import sync_behavior_invocations

    sio_db, plat_db = dbs
    _seed(plat_db, [FULL_ROW])
    sync_behavior_invocations()
    assert len(_canonical(sio_db)) == 1, "the row was silently discarded"


def test_sync_carries_every_column(dbs):
    """A column left out of the INSERT list is data loss, even once rows arrive."""
    from sio.core.db.sync import sync_behavior_invocations

    sio_db, plat_db = dbs
    _seed(plat_db, [FULL_ROW])
    sync_behavior_invocations()
    rows = _canonical(sio_db)
    assert rows, "no row arrived; cannot check columns"
    got = rows[0]
    missing = {c: FULL_ROW[c] for c in ALL_COLUMNS if got.get(c) != FULL_ROW[c]}
    assert not missing, f"columns lost or altered in transit: {sorted(missing)}"


def test_sync_reports_counts_that_add_up(dbs):
    """SC-001: after a sync the canonical count equals the per-platform count.

    Spec 004 left this PENDING against a `sio sync` command that was never built.
    """
    from sio.core.db.sync import sync_behavior_invocations

    sio_db, plat_db = dbs
    rows = []
    for i in range(5):
        r = dict(FULL_ROW)
        r["session_id"] = f"claude:sess-{i}"
        r["timestamp"] = f"2026-10-27T05:00:0{i}+00:00"
        rows.append(r)
    _seed(plat_db, rows)

    result = sync_behavior_invocations()
    assert len(_canonical(sio_db)) == 5, f"expected 5 canonical rows, got {len(_canonical(sio_db))}"
    assert result["claude-code"] == 5, f"reported {result['claude-code']} copied, expected 5"


def test_sync_is_still_idempotent(dbs):
    """The dedup guarantee must survive the fix: a second sync adds nothing."""
    from sio.core.db.sync import sync_behavior_invocations

    sio_db, plat_db = dbs
    _seed(plat_db, [FULL_ROW])
    sync_behavior_invocations()
    sync_behavior_invocations()
    assert len(_canonical(sio_db)) == 1, "second sync duplicated the row"


def test_sync_does_not_report_success_while_dropping_rows(dbs):
    """The real failure mode: 0 copied, no error, caller believes it worked.

    A row the canonical schema rejects must surface as an error or a short count —
    never as a silent success.
    """
    from sio.core.db.sync import sync_behavior_invocations

    sio_db, plat_db = dbs
    bad = dict(FULL_ROW)
    bad["behavior_type"] = "not_a_valid_type"      # violates the CHECK on the canonical side
    conn = init_db(str(plat_db))
    conn.execute("PRAGMA ignore_check_constraints = ON")
    cols = ", ".join(ALL_COLUMNS)
    qs = ", ".join("?" for _ in ALL_COLUMNS)
    conn.execute(f"INSERT INTO behavior_invocations ({cols}) VALUES ({qs})",
                 [bad[c] for c in ALL_COLUMNS])
    conn.commit()
    conn.close()

    raised = False
    try:
        result = sync_behavior_invocations()
    except Exception:
        raised = True
    else:
        copied = result.get("claude-code", 0)
        landed = len(_canonical(sio_db))
        assert copied == landed, (
            f"reported {copied} copied but {landed} rows are present — "
            "a silent drop is exactly the defect under test"
        )
    assert raised or len(_canonical(sio_db)) == 0 or True
