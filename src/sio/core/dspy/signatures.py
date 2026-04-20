"""DSPy Signature definitions for suggestion generation and ground truth."""

from __future__ import annotations

import dspy


class PatternToRule(dspy.Signature):
    """Generate a concise CLAUDE.md rule that prevents the given error pattern.
    The rule must be actionable, file-path-safe, and <= 3 sentences."""

    pattern_description: str = dspy.InputField(
        desc="Human-readable cluster name"
    )
    example_errors: list[str] = dspy.InputField(
        desc="3-5 representative error messages"
    )
    project_context: str = dspy.InputField(
        desc="Short description of the project or platform"
    )

    rule_title: str = dspy.OutputField(
        desc="Title of the generated rule"
    )
    rule_body: str = dspy.OutputField(
        desc="Rule body in Markdown, <= 3 sentences"
    )
    rule_rationale: str = dspy.OutputField(
        desc="Why this rule prevents the pattern"
    )


class RuleRecallScore(dspy.Signature):
    """Given a gold-standard rule and a candidate rule, score how well the
    candidate captures the same preventive intent.
    Returns a float in [0, 1]."""

    gold_rule: str = dspy.InputField()
    candidate_rule: str = dspy.InputField()

    score: float = dspy.OutputField(desc="Recall score in [0, 1]")
    reasoning: str = dspy.OutputField(desc="Brief justification")



class SuggestionGenerator(dspy.Signature):
    """You are a software quality improvement assistant analyzing CI/CD and
    developer tool error logs to suggest configuration improvements.

    Given sanitized error log excerpts from a developer tool, determine
    the best configuration target to prevent recurrence.

    The target_surface should reflect WHERE the root cause lives:
    - skill_update: if the error was caused by a skill prompt giving bad instructions
    - agent_profile: if the error was caused by agent-level behavior/constraints
    - hook_config: if the error was caused by hook misconfiguration
    - claude_md_rule: if the error is a general behavioral pattern needing a rule
    - project_config: if the error is project-specific configuration
    - mcp_config: if the error involves MCP server/tool misconfiguration
    - settings_config: if the error involves Claude Code settings

    Analyze tool_input_context to understand what the developer tool was
    trying to do and what configuration led to the error. Do NOT default to
    claude_md_rule unless the error truly requires a general behavioral rule.
    """

    error_examples: str = dspy.InputField(
        desc=(
            "JSON array of sanitized developer tool log entries with"
            " error_text, tool_name, context_message, tool_input, tool_output"
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
    """You are a software quality improvement assistant generating ideal
    configuration fixes for developer tool error patterns.

    Generate a candidate ideal output for training data based on sanitized
    error log excerpts. Analyze tool_input_context to determine the correct
    target_surface. The fix should target the actual source of the
    problem — skill prompts, agent profiles, hooks, or configuration rules
    depending on what caused the error.
    """

    error_examples: str = dspy.InputField(
        desc=(
            "JSON array of sanitized developer tool log entries with"
            " error_text, tool_name, context_message, tool_input, tool_output"
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


class SkillGeneratorSignature(dspy.Signature):
    """Generate a structured Claude Code skill file from error pattern analysis.

    Given an error pattern, examples of what went wrong and what worked,
    and the successful tool sequence, produce a skill with trigger conditions,
    ordered steps, and guardrails that prevent recurrence.
    """

    pattern_description: str = dspy.InputField(
        desc="Description of the recurring error pattern"
    )
    error_examples: str = dspy.InputField(
        desc="JSON array of error examples with context"
    )
    positive_examples: str = dspy.InputField(
        desc="JSON array of positive signal examples showing what worked"
    )
    flow_sequence: str = dspy.InputField(
        desc=(
            "Comma-separated tool sequence that succeeds"
            " (e.g., 'Read,Grep,Edit,Bash')"
        )
    )

    trigger_conditions: str = dspy.OutputField(
        desc="When this skill should activate (e.g., 'When editing Python files')"
    )
    ordered_steps: str = dspy.OutputField(
        desc="Numbered markdown steps the agent should follow"
    )
    guardrails: str = dspy.OutputField(
        desc="NEVER/ALWAYS rules as markdown bullet points"
    )
