"""Unit tests for the cohort primitive — store + snapshot (T025).

Covers:
  * create / get / list / close lifecycle + error paths
  * duplicate-name and double-close guards
  * config-hash snapshot stability + structure
  * resolve_experiment_window for open and closed experiments
  * report error-rate / new-class / flow / suggestion sections
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from sio.core.cohort.models import Experiment, ExperimentRun
from sio.core.cohort.report import (
    build_report,
    compute_error_rate_delta,
    compute_new_error_classes,
    parse_baseline,
)
from sio.core.cohort.snapshot import build_manifest, snapshot_hash
from sio.core.cohort.store import (
    ExperimentAlreadyClosed,
    ExperimentExists,
    ExperimentNotFound,
    close_experiment,
    create_experiment,
    get_experiment,
    list_experiments,
)
from sio.core.cohort.window import resolve_experiment_window
from sio.core.db.schema import init_db


@pytest.fixture()
def db_path():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as t:
        p = t.name
    init_db(p).close()
    yield p
    Path(p).unlink(missing_ok=True)


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------


def test_models_are_frozen_dataclasses():
    e = Experiment(name="x", start_ts="2026-01-01T00:00:00Z")
    assert e.status == "open" and e.close_ts is None
    with pytest.raises(Exception):
        e.name = "y"  # frozen
    r = ExperimentRun(event_id=1, experiment_name="x", source_table="error_records")
    assert r.source_table == "error_records"


# --------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------


def test_create_get_close_lifecycle(db_path):
    e = create_experiment(db_path, "foo", note="n", project="SIO", config_hash="abc")
    assert e.status == "open" and e.close_ts is None
    got = get_experiment(db_path, "foo")
    assert got is not None and got.note == "n" and got.project == "SIO"
    closed = close_experiment(db_path, "foo")
    assert closed.status == "closed" and closed.close_ts


def test_duplicate_name_blocked(db_path):
    create_experiment(db_path, "dup")
    with pytest.raises(ExperimentExists):
        create_experiment(db_path, "dup")


def test_double_close_blocked(db_path):
    create_experiment(db_path, "c")
    close_experiment(db_path, "c")
    with pytest.raises(ExperimentAlreadyClosed):
        close_experiment(db_path, "c")


def test_close_missing_blocked(db_path):
    with pytest.raises(ExperimentNotFound):
        close_experiment(db_path, "nope")


def test_list_ordering_and_filters(db_path):
    create_experiment(db_path, "a", start_ts="2026-01-01T00:00:00Z")
    create_experiment(db_path, "b", start_ts="2026-02-01T00:00:00Z", project="SIO")
    rows = list_experiments(db_path)
    assert [r.name for r in rows] == ["b", "a"]  # newest first
    assert [r.name for r in list_experiments(db_path, project="SIO")] == ["b"]
    close_experiment(db_path, "a")
    assert [r.name for r in list_experiments(db_path, status="closed")] == ["a"]


def test_get_missing_returns_none(db_path):
    assert get_experiment(db_path, "ghost") is None


# --------------------------------------------------------------------------
# Snapshot
# --------------------------------------------------------------------------


def test_snapshot_hash_is_stable_and_structured(tmp_path):
    # Build a fake ~/.claude tree
    claude = tmp_path / ".claude"
    claude.mkdir()
    (claude / "CLAUDE.md").write_text("# rules\n")
    (claude / "skills").mkdir()
    (claude / "skills" / "a.md").write_text("skill a")
    (claude / "rules").mkdir()
    (claude / "rules" / "r.md").write_text("rule r")
    (claude / "settings.json").write_text(json.dumps({"hooks": {"PreToolUse": []}}))

    manifest = build_manifest(claude_home=claude)
    assert manifest["claude_md"] is not None
    assert "a.md" in manifest["skills"]
    assert "r.md" in manifest["rules"]
    assert manifest["settings_hooks"] == {"PreToolUse": []}

    d1, _ = snapshot_hash(claude_home=claude)
    d2, _ = snapshot_hash(claude_home=claude)
    assert d1 == d2 and len(d1) == 64  # sha256 hex


def test_snapshot_hash_changes_when_config_changes(tmp_path):
    claude = tmp_path / ".claude"
    claude.mkdir()
    (claude / "CLAUDE.md").write_text("v1")
    d1, _ = snapshot_hash(claude_home=claude)
    (claude / "CLAUDE.md").write_text("v2")
    d2, _ = snapshot_hash(claude_home=claude)
    assert d1 != d2


def test_snapshot_missing_files_are_null(tmp_path):
    claude = tmp_path / ".claude"
    claude.mkdir()
    manifest = build_manifest(claude_home=claude)
    assert manifest["claude_md"] is None
    assert manifest["skills"] == {}
    assert manifest["settings_hooks"] is None


# --------------------------------------------------------------------------
# Window resolver
# --------------------------------------------------------------------------


def test_resolve_window_open_uses_end_default(db_path):
    create_experiment(db_path, "w", start_ts="2026-01-01T00:00:00Z")
    s, e = resolve_experiment_window(db_path, "w", end_default="2026-01-15T00:00:00Z")
    assert s == "2026-01-01T00:00:00Z" and e == "2026-01-15T00:00:00Z"


def test_resolve_window_closed_uses_close_ts(db_path):
    create_experiment(db_path, "w", start_ts="2026-01-01T00:00:00Z")
    close_experiment(db_path, "w", close_ts="2026-01-10T00:00:00Z")
    s, e = resolve_experiment_window(db_path, "w")
    assert e == "2026-01-10T00:00:00Z"


def test_resolve_window_missing_raises(db_path):
    with pytest.raises(ExperimentNotFound):
        resolve_experiment_window(db_path, "nope")


# --------------------------------------------------------------------------
# Report engine
# --------------------------------------------------------------------------


def test_parse_baseline_units():
    assert parse_baseline("7d").days == 7
    assert parse_baseline("14").days == 14
    assert parse_baseline("48h").total_seconds() == 48 * 3600
    assert parse_baseline("2w").days == 14
    with pytest.raises(ValueError):
        parse_baseline("garbage")


def _seed_errors(conn, rows):
    for ts, et in rows:
        conn.execute(
            "INSERT INTO error_records "
            "(session_id,timestamp,source_type,source_file,error_text,mined_at,error_type) "
            "VALUES ('s',?,'jsonl','f','e','now',?)",
            (ts, et),
        )
    conn.commit()


def test_error_rate_delta_per_hour_normalized(db_path):
    conn = sqlite3.connect(db_path)
    _seed_errors(
        conn,
        [
            ("2026-05-05T00:00:00Z", "tool_failure"),
            ("2026-05-06T00:00:00Z", "tool_failure"),
            ("2026-05-11T00:00:00Z", "tool_failure"),
        ],
    )
    out = compute_error_rate_delta(
        conn,
        "2026-05-10T00:00:00Z",
        "2026-05-12T00:00:00Z",
        "2026-05-03T00:00:00Z",
        "2026-05-10T00:00:00Z",
    )
    conn.close()
    assert out["experiment"]["count"] == 1
    assert out["baseline"]["count"] == 2
    # Normalization check: experiment is 1 error / 48h = 0.0208/h while
    # baseline is 2 / 168h = 0.0119/h — the shorter window is DENSER, so
    # the per-hour delta is positive even though the raw count is lower.
    # This is the whole point of per-hour normalization.
    assert out["experiment"]["per_hour"] > out["baseline"]["per_hour"]
    assert out["delta_per_hour"] > 0
    assert out["delta_per_hour"] == pytest.approx(
        out["experiment"]["per_hour"] - out["baseline"]["per_hour"], abs=1e-4
    )


def test_new_error_classes_diff(db_path):
    conn = sqlite3.connect(db_path)
    _seed_errors(
        conn,
        [
            ("2026-05-05T00:00:00Z", "tool_failure"),  # baseline
            ("2026-05-11T00:00:00Z", "tool_failure"),  # exp (shared)
            ("2026-05-11T01:00:00Z", "undo"),  # exp NEW
        ],
    )
    res = compute_new_error_classes(
        conn,
        "2026-05-10T00:00:00Z",
        "2026-05-12T00:00:00Z",
        "2026-05-03T00:00:00Z",
        "2026-05-10T00:00:00Z",
    )
    conn.close()
    assert {r["error_type"] for r in res} == {"undo"}


def test_build_report_full_shape(db_path):
    create_experiment(
        db_path, "rpt", start_ts="2026-05-10T00:00:00Z", config_hash="abc"
    )
    close_experiment(db_path, "rpt", close_ts="2026-05-12T00:00:00Z")
    report = build_report(db_path, "rpt", baseline="7d")
    for key in (
        "experiment",
        "windows",
        "error_rate",
        "new_error_classes",
        "flow_delta",
        "suggestions",
    ):
        assert key in report
    assert report["experiment"]["name"] == "rpt"
    assert report["windows"]["baseline_spec"] == "7d"


def test_build_report_missing_experiment_raises(db_path):
    with pytest.raises(ExperimentNotFound):
        build_report(db_path, "nope")
