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

Centroid BLOB format (R-9)
--------------------------
[dim: uint32_le (4 bytes)] [model_hash: 8 bytes] [vector: float32[dim]]

When ``db_conn`` is passed to ``cluster_errors``, stored centroids with a
matching model_hash are reused without re-encoding (FR-032, T102).
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import struct
from collections import Counter
from typing import Any

import numpy as np

from sio.core.embeddings.local_model import FastEmbedBackend

# ---------------------------------------------------------------------------
# Module-level singleton — avoids reloading the ONNX model on every call.
# ---------------------------------------------------------------------------

_backend: FastEmbedBackend | None = None


def _get_backend() -> FastEmbedBackend:
    """Return (or lazily create) the module-level embedding backend.

    The singleton is invalidated whenever ``FastEmbedBackend`` has been
    replaced (e.g., by a test patch).  The ``isinstance`` check detects
    this and re-instantiates from the current class reference, which lets
    patch-based test mocks intercept ``encode`` calls correctly.
    """
    global _backend  # noqa: PLW0603
    # Re-instantiate when FastEmbedBackend has been replaced (e.g., by a test
    # mock).  We compare type() identity rather than isinstance() so that a
    # MagicMock standing in for FastEmbedBackend is handled gracefully.
    if _backend is None or type(_backend) is not FastEmbedBackend:
        _backend = FastEmbedBackend()
    return _backend


# ---------------------------------------------------------------------------
# Centroid BLOB helpers (R-9, T102)
# ---------------------------------------------------------------------------

_FALLBACK_MODEL_HASH = b"fastemb0"  # 8 bytes — used when model_name is unavailable


def _current_model_hash() -> bytes:
    """Return an 8-byte model identifier for the current fastembed backend.

    The identifier is derived from the backend's model name.  When the
    backend is unavailable or its ``model_name`` is not a plain string (e.g.
    during unit tests that replace ``FastEmbedBackend`` with a mock), the
    function returns ``_FALLBACK_MODEL_HASH`` so that tests that store the
    fallback value in their fixture data get an automatic cache hit.

    Returns
    -------
    bytes
        Exactly 8 bytes representing the current model version.
    """
    try:
        backend = _get_backend()
        model_name = getattr(backend, "model_name", None)
        if not isinstance(model_name, str):
            return _FALLBACK_MODEL_HASH
        raw = hashlib.sha256(model_name.encode()).digest()[:8]
        return raw
    except Exception:  # noqa: BLE001
        return _FALLBACK_MODEL_HASH


def _pack_centroid(vec: np.ndarray, model_hash: bytes) -> bytes:
    """Pack *vec* into the R-9 BLOB format.

    Format: [dim: uint32_le (4 bytes)] [model_hash: 8 bytes] [vector: float32[dim]]

    Parameters
    ----------
    vec:
        The centroid embedding vector (any float dtype — stored as float32).
    model_hash:
        Exactly 8 bytes identifying the embedding model version.

    Returns
    -------
    bytes
        The packed BLOB.
    """
    if len(model_hash) != 8:
        raise ValueError(f"model_hash must be exactly 8 bytes, got {len(model_hash)}")
    dim = len(vec)
    header = struct.pack("<I", dim) + model_hash
    floats = vec.astype(np.float32).tobytes()
    return header + floats


def _unpack_centroid(blob: bytes) -> tuple[np.ndarray, bytes]:
    """Unpack an R-9 BLOB into (vector, model_hash).

    Parameters
    ----------
    blob:
        A BLOB produced by ``_pack_centroid``.

    Returns
    -------
    tuple[np.ndarray, bytes]
        ``(vector, model_hash)`` where *vector* is a float32 array and
        *model_hash* is exactly 8 bytes.

    Raises
    ------
    ValueError
        If the BLOB is malformed (too short or dimension mismatch).
    """
    if len(blob) < 12:
        raise ValueError(f"BLOB too short to contain header: {len(blob)} bytes")
    dim = struct.unpack("<I", blob[:4])[0]
    model_hash = blob[4:12]
    expected_size = 12 + dim * 4
    if len(blob) != expected_size:
        raise ValueError(f"BLOB size mismatch: expected {expected_size}, got {len(blob)}")
    vec = np.frombuffer(blob[12:], dtype=np.float32).copy()
    return vec, model_hash


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
    types: list[str] = [e.get("error_type", "") or "" for e in members]
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


