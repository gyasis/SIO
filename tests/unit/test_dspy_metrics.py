"""Tests for sio.core.dspy.metrics -- Quality scoring metric for DSPy suggestions.

Covers:
  T048 - suggestion_quality_metric returns float 0-1, specificity, actionability,
         surface accuracy scoring
  T049 - Dual-mode return: bool when trace is not None (DSPy optimization),
         float when trace is None (standalone)
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from sio.core.dspy.metrics import (
    _score_actionability,
    _score_specificity,
    _score_surface_accuracy,
    suggestion_quality_metric,
)

# ---------------------------------------------------------------------------
# Helpers to build dspy.Example-like objects
# ---------------------------------------------------------------------------


def _make_example(
    error_type: str = "tool_failure",
    tool_name: str = "Read",
    error_texts: list[str] | None = None,
) -> SimpleNamespace:
    """Build a mock example with error_examples JSON."""
    if error_texts is None:
        error_texts = ["FileNotFoundError: /tmp/missing.py"]
    examples = [
        {"error_text": t, "tool_name": tool_name, "error_type": error_type}
        for t in error_texts
    ]
    return SimpleNamespace(
        error_examples=json.dumps(examples),
        error_type=error_type,
        pattern_summary=f"Tool: {tool_name}. Recurring error. 10 errors across 3 sessions.",
        tool_name=tool_name,
    )


def _make_pred(
    target_surface: str = "claude_md_rule",
    prevention_instructions: str = "Check file existence before reading.",
    rule_title: str = "Verify paths before Read",
    rationale: str = "Prevents FileNotFoundError.",
) -> SimpleNamespace:
    """Build a mock prediction."""
    return SimpleNamespace(
        target_surface=target_surface,
        prevention_instructions=prevention_instructions,
        rule_title=rule_title,
        rationale=rationale,
    )


# =========================================================================
# T048: suggestion_quality_metric returns float 0-1
# =========================================================================


class TestSuggestionQualityMetricBasic:
    """suggestion_quality_metric returns float in [0, 1] for standalone mode."""

    def test_returns_float(self):
        example = _make_example()
        pred = _make_pred()
        result = suggestion_quality_metric(example, pred, trace=None)
        assert isinstance(result, float)

    def test_score_between_0_and_1(self):
        example = _make_example()
        pred = _make_pred()
        result = suggestion_quality_metric(example, pred, trace=None)
        assert 0.0 <= result <= 1.0

    def test_high_quality_suggestion_scores_high(self):
        example = _make_example(
            error_type="tool_failure",
            tool_name="Read",
            error_texts=["FileNotFoundError: /tmp/missing.py"],
        )
        pred = _make_pred(
            target_surface="claude_md_rule",
            prevention_instructions=(
                "Before calling Read, run `test -f /tmp/missing.py` to verify "
                "the file exists. Check the path and use Glob to verify. "
                "Add a FileNotFoundError guard."
            ),
        )
        score = suggestion_quality_metric(example, pred, trace=None)
        assert score > 0.5

    def test_empty_instructions_scores_low(self):
        example = _make_example()
        pred = _make_pred(prevention_instructions="")
        score = suggestion_quality_metric(example, pred, trace=None)
        assert score <= 0.3


# =========================================================================
# T048: Specificity scoring
# =========================================================================


class TestSpecificity:
    """_score_specificity rewards references to concrete details from examples."""

    def test_mentions_tool_name_higher_score(self):
        example = _make_example(tool_name="Read")
        pred_specific = _make_pred(
            prevention_instructions="Before calling Read, verify the path exists."
        )
        pred_generic = _make_pred(
            prevention_instructions="Be more careful with operations."
        )
        score_specific = _score_specificity(example, pred_specific)
        score_generic = _score_specificity(example, pred_generic)
        assert score_specific > score_generic

    def test_mentions_error_text_details_higher(self):
        example = _make_example(
            error_texts=["FileNotFoundError: /tmp/missing.py"]
        )
        pred_with_details = _make_pred(
            prevention_instructions=(
                "Handle FileNotFoundError by checking path existence first."
            )
        )
        pred_no_details = _make_pred(
            prevention_instructions="Try harder next time."
        )
        assert _score_specificity(example, pred_with_details) > _score_specificity(
            example, pred_no_details
        )

    def test_generic_pred_lower_score(self):
        example = _make_example(
            tool_name="Bash",
            error_texts=["Permission denied: /etc/shadow"],
        )
        pred = _make_pred(
            prevention_instructions="Be careful when running commands."
        )
        score = _score_specificity(example, pred)
        # Generic instructions should score low
        assert score < 0.5

    def test_empty_examples_returns_neutral(self):
        example = SimpleNamespace(
            error_examples="[]", error_type="tool_failure", pattern_summary=""
        )
        pred = _make_pred(prevention_instructions="Do something.")
        score = _score_specificity(example, pred)
        assert score == 0.5  # neutral when no details to compare

    def test_empty_instructions_returns_zero(self):
        example = _make_example()
        pred = _make_pred(prevention_instructions="")
        score = _score_specificity(example, pred)
        assert score == 0.0


# =========================================================================
# T048: Actionability scoring
# =========================================================================


class TestActionability:
    """_score_actionability rewards concrete verbs, file paths, and code refs."""

    def test_concrete_verbs_and_paths_high(self):
        pred = _make_pred(
            prevention_instructions=(
                "Run `ruff check src/` to verify code quality. "
                "Add a pre-commit hook at .claude/hooks/lint.sh."
            )
        )
        score = _score_actionability(pred)
        assert score > 0.7

    def test_vague_instructions_low(self):
        pred = _make_pred(
            prevention_instructions="Be careful. Try harder. Think about it."
        )
        score = _score_actionability(pred)
        assert score < 0.3

    def test_action_verbs_boost_score(self):
        pred_with_verbs = _make_pred(
            prevention_instructions="Run the check, verify output, and configure settings."
        )
        pred_no_verbs = _make_pred(
            prevention_instructions="The system should hopefully work better."
        )
        assert _score_actionability(pred_with_verbs) > _score_actionability(pred_no_verbs)

    def test_file_paths_boost_score(self):
        pred_with_paths = _make_pred(
            prevention_instructions="Edit the file at src/sio/core/config.py to update."
        )
        pred_no_paths = _make_pred(
            prevention_instructions="Edit the relevant configuration to update."
        )
        assert _score_actionability(pred_with_paths) > _score_actionability(pred_no_paths)

    def test_backtick_code_boosts_score(self):
        pred_with_code = _make_pred(
            prevention_instructions="Use `git status` to check changes."
        )
        pred_no_code = _make_pred(
            prevention_instructions="Use git status to check changes."
        )
        assert _score_actionability(pred_with_code) > _score_actionability(pred_no_code)

    def test_empty_instructions_zero(self):
        pred = _make_pred(prevention_instructions="")
        assert _score_actionability(pred) == 0.0


# =========================================================================
# T048: Surface accuracy scoring (FR-027)
# =========================================================================


class TestSurfaceAccuracy:
    """_score_surface_accuracy validates target_surface vs error_type."""

    def test_tool_failure_claude_md_rule_correct(self):
        example = _make_example(error_type="tool_failure")
        pred = _make_pred(target_surface="claude_md_rule")
        assert _score_surface_accuracy(example, pred) == 1.0

    def test_tool_failure_skill_update_correct(self):
        example = _make_example(error_type="tool_failure")
        pred = _make_pred(target_surface="skill_update")
        assert _score_surface_accuracy(example, pred) == 1.0

    def test_user_correction_agent_profile_correct(self):
        example = _make_example(error_type="user_correction")
        pred = _make_pred(target_surface="agent_profile")
        assert _score_surface_accuracy(example, pred) == 1.0

    def test_agent_admission_claude_md_correct(self):
        example = _make_example(error_type="agent_admission")
        pred = _make_pred(target_surface="claude_md_rule")
        assert _score_surface_accuracy(example, pred) == 1.0

    def test_repeated_attempt_hook_config_correct(self):
        example = _make_example(error_type="repeated_attempt")
        pred = _make_pred(target_surface="hook_config")
        assert _score_surface_accuracy(example, pred) == 1.0

    def test_undo_settings_config_correct(self):
        example = _make_example(error_type="undo")
        pred = _make_pred(target_surface="settings_config")
        assert _score_surface_accuracy(example, pred) == 1.0

    def test_wrong_surface_penalty(self):
        """mcp_config for a generic undo error is a mismatch (FR-027)."""
        example = _make_example(error_type="undo", tool_name="Bash")
        pred = _make_pred(target_surface="mcp_config")
        score = _score_surface_accuracy(example, pred)
        assert score == 0.0

    def test_claude_md_rule_safe_default_partial_credit(self):
        """claude_md_rule gets 0.5 for unknown error types (safe default)."""
        example = _make_example(error_type="some_new_error_type")
        pred = _make_pred(target_surface="claude_md_rule")
        score = _score_surface_accuracy(example, pred)
        assert score == 0.5

    def test_mcp_related_tool_prefers_mcp_config(self):
        """MCP-related tool_name -> mcp_config or settings_config is correct."""
        example = _make_example(
            error_type="tool_failure",
            tool_name="mcp__graphiti__search_nodes",
        )
        pred = _make_pred(target_surface="mcp_config")
        assert _score_surface_accuracy(example, pred) == 1.0

    def test_mcp_tool_settings_config_also_correct(self):
        example = _make_example(
            error_type="tool_failure",
            tool_name="mcp__playwright__browser_navigate",
        )
        pred = _make_pred(target_surface="settings_config")
        assert _score_surface_accuracy(example, pred) == 1.0

    def test_mcp_tool_wrong_surface_zero(self):
        example = _make_example(
            error_type="tool_failure",
            tool_name="mcp__graphiti__add_memory",
        )
        pred = _make_pred(target_surface="agent_profile")
        assert _score_surface_accuracy(example, pred) == 0.0

    def test_hook_related_tool_prefers_hook_config(self):
        example = _make_example(
            error_type="tool_failure",
            tool_name="hook_pre_commit",
        )
        pred = _make_pred(target_surface="hook_config")
        assert _score_surface_accuracy(example, pred) == 1.0

    def test_unknown_error_type_claude_md_partial(self):
        """Unknown error type with claude_md_rule gets partial credit."""
        example = _make_example(error_type="unknown_type")
        pred = _make_pred(target_surface="claude_md_rule")
        assert _score_surface_accuracy(example, pred) == 0.5


# =========================================================================
# T049: Dual-mode return (trace=None -> float, trace!=None -> bool)
# =========================================================================


class TestDualModeReturn:
    """When trace is not None, returns bool(score > 0.5). When None, float."""

    def test_trace_none_returns_float(self):
        example = _make_example()
        pred = _make_pred()
        result = suggestion_quality_metric(example, pred, trace=None)
        assert isinstance(result, float)

    def test_trace_not_none_returns_bool(self):
        example = _make_example()
        pred = _make_pred()
        result = suggestion_quality_metric(example, pred, trace="some_trace")
        assert isinstance(result, bool)

    def test_high_quality_trace_returns_true(self):
        example = _make_example(
            error_type="tool_failure",
            tool_name="Read",
            error_texts=["FileNotFoundError: /tmp/missing.py"],
        )
        pred = _make_pred(
            target_surface="claude_md_rule",
            prevention_instructions=(
                "Before calling Read, run `test -f /tmp/missing.py` to verify "
                "the file exists. Check the path and use Glob to verify. "
                "Add a FileNotFoundError guard."
            ),
        )
        result = suggestion_quality_metric(example, pred, trace="optimization")
        assert result is True

    def test_low_quality_trace_returns_false(self):
        example = _make_example()
        pred = _make_pred(
            target_surface="mcp_config",  # wrong surface for tool_failure
            prevention_instructions="Try again.",  # vague
        )
        result = suggestion_quality_metric(example, pred, trace="optimization")
        assert result is False

    def test_trace_with_various_truthy_values(self):
        """trace can be any truthy value, not just strings."""
        example = _make_example()
        pred = _make_pred()
        for trace_val in [True, 1, [], {}, object()]:
            result = suggestion_quality_metric(example, pred, trace=trace_val)
            assert isinstance(result, bool)


# =========================================================================
# T048 additional: weighted combination validation
# =========================================================================


class TestWeightedCombination:
    """Verify the 0.35/0.35/0.30 weighting produces expected relative scores."""

    def test_perfect_all_axes(self):
        """Perfect specificity + actionability + surface -> high score."""
        example = _make_example(
            error_type="tool_failure",
            tool_name="Read",
            error_texts=["FileNotFoundError: /tmp/missing.py"],
        )
        pred = _make_pred(
            target_surface="claude_md_rule",  # correct for tool_failure
            prevention_instructions=(
                "Before calling Read, run `test -f /tmp/missing.py` to check "
                "if the file exists. Use Glob to verify. Add a guard for "
                "FileNotFoundError in the configuration at src/sio/config.py."
            ),
        )
        score = suggestion_quality_metric(example, pred, trace=None)
        assert score > 0.6

    def test_terrible_all_axes(self):
        """Wrong surface + vague instructions + no specifics -> low score."""
        example = _make_example(
            error_type="undo", tool_name="Bash",
            error_texts=["User undid last action"],
        )
        pred = _make_pred(
            target_surface="project_config",  # wrong for undo
            prevention_instructions="Maybe things will improve.",
        )
        score = suggestion_quality_metric(example, pred, trace=None)
        assert score < 0.3
