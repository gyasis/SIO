"""sio.core.dspy.metrics -- Quality scoring metrics for DSPy suggestions.

Contains two distinct APIs:

1. METRIC_REGISTRY (contracts/dspy-module-api.md §5) — FR-018
   - METRIC_REGISTRY: dict mapping name -> callable
   - @register(name) decorator
   - exact_match(gold, pred, trace=None) -> bool
   - embedding_similarity(gold, pred, trace=None) -> float   (fastembed cosine)
   - llm_judge_recall(gold, pred, trace=None) -> float       (dspy.Predict judge)

2. suggestion_quality_metric(example, pred, trace=None) -> float | bool
   Scores suggestions on three axes:
   - Specificity (0.35): Does prevention_instructions reference concrete details?
   - Actionability (0.35): Does it contain action verbs, file paths, commands?
   - Surface accuracy (0.30): Is target_surface appropriate for the error_type?

Public API
----------
    METRIC_REGISTRY: dict[str, callable]
    register(name: str) -> decorator
    exact_match(gold, pred, trace=None) -> bool
    embedding_similarity(gold, pred, trace=None) -> float
    llm_judge_recall(gold, pred, trace=None) -> float
    suggestion_quality_metric(example, pred, trace=None) -> float | bool

When ``trace is not None`` (DSPy optimization mode), returns ``bool``.
When ``trace is None`` (standalone evaluation), returns ``float`` in [0, 1].
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# §5 Metric Registry (contracts/dspy-module-api.md §5, FR-018)
# ---------------------------------------------------------------------------

METRIC_REGISTRY: dict[str, Any] = {}


def register(name: str):
    """Decorator that adds the decorated function to METRIC_REGISTRY.

    Usage::

        @register("my_metric")
        def my_metric(gold, pred, trace=None):
            ...

    Returns the function unchanged so it can also be called directly.
    """

    def decorator(fn):
        METRIC_REGISTRY[name] = fn
        return fn

    return decorator


@register("exact_match")
def exact_match(gold: Any, pred: Any, trace: Any = None) -> bool:
    """Return True when gold.label == pred.label (both must have the attribute).

    Args:
        gold: dspy.Example with a ``label`` attribute.
        pred: dspy.Prediction with a ``label`` attribute.
        trace: Ignored; present for DSPy metric contract.

    Returns:
        True if labels match, False otherwise (including missing attribute).
    """
    gold_label = getattr(gold, "label", None)
    pred_label = getattr(pred, "label", None)
    if gold_label is None or pred_label is None:
        return False
    return gold_label == pred_label


@register("embedding_similarity")
def embedding_similarity(gold: Any, pred: Any, trace: Any = None) -> float:
    """Cosine similarity between gold.rule_body and pred.rule_body via fastembed.

    Uses lazy import so tests can stub fastembed via the ``fake_fastembed``
    conftest fixture.  Falls back to any string attribute if ``rule_body``
    is absent.  Returns raw cosine similarity in [0.0, 1.0] — no threshold.

    Args:
        gold: dspy.Example with ``rule_body`` (or any string attribute).
        pred: dspy.Prediction with ``rule_body``.
        trace: Ignored; present for DSPy metric contract.

    Returns:
        Float cosine similarity in [0.0, 1.0].
    """
    gold_text = _get_text(gold)
    pred_text = _get_text(pred)

    if not gold_text or not pred_text:
        return 0.0

    # Lazy import so tests can stub with fake_fastembed fixture
    try:
        from fastembed import TextEmbedding  # type: ignore[import]

        embedder = TextEmbedding()
        vecs = list(embedder.embed([gold_text, pred_text]))
    except (ImportError, Exception):
        # Fall back to simple text-overlap similarity when fastembed unavailable
        return _text_overlap(gold_text, pred_text)

    import numpy as np

    g_vec = np.array(vecs[0], dtype=np.float32)
    p_vec = np.array(vecs[1], dtype=np.float32)

    g_norm = np.linalg.norm(g_vec)
    p_norm = np.linalg.norm(p_vec)
    if g_norm == 0.0 or p_norm == 0.0:
        return 0.0

    cosine = float(np.dot(g_vec, p_vec) / (g_norm * p_norm))
    # Clamp to [0, 1] (cosine can be slightly outside due to float precision)
    return max(0.0, min(1.0, cosine))


def _get_text(obj: Any) -> str:
    """Extract a string from ``rule_body`` or the first available string attr."""
    for attr in ("rule_body", "prevention_instructions", "rule_title", "text"):
        val = getattr(obj, attr, None)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _text_overlap(a: str, b: str) -> float:
    """Simple token-overlap similarity as fastembed fallback."""
    a_tokens = set(a.lower().split())
    b_tokens = set(b.lower().split())
    if not a_tokens or not b_tokens:
        return 0.0
    return len(a_tokens & b_tokens) / max(len(a_tokens), len(b_tokens))


# Lazy-initialized LLM judge predictor (one instance reused across calls)
_JUDGE_PREDICTOR: Any = None


@register("llm_judge_recall")
def llm_judge_recall(gold: Any, pred: Any, trace: Any = None) -> float:
    """Score how well pred captures the same preventive intent as gold.

    Uses a cached ``dspy.Predict(RuleRecallScore)`` instance — lazily
    initialized on first call to avoid DSPy global config requirements at
    import time.

    Args:
        gold: dspy.Example with ``rule_body``.
        pred: dspy.Prediction with ``rule_body``.
        trace: Ignored; present for DSPy metric contract.

    Returns:
        Float recall score in [0.0, 1.0] from RuleRecallScore.score.
    """
    global _JUDGE_PREDICTOR

    gold_text = _get_text(gold)
    pred_text = _get_text(pred)

    if not gold_text or not pred_text:
        return 0.0

    # Lazy init the judge predictor
    if _JUDGE_PREDICTOR is None:
        import dspy  # type: ignore[import]

        from sio.core.dspy.signatures import RuleRecallScore  # noqa: PLC0415

        _JUDGE_PREDICTOR = dspy.Predict(RuleRecallScore)

    result = _JUDGE_PREDICTOR(gold_rule=gold_text, candidate_rule=pred_text)
    raw_score = getattr(result, "score", 0.0)
    try:
        score = float(raw_score)
    except (TypeError, ValueError):
        score = 0.0
    return max(0.0, min(1.0, score))


# ---------------------------------------------------------------------------
# Constants (suggestion_quality_metric below)
# ---------------------------------------------------------------------------

_ACTION_VERBS: frozenset[str] = frozenset(
    {
        "run",
        "check",
        "verify",
        "add",
        "remove",
        "configure",
        "set",
        "use",
        "install",
        "create",
        "delete",
    }
)

# Mapping from error_type -> set of expected target surfaces
_ERROR_TYPE_SURFACE_MAP: dict[str, frozenset[str]] = {
    "tool_failure": frozenset({"claude_md_rule", "skill_update"}),
    "user_correction": frozenset({"claude_md_rule", "agent_profile"}),
    "agent_admission": frozenset({"claude_md_rule"}),
    "repeated_attempt": frozenset({"claude_md_rule", "hook_config"}),
    "undo": frozenset({"claude_md_rule", "settings_config"}),
}

# MCP-related errors -> these surfaces
_MCP_SURFACES: frozenset[str] = frozenset({"mcp_config", "settings_config"})

# Hook-related errors -> these surfaces
_HOOK_SURFACES: frozenset[str] = frozenset({"hook_config"})

# File-path-like pattern: contains / or common extensions
_FILE_PATH_RE: re.Pattern[str] = re.compile(
    r"(?:/[\w.\-]+)+|[\w.\-]+\.(?:py|js|ts|json|yaml|yml|md|sh|toml|cfg|ini)"
)

# Backtick code block pattern
_BACKTICK_RE: re.Pattern[str] = re.compile(r"`[^`]+`")


# ---------------------------------------------------------------------------
# Sub-scorers
# ---------------------------------------------------------------------------


def _extract_details_from_examples(error_examples_json: str) -> set[str]:
    """Extract concrete detail tokens (tool names, error snippets) from examples JSON.

    Parameters
    ----------
    error_examples_json:
        JSON string of error examples array.

    Returns
    -------
    set[str]
        Lowercased detail tokens extracted from the examples.
    """
    details: set[str] = set()
    try:
        examples = json.loads(error_examples_json)
    except (json.JSONDecodeError, TypeError):
        return details

    if isinstance(examples, list):
        items = examples
    elif isinstance(examples, dict):
        items = examples.get("examples", [])
    else:
        return details

    for ex in items:
        if not isinstance(ex, dict):
            continue
        # Tool name is a strong detail signal
        tool_name = ex.get("tool_name", "")
        if tool_name:
            details.add(tool_name.lower())
        # Extract meaningful words from error_text (3+ chars, not stopwords)
        error_text = ex.get("error_text", "")
        for word in re.findall(r"[A-Za-z_]{3,}", error_text):
            details.add(word.lower())

    return details


# Audit Round 2 N-R2D.2 (Hunter #2, DSPy): the suggestion metric must read
# whatever fields the current SuggestionGenerator emits. Per C-R2.6, the
# canonical PatternToRule signature is:
#     inputs  : pattern_description (str), example_errors (list[str]),
#               project_context (str)
#     outputs : rule_title, rule_body, rule_rationale
# The sub-scorers below prefer the new field names but fall back to the
# legacy ones so the metric stays usable for historical examples and
# during the transition.


def _rule_text(pred: Any) -> str:
    """Return the rule-body text from pred, new field first, legacy fallback."""
    body = getattr(pred, "rule_body", None)
    if body:
        return str(body)
    # Legacy 4-output signature fallback
    return str(getattr(pred, "prevention_instructions", "") or "")


def _example_error_texts(example: Any) -> list[str]:
    """Return a list of error message strings from example (new or old shape)."""
    # New: example.example_errors is list[str]
    new_list = getattr(example, "example_errors", None)
    if isinstance(new_list, list):
        return [str(x) for x in new_list if x]
    # Legacy: example.error_examples is JSON string
    examples_json = getattr(example, "error_examples", "[]")
    details = _extract_details_from_examples(examples_json)
    # Return the detail set as a synthetic list for consistent downstream use
    return [d for d in details if d]


def _score_specificity(example: Any, pred: Any) -> float:
    """Score how specifically the rule body references details from the example.

    Uses the new PatternToRule shape (example.example_errors list[str] +
    pred.rule_body) with legacy-shape fallback. Builds a set of "details"
    (tool names, significant words) from the error messages and measures
    how many appear in the rule body.
    """
    details: set[str] = set()

    # Extract tool_name tokens + significant words from error messages
    error_texts = _example_error_texts(example)
    for text in error_texts:
        for word in re.findall(r"[A-Za-z_]{3,}", text):
            details.add(word.lower())

    # Also mine tool_name if present on the example
    tool = _get_tool_name_from_example(example)
    if tool:
        details.add(tool.lower())

    if not details:
        return 0.5  # neutral when we can't extract details

    body = _rule_text(pred).lower()
    if not body:
        return 0.0

    matched = sum(1 for d in details if d in body)
    return min(matched / len(details), 1.0)


def _score_actionability(pred: Any) -> float:
    """Score how actionable the rule body is.

    Checks for:
    - Concrete action verbs
    - File paths
    - Code/command references (backticks)

    Reads pred.rule_body (new) with pred.prevention_instructions fallback.
    """
    body = _rule_text(pred)
    if not body:
        return 0.0

    lower_body = body.lower()
    words = set(re.findall(r"[a-z]+", lower_body))

    # Sub-signal 1: action verbs (0-1, based on count, capped at 3)
    verb_hits = len(_ACTION_VERBS & words)
    verb_score = min(verb_hits / 3.0, 1.0)

    # Sub-signal 2: file paths present
    has_paths = 1.0 if _FILE_PATH_RE.search(body) else 0.0

    # Sub-signal 3: backtick code references
    has_code = 1.0 if _BACKTICK_RE.search(body) else 0.0

    # Weighted combination: verbs most important, paths and code equally
    return 0.50 * verb_score + 0.25 * has_paths + 0.25 * has_code


def _score_surface_accuracy(example: Any, pred: Any) -> float:
    """Score whether the rule text matches the expected target surface.

    PatternToRule doesn't emit a target_surface field (routing is delegated
    to the code side). We therefore derive target_surface HEURISTICALLY
    from the rule text (keywords for hook / skill / mcp / claude_md) and
    compare against the error-type routing map.

    Legacy-shape fallback: if pred.target_surface is present (old
    SuggestionGenerator output), use it directly.
    """
    # Legacy path: explicit target_surface field present
    legacy_target = getattr(pred, "target_surface", None)
    if legacy_target:
        target_surface = legacy_target
    else:
        # Infer from rule body text keywords
        body = _rule_text(pred).lower()
        if "hook" in body:
            target_surface = "hook_config"
        elif "skill" in body or "prompt" in body:
            target_surface = "skill_update"
        elif "mcp" in body:
            target_surface = "mcp_config"
        elif "settings" in body:
            target_surface = "settings_config"
        elif "agent" in body or "profile" in body:
            target_surface = "agent_profile"
        else:
            target_surface = "claude_md_rule"

    # error_type: new shape embeds it in pattern_description prefix
    # ("[error_type] ...") or legacy explicit field
    error_type = getattr(example, "error_type", None)
    if not error_type:
        pd = getattr(example, "pattern_description", "")
        m = re.match(r"\s*\[([a-z_]+)\]", pd)
        error_type = m.group(1) if m else "unknown"

    # Check MCP-related: if tool_name contains "mcp"
    tool_name = _get_tool_name_from_example(example) or ""
    if "mcp" in tool_name.lower():
        if target_surface in _MCP_SURFACES:
            return 1.0
        if target_surface == "claude_md_rule":
            return 0.5
        return 0.0

    # Check hook-related: if tool_name contains "hook"
    if "hook" in tool_name.lower():
        if target_surface in _HOOK_SURFACES:
            return 1.0
        if target_surface == "claude_md_rule":
            return 0.5
        return 0.0

    # Standard error_type -> surface mapping
    expected_surfaces = _ERROR_TYPE_SURFACE_MAP.get(error_type)
    if expected_surfaces is None:
        if target_surface == "claude_md_rule":
            return 0.5
        return 0.0

    if target_surface in expected_surfaces:
        return 1.0

    if target_surface == "claude_md_rule":
        return 0.5

    return 0.0


def _get_tool_name_from_example(example: Any) -> str:
    """Extract tool_name from an example, checking both attributes and JSON."""
    # Try direct attribute first (pattern_summary may have it)
    tool_name = getattr(example, "tool_name", "")
    if tool_name:
        return tool_name

    # Try to extract from error_examples JSON
    examples_json = getattr(example, "error_examples", "[]")
    try:
        examples = json.loads(examples_json)
    except (json.JSONDecodeError, TypeError):
        return ""

    if isinstance(examples, list) and examples:
        return examples[0].get("tool_name", "")
    if isinstance(examples, dict):
        items = examples.get("examples", [])
        if items:
            return items[0].get("tool_name", "")
    return ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def suggestion_quality_metric(
    example: Any,
    pred: Any,
    trace: Any = None,
) -> float | bool:
    """Score a DSPy suggestion on specificity, actionability, and surface accuracy.

    Reads the PatternToRule-shaped fields by default, with legacy-shape
    fallback so the metric stays functional for historical examples.

    Parameters
    ----------
    example:
        dspy.Example-like object.
        NEW: ``pattern_description`` (str, may embed [error_type] prefix),
             ``example_errors`` (list[str]), ``project_context`` (str).
        LEGACY: ``error_examples`` (JSON str), ``error_type``, ``pattern_summary``.
    pred:
        dspy.Example-like object.
        NEW: ``rule_title``, ``rule_body``, ``rule_rationale``.
        LEGACY: ``target_surface``, ``rule_title``, ``prevention_instructions``,
                ``rationale``.
    trace:
        When not None (DSPy optimization), returns ``bool(score > 0.5)``.
        When None (standalone evaluation), returns ``float``.

    Returns
    -------
    float | bool
        Quality score in [0.0, 1.0] or bool for DSPy optimization.
    """
    specificity = _score_specificity(example, pred)
    actionability = _score_actionability(pred)
    surface_accuracy = _score_surface_accuracy(example, pred)

    score = 0.35 * specificity + 0.35 * actionability + 0.30 * surface_accuracy
    score = max(0.0, min(score, 1.0))

    if trace is not None:
        return score > 0.5

    return score