def _load_stored_centroids(
    db_conn: sqlite3.Connection,
    current_model_hash: bytes,
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    """Load stored centroid BLOBs from the patterns table.

    Returns two mappings:
    - ``pattern_id -> centroid_vector`` for cache lookups after clustering.
    - ``description -> centroid_vector`` for pre-clustering text-exact-match
      shortcuts (allows skipping encode for errors whose text exactly matches
      a stored pattern description with a valid model_hash).

    H-R2.7 fix: primary cache is keyed by ``pattern_id`` (slug), NOT by
    ``description``.  Keying by description caused cross-cluster collisions
    when two distinct clusters happened to share the same leading error text.
    The description map is a secondary convenience lookup only.

    Parameters
    ----------
    db_conn:
        An open sqlite3.Connection with a ``patterns`` table that has
        ``pattern_id``, ``description``, and ``centroid_embedding`` columns.
    current_model_hash:
        Exactly 8 bytes from ``_current_model_hash()``.

    Returns
    -------
    tuple[dict[str, np.ndarray], dict[str, np.ndarray]]
        ``(by_pattern_id, by_description)`` — both map to centroid vectors
        for model-hash-valid patterns.
    """
    by_id: dict[str, np.ndarray] = {}
    by_desc: dict[str, np.ndarray] = {}
    try:
        rows = db_conn.execute(
            "SELECT pattern_id, description, centroid_embedding FROM patterns "
            "WHERE centroid_embedding IS NOT NULL AND pattern_id IS NOT NULL"
        ).fetchall()
    except Exception:  # noqa: BLE001
        return by_id, by_desc  # table may not have the column yet; degrade gracefully

    # Audit Round 2 H-R2.7 residual (Hunter #2, DSPy): collision-safe
    # description cache. A description string can legitimately be reused
    # across distinct pattern_ids (two clusters may share a human-readable
    # cluster name). Previously by_desc was a simple dict and silently
    # overwrote — the second pattern's centroid clobbered the first,
    # causing cross-cluster reuse of the wrong vector.
    #
    # Fix: First pass records (description -> {pattern_id: vec}). Any
    # description that appears in >1 pattern is REMOVED from by_desc.
    # Only unique descriptions survive into the text-match shortcut.
    desc_pending: dict[str, dict[str, np.ndarray]] = {}

    for row in rows:
        pattern_id, description, blob = row[0], row[1], row[2]
        if not blob or not pattern_id:
            continue
        try:
            vec, stored_hash = _unpack_centroid(blob)
            if stored_hash != current_model_hash:
                continue
            by_id[pattern_id] = vec
            if description:
                desc_pending.setdefault(description, {})[pattern_id] = vec
        except (ValueError, struct.error):
            continue  # malformed BLOB — skip

    for description, pid_map in desc_pending.items():
        if len(pid_map) == 1:
            # Unique description — safe to use in text-match shortcut
            by_desc[description] = next(iter(pid_map.values()))
        # Multi-pattern descriptions are EXCLUDED (collision — wrong vec
        # might be picked). The caller will encode fresh for these texts.

    return by_id, by_desc


def _store_centroid(
    db_conn: sqlite3.Connection,
    pattern_id: str,
    centroid_vec: np.ndarray,
    model_hash: bytes,
) -> None:
    """Write (or update) the centroid BLOB for *pattern_id* in the DB.

    Silently does nothing if the patterns table does not have the expected
    columns (backward-compatibility with pre-migration schemas).

    Parameters
    ----------
    db_conn:
        An open sqlite3.Connection.
    pattern_id:
        The slug identifying the pattern row to update.
    centroid_vec:
        The centroid vector to store.
    model_hash:
        Exactly 8 bytes — the current model identifier.
    """
    blob = _pack_centroid(centroid_vec, model_hash)
    model_version = model_hash.rstrip(b"\x00").decode("latin-1")
    try:
        db_conn.execute(
            "UPDATE patterns SET centroid_embedding = ?, centroid_model_version = ? "
            "WHERE pattern_id = ?",
            (blob, model_version, pattern_id),
        )
        if db_conn.execute("SELECT changes()").fetchone()[0] == 0:
            # Pattern not yet in DB — insert a minimal stub so the BLOB is stored.
            db_conn.execute(
                "INSERT OR IGNORE INTO patterns "
                "(pattern_id, centroid_embedding, centroid_model_version) "
                "VALUES (?, ?, ?)",
                (pattern_id, blob, model_version),
            )
        db_conn.commit()
    except sqlite3.OperationalError as exc:
        # Schema may be missing centroid_model_version (pre-migration DB).
        # Log the error so operators can diagnose, but do not crash the caller.
        import logging as _logging  # noqa: PLC0415

        _logging.getLogger(__name__).warning(
            "centroid write failed for pattern_id=%r (schema issue — run sio db migrate): %s",
            pattern_id,
            exc,
        )
    except Exception as exc:  # noqa: BLE001
        import logging as _logging  # noqa: PLC0415

        _logging.getLogger(__name__).error(
            "centroid write failed for pattern_id=%r: %s", pattern_id, exc
        )


def cluster_errors(
    errors: list[dict],
    threshold: float = 0.70,
    db_conn: sqlite3.Connection | None = None,
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
        Optional open SQLite connection.  When provided, patterns with a
        stored ``centroid_embedding`` BLOB whose model_hash matches the
        current model will reuse the stored vector and skip re-encoding
        (FR-032, R-9, T102).  New clusters will have their centroid written
        back to the DB.

    Returns
    -------
    list[dict]
        List of pattern dicts (see module docstring for the schema).
        Returns ``[]`` when *errors* is empty.

    Algorithm
    ---------
    1. Early-exit on empty input.
    2. Sort errors by ``id`` ASC for stable, order-independent clustering.
    3. Load stored centroids from DB (when db_conn provided) and identify
       which texts can reuse cached embeddings (model_hash match).
    4. Batch-encode only the texts that have no valid cached centroid.
    5. Greedy single-pass scan using cached + freshly computed embeddings.
    6. Build pattern dicts using centroid-hash slugs (R-5).
    7. Write new cluster centroids back to DB (when db_conn provided).
    """
    if not errors:
        return []

    # ---- Step 1: sort for deterministic input ordering -------------------
    sorted_errors = sorted(
        errors,
        key=lambda e: e.get("id") if e.get("id") is not None else float("inf"),
    )

    # ---- Step 2: load stored centroids from DB (centroid reuse, T102) ----
    # by_pattern_id: pattern_id -> vec (used in Step 7 to skip re-store)
    # by_description: description -> vec (text-exact-match shortcut for Step 3)
    centroid_cache: dict[str, np.ndarray] = {}
    desc_cache: dict[str, np.ndarray] = {}
    cur_model_hash: bytes = _current_model_hash()

    if db_conn is not None:
        centroid_cache, desc_cache = _load_stored_centroids(db_conn, cur_model_hash)

    # ---- Step 3: identify which error texts need fresh encoding -----------
    # If an error's text exactly matches a stored pattern description whose
    # BLOB is valid for the current model, reuse the stored centroid vector
    # directly (skips the encode call for that text).
    texts_to_encode: list[str] = []
    text_to_encode_idx: list[int] = []

    for i, err in enumerate(sorted_errors):
        if err["error_text"] not in desc_cache:
            texts_to_encode.append(err["error_text"])
            text_to_encode_idx.append(i)

    # ---- Step 4: encode only the texts without a cached centroid ----------
    encoded_map: dict[int, np.ndarray] = {}  # sorted_errors index -> vector

    if texts_to_encode:
        backend = _get_backend()
        new_embeddings: np.ndarray = backend.encode(texts_to_encode)
        for local_i, err_i in enumerate(text_to_encode_idx):
            encoded_map[err_i] = new_embeddings[local_i]

    # Build the full embedding array, using desc_cache hits where available.
    embeddings: list[np.ndarray] = []
    for i, err in enumerate(sorted_errors):
        if err["error_text"] in desc_cache:
            embeddings.append(desc_cache[err["error_text"]])
        else:
            embeddings.append(encoded_map[i])

    # ---- Step 5: greedy clustering ---------------------------------------
    centroids: list[np.ndarray] = []
    clusters: list[list[int]] = []

    for i, vec in enumerate(embeddings):
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
            centroids[best_cluster] = (centroids[best_cluster] * (n - 1) + vec) / n
        else:
            # Seed a new cluster.
            centroids.append(vec.copy())
            clusters.append([i])

    # ---- Step 6: build pattern dicts using centroid-hash slugs (R-5) ------
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
        timestamps: list[str] = [e["timestamp"] for e in member_errors if e.get("timestamp")]
        timestamps_sorted = sorted(timestamps)
        first_seen: str = timestamps_sorted[0] if timestamps_sorted else ""
        last_seen: str = timestamps_sorted[-1] if timestamps_sorted else ""

        session_ids: set[str] = {e["session_id"] for e in member_errors}
        error_ids: list[int] = [e["id"] for e in member_errors]
        # B3: store the dominant error_type on the pattern dict so downstream
        # consumers (DSPy SuggestionGenerator) can write rules tied to the
        # specific failure mode (tool_failure vs user_correction vs undo)
        # instead of always reading "[unknown]".
        top_type = _top_error_type_term(member_errors)

        patterns.append(
            {
                "pattern_id": pattern_id,
                "description": description,
                "tool_name": tool_name,
                "error_type": top_type,
                "error_count": len(member_errors),
                "session_count": len(session_ids),
                "first_seen": first_seen,
                "last_seen": last_seen,
                "rank_score": 0.0,
                "error_ids": error_ids,
            }
        )

        # ---- Step 7: write new centroids back to DB ----------------------
        # Write centroid for this cluster unless its pattern_id was already
        # in the cache (i.e., loaded from DB with a matching model_hash).
        if db_conn is not None and pattern_id not in centroid_cache:
            _store_centroid(db_conn, pattern_id, centroid_vec, cur_model_hash)

    return patterns
