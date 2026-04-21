"""T097 [US7] — Deterministic slug tests (FR-014, R-5).

Tests confirm that cluster slugs are stable across different input orderings.

- Clustering the same errors twice (reverse, random shuffle) must produce
  identical ``pattern_id`` values per cluster.
- Adding one new error must not change the slug of a previously-stable cluster.
- Slug format matches ``^[a-z_]+_[0-9a-f]{10}$``.

These tests are EXPECTED RED until T098 (Wave 10) rewrites the slug algorithm.

Run to confirm RED:
    uv run pytest tests/unit/clustering/test_deterministic_slugs.py -v
"""

from __future__ import annotations

import random
import re

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# fake_fastembed fixture — deterministic embeddings for test isolation
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fake_fastembed(monkeypatch):
    """Monkeypatch FastEmbedBackend.encode with semantically-grouped deterministic vectors.

    Errors sharing the same prefix (before ``:``) get vectors extremely close
    together (cos-sim > 0.999). Errors with different prefixes get strictly
    orthogonal base vectors (cos-sim < 0.01), ensuring independent clusters
    at threshold=0.50.

    Uses a fixed 2D plane per group index rather than random seeding, so
    inter-group similarity is guaranteed near 0.
    """
    import hashlib  # noqa: PLC0415

    from sio.clustering import pattern_clusterer  # noqa: PLC0415

    DIM = 64
    # Fixed orthogonal basis vectors: group N gets a 1 in position N, 0 elsewhere.
    # We map each prefix to an index and construct a high-dimensional one-hot
    # with tiny noise so the centroid is stable regardless of member count.
    _prefix_to_idx: dict[str, int] = {}
    _next_idx = [0]

    def _group_idx(prefix: str) -> int:
        if prefix not in _prefix_to_idx:
            idx = _next_idx[0] % DIM
            _prefix_to_idx[prefix] = idx
            _next_idx[0] += 1
        return _prefix_to_idx[prefix]

    class FakeBackend:
        def encode(self, texts: list[str]) -> np.ndarray:
            vecs = []
            for t in texts:
                # Extract prefix (e.g. "tool_failure" from "tool_failure: Bash exit code 1")
                prefix = t.split(":")[0].strip().lower() if ":" in t else t.strip().lower()
                idx = _group_idx(prefix)
                # One-hot base vector
                base = np.zeros(DIM, dtype=np.float32)
                base[idx] = 1.0
                # Add tiny deterministic noise (scale 1e-4) for per-text variation
                noise_seed = int(hashlib.md5(t.encode()).hexdigest(), 16) % (2**32)
                noise_rng = np.random.default_rng(noise_seed)
                noise = noise_rng.random(DIM).astype(np.float32) * 1e-4
                v = base + noise
                v /= np.linalg.norm(v) + 1e-9
                vecs.append(v)
            return np.array(vecs, dtype=np.float32)

    monkeypatch.setattr(pattern_clusterer, "_backend", FakeBackend())
    return FakeBackend()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SLUG_RE = re.compile(r"^[a-z_]+_[0-9a-f]{10}$")

_TOOL_FAILURE_TEXTS = [
    "tool_failure: Bash exit code 1",
    "tool_failure: Bash exit code 2",
    "tool_failure: Bash exit code 3",
]

_PARSE_ERROR_TEXTS = [
    "json_parse_error: unexpected token",
    "json_parse_error: missing comma",
    "json_parse_error: unterminated string",
]

_TIMEOUT_TEXTS = [
    "timeout: command exceeded 30s limit",
    "timeout: command exceeded 60s limit",
]


def _make_errors(texts: list[str], base_id: int = 0) -> list[dict]:
    return [
        {
            "id": base_id + i,
            "session_id": f"sess_{base_id + i:03d}",
            "error_text": t,
            "tool_name": "Bash",
            "timestamp": f"2026-04-{20 + i:02d}T10:00:00Z",
        }
        for i, t in enumerate(texts)
    ]


def _slug_set(patterns: list[dict]) -> set[str]:
    return {p["pattern_id"] for p in patterns}


# ---------------------------------------------------------------------------
# T097-1: Same errors in reverse order → identical slug set
# ---------------------------------------------------------------------------


def test_slug_stable_across_reversed_input():
    """Reversing input order must not change the set of pattern_ids."""
    from sio.clustering.pattern_clusterer import cluster_errors  # noqa: PLC0415

    errors = _make_errors(_TOOL_FAILURE_TEXTS, base_id=0) + _make_errors(
        _PARSE_ERROR_TEXTS, base_id=10
    )
    errors_rev = list(reversed(errors))

    patterns_fwd = cluster_errors(errors, threshold=0.50)
    patterns_rev = cluster_errors(errors_rev, threshold=0.50)

    slugs_fwd = _slug_set(patterns_fwd)
    slugs_rev = _slug_set(patterns_rev)

    assert slugs_fwd == slugs_rev, (
        f"Slug set changed on reversal.\nForward:  {sorted(slugs_fwd)}\n"
        f"Reversed: {sorted(slugs_rev)}"
    )


