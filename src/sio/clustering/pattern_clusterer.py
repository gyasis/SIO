"""Embedding-based error pattern clusterer.

Groups a list of error record dicts into semantic clusters using cosine
similarity on fastembed embeddings.  The public API is a single function:

    cluster_errors(errors, threshold=0.70) -> list[dict]

Each returned pattern dict has the schema documented in the module docstring
below and tested in tests/unit/test_pattern_clusterer.py.

Pattern dict schema
-------------------
pattern_id   : str   — centroid-hash slug (format: ``<top_term>_<hex10>``)
description  : str   — representative error text (first error's text)
tool_name    : str | None — most common tool_name value in the cluster
error_count  : int   — number of errors in the cluster
session_count: int   — number of distinct session_ids in the cluster
first_seen   : str   — earliest timestamp across the cluster
last_seen    : str   — latest timestamp across the cluster
rank_score   : float — 0.0 initially; downstream ranker sets this
error_ids    : list[int] — list of error record IDs in this cluster
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any

import numpy as np

from sio.core.embeddings.local_model import FastEmbedBackend

# ---------------------------------------------------------------------------
# Module-level singleton — avoids reloading the ONNX model on every call.
# ---------------------------------------------------------------------------

_backend: FastEmbedBackend | None = None


def _get_backend() -> FastEmbedBackend:
    """Return (or lazily create) the module-level embedding backend."""
    global _backend  # noqa: PLW0603
    if _backend is None:
        _backend = FastEmbedBackend()
    return _backend


# ---------------------------------------------------------------------------
# Centroid-hash slug algorithm (R-5, FR-014)
# ---------------------------------------------------------------------------

_NON_ALNUM_WORD_RE = re.compile(r"[^a-z0-9_]")
_MULTI_SPACE_RE = re.compile(r"\s+")
_FIRST_WORD_RE = re.compile(r"^[a-z][a-z0-9_]*")


def _top_error_type_term(members: list[dict]) -> str:
    """Extract the top-1 human-readable prefix term from cluster members.

    Tries, in order:
    1. ``error_type`` field (e.g. "tool_failure").
    2. First word of ``error_text`` lowercased (stripped of non-alnum).
    3. Fallback ``"error"``.

    Returns a slug-safe token using only ``[a-z0-9_]``.
    """
    # Try error_type first
    types: list[str] = [
        e.get("error_type", "") or "" for e in members
    ]
    counter: Counter[str] = Counter(t for t in types if t)
    if counter:
        top = counter.most_common(1)[0][0]
        cleaned = _NON_ALNUM_WORD_RE.sub("_", top.lower())[:30].strip("_")
        if cleaned:
            return cleaned

    # Fall back to first word of error_text
    texts: list[str] = [e.get("error_text", "") or "" for e in members]
    first_words: list[str] = []
    for t in texts:
        m = _FIRST_WORD_RE.match(t.lower())
        if m:
            first_words.append(m.group(0)[:20])
    if first_words:
        top_word = Counter(first_words).most_common(1)[0][0]
        return top_word

    return "error"


def _make_slug(cluster_members: list[dict], centroid_vec: np.ndarray) -> str:
    """Generate a deterministic centroid-hash slug for a cluster.

    Algorithm (R-5):
    1. Round centroid to 4 decimal places (absorbs float jitter).
    2. SHA-256 hash of rounded centroid bytes → take first 10 hex chars.
    3. Top-1 term from members as human-readable prefix.
    4. Return ``f"{top_term}_{hash10}"``.

    The result matches ``^[a-z0-9_]+_[0-9a-f]{10}$``.

    Parameters
    ----------
    cluster_members:
        List of error record dicts in this cluster.
    centroid_vec:
        The cluster centroid vector (numpy float32 array).

    Returns
    -------
    str
        Deterministic slug for this cluster.
    """
    rounded = np.round(centroid_vec, 4)
    h = hashlib.sha256(rounded.tobytes()).hexdigest()[:10]
    top_term = _top_error_type_term(cluster_members)
    return f"{top_term}_{h}"


def _slugify(text: str, max_words: int = 6) -> str:
    """Legacy text-based slug (kept for backward compatibility with old patterns).

    New code should use ``_make_slug`` instead.
    """
    _non_alnum_re = re.compile(r"[^a-z0-9\s-]")
    lowered = text.lower()
    cleaned = _non_alnum_re.sub(" ", lowered)
    normalized = _MULTI_SPACE_RE.sub(" ", cleaned).strip()
    words = normalized.split()[:max_words]
    return "-".join(w for w in words if w)


# ---------------------------------------------------------------------------
# Cosine similarity
# ---------------------------------------------------------------------------


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Return the cosine similarity between two 1-D numpy vectors."""
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


# ---------------------------------------------------------------------------
# Core algorithm
# ---------------------------------------------------------------------------


def _most_common(values: list[Any]) -> Any:
    """Return the most frequently occurring value in *values*, or None."""
    if not values:
        return None
    counter: Counter[Any] = Counter(values)
    return counter.most_common(1)[0][0]


