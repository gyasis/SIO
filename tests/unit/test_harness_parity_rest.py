"""#81 remainder: ids (F7), adapter bulk mine (F10), agent scoping (F9), id merge."""

from __future__ import annotations

import json
import sqlite3

import click
import pytest

from sio.core.db.schema import init_db
from sio.core.session_handle import from_path

# --- F7: session ids -----------------------------------------------------


def test_promptchain_path_maps_to_directory_uuid():
    assert (
        from_path("/home/u/.promptchain/sessions/0913de5f-f19c/messages.jsonl")
        == "promptchain:0913de5f-f19c"
    )


def test_paperclip_path_maps_to_instance_and_run():
    path = "/home/u/.paperclip/instances/default/data/run-logs/c1/a1/run-9.ndjson"
    assert from_path(path) == "paperclip:default:run-9"


def test_mine_session_with_zero_events_fails_loudly(tmp_path, monkeypatch):
    from sio.adapters.base import SessionManifest
    from sio.cli import main as m

    db = str(tmp_path / "s.db")
    init_db(db).close()

    class Empty:
        def get_events(self, manifest):
            return iter(())

    monkeypatch.setattr(
        "sio.adapters.factory.manifest_from_handle",
        lambda h: SessionManifest(agent="gemini", native_id="nope", kind="store", path=""),
    )
    monkeypatch.setattr("sio.adapters.factory.adapter_for", lambda a: Empty())
    with pytest.raises(click.ClickException, match="read 0 events"):
        m._mine_session_via_adapter(db, "gemini:nope", "gemini")
    rows = sqlite3.connect(db).execute("SELECT COUNT(*) FROM processed_sessions").fetchone()
    assert rows[0] == 0


# --- F10: bulk mine through the adapter ----------------------------------


def _omp_session(path, n_failures=1):
    recs = [
        {"type": "session", "version": "3", "id": "u", "timestamp": "2026-11-09T00:00:00Z"},
        {"type": "message", "timestamp": "2026-11-09T00:00:01Z",
         "message": {"role": "user", "content": [{"type": "text", "text": "build it"}]}},
    ]
    for i in range(n_failures):
        recs += [
            {"type": "message", "timestamp": f"2026-11-09T00:01:{i:02d}Z",
             "message": {"role": "assistant", "content": [
                 {"type": "toolCall", "id": f"c{i}", "name": "bash",
                  "arguments": {"command": "make"}}]}},
            {"type": "message", "timestamp": f"2026-11-09T00:02:{i:02d}Z",
             "message": {"role": "toolResult", "toolCallId": f"c{i}", "toolName": "bash",
                         "isError": True,
                         "content": [{"type": "text", "text": f"make: *** error {i}"}]}},
        ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in recs) + "\n")


def test_bulk_mine_store_agent_uses_adapter_and_records_signals(tmp_path, monkeypatch):
    from sio.cli import live
    from sio.cli import main as m

    store = tmp_path / ".omp" / "agent" / "sessions"
    _omp_session(store / "-x" / "2026-11-09T00-00-00Z_u1.jsonl", n_failures=2)
    _omp_session(store / "-y" / "2026-11-09T01-00-00Z_u2.jsonl", n_failures=1)
    monkeypatch.setattr(live, "OMP_SESSIONS", store)

    db = str(tmp_path / "s.db")
    init_db(db).close()
    m._mine_adapter_bulk(db, "omp", None)

    c = sqlite3.connect(db)
    by_session = dict(
        c.execute("SELECT session_id, COUNT(*) FROM error_records GROUP BY session_id")
    )
    assert by_session == {
        "omp:2026-11-09T00-00-00Z_u1": 2,
        "omp:2026-11-09T01-00-00Z_u2": 1,
    }
    assert c.execute("SELECT COUNT(*) FROM session_metrics WHERE agent='omp'").fetchone()[0] == 2

    m._mine_adapter_bulk(db, "omp", None)  # unchanged: no new rows
    assert c.execute("SELECT COUNT(*) FROM error_records").fetchone()[0] == 3


def test_harness_orchestrator_skips_memory_db_and_isolates_failures(tmp_path, monkeypatch):
    from sio.cli import main as m
    from sio.mining import harness_mine

    assert harness_mine.mine_other_harnesses(None, "1 day") == {}
    assert harness_mine.db_path_of(sqlite3.connect(":memory:")) is None

    calls: list[str] = []

    def fake_adapter_bulk(db, agent, since, **_kw):
        calls.append(agent)
        if agent == "pi":
            raise RuntimeError("boom")

    monkeypatch.setattr(m, "_mine_adapter_bulk", fake_adapter_bulk)
    monkeypatch.setattr(m, "_mine_agent_bulk", lambda db, agent, since: calls.append(agent))
    status = harness_mine.mine_other_harnesses(str(tmp_path / "x.db"), "1 day")
    assert set(status) == set(harness_mine.OTHER_HARNESSES)
    assert status["pi"].startswith("failed: RuntimeError")
    assert status["omp"] == "ok" and status["dsh"] == "ok"
    assert set(calls) == set(harness_mine.OTHER_HARNESSES)


