"""DSPy assertion helpers — FR-038.

In DSPy 3.1.3, ``dspy.Assert`` / ``dspy.Suggest`` and the backtracking
mechanism were removed.  This module replaces those with pure-Python
validator functions that:

1. Return ``True``/``False`` (used as metric pre-filters in optimizers).
2. Are called inside ``SuggestionGenerator.forward`` — on failure, the
   predictor is retried (up to ``_MAX_RETRIES`` times) with a correction
   hint appended to the prompt, providing the self-healing behaviour that
   the old ``dspy.Assert`` shim could not.

Public API
----------
    validate_rule_format(pred) -> bool   — non-empty fields + ≤ 3-sentence body
    validate_no_phi(pred)     -> bool    — no PHI tokens in any output field
    assert_rule_format(pred)  -> None    — raises ValidationError on failure
    assert_no_phi(pred)       -> None    — raises ValidationError on failure
    _PHI_TOKENS               — tuple of checked tokens (import/extend in tests)

References
----------
    contracts/dspy-module-api.md §6
    data-model.md §3 (no stubs — Constitution XI)
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# PHI token list — extend via subclassing or monkeypatching in tests
# ---------------------------------------------------------------------------

_PHI_TOKENS = (
    "SSN",
    "MRN",
    "patient_id",
    "SSN:",
    "DOB:",
    "date of birth",
)

# Sentence boundary pattern: split on punctuation followed by whitespace+capital
# (avoids splitting "e.g.", "i.e.", "etc.", decimals, URLs)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


class ValidationError(ValueError):
    """Raised by assert_rule_format / assert_no_phi on contract violation."""


# ---------------------------------------------------------------------------
# Validator functions — return bool, safe to call anywhere
# ---------------------------------------------------------------------------


def validate_rule_format(pred) -> bool:
    """Return True if *pred* has non-empty title/body and a ≤ 3-sentence body.

    Args:
        pred: A ``dspy.Prediction`` returned by a ``PatternToRule`` predictor.

    Returns:
        bool — True if all format checks pass, False otherwise.
    """
    rule_title = getattr(pred, "rule_title", "") or ""
    rule_body = getattr(pred, "rule_body", "") or ""

    # Guard 1: both required fields must be non-empty
    if not (rule_title.strip() and rule_body.strip()):
        return False

    # Guard 2: rule_body must be ≤ 3 sentences.
    # Use regex sentence boundary detection to avoid splitting abbreviations.
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(rule_body.strip()) if s.strip()]
    if len(sentences) > 3:
        return False

    return True


def validate_no_phi(pred) -> bool:
    """Return True if *pred*'s output fields contain no PHI tokens (case-insensitive).

    Scans ``rule_title``, ``rule_body``, and ``rule_rationale`` for any
    token in :data:`_PHI_TOKENS`.

    Args:
        pred: A ``dspy.Prediction`` returned by a ``PatternToRule`` predictor.

    Returns:
        bool — True if no PHI tokens found, False otherwise.
    """
    rule_title = getattr(pred, "rule_title", "") or ""
    rule_body = getattr(pred, "rule_body", "") or ""
    rule_rationale = getattr(pred, "rule_rationale", "") or ""
    blob = f"{rule_title}\n{rule_body}\n{rule_rationale}".lower()
    for token in _PHI_TOKENS:
        if token.lower() in blob:
            return False
    return True


# ---------------------------------------------------------------------------
# Assert-style wrappers — raise ValidationError on failure (for call sites
# that previously caught AssertionError from the old dspy.Assert shim)
# ---------------------------------------------------------------------------


def assert_rule_format(pred) -> None:
    """Assert that *pred* passes :func:`validate_rule_format`.

    Raises:
        ValidationError: with a descriptive message on failure.
    """
    rule_body = getattr(pred, "rule_body", "") or ""
    rule_title = getattr(pred, "rule_title", "") or ""

    if not (rule_title.strip() and rule_body.strip()):
        raise ValidationError(
            "rule_title and rule_body must both be non-empty strings. "
            "Please provide a concise rule title and a rule body of ≤ 3 sentences."
        )

    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(rule_body.strip()) if s.strip()]
    if len(sentences) > 3:
        raise ValidationError(
            f"rule_body must be ≤ 3 sentences (observed: {len(sentences)} sentence(s)). "
            "Rewrite the rule body to be more concise."
        )


def assert_no_phi(pred) -> None:
    """Assert that *pred*'s output fields contain no PHI tokens.

    Raises:
        ValidationError: with the offending token on failure.
    """
    rule_title = getattr(pred, "rule_title", "") or ""
    rule_body = getattr(pred, "rule_body", "") or ""
    rule_rationale = getattr(pred, "rule_rationale", "") or ""
    blob = f"{rule_title}\n{rule_body}\n{rule_rationale}".lower()
    for token in _PHI_TOKENS:
        if token.lower() in blob:
            raise ValidationError(
                f"rule body must not contain PHI token: {token!r}. "
                "Remove or anonymize any patient-identifying information."
            )
