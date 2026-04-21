"""T086 [US5] — Flow mining deduplication tests (FR-008).

Tests confirm that flow_pipeline.run_flow_mine():
- Inserts flow_events on first mine
- Is idempotent on second mine of unchanged file (UNIQUE constraint blocks duplicates)
- Adds exactly 1 row when 1 new flow appended
- Updates processed_sessions after each mine

Run to confirm RED before T087 impl:
    uv run pytest tests/unit/mining/test_flow_dedup.py -v
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(tool_name: str, idx: int) -> str:
    """Return a minimal JSONL event dict with a tool call."""
    return json.dumps(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "name": tool_name,
                        "id": f"tool_{idx}",
                        "input": {"path": f"/tmp/file_{idx}.py"},
                    }
                ],
            },
            "timestamp": f"2026-04-20T{idx % 24:02d}:00:00Z",
        }
    )


def _open_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS processed_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT NOT NULL,
            file_hash TEXT NOT NULL DEFAULT '',
            message_count INTEGER DEFAULT 0,
            tool_call_count INTEGER DEFAULT 0,
            skipped INTEGER DEFAULT 0,
            mined_at TEXT,
            last_offset INTEGER NOT NULL DEFAULT 0,
            last_mtime REAL,
            is_subagent INTEGER NOT NULL DEFAULT 0,
            parent_session_id TEXT,
            UNIQUE(file_path, file_hash)
        );
        CREATE TABLE IF NOT EXISTS flow_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            flow_hash TEXT NOT NULL,
            sequence TEXT NOT NULL,
            ngram_size INTEGER NOT NULL,
            was_successful INTEGER NOT NULL DEFAULT 0,
            duration_seconds REAL,
            source_file TEXT,
            file_path TEXT,
            timestamp TEXT,
            mined_at TEXT,
            is_subagent INTEGER NOT NULL DEFAULT 0,
            parent_session_id TEXT,
            UNIQUE(file_path, session_id, flow_hash)
        );
    """)
    conn.commit()
    return conn


def _write_flow_jsonl(path: Path, n_tools: int) -> None:
    """Write a JSONL with a sequence of n_tools tool calls."""
    tools = ["Read", "Edit", "Bash", "Write", "Glob"]
    with open(path, "w", encoding="utf-8") as f:
        for i in range(n_tools):
            f.write(_make_event(tools[i % len(tools)], i) + "\n")
    # Add a success signal
    with open(path, "a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "type": "user",
                    "message": {"role": "user", "content": [{"type": "text", "text": "perfect"}]},
                    "timestamp": "2026-04-20T12:30:00Z",
                }
            )
            + "\n"
        )


# ---------------------------------------------------------------------------
# T086-1: First mine inserts flow_events rows
# ---------------------------------------------------------------------------


def test_first_mine_inserts_flow_events():
    """First mine must insert at least one flow_event row."""
    from sio.mining.flow_pipeline import run_flow_mine  # noqa: PLC0415

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "sio.db"
        jsonl_path = Path(tmpdir) / "session.jsonl"

        _write_flow_jsonl(jsonl_path, 8)
        conn = _open_db(db_path)

        try:
            run_flow_mine(conn, [Path(tmpdir)], since="7d")
            count = conn.execute("SELECT COUNT(*) FROM flow_events").fetchone()[0]
            assert count > 0, "First mine must insert at least one flow_event"
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# T086-2: Second mine on unchanged file inserts 0 new rows
# ---------------------------------------------------------------------------


def test_second_mine_unchanged_file_no_new_rows():
    """Second mine on unchanged file must insert 0 new flow_event rows (dedup)."""
    from sio.mining.flow_pipeline import run_flow_mine  # noqa: PLC0415

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "sio.db"
        jsonl_path = Path(tmpdir) / "session.jsonl"

        _write_flow_jsonl(jsonl_path, 8)
        conn = _open_db(db_path)

        try:
            run_flow_mine(conn, [Path(tmpdir)], since="7d")
            count_after_first = conn.execute("SELECT COUNT(*) FROM flow_events").fetchone()[0]

            # Second mine — same file unchanged
            run_flow_mine(conn, [Path(tmpdir)], since="7d")
            count_after_second = conn.execute("SELECT COUNT(*) FROM flow_events").fetchone()[0]

            assert count_after_second == count_after_first, (
                f"Second mine added rows: before={count_after_first}, after={count_after_second}. "
                "Flow mining must be idempotent (dedup by UNIQUE(file_path, session_id, flow_hash))."
            )
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# T086-3: Append 1 new flow; third mine adds exactly 1 row
# ---------------------------------------------------------------------------


def test_append_new_flow_then_mine_adds_one_row():
    """After appending an entirely distinct session JSONL, mine must add exactly its flows."""
    from sio.mining.flow_pipeline import run_flow_mine  # noqa: PLC0415

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "sio.db"
        jsonl_path1 = Path(tmpdir) / "session1.jsonl"
        jsonl_path2 = Path(tmpdir) / "session2.jsonl"

        _write_flow_jsonl(jsonl_path1, 6)
        conn = _open_db(db_path)

        try:
            run_flow_mine(conn, [Path(tmpdir)], since="7d")
            count_before = conn.execute("SELECT COUNT(*) FROM flow_events").fetchone()[0]

            # Mine again — same file, no new rows
            run_flow_mine(conn, [Path(tmpdir)], since="7d")
            count_same = conn.execute("SELECT COUNT(*) FROM flow_events").fetchone()[0]
            assert count_same == count_before, "Re-mine of unchanged file should add 0 rows"

            # Now write a second file with different session id
            _write_flow_jsonl(jsonl_path2, 6)
            run_flow_mine(conn, [Path(tmpdir)], since="7d")
            count_after = conn.execute("SELECT COUNT(*) FROM flow_events").fetchone()[0]
            assert count_after > count_before, (
                f"Mine after new file should add rows: before={count_before}, after={count_after}"
            )
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# T086-4: processed_sessions updated after each mine
# ---------------------------------------------------------------------------


def test_processed_sessions_updated_after_mine():
    """processed_sessions must be updated after each successful mine."""
    from sio.mining.flow_pipeline import run_flow_mine  # noqa: PLC0415

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "sio.db"
        jsonl_path = Path(tmpdir) / "session.jsonl"

        _write_flow_jsonl(jsonl_path, 6)
        conn = _open_db(db_path)

        try:
            run_flow_mine(conn, [Path(tmpdir)], since="7d")
            ps_count = conn.execute(
                "SELECT COUNT(*) FROM processed_sessions WHERE file_path = ?",
                (str(jsonl_path),),
            ).fetchone()[0]
            assert ps_count >= 1, "processed_sessions must have a row for the mined file"
        finally:
            conn.close()