# --- F9: agent scoping ----------------------------------------------------


@pytest.fixture
def mixed_db(tmp_path):
    db = str(tmp_path / "mixed.db")
    conn = init_db(db)
    rows = [
        ("omp:s1", "repeated_attempt", "omp"),
        ("omp:s1", "repeated_attempt", "omp"),
        ("claude:c1", "repeated_attempt", "claude"),
        ("claude:c1", "tool_failure", "claude"),
    ]
    for i, (sid, etype, _agent) in enumerate(rows):
        conn.execute(
            "INSERT INTO error_records (session_id, timestamp, source_type, source_file, "
            "error_text, error_type, mined_at) VALUES (?, ?, 'adapter', 'f', ?, ?, ?)",
            (sid, f"2099-01-01T00:00:0{i}Z", f"never retry blindly {i}", etype,
             "2099-01-01T00:00:00Z"),
        )
    conn.commit()
    return db, conn


def test_velocity_series_is_kept_per_agent(mixed_db):
    from sio.core.metrics.velocity import compute_velocity_snapshot, get_velocity_trends

    _db, conn = mixed_db
    kw = {"window_start": "2098-01-01T00:00:00Z", "window_end": "2100-01-01T00:00:00Z"}
    omp = compute_velocity_snapshot(conn, "repeated_attempt", agent="omp", **kw)
    everyone = compute_velocity_snapshot(conn, "repeated_attempt", **kw)
    assert omp["error_count_in_window"] == 2
    assert everyone["error_count_in_window"] == 3
    assert len(get_velocity_trends(conn, "repeated_attempt", agent="omp")) == 1
    assert len(get_velocity_trends(conn, "repeated_attempt")) == 1


def test_violation_report_filters_by_agent(mixed_db, tmp_path):
    from sio.mining.violation_detector import get_violation_report

    _db, conn = mixed_db
    rules = tmp_path / "AGENTS.md"
    rules.write_text("- NEVER retry blindly\n")
    omp = get_violation_report(conn, [str(rules)], agent="omp")
    everyone = get_violation_report(conn, [str(rules)])
    sessions = {v.get("session_id") for v in omp["violations"]}
    assert sessions <= {"omp:s1"}
    assert len(everyone["violations"]) >= len(omp["violations"])


def test_scope_to_agent_shadows_tables_on_one_connection(mixed_db):
    from sio.cli.main import _scope_to_agent

    db, conn = mixed_db
    scoped = sqlite3.connect(db)
    _scope_to_agent(scoped, "omp")
    assert scoped.execute("SELECT COUNT(*) FROM error_records").fetchone()[0] == 2
    assert scoped.execute("SELECT COUNT(*) FROM main.error_records").fetchone()[0] == 4
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM error_records").fetchone()[0] == 4
    with pytest.raises(ValueError):
        _scope_to_agent(scoped, "x'; DROP TABLE error_records; --")


# --- partial id merge -----------------------------------------------------


def test_merge_ids_dry_run_changes_nothing_and_apply_folds(tmp_path):
    from sio.core.db.agents import merge_partial_session_ids

    db = str(tmp_path / "m.db")
    conn = init_db(db)
    full = "codex:rollout-2026-06-03T10-31-46-019e8de5-c6b6-7683-a9d1-68d1c426877e"
    for sid, text in [("codex:019e8de5-c6b6-7683-a9d1-68d1c426877e", "a"), (full, "b")]:
        conn.execute(
            "INSERT INTO error_records (session_id, timestamp, source_type, source_file, "
            "error_text, error_type, mined_at) VALUES (?, 't', 'adapter', 'f', ?, 'x', 'm')",
            (sid, text),
        )
    conn.commit()
    conn.close()

    dry = merge_partial_session_ids(db)
    assert dry["applied"] is False
    assert dry["merged_ids"]["error_records"]["codex"] == 1
    ids = {r[0] for r in sqlite3.connect(db).execute("SELECT session_id FROM error_records")}
    assert len(ids) == 2

    merge_partial_session_ids(db, apply=True)
    ids = {r[0] for r in sqlite3.connect(db).execute("SELECT session_id FROM error_records")}
    assert ids == {full}