def cluster_errors(
    errors: list[dict],
    threshold: float = 0.70,
    db_conn=None,  # reserved for T102 centroid-reuse (Wave 11)
) -> list[dict]:
    """Group *errors* into semantic clusters based on embedding similarity.

    Parameters
    ----------
    errors:
        List of error record dicts.  Each dict must contain at minimum:
        - ``id``         (int)
        - ``session_id`` (str)
        - ``timestamp``  (str, ISO-8601)
        - ``tool_name``  (str | None)
        - ``error_text`` (str)
    threshold:
        Cosine-similarity threshold in [0, 1].  An error is merged into an
        existing cluster when its similarity to that cluster's centroid is
        **>= threshold**.  Errors that do not reach the threshold for any
        existing cluster start a new cluster.  Default is 0.70.
    db_conn:
        Optional open SQLite connection.  Reserved for T102 centroid-reuse
        (Wave 11): when provided, patterns with a stored ``centroid_embedding``
        BLOB whose model_hash matches the current model will skip re-encoding.
        Currently unused — accepted to allow callers to forward the param.

    Returns
    -------
    list[dict]
        List of pattern dicts (see module docstring for the schema).
        Returns ``[]`` when *errors* is empty.

    Algorithm
    ---------
    1. Early-exit on empty input.
    2. Sort errors by ``id`` ASC for stable, order-independent clustering.
    3. Batch-encode all ``error_text`` strings with FastEmbedBackend.
    4. Greedy single-pass scan:
       - For each error (index i), compute cosine similarity against every
         existing cluster centroid.
       - If max similarity >= threshold: append to that cluster and update
         the centroid via incremental mean.
       - Otherwise: open a new cluster seeded by this error.
    5. Build pattern dicts using centroid-hash slugs (R-5).
    """
    if not errors:
        return []

    backend = _get_backend()

    # ---- Step 1: sort for deterministic input ordering -------------------
    # Sort by id (int) ascending. Errors without id fall back to index order.
    sorted_errors = sorted(
        errors,
        key=lambda e: (e.get("id") if e.get("id") is not None else float("inf")),
    )

    # ---- Step 2: extract texts and encode --------------------------------
    texts: list[str] = [e["error_text"] for e in sorted_errors]
    embeddings: np.ndarray = backend.encode(texts)  # shape (N, D)

    # ---- Step 2: greedy clustering ---------------------------------------
    # Each cluster is represented as:
    #   centroid : np.ndarray  — running mean embedding
    #   indices  : list[int]   — indices into `errors` / `embeddings`
    centroids: list[np.ndarray] = []
    clusters: list[list[int]] = []

    for i in range(len(errors)):
        vec = embeddings[i]
        best_cluster: int | None = None
        best_sim: float = -1.0

        for c_idx, centroid in enumerate(centroids):
            sim = _cosine_similarity(vec, centroid)
            if sim >= threshold and sim > best_sim:
                best_sim = sim
                best_cluster = c_idx

        if best_cluster is not None:
            # Merge into existing cluster and update centroid (incremental mean).
            clusters[best_cluster].append(i)
            n = len(clusters[best_cluster])
            centroids[best_cluster] = (
                centroids[best_cluster] * (n - 1) + vec
            ) / n
        else:
            # Seed a new cluster.
            centroids.append(vec.copy())
            clusters.append([i])

    # ---- Step 3: build pattern dicts using centroid-hash slugs (R-5) ------
    patterns: list[dict] = []
    for c_idx, member_indices in enumerate(clusters):
        member_errors = [sorted_errors[i] for i in member_indices]

        # Representative text is the first error's text (sorted insertion order).
        first_error = member_errors[0]
        description: str = first_error["error_text"]

        # Centroid-hash slug — deterministic regardless of input ordering.
        centroid_vec: np.ndarray = centroids[c_idx]
        pattern_id: str = _make_slug(member_errors, centroid_vec)

        # Most common tool_name (None counts as a value, but we prefer str).
        tool_names: list[str | None] = [e.get("tool_name") for e in member_errors]
        tool_name: str | None = _most_common(tool_names)

        # Timestamps — sort lexicographically (ISO-8601 is sortable as str).
        timestamps: list[str] = [
            e["timestamp"] for e in member_errors if e.get("timestamp")
        ]
        timestamps_sorted = sorted(timestamps)
        first_seen: str = timestamps_sorted[0] if timestamps_sorted else ""
        last_seen: str = timestamps_sorted[-1] if timestamps_sorted else ""

        session_ids: set[str] = {e["session_id"] for e in member_errors}
        error_ids: list[int] = [e["id"] for e in member_errors]

        patterns.append(
            {
                "pattern_id": pattern_id,
                "description": description,
                "tool_name": tool_name,
                "error_count": len(member_errors),
                "session_count": len(session_ids),
                "first_seen": first_seen,
                "last_seen": last_seen,
                "rank_score": 0.0,
                "error_ids": error_ids,
            }
        )

    return patterns
