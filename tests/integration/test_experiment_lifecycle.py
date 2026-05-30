"""Integration test — full experiment lifecycle (T027).

Drives the real path: migrate_005 → start → simulate error/flow events
inside the window → close --report → assert the A/B report reflects the
seeded data. Uses the canonical schema (init_db + migrate_005) rather
than hand-rolled DDL so it catches schema/bootstrap regressions too.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile

import pytest
from click.testing import CliRunner

from sio.cli.main import cli
from sio.core.db.schema import init_db, migrate_005_experiments


@pytest.fixture()
def env_db(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "sio.db")
        # Canonical bring-up
        init_db(db).close()
        migrate_005_experiments(db)
        monkeypatch.setenv("SIO_DB_PATH", db)
        yield db


def _run(args):
    return CliRunner().invoke(cli, args, catch_exceptions=False)


def _seed_error(db, ts, error_type, text="err"):
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO error_records "
        "(session_id,timestamp,source_type,source_file,error_text,mined_at,error_type) "
        "VALUES ('sess',?,'jsonl','/p/f.py',?, 'now', ?)",
        (ts, text, error_type),
    )
    conn.commit()
    conn.close()


def _seed_flow(db, ts, flow_hash, sequence):
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO flow_events "
        "(session_id,flow_hash,sequence,ngram_size,was_successful,timestamp,mined_at) "
        "VALUES ('sess',?,?,2,1,?,?)",
        (flow_hash, sequence, ts, ts),
    )
    conn.commit()
    conn.close()


def test_full_lifecycle_start_events_close_report(env_db):
    db = env_db

    # 1. start (fixed start_ts via direct write so the window is deterministic)
    res = _run(["experiment", "start", "mvp", "--note", "lifecycle"])
    assert res.exit_code == 0
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE experiments SET start_ts='2026-05-10T00:00:00Z' WHERE name='mvp'"
    )
    conn.commit()
    conn.close()

    # 2. simulate events
    # baseline window (05-03 .. 05-10): tool_failure x3, flow A->B
    _seed_error(db, "2026-05-05T00:00:00Z", "tool_failure")
    _seed_error(db, "2026-05-06T00:00:00Z", "tool_failure")
    _seed_error(db, "2026-05-07T00:00:00Z", "tool_failure")
    _seed_flow(db, "2026-05-05T00:00:00Z", "h1", "Read->Edit")
    # experiment window (05-10 .. 05-12): tool_failure x1 + NEW class 'undo', flow C->D
    _seed_error(db, "2026-05-11T00:00:00Z", "tool_failure")
    _seed_error(db, "2026-05-11T01:00:00Z", "undo", text="undo happened")
    _seed_flow(db, "2026-05-11T00:00:00Z", "h2", "Bash->Bash")

    # 3. close with fixed close_ts then report (json for assertions)
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE experiments SET close_ts='2026-05-12T00:00:00Z', status='open' "
        "WHERE name='mvp'"
    )
    conn.commit()
    conn.close()

    res = _run(
        ["experiment", "close", "mvp", "--report", "--format", "json", "--baseline", "7d"]
    )
    assert res.exit_code == 0, res.output
    report = json.loads(res.output)

    # Error-rate: baseline 3 errors / 168h, experiment 2 errors / 48h
    assert report["error_rate"]["baseline"]["count"] == 3
    assert report["error_rate"]["experiment"]["count"] == 2

    # New error class: 'undo' appeared only in the experiment window
    new_types = {c["error_type"] for c in report["new_error_classes"]}
    assert "undo" in new_types
    assert "tool_failure" not in new_types  # present in baseline too

    # Flow delta: C->D (Bash->Bash) emerged, A->B (Read->Edit) died
    emerged = {f["sequence"] for f in report["flow_delta"]["emerged"]}
    died = {f["sequence"] for f in report["flow_delta"]["died"]}
    assert "Bash->Bash" in emerged
    assert "Read->Edit" in died

    # Experiment is now closed
    conn = sqlite3.connect(db)
    status = conn.execute(
        "SELECT status FROM experiments WHERE name='mvp'"
    ).fetchone()[0]
    conn.close()
    assert status == "closed"


def test_scope_filters_resolve_window(env_db):
    """`sio mine --experiment` resolves a window without --since."""
    _run(["experiment", "start", "scoped"])
    # mine with --experiment should not error on the 'either --since or
    # --experiment' guard (window resolves from the experiment row).
    res = _run(["mine", "--experiment", "scoped", "--source", "jsonl"])
    # Exit 0 (mining may find nothing, that's fine) — the point is the
    # guard passed and the window resolved.
    assert res.exit_code == 0, res.output
