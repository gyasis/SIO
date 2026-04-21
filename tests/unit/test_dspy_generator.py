"""Tests for sio.suggestions.dspy_generator -- DSPy-powered suggestion generation.

Covers:
  T025 - generate_dspy_suggestion() output schema and DSPy integration
  T026 - Input sanitization (_sanitize_examples, _truncate_fields)
  T027 - Verbose trace logging
  T028 - Template fallback in generator.py when no LLM is available
  T030 - Surface-to-file mapping
"""

from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Valid target surfaces (FR-010)
# ---------------------------------------------------------------------------
VALID_TARGET_SURFACES = frozenset(
    {
        "claude_md_rule",
        "skill_update",
        "hook_config",
        "mcp_config",
        "settings_config",
        "agent_profile",
        "project_config",
    }
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_pattern():
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
        "last_seen": "2026-02-25T14:00:00Z",
        "rank_score": 0.78,
    }


@pytest.fixture()
def sample_dataset(tmp_path):
    """A realistic dataset metadata dict with a real examples file."""
    examples_file = tmp_path / "test_dataset.json"
    examples_file.write_text(
        json.dumps(
            {
                "examples": [
                    {
                        "error_text": "FileNotFoundError: /tmp/missing.py",
                        "error_type": "tool_failure",
                        "tool_name": "Read",
                        "user_message": "Read the config file.",
                    },
                    {
                        "error_text": "FileNotFoundError: /home/user/gone.txt",
                        "error_type": "tool_failure",
                        "tool_name": "Read",
                        "user_message": "Show me gone.txt.",
                    },
                ]
            }
        )
    )
    return {
        "id": 7,
        "pattern_id": "pat-tool-failure-Read-abc123",
        "file_path": str(examples_file),
        "positive_count": 8,
        "negative_count": 4,
    }


@pytest.fixture()
def mock_config():
    """A mock SIOConfig with LLM settings."""
    cfg = MagicMock()
    cfg.llm_model = "test/model"
    cfg.llm_temperature = 0.7
    cfg.llm_max_tokens = 2000
    cfg.llm_api_key_env = None
    cfg.llm_api_base_env = None
    cfg.llm_sub_model = None
    return cfg


@pytest.fixture()
def mock_dspy_prediction():
    """A MagicMock that mimics a Prediction from SuggestionGenerator.forward().

    Audit Round 2 C-R2.6 migration: SuggestionGenerator now emits the
    PatternToRule output fields (rule_title / rule_body / rule_rationale).
    Legacy aliases (prevention_instructions, rationale, target_surface) are
    also populated so assertions against the legacy-shaped suggestion dict
    (whose keys are mapped from new fields in generate_dspy_suggestion)
    continue to work.
    """
    pred = MagicMock()
    # New canonical output fields (what production reads)
    pred.rule_title = "Verify file existence before Read calls"
    pred.rule_body = (
        "Before calling the Read tool, check that the file path exists "
        "using Glob or Bash `test -f`. This prevents FileNotFoundError."
    )
    pred.rule_rationale = (
        "12 failures across 4 sessions show Read is called on missing paths. "
        "A precondition check eliminates this recurring error class."
    )
    pred.reasoning = "The pattern shows repeated FileNotFoundError..."
    # Legacy aliases (for any remaining legacy-path assertions)
    pred.target_surface = "claude_md_rule"
    pred.prevention_instructions = pred.rule_body
    pred.rationale = pred.rule_rationale
    return pred


def _make_dummy_lm():
    """Return a DSPy-compatible LM stub.

    DSPy 3.1.3's `dspy.configure(lm=...)` type-checks against BaseLM and
    rejects MagicMock. DummyLM is a BaseLM subclass that passes the check.
    """
    from dspy.utils.dummies import DummyLM

    return DummyLM(answers=[{}])


# =========================================================================
# T026: Test input sanitization
# =========================================================================


