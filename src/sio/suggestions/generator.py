"""sio.suggestions.generator — targeted improvement proposal generation.

Analyzes actual error content from datasets to produce specific, actionable
improvement suggestions (CLAUDE.md rules, skill updates, hook configs).

Public API
----------
    generate_suggestions(patterns, datasets, db_conn) -> list[dict]

Suggestion dict schema
----------------------
    pattern_id      (int)   integer row-id of the patterns row
    dataset_id      (int)   integer row-id of the datasets row
    description     (str)   human-readable summary
    confidence      (float) 0.0-1.0 quality signal
    proposed_change (str)   targeted rule/update text
    target_file     (str)   destination config file path
    change_type     (str)   one of "claude_md_rule", "skill_md_update", "hook_config"
    status          (str)   always "pending" for freshly generated suggestions
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from sio.suggestions.confidence import score_confidence

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_DEFAULT_CHANGE_TYPE = "tool_rule"
_DEFAULT_TARGET_FILE = ".claude/rules/tools/"

# Map change_type -> canonical target file
_TARGET_FILE_MAP: dict[str, str] = {
    "claude_md_rule": "CLAUDE.md",
    "tool_rule": ".claude/rules/tools/",
    "domain_rule": ".claude/rules/domains/",
    "skill_update": ".claude/skills/",
    "hook_config": ".claude/hooks/",
}

# Known tool name -> rule file mapping for tiered routing
_TOOL_RULE_FILES: dict[str, str] = {
    "graphiti": ".claude/rules/tools/graphiti.md",
    "atlassian": ".claude/rules/tools/atlassian.md",
    "superset": ".claude/rules/tools/superset.md",
    "playwright": ".claude/rules/tools/playwright.md",
    "snowflake": ".claude/rules/tools/snowflake.md",
    "read": ".claude/rules/tools/read.md",
    "bash": ".claude/rules/tools/bash.md",
    "clipboard": ".claude/rules/tools/clipboard.md",
}


def _infer_change_type(pattern: dict) -> str:
    """Infer the change type from pattern metadata.

    CLAUDE.md Constitution: CLAUDE.md must stay under 200 lines.
    Tool-specific rules go to ~/.claude/rules/tools/{tool}.md.
    Domain rules go to ~/.claude/rules/domains/{domain}.md.
    Only CORE behavioral rules go to CLAUDE.md.

    Rules
    -----
    - Patterns about a specific tool -> "tool_rule" (routed to rules/tools/)
    - Patterns about hooks -> "hook_config"
    - Patterns about skills -> "skill_update"
    - Patterns about general behavior -> "claude_md_rule" (ONLY if no tool match)
    """
    tool_name: str = (pattern.get("tool_name") or "").lower()

    # Check if this matches a known tool rule file
    for key in _TOOL_RULE_FILES:
        if key in tool_name:
            return "tool_rule"

    if "hook" in tool_name:
        return "hook_config"
    if "skill" in tool_name:
        return "skill_update"

    # If tool_name is a specific tool (not "unknown"), route to tool rules
    if tool_name and tool_name != "unknown" and tool_name != "unknown tool":
        return "tool_rule"

    # Only truly general patterns go to CLAUDE.md
    return "claude_md_rule"


def _infer_target_file(pattern: dict, change_type: str) -> str:
    """Determine the exact target file for a suggestion.

    For tool_rule type, tries to match to an existing rule file.
    Falls back to creating a new one based on tool name.
    """
    if change_type != "tool_rule":
        return _TARGET_FILE_MAP.get(change_type, "CLAUDE.md")

    tool_name: str = (pattern.get("tool_name") or "").lower()

    # Check known tool mappings
    for key, filepath in _TOOL_RULE_FILES.items():
        if key in tool_name:
            return filepath

    # For unknown tools, create a new rule file
    # Sanitize tool name for filename
    safe_name = tool_name.replace("mcp__", "").split("__")[0]
    safe_name = safe_name.replace(" ", "_").replace("/", "_")
    if safe_name:
        return f".claude/rules/tools/{safe_name}.md"

    return _TARGET_FILE_MAP.get(change_type, ".claude/rules/tools/")


# ---------------------------------------------------------------------------
# Content analysis — extracts actionable insights from error examples
# ---------------------------------------------------------------------------


def _load_dataset_examples(dataset: dict) -> list[dict]:
    """Load actual examples from the dataset JSON file."""
    file_path = dataset.get("file_path")
    if not file_path:
        return []
    path = Path(file_path)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("examples", [])
    except (json.JSONDecodeError, OSError):
        return []


def _extract_key_phrases(texts: list[str], max_phrases: int = 5) -> list[str]:
    """Extract the most informative phrases from a list of error texts.

    Returns up to max_phrases representative snippets, deduplicated.
    """
    if not texts:
        return []

    # Take first 200 chars of each text as a representative snippet
    snippets: list[str] = []
    seen: set[str] = set()
    for text in texts:
        # Normalize and truncate
        snippet = text.strip()[:200]
        if not snippet:
            continue
        # Simple dedup on first 80 chars
        key = snippet[:80].lower()
        if key not in seen:
            seen.add(key)
            snippets.append(snippet)
        if len(snippets) >= max_phrases:
            break

    return snippets


def _analyze_tool_failures(examples: list[dict]) -> dict[str, Any]:
    """Analyze tool_failure examples to extract specific failure patterns."""
    errors = [e for e in examples if e.get("error_type") == "tool_failure"]
    if not errors:
        return {}

    # Count tool names
    tool_counts: Counter[str] = Counter()
    error_snippets: list[str] = []
    user_contexts: list[str] = []

    for e in errors:
        tn = e.get("tool_name") or "unknown"
        tool_counts[tn] += 1
        if e.get("error_text"):
            error_snippets.append(e["error_text"])
        if e.get("user_message"):
            user_contexts.append(e["user_message"])

    return {
        "type": "tool_failure",
        "count": len(errors),
        "top_tools": tool_counts.most_common(3),
        "error_snippets": _extract_key_phrases(error_snippets),
        "user_contexts": _extract_key_phrases(user_contexts, max_phrases=3),
    }


def _analyze_user_corrections(examples: list[dict]) -> dict[str, Any]:
    """Analyze user_correction examples to extract what users corrected."""
    corrections = [e for e in examples if e.get("error_type") == "user_correction"]
    if not corrections:
        return {}

    correction_texts: list[str] = []
    for e in corrections:
        text = e.get("error_text") or e.get("user_message") or ""
        # Strip the "User correction: " prefix if present
        if text.startswith("User correction: "):
            text = text[17:]
        if text:
            correction_texts.append(text)

    return {
        "type": "user_correction",
        "count": len(corrections),
        "correction_phrases": _extract_key_phrases(correction_texts),
    }


def _analyze_agent_admissions(examples: list[dict]) -> dict[str, Any]:
    """Analyze agent_admission examples to extract what the agent admitted."""
    admissions = [e for e in examples if e.get("error_type") == "agent_admission"]
    if not admissions:
        return {}

    admission_texts: list[str] = []
    user_contexts: list[str] = []

    for e in admissions:
        text = e.get("error_text") or ""
        # Strip "Agent admission: " prefix
        if text.startswith("Agent admission: "):
            text = text[17:]
        if text:
            admission_texts.append(text)
        if e.get("user_message"):
            user_contexts.append(e["user_message"])

    return {
        "type": "agent_admission",
        "count": len(admissions),
        "admission_phrases": _extract_key_phrases(admission_texts),
        "user_contexts": _extract_key_phrases(user_contexts, max_phrases=3),
    }


def _analyze_repeated_attempts(examples: list[dict]) -> dict[str, Any]:
    """Analyze repeated_attempt examples."""
    repeated = [e for e in examples if e.get("error_type") == "repeated_attempt"]
    if not repeated:
        return {}

    tool_counts: Counter[str] = Counter()
    for e in repeated:
        tn = e.get("tool_name") or "unknown"
        tool_counts[tn] += 1

    return {
        "type": "repeated_attempt",
        "count": len(repeated),
        "top_tools": tool_counts.most_common(3),
        "error_snippets": _extract_key_phrases(
            [e.get("error_text", "") for e in repeated]
        ),
    }


def _analyze_undos(examples: list[dict]) -> dict[str, Any]:
    """Analyze undo examples."""
    undos = [e for e in examples if e.get("error_type") == "undo"]
    if not undos:
        return {}

    return {
        "type": "undo",
        "count": len(undos),
        "undo_phrases": _extract_key_phrases(
            [e.get("error_text", "") for e in undos]
        ),
    }


def _analyze_examples(examples: list[dict]) -> list[dict[str, Any]]:
    """Run all error-type analyzers and return non-empty results."""
    analyses = [
        _analyze_tool_failures(examples),
        _analyze_user_corrections(examples),
        _analyze_agent_admissions(examples),
        _analyze_repeated_attempts(examples),
        _analyze_undos(examples),
    ]
    return [a for a in analyses if a]


# ---------------------------------------------------------------------------
# Targeted change builders — one per error type
# ---------------------------------------------------------------------------


def _build_tool_failure_rule(
    pattern: dict,
    analysis: dict[str, Any],
) -> str:
    """Build a targeted CLAUDE.md rule from tool failure analysis."""
    tool_name = pattern.get("tool_name") or "the tool"
    lines = [
        f"## Rule: {tool_name} — Prevent Recurring Failures",
        "",
        f"**Pattern**: {pattern.get('error_count', 0)} failures detected "
        f"across {pattern.get('session_count', 0)} sessions.",
        "",
    ]

    # Show actual error messages observed
    snippets = analysis.get("error_snippets", [])
    if snippets:
        lines.append("**Observed failures**:")
        for s in snippets[:3]:
            # Truncate long snippets for readability
            display = s[:150] + ("..." if len(s) > 150 else "")
            lines.append(f"- `{display}`")
        lines.append("")

    # Show what users were trying to do
    user_ctx = analysis.get("user_contexts", [])
    if user_ctx:
        lines.append("**User intent when failures occurred**:")
        for ctx in user_ctx[:2]:
            display = ctx[:120] + ("..." if len(ctx) > 120 else "")
            lines.append(f"- {display}")
        lines.append("")

    # Generate specific prevention rules based on error content
    top_tools = analysis.get("top_tools", [])
    lines.append("**Prevention rules**:")
    for tn, count in top_tools:
        lines.append(
            f"- Before calling `{tn}`, verify the target exists and inputs "
            f"are valid ({count} failures observed)."
        )

    # Add specific rules based on error snippet content
    for s in snippets[:2]:
        lower = s.lower()
        if "not found" in lower or "no such file" in lower:
            lines.append(
                f"- Always check file/path existence before `{tool_name}` calls."
            )
            break
        if "permission" in lower or "denied" in lower:
            lines.append(
                f"- Verify file permissions before `{tool_name}` operations."
            )
            break
        if "timeout" in lower or "timed out" in lower:
            lines.append(
                f"- Use shorter timeout or chunked operations with `{tool_name}`."
            )
            break
        if "syntax" in lower or "parse" in lower:
            lines.append(
                f"- Validate input syntax before passing to `{tool_name}`."
            )
            break

    return "\n".join(lines)


def _build_user_correction_rule(
    pattern: dict,
    analysis: dict[str, Any],
) -> str:
    """Build a targeted rule from user correction analysis."""
    lines = [
        "## Rule: Address Repeated User Corrections",
        "",
        f"**Pattern**: Users corrected the agent {analysis.get('count', 0)} "
        f"times with similar feedback.",
        "",
    ]

    phrases = analysis.get("correction_phrases", [])
    if phrases:
        lines.append("**What users said**:")
        for p in phrases[:4]:
            display = p[:180] + ("..." if len(p) > 180 else "")
            lines.append(f"- \"{display}\"")
        lines.append("")

    # Derive actionable rules from correction content
    lines.append("**Corrective rules**:")
    for p in phrases[:3]:
        lower = p.lower()
        if "wrong file" in lower or "wrong path" in lower:
            lines.append(
                "- Always confirm the target file path with the user before "
                "editing or creating files."
            )
        elif "not what i wanted" in lower or "i meant" in lower:
            lines.append(
                "- When the task is ambiguous, ask a clarifying question "
                "before proceeding with implementation."
            )
        elif "that's not right" in lower or "that's wrong" in lower:
            lines.append(
                "- Re-read the user's original request before generating output. "
                "Verify your interpretation matches their intent."
            )
        else:
            # Use the correction text itself as the rule basis
            short = p[:100]
            lines.append(f"- User feedback: \"{short}\" — adjust behavior accordingly.")

    return "\n".join(lines)


def _build_agent_admission_rule(
    pattern: dict,
    analysis: dict[str, Any],
) -> str:
    """Build a targeted rule from agent self-admission analysis.

    This is the most valuable error type — the agent itself identified
    what went wrong, giving us direct insight into skill/prompt gaps.
    """
    lines = [
        "## Rule: Agent Self-Identified Mistakes",
        "",
        f"**Pattern**: Agent admitted errors {analysis.get('count', 0)} times "
        f"with similar root causes.",
        "",
    ]

    phrases = analysis.get("admission_phrases", [])
    if phrases:
        lines.append("**What the agent said**:")
        for p in phrases[:4]:
            display = p[:200] + ("..." if len(p) > 200 else "")
            lines.append(f"- \"{display}\"")
        lines.append("")

    user_ctx = analysis.get("user_contexts", [])
    if user_ctx:
        lines.append("**What the user was asking for**:")
        for ctx in user_ctx[:2]:
            display = ctx[:150] + ("..." if len(ctx) > 150 else "")
            lines.append(f"- {display}")
        lines.append("")

    # Derive specific prevention rules from admission content
    lines.append("**Prevention rules** (derived from agent's own words):")
    rules_added: set[str] = set()

    for p in phrases:
        lower = p.lower()
        if "should have" in lower and "read" not in rules_added:
            rules_added.add("read")
            lines.append(
                "- Always read the full file/context before making changes. "
                "The agent admitted skipping this step."
            )
        if ("missed" in lower or "overlooked" in lower) and "verify" not in rules_added:
            rules_added.add("verify")
            lines.append(
                "- After completing a change, verify all related files are "
                "consistent. The agent missed dependent changes."
            )
        if ("accidentally" in lower or "mistakenly" in lower) and "confirm" not in rules_added:
            rules_added.add("confirm")
            lines.append(
                "- For destructive operations (delete, overwrite, rename), "
                "pause and confirm the target before executing."
            )
        if ("forgot" in lower or "neglected" in lower) and "checklist" not in rules_added:
            rules_added.add("checklist")
            lines.append(
                "- Before completing a task, run through a mental checklist: "
                "tests pass, imports updated, no leftover debug code."
            )
        if "sorry" in lower and "quality" not in rules_added:
            rules_added.add("quality")
            lines.append(
                "- When generating code or text, review output quality before "
                "presenting. The agent had to apologize for subpar output."
            )
        if ("fix" in lower or "correct" in lower) and "self-correct" not in rules_added:
            rules_added.add("self-correct")
            lines.append(
                "- When self-correcting, state what was wrong and why — "
                "this helps prevent the same mistake in future sessions."
            )
        if ("didn't" in lower or "did not" in lower) and "diligence" not in rules_added:
            rules_added.add("diligence")
            lines.append(
                "- Read error messages and tool output carefully before "
                "proceeding. The agent admitted not checking results."
            )

    if not rules_added:
        lines.append(
            "- Review the admission phrases above and add specific "
            "preventive instructions to your workflow."
        )

    return "\n".join(lines)


def _build_repeated_attempt_rule(
    pattern: dict,
    analysis: dict[str, Any],
) -> str:
    """Build a targeted rule from repeated attempt analysis."""
    lines = [
        "## Rule: Avoid Repeated Tool Retries",
        "",
        f"**Pattern**: Same tool called 3+ consecutive times "
        f"({analysis.get('count', 0)} occurrences detected).",
        "",
    ]

    top_tools = analysis.get("top_tools", [])
    if top_tools:
        lines.append("**Tools repeatedly retried**:")
        for tn, count in top_tools:
            lines.append(f"- `{tn}`: {count} retry sequences")
        lines.append("")

    snippets = analysis.get("error_snippets", [])
    if snippets:
        lines.append("**Context**:")
        for s in snippets[:2]:
            display = s[:150] + ("..." if len(s) > 150 else "")
            lines.append(f"- {display}")
        lines.append("")

    lines.append("**Prevention rules**:")
    for tn, _ in top_tools[:2]:
        lines.append(
            f"- If `{tn}` fails twice, stop and diagnose the root cause "
            f"instead of retrying with minor variations."
        )
    lines.append(
        "- After 2 failed attempts with any tool, try an alternative "
        "approach or ask the user for guidance."
    )

    return "\n".join(lines)


def _build_undo_rule(
    pattern: dict,
    analysis: dict[str, Any],
) -> str:
    """Build a targeted rule from undo request analysis."""
    lines = [
        "## Rule: Reduce Undo/Revert Requests",
        "",
        f"**Pattern**: Users requested undo/revert {analysis.get('count', 0)} "
        f"times for similar changes.",
        "",
    ]

    phrases = analysis.get("undo_phrases", [])
    if phrases:
        lines.append("**What users said**:")
        for p in phrases[:3]:
            display = p[:150] + ("..." if len(p) > 150 else "")
            lines.append(f"- \"{display}\"")
        lines.append("")

    lines.append("**Prevention rules**:")
    lines.append(
        "- Before making significant changes, describe the planned "
        "modifications and get user confirmation."
    )
    lines.append(
        "- Prefer small, incremental edits over large rewrites. "
        "Users can more easily review and accept small diffs."
    )
    lines.append(
        "- Always create a git commit before making risky changes "
        "so the user has a clean rollback point."
    )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main change builder — dispatches to type-specific builders
# ---------------------------------------------------------------------------

# Maps error_type -> builder function
_CHANGE_BUILDERS: dict[str, Any] = {
    "tool_failure": _build_tool_failure_rule,
    "user_correction": _build_user_correction_rule,
    "agent_admission": _build_agent_admission_rule,
    "repeated_attempt": _build_repeated_attempt_rule,
    "undo": _build_undo_rule,
}


def _build_proposed_change(
    pattern: dict,
    examples: list[dict] | None = None,
) -> str:
    """Generate a targeted proposed change from pattern context and examples.

    Analyzes actual error content from dataset examples to produce specific,
    actionable rules — not generic boilerplate.

    Parameters
    ----------
    pattern:
        Pattern dict with tool_name, description, error_count, etc.
    examples:
        List of dataset examples with error_text, error_type, user_message.
        When None or empty, falls back to pattern-only analysis.
    """
    if not examples:
        # Fallback: use pattern description to infer error type
        return _build_fallback_change(pattern)

    # Analyze examples by error type
    analyses = _analyze_examples(examples)

    if not analyses:
        return _build_fallback_change(pattern)

    # Build targeted rules for each error type present
    sections: list[str] = []
    for analysis in analyses:
        error_type = analysis.get("type", "")
        builder = _CHANGE_BUILDERS.get(error_type)
        if builder:
            sections.append(builder(pattern, analysis))

    if not sections:
        return _build_fallback_change(pattern)

    return "\n\n---\n\n".join(sections)


def _build_fallback_change(pattern: dict) -> str:
    """Fallback when no dataset examples are available.

    Still uses the pattern's description (real error text) for specificity.
    """
    tool_name: str = pattern.get("tool_name") or "the tool"
    description: str = pattern.get("description") or f"errors with {tool_name}"

    lines = [
        f"## Rule: {tool_name} Error Prevention",
        "",
        f"**Pattern observed**: {description[:200]}",
        f"**Occurrences**: {pattern.get('error_count', 0)} errors "
        f"across {pattern.get('session_count', 0)} sessions.",
        "",
        "**Action items** (review dataset examples for more specific rules):",
        f"- Investigate the root cause of `{tool_name}` failures described above.",
        f"- Check inputs and preconditions before calling `{tool_name}`.",
        "- Run `sio datasets --pattern <id>` to see full error examples.",
    ]
    return "\n".join(lines)


def _build_description(pattern: dict, dataset: dict, examples: list[dict]) -> str:
    """Build a human-readable description for the suggestion."""
    tool_name: str = pattern.get("tool_name") or "unknown tool"
    error_count: int = int(pattern.get("error_count") or 0)
    positive_count: int = int(dataset.get("positive_count") or 0)
    negative_count: int = int(dataset.get("negative_count") or 0)

    # Identify the dominant error type
    type_counts: Counter[str] = Counter()
    for ex in examples:
        et = ex.get("error_type")
        if et:
            type_counts[et] += 1

    type_summary = ""
    if type_counts:
        top_types = type_counts.most_common(2)
        type_parts = [f"{count} {etype}" for etype, count in top_types]
        type_summary = f" ({', '.join(type_parts)})"

    return (
        f"Improve reliability of {tool_name}: "
        f"{error_count} error(s) detected across sessions{type_summary} "
        f"({positive_count} positive / {negative_count} negative examples)."
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_suggestions(
    patterns: list[dict[str, Any]],
    datasets: dict[str, dict[str, Any]],
    db_conn: sqlite3.Connection,
    *,
    verbose: bool = False,
    mode: str | None = None,
) -> list[dict[str, Any]]:
    """Generate improvement suggestion dicts from ranked patterns and datasets.

    When an LLM backend is configured (via ``~/.sio/config.toml`` or env
    vars), delegates to DSPy for LLM-powered generation.  Otherwise falls
    back to the deterministic template path.

    Loads actual error examples from dataset files to produce targeted,
    content-aware suggestions instead of generic boilerplate rules.

    Parameters
    ----------
    patterns:
        List of pattern dicts produced by the clusterer/ranker.  Each dict
        must have at minimum: ``id`` (int row-id), ``pattern_id`` (str),
        ``description``, ``tool_name``, ``error_count``, ``rank_score``.
    datasets:
        Mapping of pattern_id (str) to dataset metadata dicts.  Each metadata
        dict must carry: ``id`` (int row-id), ``pattern_id``, ``positive_count``,
        ``negative_count``, ``file_path``.
    db_conn:
        Open SQLite connection (used for any future DB operations; not mutated
        by this function in the current implementation).
    verbose:
        When True and the DSPy path is active, log DSPy input/output
        and reasoning traces.

    Returns
    -------
    list[dict]
        One suggestion dict per pattern that has a matching dataset entry.
        Patterns without a dataset entry are silently skipped.  Each dict
        includes ``_using_dspy`` (bool) indicating which path produced it.
    """
    import logging

    _log = logging.getLogger(__name__)

    # --- Attempt DSPy path ---
    use_dspy = False
    try:
        from sio.core.config import load_config
        from sio.core.dspy.lm_factory import create_lm

        config = load_config()
        lm = create_lm(config)
        if lm is not None:
            use_dspy = True
            _log.info("LLM backend detected — using DSPy suggestion path")
    except Exception as exc:  # noqa: BLE001
        _log.debug("DSPy path unavailable: %s", exc)

    suggestions: list[dict[str, Any]] = []

    for pattern in patterns:
        pattern_str_id: str = pattern.get("pattern_id", "")
        dataset = datasets.get(pattern_str_id)
        if dataset is None:
            continue

        # --- DSPy path (when LLM is available) ---
        if use_dspy:
            try:
                from sio.suggestions.dspy_generator import (
                    generate_auto_suggestion,
                    generate_dspy_suggestion,
                    generate_hitl_suggestion,
                )

                if mode == "auto":
                    suggestion = generate_auto_suggestion(
                        pattern, dataset, config, verbose=verbose,
                    )
                    if suggestion is None:
                        _log.warning(
                            "Auto mode generation returned None for "
                            "pattern %s, falling back to template",
                            pattern_str_id,
                        )
                    else:
                        suggestions.append(suggestion)
                        continue
                elif mode == "hitl":
                    suggestion = generate_hitl_suggestion(
                        pattern, dataset, config, db_conn,
                        verbose=verbose,
                    )
                    if suggestion is None:
                        _log.info(
                            "HITL mode: user skipped pattern %s",
                            pattern_str_id,
                        )
                        continue
                    else:
                        suggestions.append(suggestion)
                        continue
                else:
                    suggestion = generate_dspy_suggestion(
                        pattern, dataset, config, verbose=verbose,
                    )
                    suggestions.append(suggestion)
                    continue
            except Exception as exc:  # noqa: BLE001
                _log.warning(
                    "DSPy generation failed for pattern %s, "
                    "falling back to template: %s",
                    pattern_str_id,
                    exc,
                )
                # Fall through to template path for this pattern

        # --- Template path (deterministic fallback) ---
        examples = _load_dataset_examples(dataset)

        change_type = _infer_change_type(pattern)
        target_file = _infer_target_file(pattern, change_type)
        confidence = score_confidence(pattern, dataset)
        proposed_change = _build_proposed_change(pattern, examples)
        description = _build_description(pattern, dataset, examples)

        suggestion_dict: dict[str, Any] = {
            "pattern_id": int(pattern["id"]),
            "pattern_str_id": pattern_str_id,
            "dataset_id": int(dataset["id"]),
            "description": description,
            "confidence": float(confidence),
            "proposed_change": proposed_change,
            "target_file": target_file,
            "change_type": change_type,
            "status": "pending",
            "_using_dspy": False,
        }
        suggestions.append(suggestion_dict)

    # --- SECOND PASS: Refine all suggestions for specificity ---
    suggestions = _refine_all_suggestions(suggestions, datasets, _log)

    return suggestions


def _refine_all_suggestions(
    suggestions: list[dict[str, Any]],
    datasets: dict[str, dict[str, Any]],
    _log: Any,
) -> list[dict[str, Any]]:
    """Second pass: refine generic suggestions into specific, actionable rules.

    For each suggestion, extracts error samples from the dataset and runs
    the refiner to produce concise, machine-actionable rules. If refinement
    fails or produces lower-quality output, keeps the original.
    """
    try:
        from sio.suggestions.refiner import refine_suggestion
    except ImportError:
        _log.debug("Refiner module not available, skipping second pass")
        return suggestions

    refined_count = 0
    for suggestion in suggestions:
        # Skip refinement for DSPy-generated suggestions — they already
        # have LLM-quality proposed_change text.
        if suggestion.get("_using_dspy"):
            continue

        # Get the original proposed change
        original = suggestion.get("proposed_change", "")
        if not original:
            continue

        # Look up the CORRECT dataset for this suggestion's pattern
        pattern_str_id = suggestion.get("pattern_str_id", "")
        ds = datasets.get(pattern_str_id)
        error_samples: list[str] = []
        if ds is not None:
            ds_examples = _load_dataset_examples(ds)
            for ex in ds_examples:
                error_text = ex.get("error_text", "")
                if error_text:
                    error_samples.append(error_text)

        if not error_samples:
            continue

        # Extract tool name from suggestion description
        tool_name = "unknown"
        desc = suggestion.get("description", "")
        if "of " in desc:
            # "Improve reliability of Bash: ..." -> "Bash"
            tool_name = desc.split("of ", 1)[1].split(":")[0].strip()

        try:
            refined = refine_suggestion(original, error_samples, tool_name)
            if refined != original:
                suggestion["proposed_change"] = refined
                suggestion["_refined"] = True
                refined_count += 1
        except Exception as exc:  # noqa: BLE001
            _log.debug("Refinement failed for suggestion: %s", exc)

    if refined_count > 0:
        _log.info("Refined %d/%d suggestions", refined_count, len(suggestions))

    return suggestions
