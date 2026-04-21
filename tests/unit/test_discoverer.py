"""Unit tests for sio.suggestions.discoverer — skill candidate discovery.

Tests cover:
- ``discover_skill_candidates()`` with populated patterns + flows
- Classification into tool-specific, workflow-sequence, repo-specific
- Confidence scoring
- Cross-referencing of patterns with flow events
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sio.core.db.schema import init_db
from sio.suggestions.discoverer import (
    _classify_candidate,
    _compute_confidence,
    _extract_extensions_from_context,
    discover_skill_candidates,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(timezone.utc)


def _ts(days_ago: float = 0) -> str:
    return (_NOW - timedelta(days=days_ago)).isoformat()


@pytest.fixture()
def db():
    """In-memory SIO database with schema initialized."""
    conn = init_db(":memory:")
    yield conn
    conn.close()


def _insert_pattern(
    db,
    *,
    pattern_id: str,
    tool_name: str = "Bash",
    error_count: int = 3,
    session_count: int = 3,
    description: str = "Test pattern",
) -> int:
    now = _NOW.isoformat()
    cur = db.execute(
        "INSERT INTO patterns "
        "(pattern_id, description, tool_name, error_count, session_count, "
        "first_seen, last_seen, rank_score, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            pattern_id,
            description,
            tool_name,
            error_count,
            session_count,
            _ts(10),
            _ts(0),
            0.8,
            now,
            now,
        ),
    )
    db.commit()
    return cur.lastrowid


def _insert_error_record(
    db,
    *,
    session_id: str = "sess-001",
    tool_name: str = "Bash",
    error_type: str = "tool_failure",
    error_text: str = "command failed",
    context_before: str = "",
    source_file: str = "test.jsonl",
) -> int:
    cur = db.execute(
        "INSERT INTO error_records "
        "(session_id, timestamp, source_type, source_file, tool_name, "
        "error_text, user_message, context_before, error_type, mined_at) "
        "VALUES (?, ?, 'jsonl', ?, ?, ?, '', ?, ?, ?)",
        (
            session_id,
            _ts(1),
            source_file,
            tool_name,
            error_text,
            context_before,
            error_type,
            _NOW.isoformat(),
        ),
    )
    db.commit()
    return cur.lastrowid


def _link_error_to_pattern(db, pattern_id: int, error_id: int) -> None:
    db.execute(
        "INSERT INTO pattern_errors (pattern_id, error_id) VALUES (?, ?)",
        (pattern_id, error_id),
    )
    db.commit()


def _insert_flow_event(
    db,
    *,
    flow_hash: str = "abc123",
    sequence: str = "Read \u2192 Edit \u2192 Bash",
    session_id: str = "sess-001",
    was_successful: int = 1,
    ngram_size: int = 3,
) -> int:
    cur = db.execute(
        "INSERT INTO flow_events "
        "(session_id, flow_hash, sequence, ngram_size, was_successful, "
        "duration_seconds, source_file, timestamp, mined_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            session_id,
            flow_hash,
            sequence,
            ngram_size,
            was_successful,
            10.0,
            "test.jsonl",
            _ts(1),
            _NOW.isoformat(),
        ),
    )
    db.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Tests: internal helpers
# ---------------------------------------------------------------------------


class TestExtractExtensions:
    def test_extracts_py_extension(self) -> None:
        exts = _extract_extensions_from_context('file "/home/user/test.py" not found')
        assert ".py" in exts

    def test_extracts_multiple(self) -> None:
        exts = _extract_extensions_from_context('"a.py" and "b.sql" changed')
        assert ".py" in exts
        assert ".sql" in exts

    def test_empty_context(self) -> None:
        assert _extract_extensions_from_context(None) == []
        assert _extract_extensions_from_context("") == []


class TestClassifyCandidate:
    def test_workflow_sequence(self) -> None:
        from collections import Counter

        result = _classify_candidate(
            Counter({"Bash": 5}),
            ["flow1"],
            0,
            5,
        )
        assert result == "workflow-sequence"

    def test_tool_specific(self) -> None:
        from collections import Counter

        result = _classify_candidate(
            Counter({"Edit": 10}),
            [],
            0,
            10,
        )
        assert result == "tool-specific"

    def test_repo_specific(self) -> None:
        from collections import Counter

        result = _classify_candidate(
            Counter({"Bash": 3, "Read": 2}),
            [],
            8,
            10,
        )
        assert result == "repo-specific"


class TestComputeConfidence:
    def test_low_counts(self) -> None:
        conf = _compute_confidence(1, 1, None)
        assert 0.0 < conf < 0.3

    def test_high_counts_with_flow(self) -> None:
        conf = _compute_confidence(20, 10, 90.0)
        assert conf >= 0.7

    def test_no_flow(self) -> None:
        conf = _compute_confidence(10, 5, None)
        assert conf > 0.0
        # Without flow, max is 0.4 + 0.3 = 0.7
        assert conf <= 0.7


# ---------------------------------------------------------------------------
# Tests: discover_skill_candidates (integration with DB)
# ---------------------------------------------------------------------------


class TestDiscoverSkillCandidates:
    def test_empty_db_returns_empty(self, db) -> None:
        result = discover_skill_candidates(db)
        assert result == []

    def test_finds_tool_specific_candidate(self, db) -> None:
        """Patterns for a single tool should produce a tool-specific candidate."""
        pid = _insert_pattern(db, pattern_id="p1", tool_name="Edit", error_count=5)
        eid1 = _insert_error_record(db, tool_name="Edit", session_id="s1")
        eid2 = _insert_error_record(db, tool_name="Edit", session_id="s2")
        _link_error_to_pattern(db, pid, eid1)
        _link_error_to_pattern(db, pid, eid2)

        candidates = discover_skill_candidates(db)
        assert len(candidates) >= 1

        edit_candidates = [c for c in candidates if c["tool_name"] == "Edit"]
        assert len(edit_candidates) == 1
        assert edit_candidates[0]["suggested_skill_type"] == "tool-specific"
        assert edit_candidates[0]["error_count"] == 5

    def test_finds_workflow_sequence(self, db) -> None:
        """Patterns + matching flows should produce a workflow-sequence candidate."""
        pid = _insert_pattern(db, pattern_id="p2", tool_name="Bash", error_count=4)
        eid = _insert_error_record(db, tool_name="Bash")
        _link_error_to_pattern(db, pid, eid)

        # Insert flow events that mention "Bash"
        for i in range(5):
            _insert_flow_event(
                db,
                flow_hash="flow-bash-001",
                sequence="Read \u2192 Bash \u2192 Edit",
                session_id=f"sess-flow-{i}",
                was_successful=1,
            )

        candidates = discover_skill_candidates(db)
        bash_candidates = [c for c in candidates if c["tool_name"] == "Bash"]
        assert len(bash_candidates) >= 1
        assert bash_candidates[0]["suggested_skill_type"] == "workflow-sequence"
        assert len(bash_candidates[0]["flow_hashes"]) > 0

    def test_flow_only_candidates(self, db) -> None:
        """Flows without matching patterns should appear as workflow-sequence."""
        for i in range(5):
            _insert_flow_event(
                db,
                flow_hash="orphan-flow",
                sequence="Glob \u2192 Read \u2192 Grep",
                session_id=f"sess-orphan-{i}",
                was_successful=1,
            )

        candidates = discover_skill_candidates(db)
        flow_candidates = [c for c in candidates if "orphan-flow" in c["flow_hashes"]]
        assert len(flow_candidates) == 1
        assert flow_candidates[0]["suggested_skill_type"] == "workflow-sequence"

    def test_extensions_extracted(self, db) -> None:
        """File extensions from error context should appear in candidates."""
        pid = _insert_pattern(db, pattern_id="p3", tool_name="Read", error_count=3)
        eid = _insert_error_record(
            db,
            tool_name="Read",
            context_before='Reading "/home/user/app.py" failed',
        )
        _link_error_to_pattern(db, pid, eid)

        candidates = discover_skill_candidates(db)
        read_candidates = [c for c in candidates if c["tool_name"] == "Read"]
        assert len(read_candidates) >= 1
        assert ".py" in read_candidates[0]["extensions"]

    def test_sorted_by_confidence(self, db) -> None:
        """Candidates should be sorted by confidence descending."""
        _insert_pattern(
            db,
            pattern_id="low",
            tool_name="Glob",
            error_count=2,
            session_count=1,
        )
        _insert_pattern(
            db,
            pattern_id="high",
            tool_name="Write",
            error_count=15,
            session_count=8,
        )

        candidates = discover_skill_candidates(db)
        if len(candidates) >= 2:
            assert candidates[0]["confidence"] >= candidates[1]["confidence"]

    def test_multiple_tools_distinct_candidates(self, db) -> None:
        """Different tools should produce separate candidates."""
        _insert_pattern(db, pattern_id="pa", tool_name="Edit", error_count=3)
        _insert_pattern(db, pattern_id="pb", tool_name="Bash", error_count=4)

        candidates = discover_skill_candidates(db)
        tool_names = {c["tool_name"] for c in candidates if c["tool_name"]}
        assert "Edit" in tool_names
        assert "Bash" in tool_names
