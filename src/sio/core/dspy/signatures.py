"""DSPy Signature definitions for suggestion generation and ground truth."""

import dspy


class SuggestionGenerator(dspy.Signature):
    """Analyze error patterns and determine the best target to fix them.

    The target_surface should reflect WHERE the root cause lives:
    - skill_update: if the error was caused by a skill prompt giving bad instructions
    - agent_profile: if the error was caused by agent-level behavior/constraints
    - hook_config: if the error was caused by hook misconfiguration
    - claude_md_rule: if the error is a general behavioral pattern needing a rule
    - project_config: if the error is project-specific configuration
    - mcp_config: if the error involves MCP server/tool misconfiguration
    - settings_config: if the error involves Claude Code settings

    Analyze tool_input_context to understand what the agent was trying to do
    and what instructions led to the error. Do NOT default to claude_md_rule
    unless the error truly requires a general behavioral rule.
    """

    error_examples: str = dspy.InputField(
        desc=(
            "JSON array of error examples with error_text,"
            " tool_name, user_message, tool_input, tool_output"
        )
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
    tool_input_context: str = dspy.InputField(
        desc=(
            "JSON showing what the agent sent to tools (parameters, file paths, inputs). "
            "Analyze this to determine if the root cause is in a skill prompt, "
            "agent profile, hook config, or general CLAUDE.md rule."
        )
    )
    target_surface: str = dspy.OutputField(
        desc=(
            "MUST be one of: claude_md_rule, skill_update, hook_config,"
            " mcp_config, settings_config, agent_profile, project_config. "
            "Choose based on WHERE the root cause is — not always claude_md_rule."
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
    """Generate a candidate ideal output for training data.

    Analyze tool_input_context to determine the correct target_surface.
    The fix should target the actual source of the problem — skill prompts,
    agent profiles, hooks, or CLAUDE.md rules depending on what caused the error.
    """

    error_examples: str = dspy.InputField(
        desc=(
            "JSON array of error examples with error_text,"
            " tool_name, user_message, tool_input, tool_output"
        )
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
    tool_input_context: str = dspy.InputField(
        desc=(
            "JSON showing what the agent sent to tools (parameters, file paths, inputs). "
            "Analyze this to determine if the root cause is in a skill prompt, "
            "agent profile, hook config, or general CLAUDE.md rule."
        )
    )
    target_surface: str = dspy.OutputField(
        desc=(
            "MUST be one of: claude_md_rule, skill_update, hook_config,"
            " mcp_config, settings_config, agent_profile, project_config. "
            "Choose based on WHERE the root cause is — not always claude_md_rule."
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
