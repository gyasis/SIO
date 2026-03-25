"""sio.suggestions.refiner — Second-pass refinement of generic suggestions.

Takes a template-generated suggestion + raw error examples and produces a
SPECIFIC, actionable rule by extracting exact parameter names, values, and
syntax from the error content.

Works WITHOUT DSPy — uses direct Anthropic or OpenAI API calls.
Falls back gracefully to the original suggestion if no LLM is available.

Public API
----------
    refine_suggestion(generic_rule: str, error_samples: list[str], tool_name: str) -> str | None
    is_refinement_available() -> bool
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Forbidden phrases — if the LLM returns these, it was lazy
# ---------------------------------------------------------------------------

_FORBIDDEN_PHRASES = [
    "verify that",
    "make sure",
    "be careful",
    "ensure inputs",
    "ensure the target",
    "verify the target exists",
    "verify inputs are valid",
    "check that the",
    "always validate",
    "properly configure",
    "double-check",
    "take care to",
]

_MIN_REFINED_LENGTH = 40  # too short = probably useless
_MAX_REFINED_LENGTH = 800  # too long = probably rambling


def _count_forbidden(text: str) -> int:
    """Count how many forbidden phrases appear in the text."""
    lower = text.lower()
    return sum(1 for phrase in _FORBIDDEN_PHRASES if phrase in lower)


def _quality_check(refined: str, original: str) -> bool:
    """Check if the refined rule is actually better than the original.

    Returns True if the refined rule passes quality checks.
    """
    if not refined or not refined.strip():
        return False

    # Too short
    if len(refined.strip()) < _MIN_REFINED_LENGTH:
        logger.debug("Refined rule too short (%d chars)", len(refined.strip()))
        return False

    # Too many forbidden phrases
    forbidden_count = _count_forbidden(refined)
    if forbidden_count >= 2:
        logger.debug("Refined rule has %d forbidden phrases", forbidden_count)
        return False

    # Must be meaningfully different from original
    if refined.strip() == original.strip():
        return False

    return True


# ---------------------------------------------------------------------------
# LLM refinement — direct API call (no DSPy)
# ---------------------------------------------------------------------------

_REFINE_SYSTEM_PROMPT = """\
You are a Senior SRE writing machine-actionable rules for an AI coding assistant's config file.

Your job: Transform a GENERIC rule into a SPECIFIC technical constraint using actual error logs.

RULES FOR YOUR OUTPUT:
1. Extract EXACT parameter names, constant values, or syntax patterns from the error logs
2. Write the rule so a developer can check compliance in 5 seconds by looking at code
3. NEVER use: "ensure", "verify", "be careful", "make sure", "properly", "double-check"
4. ALWAYS use: specific parameter names, correct values, exact syntax examples
5. Include a WRONG vs RIGHT example when the error shows a parameter/value mismatch
6. Keep it under 5 lines. Concise beats thorough.
7. Format: "[TOOL] RULE: [action]. WRONG: [example]. RIGHT: [example]. WHY: [1 sentence]."
"""

_REFINE_USER_TEMPLATE = """\
Generic rule to improve:
{generic_rule}

Tool name: {tool_name}

Actual error logs (extract the specific fix from these):
{error_logs}

Write the specific, machine-actionable rule:"""


def _try_anthropic(prompt: str, system: str) -> str | None:
    """Try to refine using the Anthropic API directly."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=300,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text
    except Exception as exc:
        logger.debug("Anthropic refinement failed: %s", exc)
        return None


