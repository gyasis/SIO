"""Tests for Phase 8 — US7: Automated and Human-in-the-Middle Modes.

Covers:
  T065 - Mode selection logic (_select_mode)
  T066 - HITL interactive flow (generate_hitl_suggestion)
  T067 - Dataset inspect command (sio datasets inspect)
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner


def _mock_db_conn(conn):
    """Return a callable that mimics _db_conn(db_path) context manager."""

    @contextmanager
    def _inner(_db_path=None):
        yield conn

    return _inner


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_LOW_IMPACT_SURFACES = frozenset({"claude_md_rule", "agent_profile"})
_HIGH_IMPACT_SURFACES = frozenset(
    {
        "hook_config",
        "mcp_config",
        "settings_config",
        "project_config",
        "skill_update",
    }
)


@pytest.fixture()
def sample_pattern() -> dict[str, Any]:
    """A realistic pattern dict matching the patterns table schema."""
    return {
        "id": 42,
        "pattern_id": "pat-tool-failure-Read-abc123",
        "description": "Read tool fails on nonexistent paths",
        "tool_name": "Read",
        "error_type": "tool_failure",
        "error_count": 12,
        "session_count": 4,
        "first_seen": "2026-02-20T10:00:00Z",
        "last_seen": "2026-02-25T10:00:00Z",
        "rank_score": 0.85,
    }


@pytest.fixture()
def sample_dataset(tmp_path) -> dict[str, Any]:
    """A dataset dict with a JSON file on disk."""
    examples = {
        "examples": [
            {
                "error_type": "tool_failure",
                "tool_name": "Read",
                "error_text": "File not found: /tmp/missing.txt",
                "user_message": "Read the config file",
                "session_id": "sess-001",
                "timestamp": "2026-02-20T10:05:00Z",
            },
            {
                "error_type": "tool_failure",
                "tool_name": "Read",
                "error_text": "Permission denied: /etc/shadow",
                "user_message": "Show file contents",
                "session_id": "sess-002",
                "timestamp": "2026-02-21T14:00:00Z",
            },
        ]
    }
    fp = tmp_path / "dataset_42.json"
    fp.write_text(json.dumps(examples))
    return {
        "id": 10,
        "pattern_id": "pat-tool-failure-Read-abc123",
        "file_path": str(fp),
        "positive_count": 8,
        "negative_count": 4,
    }


@pytest.fixture()
def mock_config():
    """A minimal SIOConfig mock."""
    cfg = MagicMock()
    cfg.llm_model = "openai/gpt-4o-mini"
    cfg.llm_api_key = "sk-test"
    cfg.llm_api_base = None
    return cfg


@pytest.fixture()
def in_memory_db():
    """In-memory SQLite DB with SIO schema."""
    from sio.core.db.schema import init_db

    conn = init_db(":memory:")
    return conn


# ===========================================================================
# T065 — Mode selection logic
# ===========================================================================


class TestModeSelection:
    """Tests for _select_mode function."""

    def test_auto_mode_high_confidence_low_impact(self, sample_pattern):
        """High confidence + low-impact surface => auto mode."""
        from sio.suggestions.dspy_generator import _select_mode

        result = _select_mode(sample_pattern, confidence=0.85, target_surface="claude_md_rule")
        assert result == "auto"

    def test_auto_mode_exactly_at_threshold(self, sample_pattern):
        """Confidence exactly at 0.8 threshold with low-impact surface => auto."""
        from sio.suggestions.dspy_generator import _select_mode

        result = _select_mode(sample_pattern, confidence=0.8, target_surface="agent_profile")
        assert result == "auto"

    def test_hitl_mode_low_confidence(self, sample_pattern):
        """Low confidence even with low-impact surface => HITL."""
        from sio.suggestions.dspy_generator import _select_mode

        result = _select_mode(sample_pattern, confidence=0.79, target_surface="claude_md_rule")
        assert result == "hitl"

    def test_hitl_mode_high_impact_surface(self, sample_pattern):
        """High confidence but high-impact surface => HITL."""
        from sio.suggestions.dspy_generator import _select_mode

        for surface in _HIGH_IMPACT_SURFACES:
            result = _select_mode(sample_pattern, confidence=0.95, target_surface=surface)
            assert result == "hitl", f"Expected hitl for surface {surface}"

    def test_hitl_mode_both_low_confidence_high_impact(self, sample_pattern):
        """Low confidence AND high-impact => definitely HITL."""
        from sio.suggestions.dspy_generator import _select_mode

        result = _select_mode(sample_pattern, confidence=0.3, target_surface="hook_config")
        assert result == "hitl"

    def test_hitl_mode_zero_confidence(self, sample_pattern):
        """Zero confidence => HITL regardless of surface."""
        from sio.suggestions.dspy_generator import _select_mode

        result = _select_mode(sample_pattern, confidence=0.0, target_surface="claude_md_rule")
        assert result == "hitl"

    def test_auto_mode_all_low_impact_surfaces(self, sample_pattern):
        """All low-impact surfaces with high confidence => auto."""
        from sio.suggestions.dspy_generator import _select_mode

        for surface in _LOW_IMPACT_SURFACES:
            result = _select_mode(sample_pattern, confidence=0.9, target_surface=surface)
            assert result == "auto", f"Expected auto for surface {surface}"

    def test_unknown_surface_defaults_to_hitl(self, sample_pattern):
        """Unknown surface name should be treated as HITL (conservative)."""
        from sio.suggestions.dspy_generator import _select_mode

        result = _select_mode(sample_pattern, confidence=0.95, target_surface="unknown_thing")
        assert result == "hitl"


# ===========================================================================
# T065 extended — generate_auto_suggestion
# ===========================================================================


class TestAutoMode:
    """Tests for generate_auto_suggestion."""

    @patch("sio.suggestions.dspy_generator.generate_dspy_suggestion")
    def test_auto_generates_suggestion_dict(
        self,
        mock_gen,
        sample_pattern,
        sample_dataset,
        mock_config,
    ):
        """Auto mode returns a suggestion dict with mode='auto' and status='auto_approved'."""
        from sio.suggestions.dspy_generator import generate_auto_suggestion

        mock_gen.return_value = {
            "pattern_id": 42,
            "dataset_id": 10,
            "description": "Test suggestion",
            "confidence": 0.9,
            "proposed_change": "## Fix the tool",
            "target_file": "CLAUDE.md",
            "target_surface": "claude_md_rule",
            "change_type": "claude_md_rule",
            "rule_title": "Fix Read Tool",
            "prevention_instructions": "Check path exists",
            "rationale": "12 errors observed",
            "reasoning_trace": "...",
            "status": "pending",
            "_using_dspy": True,
        }

        result = generate_auto_suggestion(
            sample_pattern,
            sample_dataset,
            mock_config,
        )

        assert result is not None
        assert result["_mode"] == "auto"
        assert result["status"] == "auto_approved"
        assert result["confidence"] == 0.9
        mock_gen.assert_called_once()

    @patch("sio.suggestions.dspy_generator.generate_dspy_suggestion")
    def test_auto_falls_back_on_dspy_error(
        self,
        mock_gen,
        sample_pattern,
        sample_dataset,
        mock_config,
    ):
        """If DSPy generation raises, auto mode returns None."""
        from sio.suggestions.dspy_generator import generate_auto_suggestion

        mock_gen.side_effect = RuntimeError("LLM unavailable")

        result = generate_auto_suggestion(
            sample_pattern,
            sample_dataset,
            mock_config,
        )
        assert result is None


# ===========================================================================
# T066 — HITL interactive flow
# ===========================================================================


class TestHITLFlow:
    """Tests for generate_hitl_suggestion interactive flow."""

    @patch("sio.suggestions.dspy_generator.generate_dspy_suggestion")
    def test_hitl_approve_flow(
        self,
        mock_gen,
        sample_pattern,
        sample_dataset,
        mock_config,
        in_memory_db,
    ):
        """HITL flow with all 'y' approvals returns a suggestion dict."""
        from sio.suggestions.dspy_generator import generate_hitl_suggestion

        mock_gen.return_value = {
            "pattern_id": 42,
            "dataset_id": 10,
            "description": "Test suggestion",
            "confidence": 0.7,
            "proposed_change": "## Fix the tool\n\nCheck paths",
            "target_file": "CLAUDE.md",
            "target_surface": "claude_md_rule",
            "change_type": "claude_md_rule",
            "rule_title": "Fix Read Tool",
            "prevention_instructions": "Check path exists",
            "rationale": "12 errors observed",
            "reasoning_trace": "Step 1: analyzed...",
            "status": "pending",
            "_using_dspy": True,
        }

        # Simulate user pressing 'y' at each pause:
        # 1. Continue after dataset summary?
        # 2. Continue after suggestion review?
        # 3. Approve suggestion?
        responses = iter(["y", "y", "y"])

        result = generate_hitl_suggestion(
            sample_pattern,
            sample_dataset,
            mock_config,
            in_memory_db,
            input_fn=lambda prompt: next(responses),
        )

        assert result is not None
        assert result["_mode"] == "hitl"
        assert result["status"] == "approved"

    @patch("sio.suggestions.dspy_generator.generate_dspy_suggestion")
    def test_hitl_reject_at_dataset_summary(
        self,
        mock_gen,
        sample_pattern,
        sample_dataset,
        mock_config,
        in_memory_db,
    ):
        """User rejects at dataset summary stage => returns None."""
        from sio.suggestions.dspy_generator import generate_hitl_suggestion

        # User says 'n' at first prompt (dataset summary)
        responses = iter(["n"])

        result = generate_hitl_suggestion(
            sample_pattern,
            sample_dataset,
            mock_config,
            in_memory_db,
            input_fn=lambda prompt: next(responses),
        )
        assert result is None
        # DSPy generation should never have been called
        mock_gen.assert_not_called()

    @patch("sio.suggestions.dspy_generator.generate_dspy_suggestion")
    def test_hitl_reject_at_suggestion_review(
        self,
        mock_gen,
        sample_pattern,
        sample_dataset,
        mock_config,
        in_memory_db,
    ):
        """User approves dataset but rejects suggestion => returns None."""
        from sio.suggestions.dspy_generator import generate_hitl_suggestion

        mock_gen.return_value = {
            "pattern_id": 42,
            "dataset_id": 10,
            "description": "Test suggestion",
            "confidence": 0.7,
            "proposed_change": "## Fix",
            "target_file": "CLAUDE.md",
            "target_surface": "claude_md_rule",
            "change_type": "claude_md_rule",
            "rule_title": "Fix Read",
            "prevention_instructions": "Check path",
            "rationale": "errors observed",
            "reasoning_trace": "...",
            "status": "pending",
            "_using_dspy": True,
        }

        # y for dataset summary, y to continue to suggestion, n to reject
        responses = iter(["y", "y", "n"])

        result = generate_hitl_suggestion(
            sample_pattern,
            sample_dataset,
            mock_config,
            in_memory_db,
            input_fn=lambda prompt: next(responses),
        )
        assert result is None

    @patch("sio.suggestions.dspy_generator.generate_dspy_suggestion")
    def test_hitl_dspy_failure_returns_none(
        self,
        mock_gen,
        sample_pattern,
        sample_dataset,
        mock_config,
        in_memory_db,
    ):
        """If DSPy generation fails during HITL, returns None gracefully."""
        from sio.suggestions.dspy_generator import generate_hitl_suggestion

        mock_gen.side_effect = RuntimeError("LLM down")

        # User approves dataset summary, then generation fails
        responses = iter(["y"])

        result = generate_hitl_suggestion(
            sample_pattern,
            sample_dataset,
            mock_config,
            in_memory_db,
            input_fn=lambda prompt: next(responses),
        )
        assert result is None


# ===========================================================================
# T067 — Dataset inspect command (sio datasets inspect <pattern_id>)
# ===========================================================================


class TestDatasetInspect:
    """Tests for 'sio datasets inspect' CLI command."""

    def _setup_db_with_data(self, conn: sqlite3.Connection) -> str:
        """Insert test data into the DB and return the pattern_id slug."""
        now = datetime.now(timezone.utc).isoformat()
        pattern_id_slug = "pat-tool-failure-Read-abc123"

        # Insert pattern
        conn.execute(
            "INSERT INTO patterns (pattern_id, description, tool_name, error_count, "
            "session_count, first_seen, last_seen, rank_score, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (pattern_id_slug, "Read tool fails", "Read", 12, 4, now, now, 0.85, now, now),
        )
        conn.commit()
        pat_row_id = conn.execute(
            "SELECT id FROM patterns WHERE pattern_id = ?", (pattern_id_slug,)
        ).fetchone()[0]

        # Insert error records
        for i in range(5):
            etype = "tool_failure" if i < 3 else "user_correction"
            conn.execute(
                "INSERT INTO error_records (session_id, timestamp, source_type, source_file, "
                "tool_name, error_text, user_message, error_type, mined_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f"sess-{i:03d}",
                    now,
                    "specstory",
                    f"file-{i}.md",
                    "Read",
                    f"Error #{i}",
                    f"User msg #{i}",
                    etype,
                    now,
                ),
            )
        conn.commit()

        # Link errors to pattern
        error_ids = [r[0] for r in conn.execute("SELECT id FROM error_records").fetchall()]
        for eid in error_ids:
            conn.execute(
                "INSERT OR IGNORE INTO pattern_errors (pattern_id, error_id) VALUES (?, ?)",
                (pat_row_id, eid),
            )
        conn.commit()

        # Insert dataset
        conn.execute(
            "INSERT INTO datasets (pattern_id, file_path, positive_count, negative_count, "
            "min_threshold, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (pat_row_id, "/tmp/fake_dataset.json", 8, 4, 3, now, now),
        )
        conn.commit()

        # Insert ground truth entries
        for label in ("positive", "pending", "negative"):
            conn.execute(
                "INSERT INTO ground_truth (pattern_id, error_examples_json, error_type, "
                "pattern_summary, target_surface, rule_title, prevention_instructions, "
                "rationale, label, source, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    pattern_id_slug,
                    "[]",
                    "tool_failure",
                    "Read tool errors",
                    "claude_md_rule",
                    "Fix Read",
                    "Check paths",
                    "Errors seen",
                    label,
                    "agent",
                    now,
                ),
            )
        conn.commit()

        return pattern_id_slug

    def test_inspect_shows_error_distribution(self, in_memory_db):
        """Inspect command outputs error type distribution."""
        from sio.cli.main import cli

        pattern_slug = self._setup_db_with_data(in_memory_db)

        runner = CliRunner()
        with (
            patch("sio.cli.main._db_conn", _mock_db_conn(in_memory_db)),
            patch("os.path.exists", return_value=True),
        ):
            result = runner.invoke(cli, ["datasets", "inspect", pattern_slug])

        assert result.exit_code == 0, f"CLI error: {result.output}"
        # Should show error type counts
        assert "tool_failure" in result.output
        assert "user_correction" in result.output

    def test_inspect_shows_ground_truth_info(self, in_memory_db):
        """Inspect command shows ground truth label distribution."""
        from sio.cli.main import cli

        pattern_slug = self._setup_db_with_data(in_memory_db)

        runner = CliRunner()
        with (
            patch("sio.cli.main._db_conn", _mock_db_conn(in_memory_db)),
            patch("os.path.exists", return_value=True),
        ):
            result = runner.invoke(cli, ["datasets", "inspect", pattern_slug])

        assert result.exit_code == 0, f"CLI error: {result.output}"
        assert "Ground Truth" in result.output or "ground truth" in result.output.lower()

    def test_inspect_pattern_not_found(self, in_memory_db):
        """Inspect with nonexistent pattern_id returns helpful message."""
        from sio.cli.main import cli

        runner = CliRunner()
        with (
            patch("sio.cli.main._db_conn", _mock_db_conn(in_memory_db)),
            patch("os.path.exists", return_value=True),
        ):
            result = runner.invoke(cli, ["datasets", "inspect", "nonexistent-pattern"])

        assert result.exit_code == 0
        assert "not found" in result.output.lower() or "No pattern" in result.output

    def test_inspect_shows_session_info(self, in_memory_db):
        """Inspect command includes session count or timeline information."""
        from sio.cli.main import cli

        pattern_slug = self._setup_db_with_data(in_memory_db)

        runner = CliRunner()
        with (
            patch("sio.cli.main._db_conn", _mock_db_conn(in_memory_db)),
            patch("os.path.exists", return_value=True),
        ):
            result = runner.invoke(cli, ["datasets", "inspect", pattern_slug])

        assert result.exit_code == 0, f"CLI error: {result.output}"
        # Should show session-related info
        assert "session" in result.output.lower() or "Session" in result.output

    def test_inspect_shows_coverage_gaps(self, in_memory_db):
        """Inspect command identifies surface coverage gaps."""
        from sio.cli.main import cli

        pattern_slug = self._setup_db_with_data(in_memory_db)

        runner = CliRunner()
        with (
            patch("sio.cli.main._db_conn", _mock_db_conn(in_memory_db)),
            patch("os.path.exists", return_value=True),
        ):
            result = runner.invoke(cli, ["datasets", "inspect", pattern_slug])

        assert result.exit_code == 0, f"CLI error: {result.output}"
        # Should mention coverage or surfaces
        assert "coverage" in result.output.lower() or "surface" in result.output.lower()


# ===========================================================================
# T071 — CLI flags (--auto, --analyze)
# ===========================================================================


class TestCLIFlags:
    """Tests for --auto and --analyze flags on sio suggest."""

    def test_suggest_has_auto_flag(self):
        """The suggest command accepts --auto flag."""
        from sio.cli.main import suggest

        param_names = [p.name for p in suggest.params]
        assert "auto_mode" in param_names or "auto" in param_names

    def test_suggest_has_analyze_flag(self):
        """The suggest command accepts --analyze flag."""
        from sio.cli.main import suggest

        param_names = [p.name for p in suggest.params]
        assert "analyze_mode" in param_names or "analyze" in param_names

    def test_auto_and_analyze_both_accepted(self):
        """Both --auto and --analyze flags are recognized by Click."""
        from sio.cli.main import suggest

        param_names = [p.name for p in suggest.params]
        # Both flags exist as parameters
        assert "auto_mode" in param_names
        assert "analyze_mode" in param_names
        # They are independent boolean flags (not mutually exclusive Click group)
        auto_param = next(p for p in suggest.params if p.name == "auto_mode")
        analyze_param = next(p for p in suggest.params if p.name == "analyze_mode")
        assert auto_param.is_flag
        assert analyze_param.is_flag


# ===========================================================================
# T073 — Dataset analysis summary
# ===========================================================================


class TestDatasetAnalysisSummary:
    """Tests for build_dataset_analysis_summary used in HITL mode."""

    def test_summary_returns_dict(self, sample_pattern, sample_dataset):
        """build_dataset_analysis_summary returns a dict with expected keys."""
        from sio.suggestions.dspy_generator import build_dataset_analysis_summary

        summary = build_dataset_analysis_summary(sample_pattern, sample_dataset)

        assert isinstance(summary, dict)
        assert "error_count" in summary
        assert "session_count" in summary
        assert "date_range" in summary
        assert "top_tools" in summary
        assert "top_error_messages" in summary
        assert "surface_prediction" in summary

    def test_summary_error_count_matches_pattern(self, sample_pattern, sample_dataset):
        """Summary error_count comes from the pattern."""
        from sio.suggestions.dspy_generator import build_dataset_analysis_summary

        summary = build_dataset_analysis_summary(sample_pattern, sample_dataset)
        assert summary["error_count"] == sample_pattern["error_count"]

    def test_summary_with_empty_dataset(self, sample_pattern, tmp_path):
        """Summary handles empty dataset gracefully."""
        from sio.suggestions.dspy_generator import build_dataset_analysis_summary

        empty_ds = {
            "id": 99,
            "pattern_id": "test",
            "file_path": str(tmp_path / "nonexistent.json"),
            "positive_count": 0,
            "negative_count": 0,
        }
        summary = build_dataset_analysis_summary(sample_pattern, empty_ds)
        assert summary["error_count"] == sample_pattern["error_count"]
        assert summary["top_error_messages"] == []

    def test_summary_extracts_top_tools(self, sample_pattern, sample_dataset):
        """Summary extracts tool names from examples."""
        from sio.suggestions.dspy_generator import build_dataset_analysis_summary

        summary = build_dataset_analysis_summary(sample_pattern, sample_dataset)
        # Our fixture has Read tool in examples
        assert len(summary["top_tools"]) > 0

    def test_summary_extracts_top_error_messages(self, sample_pattern, sample_dataset):
        """Summary extracts error message snippets from examples."""
        from sio.suggestions.dspy_generator import build_dataset_analysis_summary

        summary = build_dataset_analysis_summary(sample_pattern, sample_dataset)
        assert len(summary["top_error_messages"]) > 0
