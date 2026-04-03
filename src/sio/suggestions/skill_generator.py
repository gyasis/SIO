"""Structured skill file generator — converts mined patterns and positive signals
into Claude Code skill.md files that agents can follow.

Takes graded error patterns, positive examples (user approval signals), and
optional flow sequences to produce complete, actionable skill files with
trigger conditions, ordered steps, guardrails, and provenance metadata.

Public API
----------
    generate_skill_from_pattern(pattern, positive_examples, flow_sequence) -> str
    generate_skill_from_flow(flow_ngram, success_rate, session_examples) -> str
    write_skill_file(content, slug, target_dir) -> str
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_DEFAULT_SKILLS_DIR = os.path.expanduser("~/.claude/skills/learned/")


def _slugify(text: str, max_len: int = 60) -> str:
    """Convert arbitrary text into a filesystem-safe slug."""
    slug = text.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug[:max_len]


def _derive_title(pattern: dict[str, Any]) -> str:
    """Derive a human-readable skill title from a pattern dict.

    Uses the pattern's description or label, truncated and cleaned up
    to work as a markdown heading.
    """
    raw = (
        pattern.get("description")
        or pattern.get("label")
        or pattern.get("tool_name")
        or "Unknown Pattern"
    )
    # Take first sentence or first 80 chars
    title = raw.split(".")[0].split("\n")[0].strip()
    if len(title) > 80:
        title = title[:77] + "..."
    # Capitalize first letter
    if title and title[0].islower():
        title = title[0].upper() + title[1:]
    return title


def _tool_display_name(tool_name: str | None) -> str:
    """Normalize a tool name for display (strip MCP prefixes, etc.)."""
    if not tool_name:
        return "the tool"
    name = tool_name
    if name.startswith("mcp__"):
        parts = name.replace("mcp__", "").split("__")
        name = ".".join(parts)
    return name


def _extract_trigger_conditions(
    pattern: dict[str, Any],
    positive_examples: list[dict[str, Any]],
) -> list[str]:
    """Build trigger condition bullet points from pattern + positive signals.

    Trigger conditions describe WHEN the agent should activate this skill.
    They combine the tool context from the pattern with the situations
    where the agent previously succeeded or failed.
    """
    conditions: list[str] = []
    tool_name = _tool_display_name(pattern.get("tool_name"))

    # Primary trigger: the tool involved
    if tool_name != "the tool":
        conditions.append(f"You are about to call `{tool_name}`")

    # Secondary triggers from pattern description (error context)
    description = pattern.get("description") or ""
    desc_lower = description.lower()

    if "not found" in desc_lower or "no such file" in desc_lower:
        conditions.append("The operation targets a file or path that may not exist")
    if "permission" in desc_lower or "denied" in desc_lower:
        conditions.append("The operation requires elevated permissions or ownership")
    if "timeout" in desc_lower:
        conditions.append("The operation may take longer than expected")
    if "syntax" in desc_lower or "parse" in desc_lower:
        conditions.append("The input contains structured syntax that must be validated")
    if "duplicate" in desc_lower or "already exists" in desc_lower:
        conditions.append("The target resource may already exist")
    if "retry" in desc_lower or "repeated" in desc_lower:
        conditions.append(
            "You have already attempted this operation once and it failed"
        )

    # Triggers from positive examples (what context preceded success)
    seen_contexts: set[str] = set()
    for ex in positive_examples[:5]:
        context = ex.get("context_before") or ""
        if context and len(context) > 15:
            # Extract a short trigger phrase from the context
            short = context[:120].strip()
            key = short[:40].lower()
            if key not in seen_contexts:
                seen_contexts.add(key)
                conditions.append(f"Context resembles: {short}")

    if not conditions:
        conditions.append("The current task matches this error pattern")

    return conditions


def _extract_steps_from_positives(
    positive_examples: list[dict[str, Any]],
) -> list[str]:
    """Infer ordered steps from positive signal examples.

    Positive examples carry tool_name (what the agent did) and
    context_before (what the assistant said/did before the user approved).
    We reconstruct an ordered procedure from these signals.
    """
    steps: list[str] = []
    seen_tools: set[str] = set()

    for ex in positive_examples:
        tool = ex.get("tool_name")
        context = ex.get("context_before") or ""

        if tool and tool not in seen_tools:
            seen_tools.add(tool)
            display = _tool_display_name(tool)
            # Build a meaningful step from the tool + context
            if context:
                short_context = context[:100].strip()
                steps.append(f"Use `{display}` -- {short_context}")
            else:
                steps.append(f"Use `{display}` to perform the required operation")

    if not steps:
        steps.append("Read the relevant file or context before making changes")
        steps.append("Validate inputs and preconditions")
        steps.append("Execute the operation")
        steps.append("Verify the result matches expectations")

    return steps


def _build_steps_section(
    flow_sequence: list[str] | None,
    positive_examples: list[dict[str, Any]],
) -> str:
    """Build the ## Steps section content.

    If flow_sequence is provided, use it as the authoritative step order.
    Otherwise, infer steps from positive examples.
    """
    lines: list[str] = []

    if flow_sequence and len(flow_sequence) > 0:
        for i, tool in enumerate(flow_sequence, 1):
            display = _tool_display_name(tool)
            lines.append(f"{i}. Call `{display}`")
    else:
        steps = _extract_steps_from_positives(positive_examples)
        for i, step in enumerate(steps, 1):
            lines.append(f"{i}. {step}")

    return "\n".join(lines)


def _build_guardrails(pattern: dict[str, Any]) -> list[str]:
    """Build NEVER/ALWAYS guardrail rules that negate the error pattern.

    Each guardrail is the inverse of the observed failure mode.
    """
    guardrails: list[str] = []
    description = (pattern.get("description") or "").lower()
    tool_name = _tool_display_name(pattern.get("tool_name"))

    # File existence errors
    if "not found" in description or "no such file" in description:
        guardrails.append(
            f"ALWAYS verify the target file/path exists before calling `{tool_name}`."
        )
        guardrails.append(
            "NEVER assume a file path is correct without checking -- "
            "use `Glob` or `Read` to confirm."
        )

    # Permission errors
    if "permission" in description or "denied" in description:
        guardrails.append(
            f"ALWAYS check file permissions before `{tool_name}` operations."
        )

    # Timeout / long-running
    if "timeout" in description or "timed out" in description:
        guardrails.append(
            f"NEVER call `{tool_name}` on large inputs without "
            "chunking or setting an appropriate timeout."
        )

    # Syntax / parse errors
    if "syntax" in description or "parse" in description:
        guardrails.append(
            f"ALWAYS validate input syntax before passing to `{tool_name}`."
        )

    # Retry / repeated attempt
    if "retry" in description or "repeated" in description:
        guardrails.append(
            f"NEVER retry `{tool_name}` more than twice with the same inputs. "
            "After 2 failures, diagnose the root cause or try a different approach."
        )

    # Duplicate / conflict
    if "duplicate" in description or "already exists" in description:
        guardrails.append(
            f"ALWAYS check for existing resources before creating new ones "
            f"via `{tool_name}`."
        )

    # Multiple occurrences / uniqueness
    if "multiple" in description or "ambiguous" in description:
        guardrails.append(
            "NEVER assume string uniqueness. Verify via search before "
            "targeted replacements."
        )

    # Overwrite / destructive
    if "overwrite" in description or "undo" in description or "revert" in description:
        guardrails.append(
            "NEVER overwrite files without reading current content first."
        )
        guardrails.append(
            "ALWAYS prefer incremental edits over full file rewrites."
        )

    # Generic fallback if no specific guardrails matched
    if not guardrails:
        guardrails.append(
            f"ALWAYS verify preconditions before calling `{tool_name}`."
        )
        guardrails.append(
            f"NEVER retry `{tool_name}` more than twice without "
            "changing your approach."
        )
        guardrails.append(
            "ALWAYS check tool output for errors before proceeding."
        )

    return guardrails


def _build_provenance(pattern: dict[str, Any]) -> str:
    """Build the ## Why This Skill Exists section with stats."""
    error_count = pattern.get("error_count", pattern.get("count", 0))
    session_count = pattern.get("session_count", 0)
    confidence = pattern.get("confidence", pattern.get("grade", 0.0))

    lines: list[str] = []
    lines.append(
        f"This skill was auto-generated by SIO on "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')} from mined session data."
    )
    lines.append("")

    stats: list[str] = []
    if error_count:
        stats.append(f"**Error occurrences**: {error_count}")
    if session_count:
        stats.append(f"**Sessions affected**: {session_count}")
    if confidence:
        if isinstance(confidence, float):
            stats.append(f"**Confidence**: {confidence:.2f}")
        else:
            stats.append(f"**Grade**: {confidence}")

    if stats:
        lines.extend(stats)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_skill_from_pattern(
    pattern: dict[str, Any],
    positive_examples: list[dict[str, Any]],
    flow_sequence: list[str] | None = None,
) -> str:
    """Generate a complete Claude Code skill.md from a graded error pattern.

    Parameters
    ----------
    pattern:
        A pattern dict from the patterns table. Expected keys:
        ``description``, ``tool_name``, ``error_count``, ``session_count``,
        ``grade`` or ``confidence``. Also accepts ``label`` and ``count``
        from lightweight pattern detection.
    positive_examples:
        List of positive signal dicts from positive_records. Each carries:
        ``signal_type``, ``context_before``, ``tool_name``, ``signal_text``,
        ``timestamp``.
    flow_sequence:
        Optional ordered list of tool names that form the successful
        workflow. When provided, these become the authoritative step order.

    Returns
    -------
    str
        Complete markdown content for a Claude Code skill file.
    """
    title = _derive_title(pattern)
    triggers = _extract_trigger_conditions(pattern, positive_examples)
    steps = _build_steps_section(flow_sequence, positive_examples)
    guardrails = _build_guardrails(pattern)
    provenance = _build_provenance(pattern)

    sections: list[str] = []

    # Title
    sections.append(f"# Skill: {title}")
    sections.append("")

    # When to Use
    sections.append("## When to Use")
    sections.append("")
    for trigger in triggers:
        sections.append(f"- {trigger}")
    sections.append("")

    # Steps
    sections.append("## Steps")
    sections.append("")
    sections.append(steps)
    sections.append("")

    # Guardrails
    sections.append("## Guardrails")
    sections.append("")
    for rule in guardrails:
        sections.append(f"- {rule}")
    sections.append("")

    # Why This Skill Exists
    sections.append("## Why This Skill Exists")
    sections.append("")
    sections.append(provenance)
    sections.append("")

    return "\n".join(sections)


