"""DSPy Signature definitions for suggestion generation and ground truth."""

# Long lines are inherent to the few-shot demo blocks in the docstrings below
# (each EXAMPLE encodes a multi-field pattern → rule mapping inline). Reflowing
# them harms LM readability of the demos, so disable E501 file-wide.
# ruff: noqa: E501

from __future__ import annotations

import dspy


class PatternToRule(dspy.Signature):
    """Generate a concise CLAUDE.md rule that prevents the given error pattern.

    The rule must be actionable, file-path-safe, and <= 3 sentences.

    CRITICAL — required specificity (B5 grounding directives):
    - The rule TITLE must reference the SPECIFIC tokens from `pattern_description`
      (tool name, env var, path, command — whatever recurs in `Common phrases:`).
      Generic titles like "Always check inputs" or "Verify configuration" are
      WRONG — they lose the discriminating signal that justifies the rule.
    - The rule BODY must cite the concrete failure observed in `example_errors`
      (the `[before] ... [error] ... [after]` excerpts). Do NOT paraphrase into
      a tool-agnostic platitude.
    - If `example_errors` shows an env var, file path, or command literal,
      that literal MUST appear verbatim in the rule.

    Few-shot guidance (T109, GEPA optimization target):
    ---
    EXAMPLE 1 — tool_failure (file wipe)
    pattern_description: "[tool_failure] Tool: Bash. Common phrases: sed -i emptied, file truncated, .env gone. 12 errors across 5 sessions."
    example_errors: ["[error] sed -i silently emptied .env", "[error] file truncated after sed"]
    project_context: "WSL2 Claude Code project"
    ---
    rule_title: "Never use sed -i for file edits — use Edit tool"
    rule_body: |
      Use the Edit tool instead of `sed -i` for all in-place file
      modifications. The `sed -i` temp-rename pattern races with Windows
      filesystem watchers and has silently wiped files on this WSL2 system.
    rule_rationale: "Prevents file wipes caused by atomic-rename races in WSL2."
    ---
    EXAMPLE 2 — tool_failure (cascade)
    pattern_description: "[tool_failure] Tool: parallel. Common phrases: sibling tool call errored, MCP timeout, Bash cancelled. 8 errors across 4 sessions."
    example_errors: ["[error] sibling tool call errored", "[error] MCP timeout cancelled Bash"]
    project_context: "Claude Code multi-tool pipeline"
    ---
    rule_title: "Never mix MCP and Bash in the same parallel batch"
    rule_body: |
      MCP tools and Bash must never be called in the same parallel batch.
      If any tool in a parallel batch fails, all sibling calls are cancelled.
      Run Bash first (sequentially), then MCP tools.
    rule_rationale: "Prevents cascade cancellation of valid tool calls."
    ---
    EXAMPLE 3 — agent_admission (env-var mismatch)
    pattern_description: "[agent_admission] Tool: dev-server. Common phrases: WORKSPACE_DIR mismatch, cwd proj-a, stale dev server, patch failed. 21 errors across 3 sessions."
    example_errors: ["[before] dev-server start from proj-b cwd | [error] WORKSPACE_DIR points to proj-a not proj-b | [after] stale server on :3000 from old clone", "[before] dev-server start | [error] project-specific patch failed to apply on unrelated branch"]
    project_context: "monorepo with project-specific dev-server boots"
    ---
    rule_title: "Verify $WORKSPACE_DIR matches cwd before starting dev server"
    rule_body: |
      Before running the dev-server bootstrap script, confirm
      `realpath $WORKSPACE_DIR` points to the workspace where the current task
      lives. If the dev port is already bound, check the holding process's cwd
      via `readlink /proc/$(lsof -t -i:<port> | head -1)/cwd` and kill any
      stale process from an old project clone before restart.
    rule_rationale: "Prevents stale-server pollution and project-specific patches applied to the wrong branch."
    ---
    EXAMPLE 4 — user_correction (agent re-pivot)
    pattern_description: "[user_correction] Tool: Agent. Common phrases: not what I meant, wrong session, that's the old one. 6 errors across 3 sessions."
    example_errors: ["[before] surfaced session from 14 days ago | [error] user said 'no, the recent one' | [after] re-ran search with --recent 7"]
    project_context: "Claude Code session resume / memory recall"
    ---
    rule_title: "Recency-first on resume — never default to `--all` for 'continue X' intents"
    rule_body: |
      When the user says "remember", "resume", or "continue" without a date,
      MUST start with `session-search "<keywords>" --recent 7 --files`. Widen to
      30 only on zero hits, and explicitly flag the age. Never auto-resume work
      >7 days old without confirming.
    rule_rationale: "Prevents resuming stale sessions as if they were current work."
    ---
    EXAMPLE 5 — undo (data loss)
    pattern_description: "[undo] Tool: Edit. Common phrases: file overwritten, rolled back, lost work, undo. 4 errors across 2 sessions."
    example_errors: ["[before] Write tool replaced full file with new content | [error] user said 'I lost 200 lines' | [after] git checkout HEAD -- file restored"]
    project_context: "Claude Code file editing"
    ---
    rule_title: "Use Edit not Write for existing files — Write overwrites"
    rule_body: |
      The Write tool fully replaces the target file. For existing files always
      use the Edit tool with explicit old_string/new_string. Only use Write to
      create new files or after explicit user "overwrite" confirmation.
    rule_rationale: "Prevents accidental whole-file overwrites of existing work."
    ---
    """

    pattern_description: str = dspy.InputField(desc="Human-readable cluster name")
    example_errors: list[str] = dspy.InputField(desc="3-5 representative error messages")
    project_context: str = dspy.InputField(desc="Short description of the project or platform")

    rule_title: str = dspy.OutputField(desc="Title of the generated rule")
    rule_body: str = dspy.OutputField(desc="Rule body in Markdown, <= 3 sentences")
    rule_rationale: str = dspy.OutputField(desc="Why this rule prevents the pattern")


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
    pattern_summary: str = dspy.InputField(desc="Description of the recurring error pattern")
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
    rule_title: str = dspy.OutputField(desc="Concise title for the improvement")
    prevention_instructions: str = dspy.OutputField(
        desc="Specific, actionable prevention/improvement text in markdown"
    )
    rationale: str = dspy.OutputField(desc="Why this improvement addresses the error pattern")


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
    pattern_summary: str = dspy.InputField(desc="Description of the recurring error pattern")
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
    rule_title: str = dspy.OutputField(desc="Concise title for the improvement")
    prevention_instructions: str = dspy.OutputField(
        desc="Specific, actionable prevention/improvement text in markdown"
    )
    rationale: str = dspy.OutputField(desc="Why this improvement addresses the error pattern")
    quality_assessment: str = dspy.OutputField(
        desc="Self-assessment of this candidate's quality and completeness"
    )


class SkillGeneratorSignature(dspy.Signature):
    """Generate a structured Claude Code skill file from error pattern analysis.

    Given an error pattern, examples of what went wrong and what worked,
    and the successful tool sequence, produce a skill with trigger conditions,
    ordered steps, and guardrails that prevent recurrence.
    """

    pattern_description: str = dspy.InputField(desc="Description of the recurring error pattern")
    error_examples: str = dspy.InputField(desc="JSON array of error examples with context")
    positive_examples: str = dspy.InputField(
        desc="JSON array of positive signal examples showing what worked"
    )
    flow_sequence: str = dspy.InputField(
        desc=("Comma-separated tool sequence that succeeds (e.g., 'Read,Grep,Edit,Bash')")
    )

    trigger_conditions: str = dspy.OutputField(
        desc="When this skill should activate (e.g., 'When editing Python files')"
    )
    ordered_steps: str = dspy.OutputField(desc="Numbered markdown steps the agent should follow")
    guardrails: str = dspy.OutputField(desc="NEVER/ALWAYS rules as markdown bullet points")
