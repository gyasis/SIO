"""sio.suggestions.dspy_generator -- DSPy-powered suggestion generation.

Replaces string templates with real LLM-generated improvement suggestions
when an LLM backend is configured. Falls back gracefully if DSPy is
unavailable or the LLM call fails.

Public API
----------
    SuggestionGenerator               -- dspy.Module subclass (T066, US9)
    generate_suggestion(...)          -- thin legacy wrapper around SuggestionGenerator
    generate_dspy_suggestion(pattern, dataset, config, verbose=False) -> dict
    _select_mode(pattern, confidence, target_surface) -> "auto" | "hitl"
    generate_auto_suggestion(pattern, dataset, config) -> dict | None
    generate_hitl_suggestion(pattern, dataset, config, conn, input_fn=None) -> dict | None
    build_dataset_analysis_summary(pattern, dataset) -> dict
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any, Callable

import dspy

from sio.core.dspy.assertions import (
    assert_no_phi,
    assert_rule_format,
    validate_no_phi,
    validate_rule_format,
)
from sio.core.dspy.signatures import PatternToRule

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# T030: Surface -> target file mapping
# ---------------------------------------------------------------------------

_SURFACE_TARGET_MAP: dict[str, str] = {
    "claude_md_rule": "CLAUDE.md",
    "skill_update": ".claude/skills/",
    "hook_config": ".claude/hooks/",
    "mcp_config": ".claude.json",
    "settings_config": ".claude/settings.json",
    "agent_profile": ".claude/agents/",
    "project_config": "CLAUDE.md",
}

_VALID_SURFACES = frozenset(_SURFACE_TARGET_MAP.keys())


# ---------------------------------------------------------------------------
# T062: Optimized module loading (FR-011)
# ---------------------------------------------------------------------------


def _load_optimized_or_default(config: Any) -> Any:
    """Load the active optimized SuggestionGenerator, or create a fresh one.

    Audit Round 2 C-R2.6 consolidation (Hunter #2, DSPy): the canonical
    suggestion class is ``SuggestionGenerator`` (3-input PatternToRule),
    not the legacy ``SuggestionModule`` (4-input old SuggestionGenerator
    signature). This function now returns the canonical class per
    ``contracts/dspy-module-api.md`` §3.

    Args:
        config: SIOConfig instance (used to locate the DB).

    Returns:
        A DSPy SuggestionGenerator — either optimized or freshly created.
    """
    try:
        db_path = os.path.expanduser("~/.sio/sio.db")
        if not os.path.exists(db_path):
            return SuggestionGenerator()

        import sqlite3

        from sio.core.dspy.module_store import get_active_module, load_module

        conn = sqlite3.connect(db_path)
        try:
            conn.row_factory = sqlite3.Row
            active = get_active_module(conn, "suggestion")
        finally:
            conn.close()

        if active and os.path.exists(active["file_path"]):
            logger.info(
                "Loading optimized SuggestionGenerator from %s",
                active["file_path"],
            )
            return load_module(SuggestionGenerator, active["file_path"])
    except Exception:
        logger.warning(
            "Failed to load optimized module, falling back to default",
            exc_info=True,
        )

    return SuggestionGenerator()


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
    # Anthropic API keys
    re.compile(r"sk-ant-[a-zA-Z0-9_\-]{20,}"),
    # SSH / PEM private keys
    re.compile(r"-----BEGIN [A-Z ]+ KEY-----"),
    # JWT tokens
    re.compile(r"eyJ[a-zA-Z0-9_-]+\.eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+"),
]

# Patterns that trigger Azure's content filter (jailbreak false positives).
# These appear in raw error output when tool_output contains system prompts,
# XML tags, or other content that Azure interprets as prompt injection.
_CONTENT_FILTER_PATTERNS: list[re.Pattern[str]] = [
    # Generic "IMPORTANT:" instruction blocks — must not cross JSON field
    # boundaries (stop at double quotes to preserve JSON structure)
    re.compile(r'IMPORTANT:[^"]{10,}?\.'),
    # Embedded system prompts / instruction-like text in tool output
    re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL),
    re.compile(r"<system>.*?</system>", re.DOTALL),
    re.compile(r"<user-prompt-submit-hook>.*?</user-prompt-submit-hook>", re.DOTALL),
    # Embedded XML/HTML tags that look like injection (common in Claude tool output)
    re.compile(r"<tool_use_error>.*?</tool_use_error>", re.DOTALL),
    re.compile(r"<[a-z_-]+>.*?</[a-z_-]+>", re.DOTALL),
    # Long base64 blobs (>50 chars)
    re.compile(r"[A-Za-z0-9+/]{50,}={0,2}"),
    # Raw hex dumps
    re.compile(r"(?:0x)?[0-9a-fA-F]{32,}"),
    # Embedded JSON with "role": "system" or "role": "user" (chat message leaks)
    re.compile(r'"role"\s*:\s*"(?:system|user|assistant)"'),
]

# Aggressive patterns for retry after content filter hit — strips stack traces
# and verbose error output down to just the error message.
_AGGRESSIVE_FILTER_PATTERNS: list[re.Pattern[str]] = [
    # Python tracebacks: "File "/path...", line N, in func"
    re.compile(r'File ".*?", line \d+, in .*?(?:\\n|$)'),
    # Multi-line stack traces (Traceback ... raise)
    re.compile(r"Traceback \(most recent call last\):.*?(?=\\n[A-Z])", re.DOTALL),
    # Full exception chains ("During handling of...")
    re.compile(r"During handling of the above exception.*?(?=\\n[A-Z]|$)", re.DOTALL),
    # Raw file paths with user directories
    re.compile(r"/home/[a-z]+/[^\s\"']{20,}"),
    # Node.js stack traces
    re.compile(r"at (?:Object\.|Module\.|Function\.)[^\n]{10,}"),
    # Verbose litellm/openai error chains
    re.compile(r"litellm\.[a-zA-Z.]+Error:.*?(?=\\n|$)"),
]


def _sanitize_example_dicts(
    examples: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Sanitize example dicts BEFORE JSON serialization to avoid breaking JSON.

    Replaces entire field values that are known to trigger Azure's content
    filter (permission denial messages, instruction-like text, etc.).
    """
    # Patterns that indicate the entire error_text is a permission denial
    # or instruction block that will trigger Azure's jailbreak filter
    _DENIAL_STARTS = (
        "Permission to use",
        "The user doesn't want to proceed",
    )
    _INSTRUCTION_PHRASES = (
        "malicious ways",
        "bypass the intent",
        "work around this denial",
        "work around this restriction",
        "You *should not*",
        "you should not attempt",
        "Let the user decide how to proceed",
    )

    sanitized = []
    for ex in examples:
        ex = dict(ex)  # copy
        error_text = ex.get("error_text", "")

        # Replace entire error_text if it's a permission denial
        if any(error_text.startswith(s) for s in _DENIAL_STARTS):
            tool = ex.get("tool_name", "tool")
            ex["error_text"] = f"[Permission denied for {tool}]"
        elif any(phrase in error_text for phrase in _INSTRUCTION_PHRASES):
            # Strip instruction-like content but keep the error summary
            first_sentence = error_text.split(".")[0] + "."
            ex["error_text"] = first_sentence

        # Also sanitize tool_output if present
        tool_output = ex.get("tool_output", "")
        if isinstance(tool_output, str) and any(
            phrase in tool_output for phrase in _INSTRUCTION_PHRASES
        ):
            ex["tool_output"] = "[filtered — contained instruction-like text]"

        # Strip user_message if it contains system-reminder tags
        user_msg = ex.get("user_message", "")
        if isinstance(user_msg, str) and "<system-reminder>" in user_msg:
            ex["user_message"] = "[filtered — contained system tags]"

        sanitized.append(ex)
    return sanitized


def _sanitize_field(value: str, *, aggressive: bool = False) -> str:
    """Sanitize a single string field value (not JSON).

    Applies sensitive, content-filter, and optionally aggressive patterns
    to a plain string. Safe to call on individual dict values before
    JSON serialization.
    """
    result = value
    for pattern in _SENSITIVE_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    for pattern in _CONTENT_FILTER_PATTERNS:
        result = pattern.sub("[FILTERED]", result)
    if aggressive:
        for pattern in _AGGRESSIVE_FILTER_PATTERNS:
            result = pattern.sub("[STACK_TRACE]", result)
    return result


def _sanitize_examples(examples_json: str, *, aggressive: bool = False) -> str:
    """Strip API keys, passwords, sensitive data, and content-filter triggers.

    Parses JSON, sanitizes each field individually (to avoid breaking JSON
    structure with cross-boundary regex matches), then re-serializes.
    Falls back to regex-on-string if JSON parsing fails.

    Parameters
    ----------
    examples_json:
        JSON string of error examples to sanitize.
    aggressive:
        When True, apply additional sanitization to avoid Azure content
        filter false positives (strips raw stack traces, system prompts,
        tool_output blobs, and injection-like patterns).

    Returns
    -------
    str
        Sanitized JSON string with secrets replaced by ``[REDACTED]``.
    """
    # Preferred path: parse, sanitize fields, re-serialize
    try:
        examples = json.loads(examples_json)
        if isinstance(examples, list):
            for ex in examples:
                if isinstance(ex, dict):
                    for key, val in ex.items():
                        if isinstance(val, str):
                            ex[key] = _sanitize_field(val, aggressive=aggressive)
            return json.dumps(examples, default=str)
    except (json.JSONDecodeError, TypeError):
        pass

    # Fallback: regex on raw string (less safe but better than nothing)
    result = examples_json
    for pattern in _SENSITIVE_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    for pattern in _CONTENT_FILTER_PATTERNS:
        result = pattern.sub("[FILTERED]", result)
    if aggressive:
        for pattern in _AGGRESSIVE_FILTER_PATTERNS:
            result = pattern.sub("[STACK_TRACE]", result)
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
    if raw_surface is None:
        return "claude_md_rule"
    cleaned = raw_surface.strip().lower().replace("-", "_").replace(" ", "_")
    if cleaned in _VALID_SURFACES:
        return cleaned
    # Fuzzy match: check if any valid surface is a substring (deterministic order)
    for valid in sorted(_VALID_SURFACES):
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
    from sio.suggestions.confidence import score_confidence

    # --- Create and configure LM ---
    lm = create_lm(config)
    if lm is None:
        raise RuntimeError("No LLM backend available")

    dspy.configure(lm=lm)

    # --- Prepare inputs ---
    examples = _load_dataset_examples(dataset)
    examples = _sanitize_example_dicts(examples[:20])  # pre-serialization cleanup
    examples_json = json.dumps(examples, default=str)  # cap at 20 examples
    examples_json = _sanitize_examples(examples_json)
    examples_json = _truncate_fields(examples_json, max_chars=6000)

    # --- Extract tool_input context for target determination ---
    # Collect unique tool_input values from examples so DSPy can analyze
    # what the agent was doing and determine the correct fix target
    # (skill_update vs claude_md_rule vs hook_config etc.)
    tool_inputs = []
    for ex in examples[:10]:
        ti = ex.get("tool_input")
        if ti:
            tool_inputs.append({"tool_name": ex.get("tool_name", ""), "tool_input": ti})
    tool_input_context = json.dumps(tool_inputs, default=str) if tool_inputs else "{}"
    tool_input_context = _truncate_fields(tool_input_context, max_chars=4000)

    error_type = pattern.get("error_type") or "unknown"
    # Build a concise pattern description from error_type + description + counts.
    # Audit Round 2 C-R2.6: this maps the old 4-input signature onto the new
    # PatternToRule 3-input contract (pattern_description / example_errors /
    # project_context). error_type is folded into pattern_description.
    pattern_description = (
        f"[{error_type}] Tool: {pattern.get('tool_name', 'unknown')}. "
        f"{pattern.get('description', 'Recurring error pattern')}. "
        f"{pattern.get('error_count', 0)} errors across "
        f"{pattern.get('session_count', 0)} sessions."
    )
    pattern_description = _truncate_fields(pattern_description, max_chars=500)

    # Extract 3-5 representative error messages as a list[str] (new signature's
    # `example_errors` field). Prefer error_text, fall back to context_message.
    example_errors: list[str] = []
    for ex in examples[:5]:
        msg = ex.get("error_text") or ex.get("error_message") or ex.get("context_message")
        if msg:
            example_errors.append(str(msg)[:500])
    if not example_errors:
        example_errors = [pattern.get("description", "Recurring error pattern")]

    # project_context = old tool_input_context; both describe "what the agent
    # was trying to do" context that informs the rule target.
    project_context = tool_input_context

    # --- T027: Verbose trace logging (inputs) ---
    if verbose:
        logger.info(
            "DSPy input (PatternToRule) — pattern_description=%s, "
            "example_errors=%d items, project_context_len=%d",
            pattern_description[:200],
            len(example_errors),
            len(project_context),
        )

    # --- T062: Load optimized module if available (FR-011) ---
    module = _load_optimized_or_default(config)

    try:
        result = module.forward(
            pattern_description=pattern_description,
            example_errors=example_errors,
            project_context=project_context,
        )
    except Exception as exc:
        # Check if this is an Azure content filter / jailbreak false positive
        exc_str = str(exc)
        is_content_filter = (
            "ContentPolicyViolation" in exc_str
            or "content_filter" in exc_str
            or "content management policy" in exc_str
            or "jailbreak" in exc_str
        )
        if not is_content_filter:
            raise RuntimeError(f"DSPy call failed: {exc}") from exc

        # Retry with aggressive sanitization — strip stack traces, paths, etc.
        logger.info("Azure content filter triggered — retrying with aggressive sanitization")
        example_errors_clean = [
            _truncate_fields(_sanitize_examples(m, aggressive=True), max_chars=300)
            for m in example_errors
        ]
        project_context_clean = _sanitize_examples(project_context, aggressive=True)
        project_context_clean = _truncate_fields(project_context_clean, max_chars=2000)

        try:
            result = module.forward(
                pattern_description=pattern_description,
                example_errors=example_errors_clean,
                project_context=project_context_clean,
            )
        except Exception as retry_exc:
            raise RuntimeError(
                f"DSPy call failed after aggressive sanitization: {retry_exc}"
            ) from retry_exc

    # --- Extract outputs (PatternToRule — new signature) ---
    # Audit Round 2 C-R2.6: new SuggestionGenerator produces
    # rule_title / rule_body / rule_rationale. Legacy field names
    # (prevention_instructions, rationale, target_surface) are mapped
    # for downstream-dict compat:
    #   - prevention_instructions = rule_body   (the actionable rule text)
    #   - rationale               = rule_rationale
    #   - target_surface          = derived from pattern via existing routing
    rule_title = getattr(result, "rule_title", "Improvement suggestion")
    prevention_instructions = getattr(
        result, "rule_body", "Review the error pattern."
    )
    rationale = getattr(result, "rule_rationale", "Based on observed error patterns.")
    reasoning_trace = getattr(result, "reasoning", "")

    # target_surface is no longer a DSPy output field in the new signature;
    # derive it via the existing rule-based router (generator._infer_change_type)
    # which inspects pattern.tool_name etc. This keeps downstream callers
    # (apply path, change_type filter) working unchanged.
    from sio.suggestions.generator import _infer_change_type  # noqa: PLC0415

    target_surface = _normalize_surface(_infer_change_type(pattern))

    # Warn if DSPy returned default/empty fields — low quality signal
    _DEFAULT_VALUES = {
        "Improvement suggestion",
        "Review the error pattern.",
        "Based on observed error patterns.",
    }
    if rule_title in _DEFAULT_VALUES or prevention_instructions in _DEFAULT_VALUES:
        logger.warning("DSPy returned default/empty fields — suggestion may be low quality")

    # --- T027: Verbose trace logging (outputs) ---
    if verbose:
        logger.info(
            "DSPy output — target_surface=%s, rule_title=%s, reasoning_trace=%s",
            target_surface,
            rule_title,
            (reasoning_trace[:300] if reasoning_trace else "(none)"),
        )

    # --- Build suggestion dict ---
    target_file = _SURFACE_TARGET_MAP.get(target_surface, "CLAUDE.md")
    proposed_change = _format_proposed_change(
        rule_title,
        prevention_instructions,
        target_surface,
        rationale,
    )
    pattern_confidence = score_confidence(pattern, dataset)

    # --- T054: Blend pattern confidence with quality metric ---
    from types import SimpleNamespace

    from sio.core.dspy.metrics import suggestion_quality_metric

    quality_example = SimpleNamespace(
        error_examples=examples_json,
        error_type=error_type,
        pattern_summary=pattern_summary,
        tool_name=pattern.get("tool_name", ""),
    )
    quality_pred = SimpleNamespace(
        target_surface=target_surface,
        prevention_instructions=prevention_instructions,
        rule_title=rule_title,
        rationale=rationale,
    )
    quality_score = suggestion_quality_metric(quality_example, quality_pred, trace=None)
    confidence = 0.5 * pattern_confidence + 0.5 * quality_score

    # Build description consistent with template generator
    tool_name = pattern.get("tool_name") or "unknown tool"
    error_count = int(pattern.get("error_count") or 0)
    description = f"[DSPy] {rule_title} — {tool_name}: {error_count} error(s) detected."

    return {
        "pattern_id": int(pattern["id"]),
        "pattern_str_id": pattern.get("pattern_id", ""),
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


# ---------------------------------------------------------------------------
# T068: Mode selection logic (US7)
# ---------------------------------------------------------------------------

_LOW_IMPACT_SURFACES: frozenset[str] = frozenset({"claude_md_rule", "agent_profile"})
_HIGH_IMPACT_SURFACES: frozenset[str] = frozenset(
    {
        "hook_config",
        "mcp_config",
        "settings_config",
        "project_config",
        "skill_update",
    }
)
_AUTO_CONFIDENCE_THRESHOLD: float = 0.8


def _select_mode(
    pattern: dict[str, Any],
    confidence: float,
    target_surface: str,
) -> str:
    """Select pipeline mode based on confidence and surface impact.

    Returns ``"auto"`` when confidence >= 0.8 AND the target surface is
    low-impact (claude_md_rule, agent_profile). Returns ``"hitl"`` for
    everything else, including unknown surfaces.

    Parameters
    ----------
    pattern:
        Pattern dict (currently unused but available for future heuristics).
    confidence:
        Blended confidence score in [0.0, 1.0].
    target_surface:
        Normalized target surface string.

    Returns
    -------
    str
        Either ``"auto"`` or ``"hitl"``.
    """
    if confidence >= _AUTO_CONFIDENCE_THRESHOLD and target_surface in _LOW_IMPACT_SURFACES:
        return "auto"
    return "hitl"


# ---------------------------------------------------------------------------
# T069: Automated mode flow (US7)
# ---------------------------------------------------------------------------


def generate_auto_suggestion(
    pattern: dict[str, Any],
    dataset: dict[str, Any],
    config: Any,
    *,
    verbose: bool = False,
) -> dict[str, Any] | None:
    """Generate a suggestion in fully automated mode (no human interaction).

    Calls ``generate_dspy_suggestion`` and, if successful, marks the result
    as ``auto_approved``. Returns ``None`` on any generation failure.

    Parameters
    ----------
    pattern:
        Pattern dict with standard keys.
    dataset:
        Dataset metadata dict.
    config:
        SIOConfig instance.
    verbose:
        Pass through to DSPy generator for trace logging.

    Returns
    -------
    dict | None
        Suggestion dict with ``_mode="auto"`` and ``status="auto_approved"``,
        or ``None`` if generation failed.
    """
    try:
        suggestion = generate_dspy_suggestion(
            pattern,
            dataset,
            config,
            verbose=verbose,
        )
    except Exception:
        logger.warning(
            "Auto mode: DSPy generation failed for pattern %s",
            pattern.get("pattern_id", pattern.get("id", "?")),
            exc_info=True,
        )
        return None

    suggestion["_mode"] = "auto"
    suggestion["status"] = "auto_approved"
    return suggestion


# ---------------------------------------------------------------------------
# T070: HITL (Human-in-the-Loop) mode flow (US7)
# ---------------------------------------------------------------------------


def generate_hitl_suggestion(
    pattern: dict[str, Any],
    dataset: dict[str, Any],
    config: Any,
    conn: sqlite3.Connection,
    *,
    verbose: bool = False,
    input_fn: Callable[[str], str] | None = None,
) -> dict[str, Any] | None:
    """Generate a suggestion with interactive human review at each stage.

    The flow has three pause points where the human can abort:
    1. After dataset analysis summary — continue or skip this pattern.
    2. After suggestion generation and review — continue or reject.
    3. Final approval — approve or reject the suggestion.

    Parameters
    ----------
    pattern:
        Pattern dict with standard keys.
    dataset:
        Dataset metadata dict.
    config:
        SIOConfig instance.
    conn:
        SQLite connection (for ground truth lookups if needed).
    verbose:
        Pass through to DSPy generator.
    input_fn:
        Callable that accepts a prompt string and returns user input.
        Defaults to ``input()`` for real interactive use. Pass a mock
        for testing.

    Returns
    -------
    dict | None
        Suggestion dict with ``_mode="hitl"`` and ``status="approved"``,
        or ``None`` if the user aborted at any stage or generation failed.
    """
    if input_fn is None:
        input_fn = input

    # ---- Stage 1: Dataset analysis summary ----
    summary = build_dataset_analysis_summary(pattern, dataset)
    logger.info(
        "HITL dataset summary: %d errors, %d sessions, tools=%s",
        summary["error_count"],
        summary["session_count"],
        summary["top_tools"],
    )

    response = input_fn(
        f"Dataset summary: {summary['error_count']} errors across "
        f"{summary['session_count']} sessions. Continue? [y/n] "
    )
    if response.strip().lower() != "y":
        logger.info("HITL: user declined at dataset summary stage")
        return None

    # ---- Stage 2: Generate suggestion via DSPy ----
    try:
        suggestion = generate_dspy_suggestion(
            pattern,
            dataset,
            config,
            verbose=verbose,
        )
    except Exception:
        logger.warning(
            "HITL: DSPy generation failed for pattern %s",
            pattern.get("pattern_id", pattern.get("id", "?")),
            exc_info=True,
        )
        return None

    # ---- Stage 3: Show suggestion and get review ----
    response = input_fn(
        f"Suggestion: {suggestion['rule_title']} "
        f"(confidence={suggestion['confidence']:.0%}, "
        f"target={suggestion['target_surface']}). "
        f"Continue to approval? [y/n] "
    )
    if response.strip().lower() != "y":
        logger.info("HITL: user declined at suggestion review stage")
        return None

    # ---- Stage 4: Final approval ----
    response = input_fn(f"Approve suggestion '{suggestion['rule_title']}'? [y/n] ")
    if response.strip().lower() != "y":
        logger.info("HITL: user rejected final approval")
        return None

    suggestion["_mode"] = "hitl"
    suggestion["status"] = "approved"
    return suggestion


# ---------------------------------------------------------------------------
# T073: Dataset analysis summary for HITL mode (US7)
# ---------------------------------------------------------------------------


def build_dataset_analysis_summary(
    pattern: dict[str, Any],
    dataset: dict[str, Any],
) -> dict[str, Any]:
    """Build a summary of the dataset for human review in HITL mode.

    Analyzes the dataset examples to extract:
    - Error count and session count from the pattern
    - Date range of errors
    - Top tool names
    - Top error message snippets
    - Predicted target surface based on error type

    Parameters
    ----------
    pattern:
        Pattern dict with standard keys.
    dataset:
        Dataset metadata dict with ``file_path``.

    Returns
    -------
    dict
        Summary dict with keys: ``error_count``, ``session_count``,
        ``date_range``, ``top_tools``, ``top_error_messages``,
        ``surface_prediction``.
    """
    error_count = int(pattern.get("error_count") or 0)
    session_count = int(pattern.get("session_count") or 0)

    examples = _load_dataset_examples(dataset)

    # Extract date range
    timestamps = [e.get("timestamp", "") for e in examples if e.get("timestamp")]
    if timestamps:
        sorted_ts = sorted(timestamps)
        date_range = {"earliest": sorted_ts[0], "latest": sorted_ts[-1]}
    else:
        first_seen = pattern.get("first_seen", "")
        last_seen = pattern.get("last_seen", "")
        date_range = {"earliest": first_seen, "latest": last_seen}

    # Extract top tools
    tool_counter: Counter[str] = Counter()
    for ex in examples:
        tn = ex.get("tool_name")
        if tn:
            tool_counter[tn] += 1
    # Fall back to pattern tool_name
    if not tool_counter and pattern.get("tool_name"):
        tool_counter[pattern["tool_name"]] = error_count
    top_tools = [name for name, _ in tool_counter.most_common(5)]

    # Extract top error message snippets
    error_messages: list[str] = []
    seen_prefixes: set[str] = set()
    for ex in examples:
        msg = (ex.get("error_text") or "").strip()
        if not msg:
            continue
        prefix = msg[:80].lower()
        if prefix not in seen_prefixes:
            seen_prefixes.add(prefix)
            error_messages.append(msg[:200])
        if len(error_messages) >= 5:
            break

    # Predict surface based on error type
    error_type = pattern.get("error_type") or "unknown"
    surface_map = {
        "tool_failure": "claude_md_rule",
        "user_correction": "claude_md_rule",
        "agent_admission": "claude_md_rule",
        "repeated_attempt": "hook_config",
        "undo": "settings_config",
    }
    surface_prediction = surface_map.get(error_type, "claude_md_rule")

    return {
        "error_count": error_count,
        "session_count": session_count,
        "date_range": date_range,
        "top_tools": top_tools,
        "top_error_messages": error_messages,
        "surface_prediction": surface_prediction,
    }


# ---------------------------------------------------------------------------
# T066 [US9]: SuggestionGenerator — dspy.Module (contracts/dspy-module-api.md §3)
# ---------------------------------------------------------------------------


class SuggestionGenerator(dspy.Module):
    """DSPy module that turns a clustered error pattern into a candidate rule.

    Optimizable via GEPA / MIPROv2 / BootstrapFewShot (FR-037).

    Attributes
    ----------
    DEFAULT_METRIC:
        The metric key used by the optimizer registry to evaluate this module.
    generate:
        ``dspy.ChainOfThought(PatternToRule)`` — the core predictor.

    Instrumentation (T108, FR-029)
    --------------------------------
    Each ``forward()`` call records per-run counters in the returned
    ``dspy.Prediction`` as the field ``instrumentation_json`` (a JSON string).
    The JSON object contains:

    - ``backtrack_count``:  number of DSPy assertion backtracks triggered
    - ``forward_count``:    always 1 per forward() invocation
    - ``rejection_reasons``: dict mapping stage name → reason string or None
      (stages: ``format_valid``, ``no_phi``)
    """

    DEFAULT_METRIC: str = "llm_judge_recall"

    def __init__(self) -> None:
        super().__init__()
        self.generate = dspy.ChainOfThought(PatternToRule)

    def forward(
        self,
        pattern_description: str,
        example_errors: list[str],
        project_context: str,
    ) -> dspy.Prediction:
        """Generate a rule candidate for the given error pattern.

        Parameters
        ----------
        pattern_description:
            Human-readable name / summary of the clustered error pattern.
        example_errors:
            3–5 representative error messages from the cluster.
        project_context:
            Short description of the project or platform context.

        Returns
        -------
        dspy.Prediction
            Prediction with ``rule_title``, ``rule_body``, ``rule_rationale``,
            and ``instrumentation_json`` (JSON string, T108/FR-029).
        """
        import json

        instrumentation: dict = {
            "forward_count": 1,
            "backtrack_count": 0,
            "rejection_reasons": {
                "format_valid": None,
                "no_phi": None,
            },
        }

        _MAX_RETRIES = 2
        last_format_error: str | None = None
        last_phi_error: str | None = None

        for attempt in range(_MAX_RETRIES + 1):
            context = project_context
            if attempt > 0:
                # Append correction hint from the previous attempt's failure
                hints = []
                if last_format_error:
                    hints.append(f"FORMAT CORRECTION NEEDED: {last_format_error}")
                if last_phi_error:
                    hints.append(f"PHI REMOVAL NEEDED: {last_phi_error}")
                if hints:
                    context = context + "\n\n" + "\n".join(hints)

            pred = self.generate(
                pattern_description=pattern_description,
                example_errors=example_errors,
                project_context=context,
            )

            # Stage 1: validate_rule_format
            if not validate_rule_format(pred):
                try:
                    assert_rule_format(pred)
                except Exception as exc:  # noqa: BLE001
                    last_format_error = str(exc)
                else:
                    last_format_error = "format validation failed (unknown reason)"
                instrumentation["rejection_reasons"]["format_valid"] = last_format_error
                instrumentation["backtrack_count"] += 1
                if attempt < _MAX_RETRIES:
                    continue
                raise Exception(last_format_error)  # noqa: TRY002

            last_format_error = None

            # Stage 2: validate_no_phi
            if not validate_no_phi(pred):
                try:
                    assert_no_phi(pred)
                except Exception as exc:  # noqa: BLE001
                    last_phi_error = str(exc)
                else:
                    last_phi_error = "PHI validation failed (unknown reason)"
                instrumentation["rejection_reasons"]["no_phi"] = last_phi_error
                instrumentation["backtrack_count"] += 1
                if attempt < _MAX_RETRIES:
                    continue
                raise Exception(last_phi_error)  # noqa: TRY002

            last_phi_error = None
            # Both validations passed
            break

        # Attach instrumentation to the prediction as a JSON string.
        pred.instrumentation_json = json.dumps(instrumentation)
        return pred


def generate_suggestion(
    pattern_description: str,
    example_errors: list[str],
    project_context: str,
) -> dspy.Prediction:
    """Thin module-level wrapper around SuggestionGenerator for legacy call sites.

    Instantiates a fresh (unoptimized) SuggestionGenerator and calls
    ``forward`` with the provided arguments.

    Parameters
    ----------
    pattern_description:
        Human-readable cluster name.
    example_errors:
        3–5 representative error messages.
    project_context:
        Short project or platform description.

    Returns
    -------
    dspy.Prediction
        With ``rule_title``, ``rule_body``, ``rule_rationale`` fields.
    """
    module = SuggestionGenerator()
    return module.forward(
        pattern_description=pattern_description,
        example_errors=example_errors,
        project_context=project_context,
    )
