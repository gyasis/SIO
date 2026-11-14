"""Positive signals, session metrics and flows for non-Claude sessions."""

from __future__ import annotations

import pytest

from sio.adapters.messages import pair_message_dicts
from sio.core.db.schema import init_db
from sio.mining.flow_pipeline import _store_session_flows, query_flows
from sio.mining.session_signals import record_session_signals

CANON = "omp:2026-11-09T00-00-00Z_u1"


def _messages():
    """Unpaired per-event dicts, the shape the adapter mine paths build."""
    rows = [
        {"role": "user", "content": "fix the failing build please"},
        {"role": "assistant", "tool_name": "bash", "content": '{"command": "make"}'},
        {"role": "tool", "tool_name": "bash", "content": "ok"},
        {"role": "assistant", "tool_name": "read", "content": '{"path": "/a.py"}'},
        {"role": "tool", "tool_name": "read", "content": "print(1)"},
        {"role": "assistant", "tool_name": "edit", "content": '{"path": "/a.py"}'},
        {"role": "tool", "tool_name": "edit", "content": "done"},
        {"role": "user", "content": "perfect, that works, thanks!"},
    ]
    for i, r in enumerate(rows):
        r.setdefault("tool_name", None)
        r["timestamp"] = f"2026-11-09T00:00:{i:02d}Z"
        r["session_id"] = CANON
    return rows


@pytest.fixture
def conn():
    c = init_db(":memory:")
    yield c
    c.close()


def test_pair_message_dicts_joins_calls_to_results():
    paired = pair_message_dicts(_messages())
    calls = [m for m in paired if m["tool_name"]]
    assert [(c["tool_name"], c["tool_output"]) for c in calls] == [
        ("bash", "ok"), ("read", "print(1)"), ("edit", "done"),
    ]
    assert all(c["role"] == "assistant" for c in calls)


def test_signals_write_positive_rows_and_metrics(conn):
    result = record_session_signals(conn, _messages(), CANON, "omp", "/s.jsonl", [])
    assert result["metrics"] is True
    assert result["positive"] >= 1

    rows = conn.execute(
        "SELECT agent, session_id FROM positive_records"
    ).fetchall()
    assert {(r["agent"], r["session_id"]) for r in rows} == {("omp", CANON)}

    m = conn.execute("SELECT * FROM session_metrics WHERE session_id = ?", (CANON,)).fetchone()
    assert m["agent"] == "omp"
    assert m["tool_call_count"] == 3
    assert m["file_path"] == "/s.jsonl"
    assert m["positive_signal_count"] == result["positive"]


def test_remine_replaces_rows_instead_of_duplicating(conn):
    record_session_signals(conn, _messages(), CANON, "omp", "/s.jsonl", [])
    first = conn.execute("SELECT COUNT(*) FROM positive_records").fetchone()[0]
    record_session_signals(conn, _messages(), CANON, "omp", "/s.jsonl", [])
    assert conn.execute("SELECT COUNT(*) FROM positive_records").fetchone()[0] == first
    assert conn.execute("SELECT COUNT(*) FROM session_metrics").fetchone()[0] == 1


def test_claude_rows_are_refused(conn):
    with pytest.raises(ValueError):
        record_session_signals(conn, _messages(), "claude:x", "claude", None, [])


def test_flows_stored_under_the_agent_and_filtered_by_it(conn):
    paired = pair_message_dicts(_messages() * 3)
    written = _store_session_flows(conn, paired, CANON, "/s.jsonl", "omp", "2026-11-09T00:00:00Z")
    assert written > 0
    agents = {r[0] for r in conn.execute("SELECT DISTINCT agent FROM flow_events")}
    assert agents == {"omp"}
    assert query_flows(conn, since=None, min_count=1, agent="omp")
    assert query_flows(conn, since=None, min_count=1, agent="claude") == []
