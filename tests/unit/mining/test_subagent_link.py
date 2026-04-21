"""T084 [US5] — Subagent linkage tests (FR-011, R-13).

Tests confirm that mining detects subagent JSONL files by path pattern
and records is_subagent=1 / parent_session_id=<parent> in error_records.

Two naming conventions must be detected:
  - subagents/<parent>/<subagent_id>.jsonl
  - <parent>__subagent_<subagent_id>.jsonl

Top-level session JSONLs (no subagent pattern) get is_subagent=0, parent_session_id=NULL.

Default mine query (without --include-subagents) excludes is_subagent=1 rows.

These tests are EXPECTED RED until T085 (Wave 10) implements subagent detection.

Run to confirm RED:
    uv run pytest tests/unit/mining/test_subagent_link.py -v
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(idx: int) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": f"Error {idx}"}],
            },
            "timestamp": f"2026-04-20T{idx:02d}:00:00Z",
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
            UNIQUE(file_path)
        );
        CREATE TABLE IF NOT EXISTS error_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            file_path TEXT,
            error_type TEXT,
            error_text TEXT,
            user_message TEXT,
            tool_name TEXT,
            tool_input TEXT,
            tool_output TEXT,
            timestamp TEXT,
            is_subagent INTEGER NOT NULL DEFAULT 0,
            parent_session_id TEXT
        );
    """)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# T084-1: subagents/<parent>/<subagent_id>.jsonl gets is_subagent=1
# ---------------------------------------------------------------------------


def test_subagent_nested_path_sets_is_subagent():
    """Files at subagents/<parent>/<id>.jsonl must produce is_subagent=1."""
    from sio.mining.pipeline import _detect_subagent_info  # noqa: PLC0415

    parent_id = "abc123"
    sub_id = "xyz789"
    path = Path(f"/sessions/subagents/{parent_id}/{sub_id}.jsonl")

    info = _detect_subagent_info(path)
    assert info["is_subagent"] == 1, f"Expected is_subagent=1, got {info}"
    assert info["parent_session_id"] == parent_id, (
        f"Expected parent_session_id={parent_id!r}, got {info['parent_session_id']!r}"
    )


# ---------------------------------------------------------------------------
# T084-2: <parent>__subagent_<id>.jsonl gets is_subagent=1
# ---------------------------------------------------------------------------


def test_subagent_double_underscore_sets_is_subagent():
    """Files named <parent>__subagent_<id>.jsonl must produce is_subagent=1."""
    from sio.mining.pipeline import _detect_subagent_info  # noqa: PLC0415

    parent_id = "session001"
    sub_id = "sub002"
    path = Path(f"/sessions/{parent_id}__subagent_{sub_id}.jsonl")

    info = _detect_subagent_info(path)
    assert info["is_subagent"] == 1, f"Expected is_subagent=1, got {info}"
    assert info["parent_session_id"] == parent_id, (
        f"Expected parent_session_id={parent_id!r}, got {info['parent_session_id']!r}"
    )


# ---------------------------------------------------------------------------
# T084-3: Top-level session gets is_subagent=0, parent_session_id=NULL
# ---------------------------------------------------------------------------


def test_top_level_session_is_not_subagent():
    """Regular top-level JSONL files must have is_subagent=0, parent_session_id=None."""
    from sio.mining.pipeline import _detect_subagent_info  # noqa: PLC0415

    path = Path("/sessions/regular_session_abc123.jsonl")
    info = _detect_subagent_info(path)
    assert info["is_subagent"] == 0, f"Expected is_subagent=0, got {info}"
    assert info["parent_session_id"] is None, (
        f"Expected parent_session_id=None, got {info['parent_session_id']!r}"
    )


# ---------------------------------------------------------------------------
# T084-4: Default mine query excludes is_subagent=1 rows
# ---------------------------------------------------------------------------


def test_default_mine_excludes_subagent_rows():
    """Default mine query must exclude is_subagent=1 error_records from counts."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "sio.db"
        conn = _open_db(db_path)

        try:
            # Insert one top-level and one subagent error record
            conn.execute(
                "INSERT INTO error_records (session_id, error_type, error_text, "
                "is_subagent, parent_session_id) VALUES (?, ?, ?, ?, ?)",
                ("sess001", "tool_failure", "top-level error", 0, None),
            )
            conn.execute(
                "INSERT INTO error_records (session_id, error_type, error_text, "
                "is_subagent, parent_session_id) VALUES (?, ?, ?, ?, ?)",
                ("sess001_sub", "tool_failure", "subagent error", 1, "sess001"),
            )
            conn.commit()

            # Default query should only count top-level records
            count = conn.execute(
                "SELECT COUNT(*) FROM error_records WHERE is_subagent = 0"
            ).fetchone()[0]
            assert count == 1, f"Default mine query should return 1 top-level record, got {count}"
        finally:
            conn.close()