def generate_skill_from_flow(
    flow_ngram: tuple[str, ...],
    success_rate: float,
    session_examples: list[dict[str, Any]],
) -> str:
    """Generate a Claude Code skill focused on a successful tool sequence.

    Parameters
    ----------
    flow_ngram:
        Tuple of tool names (possibly with RLE suffixes like "+")
        representing a recurring successful workflow. Example:
        ``("Read", "Grep", "Edit", "Bash")``.
    success_rate:
        Float 0.0-1.0 indicating how often this flow leads to user approval.
    session_examples:
        List of session context dicts that exhibited this flow. Each may
        carry ``user_goal``, ``final_outcome``, ``duration_seconds``,
        ``tool_sequence``.

    Returns
    -------
    str
        Complete markdown content for a Claude Code skill file.
    """
    # Clean tool names (strip RLE "+" suffix for display)
    clean_tools = [t.rstrip("+") for t in flow_ngram]
    flow_label = " -> ".join(clean_tools)

    sections: list[str] = []

    # Title
    sections.append(f"# Skill: {flow_label} Workflow")
    sections.append("")

    # When to Use
    sections.append("## When to Use")
    sections.append("")
    sections.append(f"- Your task requires a {flow_label} sequence")

    # Infer trigger conditions from session examples
    goals_seen: set[str] = set()
    for ex in session_examples[:5]:
        goal = ex.get("user_goal") or ""
        if goal and len(goal) > 10:
            short_goal = goal[:120].strip()
            key = short_goal[:40].lower()
            if key not in goals_seen:
                goals_seen.add(key)
                sections.append(f"- Task resembles: {short_goal}")

    if not goals_seen:
        sections.append(
            "- The current task involves reading, searching, editing, "
            "and verifying code"
        )
    sections.append("")

    # Steps -- directly from the flow n-gram
    sections.append("## Steps")
    sections.append("")
    for i, tool in enumerate(flow_ngram, 1):
        clean = tool.rstrip("+")
        display = _tool_display_name(clean)
        repeat_note = " (repeat as needed)" if tool.endswith("+") else ""
        sections.append(f"{i}. Call `{display}`{repeat_note}")
    sections.append("")

    # Guardrails
    sections.append("## Guardrails")
    sections.append("")
    sections.append(
        "- ALWAYS follow the steps in order. Skipping steps reduces reliability."
    )
    sections.append(
        "- NEVER skip the verification step at the end of the sequence."
    )
    if any(t.rstrip("+") in ("Edit", "Write", "edit_file", "write_file")
           for t in flow_ngram):
        sections.append(
            "- ALWAYS read the target file before editing. "
            "Never edit blindly."
        )
    if any(t.rstrip("+") == "Bash" for t in flow_ngram):
        sections.append(
            "- ALWAYS check command exit codes. A silent Bash failure "
            "can corrupt downstream steps."
        )
    sections.append("")

    # Why This Skill Exists
    sections.append("## Why This Skill Exists")
    sections.append("")
    sections.append(
        f"This skill was auto-generated by SIO on "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')} from flow analysis."
    )
    sections.append("")
    sections.append(f"**Success rate**: {success_rate:.0%}")
    sections.append(f"**Flow pattern**: `{flow_label}`")
    if session_examples:
        sections.append(f"**Sessions observed**: {len(session_examples)}")
    sections.append("")

    return "\n".join(sections)


def write_skill_file(
    content: str,
    slug: str,
    target_dir: str = _DEFAULT_SKILLS_DIR,
) -> str:
    """Write skill markdown content to a file.

    Parameters
    ----------
    content:
        Complete markdown string for the skill file.
    slug:
        Short identifier used as the filename (sanitized).
    target_dir:
        Directory to write into. Created if it does not exist.
        Defaults to ``~/.claude/skills/learned/``.

    Returns
    -------
    str
        Absolute path to the written file.
    """
    target_dir = os.path.expanduser(target_dir)
    os.makedirs(target_dir, exist_ok=True)

    safe_slug = _slugify(slug)
    if not safe_slug:
        safe_slug = "unnamed-skill"

    filename = f"{safe_slug}.md"
    filepath = os.path.join(target_dir, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    return filepath
