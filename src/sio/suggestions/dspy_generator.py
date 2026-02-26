"""sio.suggestions.dspy_generator -- DSPy-powered suggestion generation.

Replaces string templates with real LLM-generated improvement suggestions
when an LLM backend is configured. Falls back gracefully if DSPy is
unavailable or the LLM call fails.

Public API
----------
    generate_dspy_suggestion(pattern, dataset, config, verbose=False) -> dict
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# T030: Surface -> target file mapping
# ---------------------------------------------------------------------------

_SURFACE_TARGET_MAP: dict[str, str] = {
    "claude_md_rule": "CLAUDE.md",
    "skill_update": ".claude/skills/",
    "hook_config": ".claude/hooks/",
    "mcp_config": ".claude/settings.json",
    "settings_config": ".claude/settings.json",
    "agent_profile": ".claude/agent-profile.md",
    "project_config": ".claude/project-config.json",
}

_VALID_SURFACES = frozenset(_SURFACE_TARGET_MAP.keys())

# ---------------------------------------------------------------------------
# T026: Input sanitization
# ---------------------------------------------------------------------------

# Patterns for sensitive data that must be redacted before sending to LLM
_SENSITIVE_PATTERNS: list[re.Pattern[str]] = [
    # OpenAI / generic sk- keys (including newer sk-proj-... format)
    re.compile(r"sk-[a-zA-Z0-9_\-]{20,}"),
    # AWS access key IDs
    re.compile(r"AKIA[A-Z0-9]{16}"),
    # Bearer tokens
    re.compile(r"Bearer [a-zA-Z0-9._\-]+"),
    # Generic password patterns: password=..., passwd=..., pwd=...
    re.compile(r'(?i)(password|passwd|pwd)\s*[=:]\s*["\']?[^\s"\']{4,}'),
    # API key patterns: api_key=..., apikey=...
    re.compile(r'(?i)(api[_-]?key)\s*[=:]\s*["\']?[^\s"\']{8,}'),
    # Generic secret patterns
    re.compile(r'(?i)(secret|token)\s*[=:]\s*["\']?[^\s"\']{8,}'),
    # GitHub personal access tokens
    re.compile(r"ghp_[a-zA-Z0-9]{20,}"),
    # GitHub OAuth tokens
    re.compile(r"gho_[a-zA-Z0-9]{20,}"),
]


def _sanitize_examples(examples_json: str) -> str:
    """Strip API keys, passwords, and other sensitive data from examples.

    Parameters
    ----------
    examples_json:
        JSON string of error examples to sanitize.

    Returns
    -------
    str
        Sanitized JSON string with secrets replaced by ``[REDACTED]``.
    """
    result = examples_json
    for pattern in _SENSITIVE_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    return result


def _truncate_fields(text: str, max_chars: int = 500) -> str:
    """Cap text at max_chars, adding ellipsis if truncated.

    Parameters
    ----------
    text:
        Input text to truncate.
    max_chars:
        Maximum character count. Defaults to 500.

    Returns
    -------
    str
        Truncated text, with ``...`` appended if it was shortened.
    """
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."


# ---------------------------------------------------------------------------
# T025 + T027: DSPy suggestion generation
# ---------------------------------------------------------------------------


def _load_dataset_examples(dataset: dict) -> list[dict]:
    """Load examples from a dataset's JSON file."""
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


def _normalize_surface(raw_surface: str) -> str:
    """Normalize a DSPy-returned target_surface to a valid value.

    The LLM may return slightly different casing or extra whitespace.
    Falls back to ``claude_md_rule`` if unrecognized.
    """
    cleaned = raw_surface.strip().lower().replace("-", "_").replace(" ", "_")
    if cleaned in _VALID_SURFACES:
        return cleaned
    # Fuzzy match: check if any valid surface is a substring
    for valid in _VALID_SURFACES:
        if valid in cleaned or cleaned in valid:
            return valid
    return "claude_md_rule"


def _format_proposed_change(
    rule_title: str,
    prevention_instructions: str,
    target_surface: str,
    rationale: str,
) -> str:
    """Format DSPy output fields into a markdown proposed change block."""
    lines = [
        f"## {rule_title}",
        "",
        prevention_instructions,
        "",
        f"**Rationale**: {rationale}",
        "",
        f"**Target surface**: `{target_surface}`",
        f"**Target file**: `{_SURFACE_TARGET_MAP.get(target_surface, 'CLAUDE.md')}`",
    ]
    return "\n".join(lines)