class TestSanitizeExamples:
    """_sanitize_examples must strip secrets from error examples."""

    def test_strips_openai_api_keys(self):
        from sio.suggestions.dspy_generator import _sanitize_examples

        raw = json.dumps(
            [
                {
                    "error_text": "Error: key sk-abc123def456ghi789jkl012mno345pqr678",
                    "tool_name": "Bash",
                }
            ]
        )
        result = _sanitize_examples(raw)
        assert "sk-abc123" not in result
        assert "[REDACTED]" in result

    def test_strips_aws_access_keys(self):
        from sio.suggestions.dspy_generator import _sanitize_examples

        raw = json.dumps([{"error_text": "AccessDenied: key=AKIAIOSFODNN7EXAMPLE"}])
        result = _sanitize_examples(raw)
        assert "AKIAIOSFODNN7EXAMPLE" not in result

    def test_strips_bearer_tokens(self):
        from sio.suggestions.dspy_generator import _sanitize_examples

        raw = json.dumps(
            [
                {
                    "error_text": "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abc.def",
                }
            ]
        )
        result = _sanitize_examples(raw)
        assert "eyJhbGciOiJIUzI1NiJ9" not in result

    def test_strips_password_patterns(self):
        from sio.suggestions.dspy_generator import _sanitize_examples

        raw = json.dumps([{"error_text": "password=SuperSecret123!"}])
        result = _sanitize_examples(raw)
        assert "SuperSecret123!" not in result

    def test_preserves_non_secret_content(self):
        from sio.suggestions.dspy_generator import _sanitize_examples

        raw = json.dumps(
            [
                {
                    "error_text": "FileNotFoundError: /tmp/missing.py",
                    "tool_name": "Read",
                }
            ]
        )
        result = _sanitize_examples(raw)
        assert "FileNotFoundError" in result
        assert "/tmp/missing.py" in result

    def test_handles_empty_input(self):
        from sio.suggestions.dspy_generator import _sanitize_examples

        result = _sanitize_examples("[]")
        assert isinstance(result, str)
        assert result == "[]"


class TestTruncateFields:
    """_truncate_fields must cap text at max_chars."""

    def test_truncates_long_text(self):
        from sio.suggestions.dspy_generator import _truncate_fields

        result = _truncate_fields("x" * 800, max_chars=500)
        assert len(result) == 503  # 500 + "..."

    def test_preserves_short_text(self):
        from sio.suggestions.dspy_generator import _truncate_fields

        result = _truncate_fields("short text", max_chars=500)
        assert result == "short text"

    def test_boundary_exactly_max(self):
        from sio.suggestions.dspy_generator import _truncate_fields

        result = _truncate_fields("a" * 500, max_chars=500)
        assert len(result) == 500
        assert not result.endswith("...")

    def test_boundary_one_over(self):
        from sio.suggestions.dspy_generator import _truncate_fields

        result = _truncate_fields("b" * 501, max_chars=500)
        assert result.endswith("...")
        assert len(result) == 503

    def test_custom_max_chars(self):
        from sio.suggestions.dspy_generator import _truncate_fields

        result = _truncate_fields("c" * 200, max_chars=100)
        assert len(result) == 103


# =========================================================================
# T025: Test generate_dspy_suggestion()
# =========================================================================