def _try_openai(prompt: str, system: str) -> str | None:
    """Try to refine using the OpenAI-compatible API."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None

    try:
        import openai

        client = openai.OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=300,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        )
        return response.choices[0].message.content
    except Exception as exc:
        logger.debug("OpenAI refinement failed: %s", exc)
        return None


def _try_gemini(prompt: str, system: str) -> str | None:
    """Try to refine using the Google Gemini API."""
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return None

    try:
        import google.generativeai as genai

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            "gemini-2.0-flash",
            system_instruction=system,
        )
        response = model.generate_content(prompt)
        return response.text
    except Exception as exc:
        logger.debug("Gemini refinement failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Variable extraction — deterministic pre-refinement
# ---------------------------------------------------------------------------

def _extract_variables(error_samples: list[str]) -> dict[str, str]:
    """Extract specific variable/parameter info from error messages.

    This is the deterministic pre-pass that pulls out concrete values
    even without an LLM.
    """
    variables: dict[str, str] = {}

    for sample in error_samples:
        # Extract parameter name mismatches
        # Formats: "group_id\n  Unexpected keyword argument" or "'group_id' Unexpected"
        match = re.search(
            r"(\w+)\s+[Uu]nexpected keyword argument", sample
        )
        if not match:
            match = re.search(
                r"[Uu]nexpected keyword argument.*?['\"]?(\w+)['\"]?", sample
            )
        if match:
            variables["wrong_param"] = match.group(1)

        # Extract "Input should be a valid list/number/string"
        # Formats: "entity_types\n  Input should be a valid list [type=list_type, input_value=..."
        match = re.search(
            r"(\w+)\s+Input should be a valid (\w+)", sample
        )
        if match:
            variables["field_name"] = match.group(1)
            variables["expected_type"] = match.group(2)
        if not match:
            match = re.search(
                r"Input should be a valid (\w+).*?input_value=([^\]]+)", sample
            )
            if match:
                variables["expected_type"] = match.group(1)
                variables["actual_value"] = match.group(2).strip("'\"")

        # Extract file size/token errors
        match = re.search(
            r"(\d+)\s*tokens?\)?\s*exceeds\s*maximum.*?(\d+)", sample
        )
        if match:
            variables["actual_tokens"] = match.group(1)
            variables["max_tokens"] = match.group(2)

        # Also catch "exceeds maximum allowed size"
        match = re.search(
            r"(\d+(?:\.\d+)?(?:MB|KB|GB))\)\s*exceeds\s*maximum.*?(\d+(?:\.\d+)?(?:MB|KB|GB))",
            sample,
        )
        if match:
            variables["actual_size"] = match.group(1)
            variables["max_size"] = match.group(2)

        # Extract HTTP status codes
        match = re.search(r"Status:\s*(\d{3})", sample)
        if match:
            variables["http_status"] = match.group(1)

        # Extract specific IDs that caused errors
        match = re.search(
            r"[Cc]loud [Ii][Dd]:\s*([a-f0-9-]{20,})", sample
        )
        if match:
            variables["wrong_id"] = match.group(1)

        # Extract "Failed to fetch" targets
        match = re.search(
            r"Failed to fetch.*?for.*?:\s*(.+?)[\.\s]", sample
        )
        if match:
            variables["failed_target"] = match.group(1).strip()

        # Extract validation error call context: "for call[search_nodes]"
        match = re.search(r"for call\[(\w+)\]", sample)
        if match:
            variables["function_name"] = match.group(1)

    return variables


def _build_deterministic_refinement(
    generic_rule: str,
    tool_name: str,
    variables: dict[str, str],
) -> str | None:
    """Build a refined rule using extracted variables, no LLM needed.

    Returns None if not enough variables were extracted.
    """
    parts: list[str] = []

    if "wrong_param" in variables:
        func = variables.get("function_name", tool_name)
        parts.append(
            f"`{func}` does NOT accept `{variables['wrong_param']}` as a parameter. "
            f"Check the API — the correct parameter name may be "
            f"`{variables['wrong_param']}s` (plural) or a different field entirely."
        )

    if "expected_type" in variables:
        field = variables.get("field_name", "parameter")
        actual = variables.get("actual_value", "?")
        expected = variables["expected_type"]
        parts.append(
            f"`{field}` must be a {expected}, not a string. "
            f"WRONG: `\"{actual}\"` RIGHT: `{_guess_correct_format(actual, expected)}`"
        )

    if "actual_tokens" in variables and "max_tokens" in variables:
        parts.append(
            f"Files exceeding {variables['max_tokens']} tokens MUST use "
            f"`offset` and `limit` parameters. "
            f"Default: `limit=200` for initial scan."
        )

    if "wrong_id" in variables:
        parts.append(
            f"ID `{variables['wrong_id']}` is invalid/unauthorized. "
            f"Use the correct ID from project constants."
        )

    if not parts:
        return None

    header = f"### {tool_name} — Specific Fix"
    return header + "\n" + "\n".join(f"- {p}" for p in parts)


def _guess_correct_format(value: str, expected_type: str) -> str:
    """Guess the correct format for a value given its expected type."""
    if expected_type == "list":
        # If it looks like a JSON string of a list, unwrap it
        if value.startswith("["):
            return value
        return f'["{value}"]'
    if expected_type == "number":
        try:
            return str(int(value))
        except ValueError:
            return value
    return value


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def is_refinement_available() -> bool:
    """Check if any LLM API is available for refinement."""
    return any([
        os.environ.get("ANTHROPIC_API_KEY"),
        os.environ.get("OPENAI_API_KEY"),
        os.environ.get("GEMINI_API_KEY"),
        os.environ.get("GOOGLE_API_KEY"),
    ])


def refine_suggestion(
    generic_rule: str,
    error_samples: list[str],
    tool_name: str = "unknown",
) -> str:
    """Refine a generic template suggestion into a specific, actionable rule.

    Pipeline:
    1. Extract variables deterministically from error messages
    2. If LLM available: send generic rule + errors to LLM for refinement
    3. Quality-check the LLM output (forbidden phrases, length)
    4. Fall back to deterministic refinement or original if LLM fails

    Parameters
    ----------
    generic_rule:
        The template-generated suggestion text.
    error_samples:
        List of raw error message strings from the pattern's examples.
    tool_name:
        Name of the tool that failed (e.g., "Bash", "Read", "search_nodes").

    Returns
    -------
    str
        The refined rule if quality checks pass, otherwise the original.
    """
    # Step 1: Deterministic variable extraction
    variables = _extract_variables(error_samples)
    logger.debug("Extracted variables: %s", variables)

    # Step 2: Try LLM refinement if available
    if is_refinement_available():
        error_logs = "\n".join(f"- {s[:300]}" for s in error_samples[:5])
        prompt = _REFINE_USER_TEMPLATE.format(
            generic_rule=generic_rule,
            tool_name=tool_name,
            error_logs=error_logs,
        )

        # Try providers in order of preference
        refined = (
            _try_anthropic(prompt, _REFINE_SYSTEM_PROMPT)
            or _try_gemini(prompt, _REFINE_SYSTEM_PROMPT)
            or _try_openai(prompt, _REFINE_SYSTEM_PROMPT)
        )

        if refined and _quality_check(refined, generic_rule):
            logger.info("LLM refinement passed quality check")
            return refined
        elif refined:
            logger.info("LLM refinement failed quality check, trying deterministic")

    # Step 3: Deterministic refinement from extracted variables
    deterministic = _build_deterministic_refinement(
        generic_rule, tool_name, variables
    )
    if deterministic and _quality_check(deterministic, generic_rule):
        logger.info("Deterministic refinement produced usable output")
        return deterministic

    # Step 4: Return original (no refinement possible)
    logger.debug("No refinement possible, returning original")
    return generic_rule