def generate_dspy_suggestion(
    pattern: dict[str, Any],
    dataset: dict[str, Any],
    config: Any,
    *,
    verbose: bool = False,
) -> dict[str, Any]:
    """Generate a single improvement suggestion using DSPy + LLM.

    Parameters
    ----------
    pattern:
        Pattern dict with ``id``, ``pattern_id``, ``description``,
        ``tool_name``, ``error_count``, ``session_count``, ``rank_score``.
    dataset:
        Dataset metadata dict with ``id``, ``file_path``,
        ``positive_count``, ``negative_count``.
    config:
        ``SIOConfig`` instance with LLM settings.
    verbose:
        When True, log DSPy input/output/reasoning via the module logger.

    Returns
    -------
    dict
        Suggestion dict with keys: ``target_surface``, ``rule_title``,
        ``prevention_instructions``, ``rationale``, ``reasoning_trace``,
        ``proposed_change``, ``confidence``, ``status``, ``target_file``,
        ``_using_dspy``, ``pattern_id``, ``dataset_id``, ``description``.

    Raises
    ------
    RuntimeError
        If the DSPy call fails (caller should catch and fall back).
    """
    import dspy

    from sio.core.dspy.lm_factory import create_lm
    from sio.core.dspy.modules import SuggestionModule
    from sio.suggestions.confidence import score_confidence

    # --- Create and configure LM ---
    lm = create_lm(config)
    if lm is None:
        raise RuntimeError("No LLM backend available")

    dspy.configure(lm=lm)

    # --- Prepare inputs ---
    examples = _load_dataset_examples(dataset)
    examples_json = json.dumps(examples[:20], default=str)  # cap at 20 examples
    examples_json = _sanitize_examples(examples_json)
    examples_json = _truncate_fields(examples_json, max_chars=2000)

    error_type = pattern.get("error_type") or "unknown"
    # Build a concise pattern summary from description + tool_name + counts
    pattern_summary = (
        f"Tool: {pattern.get('tool_name', 'unknown')}. "
        f"{pattern.get('description', 'Recurring error pattern')}. "
        f"{pattern.get('error_count', 0)} errors across "
        f"{pattern.get('session_count', 0)} sessions."
    )
    pattern_summary = _truncate_fields(pattern_summary, max_chars=500)

    # --- T027: Verbose trace logging (inputs) ---
    if verbose:
        logger.info(
            "DSPy input — error_type=%s, pattern_summary=%s, "
            "examples_json_len=%d",
            error_type,
            pattern_summary[:200],
            len(examples_json),
        )

    # --- Run DSPy module ---
    module = SuggestionModule()
    try:
        result = module.forward(
            error_examples=examples_json,
            error_type=error_type,
            pattern_summary=pattern_summary,
        )
    except Exception as exc:
        raise RuntimeError(f"DSPy call failed: {exc}") from exc

    # --- Extract outputs ---
    raw_surface = getattr(result, "target_surface", "claude_md_rule")
    target_surface = _normalize_surface(raw_surface)
    rule_title = getattr(result, "rule_title", "Improvement suggestion")
    prevention_instructions = getattr(
        result, "prevention_instructions", "Review the error pattern."
    )
    rationale = getattr(result, "rationale", "Based on observed error patterns.")
    reasoning_trace = getattr(result, "reasoning", "")

    # --- T027: Verbose trace logging (outputs) ---
    if verbose:
        logger.info(
            "DSPy output — target_surface=%s, rule_title=%s, "
            "reasoning_trace=%s",
            target_surface,
            rule_title,
            (reasoning_trace[:300] if reasoning_trace else "(none)"),
        )

    # --- Build suggestion dict ---
    target_file = _SURFACE_TARGET_MAP.get(target_surface, "CLAUDE.md")
    proposed_change = _format_proposed_change(
        rule_title, prevention_instructions, target_surface, rationale,
    )
    confidence = score_confidence(pattern, dataset)

    # Build description consistent with template generator
    tool_name = pattern.get("tool_name") or "unknown tool"
    error_count = int(pattern.get("error_count") or 0)
    description = (
        f"[DSPy] {rule_title} — {tool_name}: "
        f"{error_count} error(s) detected."
    )

    return {
        "pattern_id": int(pattern["id"]),
        "dataset_id": int(dataset["id"]),
        "description": description,
        "confidence": float(confidence),
        "proposed_change": proposed_change,
        "target_file": target_file,
        "target_surface": target_surface,
        "change_type": target_surface,
        "rule_title": rule_title,
        "prevention_instructions": prevention_instructions,
        "rationale": rationale,
        "reasoning_trace": reasoning_trace,
        "status": "pending",
        "_using_dspy": True,
    }