class TestGenerateDspySuggestion:
    """generate_dspy_suggestion calls SuggestionModule and returns correct schema."""

    def _run_with_mock(
        self,
        sample_pattern,
        sample_dataset,
        mock_config,
        mock_prediction,
    ):
        """Helper to run generate_dspy_suggestion with mocked DSPy."""
        with (
            patch("sio.core.dspy.lm_factory.create_lm") as mock_create_lm,
            patch("sio.suggestions.dspy_generator._load_optimized_or_default") as MockLoad,
            patch("dspy.configure"),
        ):
            # DSPy 3.1.3 requires BaseLM — DummyLM passes the type check
            mock_create_lm.return_value = _make_dummy_lm()

            mock_instance = MagicMock()
            mock_instance.forward.return_value = mock_prediction
            MockLoad.return_value = mock_instance

            from sio.suggestions.dspy_generator import generate_dspy_suggestion

            result = generate_dspy_suggestion(
                sample_pattern,
                sample_dataset,
                mock_config,
            )
            return result, mock_instance, mock_create_lm

    def test_returns_dict_with_required_keys(
        self,
        sample_pattern,
        sample_dataset,
        mock_config,
        mock_dspy_prediction,
    ):
        result, _, _ = self._run_with_mock(
            sample_pattern,
            sample_dataset,
            mock_config,
            mock_dspy_prediction,
        )
        required = {
            "target_surface",
            "rule_title",
            "prevention_instructions",
            "rationale",
            "reasoning_trace",
            "confidence",
            "proposed_change",
            "status",
            "target_file",
            "_using_dspy",
            "pattern_id",
            "dataset_id",
        }
        missing = required - set(result.keys())
        assert not missing, f"Missing keys: {missing}"

    def test_target_surface_is_valid(
        self,
        sample_pattern,
        sample_dataset,
        mock_config,
        mock_dspy_prediction,
    ):
        result, _, _ = self._run_with_mock(
            sample_pattern,
            sample_dataset,
            mock_config,
            mock_dspy_prediction,
        )
        assert result["target_surface"] in VALID_TARGET_SURFACES

    def test_confidence_is_float_between_0_and_1(
        self,
        sample_pattern,
        sample_dataset,
        mock_config,
        mock_dspy_prediction,
    ):
        result, _, _ = self._run_with_mock(
            sample_pattern,
            sample_dataset,
            mock_config,
            mock_dspy_prediction,
        )
        assert isinstance(result["confidence"], float)
        assert 0.0 <= result["confidence"] <= 1.0

    def test_status_is_pending(
        self,
        sample_pattern,
        sample_dataset,
        mock_config,
        mock_dspy_prediction,
    ):
        result, _, _ = self._run_with_mock(
            sample_pattern,
            sample_dataset,
            mock_config,
            mock_dspy_prediction,
        )
        assert result["status"] == "pending"

    def test_using_dspy_flag_is_true(
        self,
        sample_pattern,
        sample_dataset,
        mock_config,
        mock_dspy_prediction,
    ):
        result, _, _ = self._run_with_mock(
            sample_pattern,
            sample_dataset,
            mock_config,
            mock_dspy_prediction,
        )
        assert result["_using_dspy"] is True

    def test_calls_suggestion_module_forward(
        self,
        sample_pattern,
        sample_dataset,
        mock_config,
        mock_dspy_prediction,
    ):
        _, mock_instance, _ = self._run_with_mock(
            sample_pattern,
            sample_dataset,
            mock_config,
            mock_dspy_prediction,
        )
        mock_instance.forward.assert_called_once()
        kwargs = mock_instance.forward.call_args.kwargs
        # Audit Round 2 C-R2.6: forward() is now called with the 3 canonical
        # PatternToRule input fields (pattern_description, example_errors,
        # project_context) per contracts/dspy-module-api.md §3.
        assert "pattern_description" in kwargs
        assert "example_errors" in kwargs
        assert "project_context" in kwargs

    def test_invalid_target_surface_falls_back_to_claude_md_rule(
        self,
        sample_pattern,
        sample_dataset,
        mock_config,
    ):
        """Audit Round 2 C-R2.6: target_surface is derived code-side from the
        pattern (via _infer_change_type), NOT chosen by the LLM. The test's
        original concern ("invalid LLM output falls back to claude_md_rule")
        is moot under the new signature — LLM doesn't choose the surface.

        Reframed: assert the pipeline still produces a well-formed suggestion
        dict even when the pred is minimal (edge case: missing output fields).
        The target_surface comes from _infer_change_type(pattern) per the
        pattern's tool_name ("Read" → "tool_rule" in the default map).
        """
        bad_pred = MagicMock()
        # Explicitly set string values for the NEW canonical fields so the
        # production code's _format_proposed_change("\n".join(...)) works.
        # A MagicMock lacking these fields would return auto-MagicMocks,
        # which fail string join — a test-mock artifact, not a prod bug.
        bad_pred.rule_title = "Some rule"
        bad_pred.rule_body = "Do something"
        bad_pred.rule_rationale = "Because reasons"
        bad_pred.reasoning = "trace"

        result, _, _ = self._run_with_mock(
            sample_pattern,
            sample_dataset,
            mock_config,
            bad_pred,
        )
        # Well-formed suggestion — target_surface derived from pattern
        # (sample_pattern.tool_name == "Read" → "tool_rule" per _TOOL_RULE_FILES)
        assert isinstance(result["target_surface"], str)
        assert result["target_surface"] in {"tool_rule", "claude_md_rule"}
        # Legacy output keys still populated from new pred fields
        assert result["prevention_instructions"] == "Do something"
        assert result["rationale"] == "Because reasons"

    def test_raises_runtime_error_when_no_lm(
        self,
        sample_pattern,
        sample_dataset,
        mock_config,
    ):
        with patch("sio.core.dspy.lm_factory.create_lm") as mock_create_lm:
            mock_create_lm.return_value = None

            from sio.suggestions.dspy_generator import generate_dspy_suggestion

            with pytest.raises(RuntimeError, match="No LLM backend available"):
                generate_dspy_suggestion(
                    sample_pattern,
                    sample_dataset,
                    mock_config,
                )


