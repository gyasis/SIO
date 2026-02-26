"""DSPy Signature definitions for suggestion generation and ground truth."""

import dspy


class SuggestionGenerator(dspy.Signature):
    """Generate a targeted improvement from error patterns."""

    error_examples: str = dspy.InputField(
        desc="JSON array of error examples with error_text, tool_name, user_message"
    )
    error_type: str = dspy.InputField(
        desc=(
            "Error category: tool_failure, user_correction,"
            " agent_admission, repeated_attempt, undo"
        )
    )
    pattern_summary: str = dspy.InputField(
        desc="Description of the recurring error pattern"
    )
    target_surface: str = dspy.OutputField(
        desc=(
            "Target: claude_md_rule, skill_update, hook_config,"
            " mcp_config, settings_config, agent_profile, project_config"
        )
    )
    rule_title: str = dspy.OutputField(
        desc="Concise title for the improvement"
    )
    prevention_instructions: str = dspy.OutputField(
        desc="Specific, actionable prevention/improvement text in markdown"
    )
    rationale: str = dspy.OutputField(
        desc="Why this improvement addresses the error pattern"
    )


class GroundTruthCandidate(dspy.Signature):
    """Generate a candidate ideal output for training data."""

    error_examples: str = dspy.InputField(
        desc="JSON array of error examples with error_text, tool_name, user_message"
    )
    error_type: str = dspy.InputField(
        desc=(
            "Error category: tool_failure, user_correction,"
            " agent_admission, repeated_attempt, undo"
        )
    )
    pattern_summary: str = dspy.InputField(
        desc="Description of the recurring error pattern"
    )
    target_surface: str = dspy.OutputField(
        desc=(
            "Target: claude_md_rule, skill_update, hook_config,"
            " mcp_config, settings_config, agent_profile, project_config"
        )
    )
    rule_title: str = dspy.OutputField(
        desc="Concise title for the improvement"
    )
    prevention_instructions: str = dspy.OutputField(
        desc="Specific, actionable prevention/improvement text in markdown"
    )
    rationale: str = dspy.OutputField(
        desc="Why this improvement addresses the error pattern"
    )
    quality_assessment: str = dspy.OutputField(
        desc="Self-assessment of this candidate's quality and completeness"
    )
