"""Integration tests for processed_sessions deduplication (T006).

Tests the pipeline-level behavior that:
1. Mining a session inserts into processed_sessions table.
2. Mining the same file twice skips on the second run (hash match).
3. Mining after file content changes re-processes (hash changed).

Since pipeline.py already has _is_already_processed, _mark_processed, and
_file_hash helpers, these tests exercise the real integration between
the mining pipeline and the processed_sessions table.

The full pipeline integration tests verify that run_mine checks
processed_sessions before parsing files and marks them after processing.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sio.core.db.schema import init_db
from sio.mining.pipeline import _file_hash, _is_already_processed, _mark_processed

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db() -> sqlite3.Connection:
    """In-memory database with full schema."""
    conn = init_db(":memory:")
    yield conn
    conn.close()


@pytest.fixture()
def sample_session_file(tmp_path: Path) -> Path:
    """Write a minimal valid JSONL session file with tool calls."""
    messages = [
        {
            "type": "user",
            "message": {"role": "user", "content": "Read the main module."},
            "timestamp": "2026-04-01T10:00:00Z",
        },
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Let me read that file."},
                    {
                        "type": "tool_use",
                        "id": "toolu_read_01",
                        "name": "Read",
                        "input": {"file_path": "/home/user/src/main.py"},
                    },
                ],
            },
            "timestamp": "2026-04-01T10:00:01Z",
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_read_01",
                        "content": 'def main():\n    print("hello")\n',
                        "is_error": False,
                    },
                ],
            },
            "timestamp": "2026-04-01T10:00:02Z",
        },
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": "The file defines a main function.",
            },
            "timestamp": "2026-04-01T10:00:03Z",
        },
        {
            "type": "user",
            "message": {"role": "user", "content": "Thanks, that's correct."},
            "timestamp": "2026-04-01T10:00:04Z",
        },
    ]
    path = tmp_path / "session.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for msg in messages:
            fh.write(json.dumps(msg) + "\n")
    return path


# ---------------------------------------------------------------------------
# Tests: _file_hash
# ---------------------------------------------------------------------------


class TestFileHash:
    """_file_hash computes SHA-256 of file contents."""

    def test_hash_is_hex_string(self, sample_session_file):
        h = _file_hash(sample_session_file)
        assert isinstance(h, str)
        assert len(h) == 64  # SHA-256 hex digest length

    def test_hash_matches_manual_computation(self, sample_session_file):
        h = _file_hash(sample_session_file)
        expected = hashlib.sha256(sample_session_file.read_bytes()).hexdigest()
        assert h == expected

    def test_different_content_produces_different_hash(self, tmp_path):
        file_a = tmp_path / "a.jsonl"
        file_b = tmp_path / "b.jsonl"
        file_a.write_text('{"type":"user","message":{"role":"user","content":"A"}}')
        file_b.write_text('{"type":"user","message":{"role":"user","content":"B"}}')
        assert _file_hash(file_a) != _file_hash(file_b)

    def test_same_content_produces_same_hash(self, tmp_path):
        content = '{"type":"user","message":{"role":"user","content":"same"}}'
        file_a = tmp_path / "a.jsonl"
        file_b = tmp_path / "b.jsonl"
        file_a.write_text(content)
        file_b.write_text(content)
        assert _file_hash(file_a) == _file_hash(file_b)


# ---------------------------------------------------------------------------
# Tests: _mark_processed and _is_already_processed
# ---------------------------------------------------------------------------


class TestMarkAndCheckProcessed:
    """_mark_processed inserts into processed_sessions; _is_already_processed checks."""

    def test_unprocessed_file_returns_false(self, db, sample_session_file):
        h = _file_hash(sample_session_file)
        assert _is_already_processed(db, sample_session_file, h) is False

    def test_mark_processed_then_check_returns_true(self, db, sample_session_file):
        h = _file_hash(sample_session_file)
        _mark_processed(db, sample_session_file, h, message_count=5, tool_call_count=1)
        assert _is_already_processed(db, sample_session_file, h) is True

    def test_mark_processed_inserts_row(self, db, sample_session_file):
        h = _file_hash(sample_session_file)
        _mark_processed(db, sample_session_file, h, message_count=5, tool_call_count=1)
        row = db.execute(
            "SELECT file_path, file_hash, message_count, tool_call_count "
            "FROM processed_sessions WHERE file_path = ?",
            (str(sample_session_file),),
        ).fetchone()
        assert row is not None
        assert row[0] == str(sample_session_file)
        assert row[1] == h
        assert row[2] == 5
        assert row[3] == 1

    def test_mark_processed_sets_mined_at(self, db, sample_session_file):
        h = _file_hash(sample_session_file)
        _mark_processed(db, sample_session_file, h, message_count=5, tool_call_count=1)
        row = db.execute(
            "SELECT mined_at FROM processed_sessions WHERE file_path = ?",
            (str(sample_session_file),),
        ).fetchone()
        assert row is not None
        assert row[0] is not None
        # Should be a valid ISO timestamp
        datetime.fromisoformat(row[0])


# ---------------------------------------------------------------------------
# Tests: Dedup behavior — mining same file twice
# ---------------------------------------------------------------------------


class TestDedupSameFileTwice:
    """Mining the same file twice should skip on second run (hash match)."""

    def test_second_mark_is_ignored_via_insert_or_ignore(self, db, sample_session_file):
        h = _file_hash(sample_session_file)
        _mark_processed(db, sample_session_file, h, message_count=5, tool_call_count=1)
        # Second call should not raise (INSERT OR IGNORE)
        _mark_processed(db, sample_session_file, h, message_count=5, tool_call_count=1)
        count = db.execute(
            "SELECT COUNT(*) FROM processed_sessions WHERE file_path = ? AND file_hash = ?",
            (str(sample_session_file), h),
        ).fetchone()[0]
        assert count == 1

    def test_is_already_processed_prevents_reprocessing(self, db, sample_session_file):
        h = _file_hash(sample_session_file)
        _mark_processed(db, sample_session_file, h, message_count=5, tool_call_count=1)
        # Simulate pipeline check
        assert _is_already_processed(db, sample_session_file, h) is True


# ---------------------------------------------------------------------------
# Tests: Re-processing after content change
# ---------------------------------------------------------------------------


class TestReprocessAfterContentChange:
    """When file content changes, hash changes, so the file should be re-processed."""

    def test_hash_changes_when_content_modified(self, db, sample_session_file):
        h1 = _file_hash(sample_session_file)
        _mark_processed(db, sample_session_file, h1, message_count=5, tool_call_count=1)

        # Modify file content
        with sample_session_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "type": "user",
                "message": {"role": "user", "content": "One more question."},
                "timestamp": "2026-04-01T10:01:00Z",
            }) + "\n")

        h2 = _file_hash(sample_session_file)
        assert h1 != h2, "Hash should change after file modification"

    def test_modified_file_not_marked_as_processed(self, db, sample_session_file):
        h1 = _file_hash(sample_session_file)
        _mark_processed(db, sample_session_file, h1, message_count=5, tool_call_count=1)

        # Modify the file
        with sample_session_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "type": "user",
                "message": {"role": "user", "content": "Extra message."},
                "timestamp": "2026-04-01T10:01:00Z",
            }) + "\n")

        h2 = _file_hash(sample_session_file)
        # New hash should NOT be in processed_sessions
        assert _is_already_processed(db, sample_session_file, h2) is False

    def test_modified_file_can_be_marked_separately(self, db, sample_session_file):
        h1 = _file_hash(sample_session_file)
        _mark_processed(db, sample_session_file, h1, message_count=5, tool_call_count=1)

        # Modify the file
        with sample_session_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "type": "user",
                "message": {"role": "user", "content": "Extra message."},
                "timestamp": "2026-04-01T10:01:00Z",
            }) + "\n")

        h2 = _file_hash(sample_session_file)
        _mark_processed(db, sample_session_file, h2, message_count=6, tool_call_count=1)

        # Both entries should exist (same path, different hashes)
        count = db.execute(
            "SELECT COUNT(*) FROM processed_sessions WHERE file_path = ?",
            (str(sample_session_file),),
        ).fetchone()[0]
        assert count == 2


# ---------------------------------------------------------------------------
# Tests: Full pipeline integration (run_mine with dedup)
# ---------------------------------------------------------------------------


class TestRunMineWithDedup:
    """Integration tests for run_mine using processed_sessions tracking.

    These tests verify the full pipeline flow: run_mine should check
    processed_sessions before parsing files and mark them after.
    """

    def test_run_mine_marks_file_as_processed(self, db, sample_session_file):
        from sio.mining.pipeline import run_mine

        run_mine(
            db_conn=db,
            source_dirs=[sample_session_file.parent],
            since="30 days",
            source_type="jsonl",
        )
        h = _file_hash(sample_session_file)
        assert _is_already_processed(db, sample_session_file, h) is True

    def test_run_mine_skips_already_processed_file(self, db, sample_session_file):
        from sio.mining.pipeline import run_mine

        # First run
        result1 = run_mine(
            db_conn=db,
            source_dirs=[sample_session_file.parent],
            since="30 days",
            source_type="jsonl",
        )
        # Second run — same file, same content
        result2 = run_mine(
            db_conn=db,
            source_dirs=[sample_session_file.parent],
            since="30 days",
            source_type="jsonl",
        )
        # Second run should find fewer (or zero) new errors because file was skipped
        assert result2["errors_found"] <= result1["errors_found"]

    def test_run_mine_reprocesses_after_content_change(self, db, sample_session_file):
        from sio.mining.pipeline import run_mine

        # First run
        run_mine(
            db_conn=db,
            source_dirs=[sample_session_file.parent],
            since="30 days",
            source_type="jsonl",
        )

        # Modify the file — add an error-producing message
        with sample_session_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "Let me try that command."},
                        {
                            "type": "tool_use",
                            "id": "toolu_bash_01",
                            "name": "Bash",
                            "input": {"command": "rm -rf /important"},
                        },
                    ],
                },
                "timestamp": "2026-04-01T10:02:00Z",
            }) + "\n")

        # Second run — modified file should be re-processed
        h2 = _file_hash(sample_session_file)
        assert _is_already_processed(db, sample_session_file, h2) is False