# =========================================================================
# T030: Test surface-to-file mapping
# =========================================================================


class TestSurfaceTargetMap:
    """_SURFACE_TARGET_MAP must cover all 7 valid surfaces."""

    def test_all_surfaces_have_mapping(self):
        from sio.suggestions.dspy_generator import _SURFACE_TARGET_MAP

        for surface in VALID_TARGET_SURFACES:
            assert surface in _SURFACE_TARGET_MAP, f"Missing mapping for surface: {surface}"

    def test_claude_md_rule_maps_to_claude_md(self):
        from sio.suggestions.dspy_generator import _SURFACE_TARGET_MAP

        assert _SURFACE_TARGET_MAP["claude_md_rule"] == "CLAUDE.md"

    def test_skill_update_maps_to_skills_dir(self):
        from sio.suggestions.dspy_generator import _SURFACE_TARGET_MAP

        assert _SURFACE_TARGET_MAP["skill_update"] == ".claude/skills/"

    def test_hook_config_maps_to_hooks_dir(self):
        from sio.suggestions.dspy_generator import _SURFACE_TARGET_MAP

        assert _SURFACE_TARGET_MAP["hook_config"] == ".claude/hooks/"

    def test_mcp_config_maps_to_mcp_json(self):
        from sio.suggestions.dspy_generator import _SURFACE_TARGET_MAP

        assert _SURFACE_TARGET_MAP["mcp_config"] == ".claude.json"

    def test_agent_profile_maps_to_agents_dir(self):
        from sio.suggestions.dspy_generator import _SURFACE_TARGET_MAP

        assert _SURFACE_TARGET_MAP["agent_profile"] == ".claude/agents/"

    def test_project_config_maps_to_claude_md(self):
        from sio.suggestions.dspy_generator import _SURFACE_TARGET_MAP

        assert _SURFACE_TARGET_MAP["project_config"] == "CLAUDE.md"

    def test_target_file_set_from_surface(
        self,
        sample_pattern,
        sample_dataset,
        mock_config,
        mock_dspy_prediction,
    ):
        """The target_file in the result must match the derived surface.

        Audit Round 2 C-R2.6: target_surface is now derived code-side from
        pattern (via _infer_change_type), NOT from the LLM prediction.
        Setting `mock_dspy_prediction.target_surface = "hook_config"` has no
        effect — the code uses pattern.tool_name to route. To force
        target_surface=hook_config, the pattern.tool_name must contain 'hook'.

        Reframed to exercise the real routing: use a pattern with tool_name
        containing 'hook' and verify the target_file is hooks dir.
        """
        hook_pattern = {**sample_pattern, "tool_name": "PostToolUse_hook"}

        with (
            patch("sio.core.dspy.lm_factory.create_lm") as mock_create_lm,
            patch("sio.suggestions.dspy_generator._load_optimized_or_default") as MockLoad,
            patch("dspy.configure"),
        ):
            mock_create_lm.return_value = _make_dummy_lm()
            mock_instance = MagicMock()
            mock_instance.forward.return_value = mock_dspy_prediction
            MockLoad.return_value = mock_instance

            from sio.suggestions.dspy_generator import generate_dspy_suggestion

            result = generate_dspy_suggestion(
                hook_pattern,
                sample_dataset,
                mock_config,
            )
            assert result["target_surface"] == "hook_config"
            assert result["target_file"] == ".claude/hooks/"