# ---------------------------------------------------------------------------
# T097-2: Same errors in random shuffle → identical slug set
# ---------------------------------------------------------------------------


def test_slug_stable_across_random_shuffle():
    """Random shuffle of input must not change the set of pattern_ids."""
    from sio.clustering.pattern_clusterer import cluster_errors  # noqa: PLC0415

    errors = (
        _make_errors(_TOOL_FAILURE_TEXTS, base_id=0)
        + _make_errors(_PARSE_ERROR_TEXTS, base_id=10)
        + _make_errors(_TIMEOUT_TEXTS, base_id=20)
    )
    shuffled = errors[:]
    random.seed(42)
    random.shuffle(shuffled)

    patterns_orig = cluster_errors(errors, threshold=0.50)
    patterns_shuf = cluster_errors(shuffled, threshold=0.50)

    slugs_orig = _slug_set(patterns_orig)
    slugs_shuf = _slug_set(patterns_shuf)

    assert slugs_orig == slugs_shuf, (
        f"Slug set changed on shuffle.\nOriginal: {sorted(slugs_orig)}\n"
        f"Shuffled: {sorted(slugs_shuf)}"
    )


# ---------------------------------------------------------------------------
# T097-3: Adding a new error keeps existing cluster slugs stable
# ---------------------------------------------------------------------------


def test_existing_cluster_slug_stable_on_new_error():
    """Adding one new error must not change slugs of existing clusters."""
    from sio.clustering.pattern_clusterer import cluster_errors  # noqa: PLC0415

    base_errors = _make_errors(_TOOL_FAILURE_TEXTS, base_id=0) + _make_errors(
        _PARSE_ERROR_TEXTS, base_id=10
    )
    new_error = [
        {
            "id": 99,
            "session_id": "sess_099",
            "error_text": "network_error: connection refused to localhost",
            "tool_name": "Bash",
            "timestamp": "2026-04-25T10:00:00Z",
        }
    ]

    patterns_before = cluster_errors(base_errors, threshold=0.50)
    patterns_after = cluster_errors(base_errors + new_error, threshold=0.50)

    slugs_before = _slug_set(patterns_before)
    slugs_after = _slug_set(patterns_after)

    # All original slugs must still be present after adding the new error
    missing = slugs_before - slugs_after
    assert not missing, (
        f"Adding a new error removed existing slugs: {missing}\n"
        f"Before: {sorted(slugs_before)}\nAfter:  {sorted(slugs_after)}"
    )


# ---------------------------------------------------------------------------
# T097-4: Slug format matches ^[a-z_]+_[0-9a-f]{10}$
# ---------------------------------------------------------------------------


def test_slug_format_matches_regex():
    """All produced slugs must match the centroid-hash slug format."""
    from sio.clustering.pattern_clusterer import cluster_errors  # noqa: PLC0415

    errors = (
        _make_errors(_TOOL_FAILURE_TEXTS, base_id=0)
        + _make_errors(_PARSE_ERROR_TEXTS, base_id=10)
        + _make_errors(_TIMEOUT_TEXTS, base_id=20)
    )

    patterns = cluster_errors(errors, threshold=0.50)
    assert patterns, "Expected at least one cluster"

    for p in patterns:
        slug = p["pattern_id"]
        assert SLUG_RE.match(slug), (
            f"Slug {slug!r} does not match expected format '^[a-z_]+_[0-9a-f]{{10}}$'"
        )


# ---------------------------------------------------------------------------
# T097-5: 50-record stress test — determinism under full input size
# ---------------------------------------------------------------------------


def test_slug_determinism_50_records():
    """50 synthetic errors clustered twice must yield identical slug sets."""
    from sio.clustering.pattern_clusterer import cluster_errors  # noqa: PLC0415

    rng = random.Random(1234)
    error_types = ["tool_failure", "parse_error", "timeout", "auth_error", "io_error"]
    errors = [
        {
            "id": i,
            "session_id": f"sess_{i:03d}",
            "error_text": f"{rng.choice(error_types)}: error message variant {i % 5}",
            "tool_name": "Bash",
            "timestamp": f"2026-04-{(i % 28) + 1:02d}T10:00:00Z",
        }
        for i in range(50)
    ]
    errors2 = errors[:]
    random.seed(99)
    random.shuffle(errors2)

    patterns1 = cluster_errors(errors, threshold=0.60)
    patterns2 = cluster_errors(errors2, threshold=0.60)

    assert _slug_set(patterns1) == _slug_set(patterns2), (
        "50-record stress test: slug sets differ between orderings"
    )
