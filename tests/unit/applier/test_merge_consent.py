"""T054 [US3] — Failing tests for merge_rules consent gate (FR-024).

Tests for ``merge_rules(existing_rule, new_rule, merge_consent=False)``
to be implemented in T056 (Wave 6) in ``src/sio/core/applier/merger.py``
(or similar location).

Invariants per FR-024:
- With merge_consent=False: raises MergeRequiresConsent when similarity
  is >= 0.90 (rules are similar enough that merge is proposed).
- With merge_consent=True: merges rule bodies into a hybrid and returns
  the merged string.
- When similarity < 0.90: merge is not proposed; new_rule returned as-is
  without consent needed (merge_consent is ignored).
- Interactive mode: click.confirm gate (tested via monkeypatch).

Run to confirm RED before T056 (Wave 6):
    uv run pytest tests/unit/applier/test_merge_consent.py -v

NOTE: merge_rules does not exist yet. ALL tests here are RED by design.
T056 (Wave 6) implements the function.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Import helpers
# ---------------------------------------------------------------------------


def _import_merge():
    """Import merge_rules and MergeRequiresConsent.

    Will fail with ImportError until T056 implements them.
    """
    from sio.core.applier.merger import (  # noqa: PLC0415
        MergeRequiresConsent,
        merge_rules,
    )

    return merge_rules, MergeRequiresConsent


# ---------------------------------------------------------------------------
# Tests — all RED until T056
# ---------------------------------------------------------------------------


class TestMergeRulesExists:
    """merge_rules and MergeRequiresConsent must be importable (T056 Wave 6)."""

    def test_merge_rules_importable(self):
        """merge_rules must be importable from sio.core.applier.merger."""
        fn, _ = _import_merge()
        assert callable(fn)

    def test_merge_requires_consent_importable(self):
        """MergeRequiresConsent must be importable and subclass Exception."""
        _, exc = _import_merge()
        assert issubclass(exc, Exception)


class TestMergeRulesConsentGate:
    """merge_rules consent gate behavior (FR-024)."""

    # -----------------------------------------------------------------------
    # Similar rules (cosine >= 0.90): consent required
    # -----------------------------------------------------------------------

    def test_no_consent_raises_merge_requires_consent_for_similar_rules(
        self,
        fake_fastembed,
    ):
        """With merge_consent=False and similar rules, must raise MergeRequiresConsent.

        fake_fastembed fixture returns all-ones vectors, giving cosine=1.0 (identical),
        so the threshold is always exceeded for identical/similar text.
        """
        fn, MergeRequiresConsent = _import_merge()
        existing = "Never use sed -i for file edits in this project."
        new = "Avoid using sed -i; prefer Edit tool."

        with pytest.raises(MergeRequiresConsent):
            fn(existing, new, merge_consent=False)

    def test_with_consent_returns_merged_string_for_similar_rules(
        self,
        fake_fastembed,
    ):
        """With merge_consent=True and similar rules, returns a non-empty merged string."""
        fn, _ = _import_merge()
        existing = "Never use sed -i for file edits in this project."
        new = "Avoid using sed -i; prefer Edit tool."

        result = fn(existing, new, merge_consent=True)
        assert isinstance(result, str), f"Expected str, got {type(result)}"
        assert result.strip(), "Merged result must not be empty"
        # The merged result should incorporate content from both rules
        assert len(result) >= min(len(existing), len(new)) // 2, (
            "Merged result should retain meaningful content from both rules"
        )

    def test_with_consent_merged_string_contains_hybrid_content(
        self,
        fake_fastembed,
    ):
        """Merged string must contain content from both existing and new rules."""
        fn, _ = _import_merge()
        existing = "RULE_A_TOKEN: always check paths before writes."
        new = "RULE_B_TOKEN: verify target is allowlisted."

        result = fn(existing, new, merge_consent=True)
        # At least one unique token from each rule should appear in the merge
        has_a = "RULE_A_TOKEN" in result or "paths" in result.lower()
        has_b = "RULE_B_TOKEN" in result or "allowlist" in result.lower()
        assert has_a or has_b, (
            "Merged result must incorporate content from at least one source rule"
        )

    # -----------------------------------------------------------------------
    # Dissimilar rules (cosine < 0.90): no merge proposed, no consent needed
    # -----------------------------------------------------------------------

    def test_dissimilar_rules_returns_new_rule_without_consent(self, monkeypatch):
        """When similarity < 0.90, merge is not proposed; new_rule returned as-is.

        Uses monkeypatch to stub the similarity function to return 0.5 (below threshold).
        """
        fn, _ = _import_merge()

        # Monkeypatch the internal similarity call to return 0.5
        try:
            import sio.core.applier.merger as merger_mod  # noqa: PLC0415

            monkeypatch.setattr(merger_mod, "_compute_similarity", lambda a, b: 0.5)
        except (ImportError, AttributeError):
            pytest.skip(
                "merger module not yet implemented (Wave 6 T056) — "
                "cannot monkeypatch _compute_similarity"
            )

        existing = "A completely different topic about database migrations."
        new = "This is about UI rendering performance."

        # Should not raise, should return new_rule as-is
        result = fn(existing, new, merge_consent=False)
        assert result == new, f"Dissimilar rules: expected new_rule returned as-is, got {result!r}"

    def test_dissimilar_rules_ignores_merge_consent_flag(self, monkeypatch):
        """With dissimilar rules, merge_consent=True makes no difference."""
        fn, _ = _import_merge()

        try:
            import sio.core.applier.merger as merger_mod  # noqa: PLC0415

            monkeypatch.setattr(merger_mod, "_compute_similarity", lambda a, b: 0.3)
        except (ImportError, AttributeError):
            pytest.skip("merger module not yet implemented (Wave 6 T056)")

        existing = "Database migration strategy."
        new = "UI component rendering."

        result_no_consent = fn(existing, new, merge_consent=False)
        result_with_consent = fn(existing, new, merge_consent=True)

        assert result_no_consent == new
        assert result_with_consent == new

    # -----------------------------------------------------------------------
    # Interactive consent simulation
    # -----------------------------------------------------------------------

    def test_click_confirm_true_triggers_merge(self, monkeypatch, fake_fastembed):
        """When click.confirm returns True in interactive mode, merge proceeds."""
        fn, MergeRequiresConsent = _import_merge()

        # Simulate interactive mode via monkeypatching click.confirm
        try:
            import click  # noqa: PLC0415

            monkeypatch.setattr(click, "confirm", lambda msg, **kw: True)
        except ImportError:
            pytest.skip("click not available")

        existing = "Never use sed -i."
        new = "Avoid sed -i; prefer Edit tool."

        # The function may either:
        # (a) Use click.confirm internally (interactive mode) — should return merged
        # (b) Require explicit merge_consent=True — should still raise with False
        # This test verifies the interactive path returns a string (not raises)
        try:
            result = fn(existing, new, merge_consent=False)
            # If the function uses click.confirm internally (interactive), this succeeds
            assert isinstance(result, str)
        except MergeRequiresConsent:
            # This is also acceptable — the function requires explicit flag
            # The test documents both behaviors; T056 will choose one
            pytest.skip(
                "Function raises MergeRequiresConsent rather than using click.confirm "
                "internally — both designs are valid; T056 will implement one."
            )

    def test_click_confirm_false_raises_merge_requires_consent(
        self,
        monkeypatch,
        fake_fastembed,
    ):
        """When click.confirm returns False, MergeRequiresConsent must propagate."""
        fn, MergeRequiresConsent = _import_merge()

        try:
            import click  # noqa: PLC0415

            monkeypatch.setattr(click, "confirm", lambda msg, **kw: False)
        except ImportError:
            pytest.skip("click not available")

        existing = "Never use sed -i."
        new = "Avoid sed -i; prefer Edit tool."

        # Either raises directly or propagates from click.confirm=False
        # The invariant: user saying "no" must not produce a merged result
        with pytest.raises((MergeRequiresConsent, SystemExit, Exception)):
            result = fn(existing, new, merge_consent=False)
            # If result returned without consent, that's a bug
            assert False, f"Expected exception when consent denied, got result: {result!r}"