# =========================================================================
# T027: Test verbose trace logging
# =========================================================================


class TestVerboseLogging:
    """When verbose=True, DSPy input/output must be logged."""

    def test_verbose_logs_dspy_input(
        self,
        sample_pattern,
        sample_dataset,
        mock_config,
        mock_dspy_prediction,
        caplog,
    ):
        with (
            patch("sio.core.dspy.lm_factory.create_lm") as mock_create_lm,
            patch("sio.suggestions.dspy_generator._load_optimized_or_default") as MockLoad,
            patch("dspy.configure"),
        ):
            mock_create_lm.return_value = _make_dummy_lm()
            mock_instance = MagicMock()
            mock_instance.forward.return_value = mock_dspy_prediction
            MockLoad.return_value = mock_instance

            from sio.suggestions.dspy_generator import generate_dspy_suggestion

            with caplog.at_level(logging.INFO, logger="sio.suggestions.dspy_generator"):
                generate_dspy_suggestion(
                    sample_pattern,
                    sample_dataset,
                    mock_config,
                    verbose=True,
                )

            assert any("DSPy input" in msg for msg in caplog.messages)
            assert any("DSPy output" in msg for msg in caplog.messages)

    def test_no_logs_when_not_verbose(
        self,
        sample_pattern,
        sample_dataset,
        mock_config,
        mock_dspy_prediction,
        caplog,
    ):
        with (
            patch("sio.core.dspy.lm_factory.create_lm") as mock_create_lm,
            patch("sio.suggestions.dspy_generator._load_optimized_or_default") as MockLoad,
            patch("dspy.configure"),
        ):
            mock_create_lm.return_value = _make_dummy_lm()
            mock_instance = MagicMock()
            mock_instance.forward.return_value = mock_dspy_prediction
            MockLoad.return_value = mock_instance

            from sio.suggestions.dspy_generator import generate_dspy_suggestion

            with caplog.at_level(logging.INFO, logger="sio.suggestions.dspy_generator"):
                generate_dspy_suggestion(
                    sample_pattern,
                    sample_dataset,
                    mock_config,
                    verbose=False,
                )

            dspy_logs = [m for m in caplog.messages if "DSPy input" in m or "DSPy output" in m]
            assert len(dspy_logs) == 0


# =========================================================================
# T028: Test template fallback in generator.py
# =========================================================================


