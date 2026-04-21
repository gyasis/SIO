"""Merge-consent gate for rule deduplication (FR-024, T056).

When ``sio apply`` is called on a rule that is highly similar to an existing
rule in the target file, a merge is *proposed* rather than a blind overwrite.

The consumer must grant consent via ``merge_consent=True`` (CLI ``--merge``)
or interactively via ``click.confirm``.

Public API
----------
    merge_rules(existing_rule, new_rule, merge_consent, interactive) -> str
    MergeRequiresConsent                                               -> Exception
    _compute_similarity(text_a, text_b)                               -> float [0, 1]
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Similarity threshold — merge proposed when cosine >= this value
# ---------------------------------------------------------------------------

_MERGE_THRESHOLD = 0.90


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class MergeRequiresConsent(Exception):
    """Raised when two rules are similar but ``merge_consent`` was not granted.

    Attributes:
        similarity: Cosine similarity between the two rules (0-1).
        existing_rule: The rule already in the target file.
        new_rule: The incoming rule to be applied.
    """

    def __init__(self, similarity: float, existing_rule: str, new_rule: str) -> None:
        self.similarity = similarity
        self.existing_rule = existing_rule
        self.new_rule = new_rule
        super().__init__(
            f"Rules are {similarity:.0%} similar — merge requires consent. "
            "Re-run with --merge to proceed."
        )


# ---------------------------------------------------------------------------
# Similarity computation (lightweight — no fastembed import required for tests)
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> set[str]:
    """Return a bag-of-words set (lowercased, alpha-only tokens)."""
    return set(re.findall(r"[a-z]+", text.lower()))


def _compute_similarity(text_a: str, text_b: str) -> float:
    """Jaccard-based text similarity as a proxy for cosine similarity.

    Uses fastembed if available (more accurate), falls back to Jaccard.
    Returns a float in [0, 1].
    """
    try:
        return _fastembed_similarity(text_a, text_b)
    except Exception:
        return _jaccard_similarity(text_a, text_b)


def _jaccard_similarity(text_a: str, text_b: str) -> float:
    tokens_a = _tokenize(text_a)
    tokens_b = _tokenize(text_b)
    if not tokens_a and not tokens_b:
        return 1.0
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = len(tokens_a & tokens_b)
    union = len(tokens_a | tokens_b)
    return intersection / union


def _fastembed_similarity(text_a: str, text_b: str) -> float:
    """Cosine similarity via the project embedder (or fastembed fallback)."""
    import numpy as np  # noqa: PLC0415

    # Try the project embedder first (also covered by fake_fastembed fixture)
    try:
        from sio.core.clustering.embedder import embed_texts  # noqa: PLC0415

        vecs = embed_texts([text_a, text_b])
        va, vb = np.array(vecs[0]), np.array(vecs[1])
    except (ImportError, AttributeError, Exception):
        # Fall back to direct fastembed
        try:
            from fastembed import TextEmbedding  # noqa: PLC0415

            model = TextEmbedding()
            vecs = list(model.embed([text_a, text_b]))
            va, vb = np.array(vecs[0]), np.array(vecs[1])
        except Exception:
            raise

    denom = np.linalg.norm(va) * np.linalg.norm(vb)
    if denom == 0:
        return 0.0
    return float(np.dot(va, vb) / denom)


# ---------------------------------------------------------------------------
# Merge logic
# ---------------------------------------------------------------------------


def _do_merge(existing_rule: str, new_rule: str) -> str:
    """Produce a hybrid rule string from two similar rules.

    Strategy: concatenate with a separator, dedup sentences, normalise.
    This is a simple baseline; Wave 10 may upgrade to LLM-assisted merge.
    """

    # Split into sentences and deduplicate while preserving order
    def _sentences(text: str) -> list[str]:
        parts = re.split(r"(?<=[.!?])\s+", text.strip())
        return [p.strip() for p in parts if p.strip()]

    existing_sentences = _sentences(existing_rule)
    new_sentences = _sentences(new_rule)

    seen: set[str] = set()
    merged: list[str] = []
    for s in existing_sentences + new_sentences:
        key = s.lower()
        if key not in seen:
            seen.add(key)
            merged.append(s)

    return " ".join(merged)


def merge_rules(
    existing_rule: str,
    new_rule: str,
    merge_consent: bool = False,
    interactive: bool = False,
) -> str:
    """Gate merging of two similar rules behind explicit consent.

    Args:
        existing_rule: The rule currently in the target file.
        new_rule: The incoming rule to be applied.
        merge_consent: True if the ``--merge`` CLI flag was passed.
        interactive: If True, prompt via ``click.confirm`` when similarity
            is high but ``merge_consent`` is False.  Not used in headless mode.

    Returns:
        The merged rule string if consent is granted.
        ``new_rule`` as-is if similarity < ``_MERGE_THRESHOLD`` (no merge needed).

    Raises:
        MergeRequiresConsent: When similarity >= threshold and consent was not
            granted (either ``merge_consent=False`` and ``interactive=False``,
            or the interactive prompt was declined).
    """
    sim = _compute_similarity(existing_rule, new_rule)
    logger.debug(
        "merge_rules similarity=%.3f threshold=%.3f consent=%s",
        sim,
        _MERGE_THRESHOLD,
        merge_consent,
    )

    # Below threshold — no merge needed; return new rule verbatim
    if sim < _MERGE_THRESHOLD:
        return new_rule

    # Above threshold — consent required
    if merge_consent:
        return _do_merge(existing_rule, new_rule)

    if interactive:
        try:
            import click  # noqa: PLC0415

            confirmed = click.confirm(
                f"The new rule is {sim:.0%} similar to an existing rule. Merge them?",
                default=False,
            )
            if confirmed:
                return _do_merge(existing_rule, new_rule)
        except ImportError:
            pass  # click not available — fall through to raise

    raise MergeRequiresConsent(sim, existing_rule, new_rule)
