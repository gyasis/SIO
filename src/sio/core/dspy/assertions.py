"""DSPy assertion helpers — FR-038.

All assertions use ``dspy.Assert`` (not Python ``assert``) so DSPy's
backtracking mechanism can handle retry with the corrective message.

In DSPy 3.1.3 the Assert/Suggest helpers live inside predictor context
rather than as top-level callables.  When ``dspy.Assert`` is not present
(API changed between minor releases), this module defines a lightweight
compatibility shim **and** registers it on the ``dspy`` namespace so that
test patching via ``unittest.mock.patch("dspy.Assert")`` works correctly.

Public API
----------
    assert_rule_format(pred)   — non-empty fields + ≤ 3-sentence body
    assert_no_phi(pred)        — no PHI tokens in any output field
    _PHI_TOKENS                — tuple of checked tokens (import/extend in tests)

References
----------
    contracts/dspy-module-api.md §6
    research.md R-11 (dspy.Assert API)
"""

from __future__ import annotations

import dspy

# ---------------------------------------------------------------------------
# Compatibility: ensure dspy.Assert is available for patching in tests
# ---------------------------------------------------------------------------

if not hasattr(dspy, "Assert"):

    def _assert_compat(condition: bool, msg: str = "", **kwargs) -> None:
        """Lightweight shim for dspy.Assert when not natively available.

        In production DSPy contexts, a failing Assert triggers the
        backtracking mechanism.  This shim raises ``AssertionError`` so
        that the contract is honored outside of a DSPy execution context
        (e.g., standalone unit tests that run without an active predictor).
        """
        if not condition:
            raise AssertionError(msg)

    dspy.Assert = _assert_compat  # type: ignore[attr-defined]


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


# ---------------------------------------------------------------------------
# Assertion helpers
# ---------------------------------------------------------------------------


def assert_rule_format(pred: dspy.Prediction) -> None:
    """Assert that *pred* has non-empty title/body and a ≤ 3-sentence body.

    Uses ``dspy.Assert`` so DSPy can backtrack and ask the LM to fix the
    output rather than raising an exception to the caller.

    Args:
        pred: A ``dspy.Prediction`` returned by a ``PatternToRule`` predictor.

    Side-effects:
        Triggers DSPy's backtracking mechanism (or raises ``AssertionError``
        in compatibility mode) on violation.
    """
    # Guard 1: both required fields must be non-empty
    dspy.Assert(
        bool(pred.rule_title and pred.rule_body),
        "rule_title and rule_body must both be non-empty strings. "
        "Please provide a concise rule title and a rule body of ≤ 3 sentences.",
    )

    # Guard 2: rule_body must be ≤ 3 sentences (split on '.' gives N+1 parts)
    n_parts = len(pred.rule_body.split("."))
    dspy.Assert(
        n_parts <= 4,
        "rule_body must be ≤ 3 sentences (observed: {n} sentence(s)). "
        "Rewrite the rule body to be more concise.".format(n=n_parts - 1),
    )


def assert_no_phi(pred: dspy.Prediction) -> None:
    """Assert that *pred*'s output fields contain no PHI tokens.

    Scans ``rule_title``, ``rule_body``, and ``rule_rationale`` for any
    token in :data:`_PHI_TOKENS`.  Each token triggers a separate
    ``dspy.Assert`` so DSPy can pinpoint which token caused the violation.

    Args:
        pred: A ``dspy.Prediction`` returned by a ``PatternToRule`` predictor.

    Side-effects:
        Triggers DSPy's backtracking mechanism (or raises ``AssertionError``
        in compatibility mode) on violation.
    """
    blob = f"{pred.rule_title}\n{pred.rule_body}\n{pred.rule_rationale}"
    for token in _PHI_TOKENS:
        dspy.Assert(
            token not in blob,
            f"rule body must not contain PHI token: {token!r}. "
            "Remove or anonymize any patient-identifying information.",
        )