class TestGeneratorDspyIntegration:
    """generate_suggestions in generator.py delegates to DSPy when LLM is available."""

    def test_template_path_sets_using_dspy_false(self, v2_db):
        """When no LLM is available, _using_dspy is False."""
        from sio.suggestions.generator import generate_suggestions

        pattern = {
            "id": 1,
            "pattern_id": "pat-test-001",
            "description": "Test error",
            "tool_name": "Read",
            "error_count": 5,
            "session_count": 2,
            "rank_score": 0.5,
        }
        # Insert pattern into DB (OR REPLACE in case conftest seeded id=1)
        v2_db.execute(
            "INSERT OR REPLACE INTO patterns (id, pattern_id, description, tool_name, "
            "error_count, session_count, first_seen, last_seen, rank_score, "
            "created_at, updated_at) VALUES (1, 'pat-test-001', 'Test error', "
            "'Read', 5, 2, '2026-01-01', '2026-01-02', 0.5, '2026-01-01', '2026-01-01')"
        )
        v2_db.commit()

        dataset = {
            "id": 1,
            "pattern_id": "pat-test-001",
            "file_path": "/nonexistent/path.json",
            "positive_count": 3,
            "negative_count": 2,
        }

        # Patch create_lm to return None (no LLM)
        with patch("sio.core.dspy.lm_factory.create_lm", return_value=None):
            result = generate_suggestions(
                [pattern],
                {"pat-test-001": dataset},
                v2_db,
            )

        assert len(result) == 1
        assert result[0]["_using_dspy"] is False

    def test_dspy_path_sets_using_dspy_true(self, v2_db, mock_config, mock_dspy_prediction):
        """When LLM is available, _using_dspy is True."""
        from sio.suggestions.generator import generate_suggestions

        pattern = {
            "id": 2,
            "pattern_id": "pat-test-002",
            "description": "Test error",
            "tool_name": "Read",
            "error_type": "tool_failure",
            "error_count": 5,
            "session_count": 2,
            "rank_score": 0.5,
        }
        v2_db.execute(
            "INSERT INTO patterns (id, pattern_id, description, tool_name, "
            "error_count, session_count, first_seen, last_seen, rank_score, "
            "created_at, updated_at) VALUES (2, 'pat-test-002', 'Test error', "
            "'Read', 5, 2, '2026-01-01', '2026-01-02', 0.5, '2026-01-01', '2026-01-01')"
        )
        v2_db.commit()

        dataset = {
            "id": 2,
            "pattern_id": "pat-test-002",
            "file_path": "/nonexistent/path.json",
            "positive_count": 3,
            "negative_count": 2,
        }

        with (
            patch("sio.core.dspy.lm_factory.create_lm", return_value=_make_dummy_lm()),
            patch("sio.core.config.load_config", return_value=mock_config),
            patch("sio.suggestions.dspy_generator._load_optimized_or_default") as MockLoad,
            patch("dspy.configure"),
        ):
            mock_instance = MagicMock()
            mock_instance.forward.return_value = mock_dspy_prediction
            MockLoad.return_value = mock_instance

            result = generate_suggestions(
                [pattern],
                {"pat-test-002": dataset},
                v2_db,
            )

        assert len(result) == 1
        assert result[0]["_using_dspy"] is True

    def test_dspy_failure_falls_back_to_template(self, v2_db, mock_config):
        """If DSPy call raises, falls back to template for that pattern."""
        from sio.suggestions.generator import generate_suggestions

        pattern = {
            "id": 3,
            "pattern_id": "pat-test-003",
            "description": "Test error",
            "tool_name": "Read",
            "error_count": 5,
            "session_count": 2,
            "rank_score": 0.5,
        }
        v2_db.execute(
            "INSERT INTO patterns (id, pattern_id, description, tool_name, "
            "error_count, session_count, first_seen, last_seen, rank_score, "
            "created_at, updated_at) VALUES (3, 'pat-test-003', 'Test error', "
            "'Read', 5, 2, '2026-01-01', '2026-01-02', 0.5, '2026-01-01', '2026-01-01')"
        )
        v2_db.commit()

        dataset = {
            "id": 3,
            "pattern_id": "pat-test-003",
            "file_path": "/nonexistent/path.json",
            "positive_count": 3,
            "negative_count": 2,
        }

        mock_lm = MagicMock()
        with (
            patch("sio.core.dspy.lm_factory.create_lm", return_value=mock_lm),
            patch("sio.core.config.load_config", return_value=mock_config),
            patch(
                "sio.suggestions.dspy_generator.generate_dspy_suggestion",
                side_effect=RuntimeError("LLM exploded"),
            ),
        ):
            result = generate_suggestions(
                [pattern],
                {"pat-test-003": dataset},
                v2_db,
            )

        assert len(result) == 1
        assert result[0]["_using_dspy"] is False
