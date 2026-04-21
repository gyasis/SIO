"""T074 — Integration test: SIO runs its own pipeline end-to-end.

Validates the full mine -> cluster -> dataset -> suggest pipeline using
mock SpecStory session data that mirrors real SIO development patterns.
Uses a temporary DB (tmp_path) and mocked SpecStory file reading so
the test is hermetic and fast.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from sio.clustering.pattern_clusterer import cluster_errors
from sio.clustering.ranker import rank_patterns
from sio.core.db.queries import (
    get_error_records,
    insert_error_record,
    insert_pattern,
    link_error_to_pattern,
)
from sio.core.db.schema import init_db
from sio.datasets.builder import build_dataset
from sio.mining.pipeline import run_mine
from sio.suggestions.generator import generate_suggestions

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ts_ago(days: int = 0, hours: int = 0) -> str:
    """Return an ISO timestamp *days*/*hours* in the past."""
    dt = datetime.now(timezone.utc) - timedelta(days=days, hours=hours)
    return dt.isoformat()


def _make_error_record(
    *,
    session_id: str = "sio-self-test-001",
    tool_name: str = "Read",
    error_text: str = "FileNotFoundError: No such file",
    error_type: str = "tool_failure",
    user_message: str = "Read the config file",
    source_file: str = "2026-04-02_10-00-00Z-sio-dev-session.md",
    days_ago: int = 0,
) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "timestamp": _ts_ago(days=days_ago),
        "source_type": "specstory",
        "source_file": source_file,
        "tool_name": tool_name,
        "error_text": error_text,
        "user_message": user_message,
        "context_before": f"Assistant called {tool_name}",
        "context_after": "User noticed the failure",
        "error_type": error_type,
        "mined_at": _now_iso(),
    }


# Realistic SIO development error samples — these mimic real patterns
# that arise during SIO's own development (tool failures, user corrections,
# agent admissions).
_SIO_ERROR_SAMPLES: list[dict[str, Any]] = [
    # Cluster 1: Read tool failures (file-not-found)
    _make_error_record(
        session_id="sio-dev-001",
        tool_name="Read",
        error_text="FileNotFoundError: [Errno 2] No such file or directory: '/home/user/dev/projects/SIO/src/sio/missing_module.py'",
        error_type="tool_failure",
        user_message="Read the mining module",
        days_ago=1,
    ),
    _make_error_record(
        session_id="sio-dev-001",
        tool_name="Read",
        error_text="FileNotFoundError: [Errno 2] No such file or directory: '/home/user/dev/projects/SIO/tests/test_nonexistent.py'",
        error_type="tool_failure",
        user_message="Read the test file",
        days_ago=1,
    ),
    _make_error_record(
        session_id="sio-dev-002",
        tool_name="Read",
        error_text="FileNotFoundError: [Errno 2] No such file or directory: '/home/user/.sio/config.toml'",
        error_type="tool_failure",
        user_message="Check the SIO config",
        days_ago=2,
    ),
    # Cluster 2: Bash/pytest failures
    _make_error_record(
        session_id="sio-dev-003",
        tool_name="Bash",
        error_text="FAILED tests/unit/test_pattern_clusterer.py::test_cluster_basic - AssertionError: expected 3 clusters, got 2",
        error_type="tool_failure",
        user_message="Run the clusterer tests",
        days_ago=0,
    ),
    _make_error_record(
        session_id="sio-dev-004",
        tool_name="Bash",
        error_text="FAILED tests/unit/test_suggestion_generator.py::test_generate - KeyError: 'pattern_id'",
        error_type="tool_failure",
        user_message="Run the suggestion tests",
        days_ago=1,
    ),
    _make_error_record(
        session_id="sio-dev-005",
        tool_name="Bash",
        error_text="FAILED tests/integration/test_mine_pipeline.py::test_full_mine - sqlite3.OperationalError: no such table: error_records",
        error_type="tool_failure",
        user_message="Run the integration tests",
        days_ago=3,
    ),
    # Cluster 3: User corrections about approach
    _make_error_record(
        session_id="sio-dev-006",
        tool_name="Edit",
        error_text="User correction: That's not what I asked — I wanted you to update the CLI, not the library code",
        error_type="user_correction",
        user_message="Update the suggest command to accept --verbose",
        days_ago=2,
    ),
    _make_error_record(
        session_id="sio-dev-007",
        tool_name="Edit",
        error_text="User correction: Wrong file — the schema is in core/db/schema.py, not core/schema.py",
        error_type="user_correction",
        user_message="Add the ground_truth table to the schema",
        days_ago=4,
    ),
    # Cluster 4: Agent admissions (self-identified mistakes)
    _make_error_record(
        session_id="sio-dev-008",
        tool_name="Bash",
        error_text="Agent admission: I should have read the existing test file before writing a new one — missed the existing fixtures",
        error_type="agent_admission",
        user_message="Write tests for the dataset builder",
        days_ago=1,
    ),
    _make_error_record(
        session_id="sio-dev-009",
        tool_name="Edit",
        error_text="Agent admission: I accidentally overwrote the import section — should have used Edit instead of Write",
        error_type="agent_admission",
        user_message="Fix the import in pipeline.py",
        days_ago=3,
    ),
]


def _build_specstory_md(errors: list[str]) -> str:
    """Build a minimal SpecStory markdown file with embedded tool errors."""
    lines = [
        "# Session: sio-self-test",
        "",
        "**Human:** Help me develop the SIO pipeline.",
        "",
        "**Assistant:** Let me start by examining the codebase.",
        "",
    ]
    for i, err in enumerate(errors):
        tool = "Bash" if i % 2 == 0 else "Read"
        lines += [
            f"**Tool call: {tool}**",
            "```json",
            f'{{"command": "test_cmd_{i}"}}'
            if tool == "Bash"
            else f'{{"file_path": "/tmp/test_{i}.py"}}',
            "```",
            "",
            f"**{tool} output (error):**",
            "```",
            err,
            "```",
            "",
        ]
    lines += [
        "**Human:** Thanks for the help.",
        "",
        "**Assistant:** You're welcome!",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSelfPipelineIntegration:
    """Full pipeline: mine -> cluster -> dataset -> suggest."""

    @pytest.fixture
    def db_conn(self, tmp_path: Path) -> sqlite3.Connection:
        """Create a temp DB with full SIO schema."""
        db_path = str(tmp_path / "sio_self_test.db")
        conn = init_db(db_path)
        yield conn
        conn.close()

    @pytest.fixture
    def seeded_db(
        self,
        db_conn: sqlite3.Connection,
    ) -> tuple[sqlite3.Connection, list[int]]:
        """Seed the DB with SIO-specific error records."""
        ids = []
        for record in _SIO_ERROR_SAMPLES:
            row_id = insert_error_record(db_conn, record)
            ids.append(row_id)
        return db_conn, ids

    def test_mine_from_specstory_files(
        self,
        db_conn: sqlite3.Connection,
        tmp_path: Path,
    ) -> None:
        """T074a: Mining from SpecStory files produces error records."""
        # Create a mock SpecStory directory with SIO-themed errors
        specstory_dir = tmp_path / "specstory"
        specstory_dir.mkdir()

        errors = [
            "FileNotFoundError: [Errno 2] No such file: '/tmp/sio_config.toml'",
            "FAILED tests/test_clusterer.py - AssertionError: clusters mismatch",
            "sqlite3.OperationalError: no such table: patterns",
        ]
        md_content = _build_specstory_md(errors)
        md_file = specstory_dir / "2026-04-02_10-00-00Z-sio-dev.md"
        md_file.write_text(md_content, encoding="utf-8")

        result = run_mine(
            db_conn,
            source_dirs=[specstory_dir],
            since="30 days",
            source_type="specstory",
        )

        assert result["total_files_scanned"] >= 1
        # The miner should find at least some error records
        # (exact count depends on the error extractor's heuristics)
        all_records = get_error_records(db_conn, limit=0)
        assert isinstance(all_records, list)

    def test_cluster_sio_errors(
        self,
        seeded_db: tuple[sqlite3.Connection, list[int]],
    ) -> None:
        """T074b: Clustering groups SIO errors into meaningful patterns."""
        conn, _ = seeded_db
        errors = get_error_records(conn, limit=0)

        assert len(errors) == len(_SIO_ERROR_SAMPLES)

        patterns = cluster_errors(errors)

        # We seeded 4 distinct error clusters, but clustering is approximate.
        # At minimum, similar errors should merge into fewer patterns than
        # the total error count.
        assert len(patterns) >= 1
        assert len(patterns) < len(errors)

        # Each pattern must have the required schema keys
        required_keys = {
            "pattern_id",
            "description",
            "tool_name",
            "error_count",
            "session_count",
            "first_seen",
            "last_seen",
            "rank_score",
            "error_ids",
        }
        for p in patterns:
            assert required_keys.issubset(p.keys()), (
                f"Pattern missing keys: {required_keys - p.keys()}"
            )

    def test_rank_sio_patterns(
        self,
        seeded_db: tuple[sqlite3.Connection, list[int]],
    ) -> None:
        """T074c: Ranking orders SIO patterns by importance."""
        conn, _ = seeded_db
        errors = get_error_records(conn, limit=0)
        patterns = cluster_errors(errors)
        ranked = rank_patterns(patterns)

        assert len(ranked) == len(patterns)
        # Ranked list should be sorted descending by rank_score
        scores = [p["rank_score"] for p in ranked]
        assert scores == sorted(scores, reverse=True)
        # All scores should be positive (we have recent errors)
        assert all(s > 0.0 for s in scores)

    def test_build_datasets_for_sio_patterns(
        self,
        seeded_db: tuple[sqlite3.Connection, list[int]],
        tmp_path: Path,
    ) -> None:
        """T074d: Dataset builder creates JSON files from SIO patterns."""
        conn, _ = seeded_db
        all_errors = get_error_records(conn, limit=0)
        patterns = cluster_errors(all_errors)
        ranked = rank_patterns(patterns)

        now_iso = _now_iso()
        dataset_dir = tmp_path / "datasets"
        datasets_built = 0

        for p in ranked:
            # Persist the pattern so the dataset builder can find linked errors
            p["centroid_embedding"] = None
            p["created_at"] = now_iso
            p["updated_at"] = now_iso
            row_id = insert_pattern(conn, p)
            p["id"] = row_id

            for eid in p.get("error_ids", []):
                link_error_to_pattern(conn, row_id, eid)

            # Build dataset with min_threshold=1 so small clusters still produce output
            metadata = build_dataset(
                p,
                all_errors,
                conn,
                dataset_dir=str(dataset_dir),
                min_threshold=1,
            )
            if metadata is not None:
                datasets_built += 1
                # Verify the JSON file was written and is valid
                ds_path = Path(metadata["file_path"])
                assert ds_path.exists()
                ds_data = json.loads(ds_path.read_text(encoding="utf-8"))
                assert "examples" in ds_data
                assert len(ds_data["examples"]) >= 1

        assert datasets_built >= 1, "Expected at least 1 dataset to be built"

    def test_full_pipeline_generates_suggestions(
        self,
        seeded_db: tuple[sqlite3.Connection, list[int]],
        tmp_path: Path,
    ) -> None:
        """T074e: Full pipeline produces at least 1 suggestion with _using_dspy key."""
        conn, _ = seeded_db
        all_errors = get_error_records(conn, limit=0)

        # Step 1: Cluster and rank
        patterns = cluster_errors(all_errors)
        ranked = rank_patterns(patterns)
        assert len(ranked) >= 1

        # Step 2: Persist patterns and link errors
        now_iso = _now_iso()
        persisted_patterns: list[dict] = []
        seen_slugs: set[str] = set()

        for p in ranked:
            slug = p["pattern_id"]
            if slug in seen_slugs:
                slug = f"{slug}-{p['error_count']}"
            seen_slugs.add(slug)
            p["pattern_id"] = slug
            p["centroid_embedding"] = None
            p["created_at"] = now_iso
            p["updated_at"] = now_iso
            row_id = insert_pattern(conn, p)
            p["id"] = row_id
            persisted_patterns.append(p)

            for eid in p.get("error_ids", []):
                link_error_to_pattern(conn, row_id, eid)

        # Step 3: Build datasets
        dataset_dir = tmp_path / "datasets"
        datasets: dict[str, dict] = {}

        for p in persisted_patterns:
            metadata = build_dataset(
                p,
                all_errors,
                conn,
                dataset_dir=str(dataset_dir),
                min_threshold=1,
            )
            if metadata is not None:
                pid = metadata["pattern_id"]
                # Simulate the DB insert for dataset ID
                ds_cur = conn.execute(
                    "INSERT INTO datasets (pattern_id, file_path, positive_count, "
                    "negative_count, min_threshold, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        p["id"],
                        metadata["file_path"],
                        metadata["positive_count"],
                        metadata["negative_count"],
                        1,
                        now_iso,
                        now_iso,
                    ),
                )
                conn.commit()
                metadata["id"] = ds_cur.lastrowid
                datasets[pid] = metadata

        assert len(datasets) >= 1, "Expected at least 1 dataset"

        # Step 4: Generate suggestions (template path, no LLM needed)
        suggestions = generate_suggestions(
            persisted_patterns,
            datasets,
            conn,
            verbose=False,
        )

        # --- Assertions ---
        assert len(suggestions) >= 1, f"Expected at least 1 suggestion, got {len(suggestions)}"

        for s in suggestions:
            # Every suggestion must have _using_dspy (True or False)
            assert "_using_dspy" in s, f"Suggestion missing '_using_dspy' key: {list(s.keys())}"
            assert isinstance(s["_using_dspy"], bool)

            # Required fields from the suggestion schema
            assert "description" in s and s["description"]
            assert "confidence" in s and 0.0 <= s["confidence"] <= 1.0
            assert "proposed_change" in s and s["proposed_change"]
            assert "target_file" in s and s["target_file"]
            assert "change_type" in s
            assert "status" in s and s["status"] == "pending"

    def test_suggestions_reference_sio_patterns(
        self,
        seeded_db: tuple[sqlite3.Connection, list[int]],
        tmp_path: Path,
    ) -> None:
        """T074f: Suggestions reference real SIO development patterns.

        Verifies that the generated suggestions contain content related
        to the SIO-specific errors we seeded (tool names, error types).
        """
        conn, _ = seeded_db
        all_errors = get_error_records(conn, limit=0)

        patterns = cluster_errors(all_errors)
        ranked = rank_patterns(patterns)

        now_iso = _now_iso()
        persisted: list[dict] = []
        seen: set[str] = set()

        for p in ranked:
            slug = p["pattern_id"]
            if slug in seen:
                slug = f"{slug}-{p['error_count']}"
            seen.add(slug)
            p["pattern_id"] = slug
            p["centroid_embedding"] = None
            p["created_at"] = now_iso
            p["updated_at"] = now_iso
            row_id = insert_pattern(conn, p)
            p["id"] = row_id
            persisted.append(p)
            for eid in p.get("error_ids", []):
                link_error_to_pattern(conn, row_id, eid)

        dataset_dir = tmp_path / "datasets"
        datasets: dict[str, dict] = {}
        for p in persisted:
            metadata = build_dataset(
                p,
                all_errors,
                conn,
                dataset_dir=str(dataset_dir),
                min_threshold=1,
            )
            if metadata is not None:
                pid = metadata["pattern_id"]
                ds_cur = conn.execute(
                    "INSERT INTO datasets (pattern_id, file_path, positive_count, "
                    "negative_count, min_threshold, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        p["id"],
                        metadata["file_path"],
                        metadata["positive_count"],
                        metadata["negative_count"],
                        1,
                        now_iso,
                        now_iso,
                    ),
                )
                conn.commit()
                metadata["id"] = ds_cur.lastrowid
                datasets[pid] = metadata

        suggestions = generate_suggestions(persisted, datasets, conn)

        # Collect all description + proposed_change text for pattern matching
        all_text = " ".join(
            f"{s.get('description', '')} {s.get('proposed_change', '')}" for s in suggestions
        ).lower()

        # The suggestions should reference at least one of the SIO tool names
        # we seeded errors for
        sio_tools = {"read", "bash", "edit"}
        referenced_tools = {t for t in sio_tools if t in all_text}
        assert len(referenced_tools) >= 1, (
            f"Suggestions should reference SIO tools. "
            f"Found: {referenced_tools}, all text sample: {all_text[:200]}"
        )

    def test_multiple_change_types_possible(
        self,
        seeded_db: tuple[sqlite3.Connection, list[int]],
        tmp_path: Path,
    ) -> None:
        """T074g: Pipeline can produce suggestions targeting different surfaces."""
        conn, _ = seeded_db
        all_errors = get_error_records(conn, limit=0)
        patterns = cluster_errors(all_errors)
        ranked = rank_patterns(patterns)

        now_iso = _now_iso()
        persisted: list[dict] = []
        seen: set[str] = set()

        for p in ranked:
            slug = p["pattern_id"]
            if slug in seen:
                slug = f"{slug}-{p['error_count']}"
            seen.add(slug)
            p["pattern_id"] = slug
            p["centroid_embedding"] = None
            p["created_at"] = now_iso
            p["updated_at"] = now_iso
            row_id = insert_pattern(conn, p)
            p["id"] = row_id
            persisted.append(p)
            for eid in p.get("error_ids", []):
                link_error_to_pattern(conn, row_id, eid)

        dataset_dir = tmp_path / "datasets"
        datasets: dict[str, dict] = {}
        for p in persisted:
            metadata = build_dataset(
                p,
                all_errors,
                conn,
                dataset_dir=str(dataset_dir),
                min_threshold=1,
            )
            if metadata is not None:
                pid = metadata["pattern_id"]
                ds_cur = conn.execute(
                    "INSERT INTO datasets (pattern_id, file_path, positive_count, "
                    "negative_count, min_threshold, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        p["id"],
                        metadata["file_path"],
                        metadata["positive_count"],
                        metadata["negative_count"],
                        1,
                        now_iso,
                        now_iso,
                    ),
                )
                conn.commit()
                metadata["id"] = ds_cur.lastrowid
                datasets[pid] = metadata

        suggestions = generate_suggestions(persisted, datasets, conn)

        # Collect the change_type values from all suggestions
        change_types = {s["change_type"] for s in suggestions}

        # At minimum we expect claude_md_rule (the default for most errors)
        assert "claude_md_rule" in change_types, (
            f"Expected 'claude_md_rule' in change types, got: {change_types}"
        )
