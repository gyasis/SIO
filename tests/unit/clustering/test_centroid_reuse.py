"""T101 [US7] — Centroid reuse tests (FR-032, R-9).

Tests confirm that pattern_clusterer reads centroid embeddings from the
``centroid_embedding`` BLOB column rather than re-computing them when:
- The same cluster members are re-clustered
- The stored model_hash matches the current model

Tests for invalidation when model_hash mismatches.

BLOB format (per R-9):
    [dim: uint32_le (4 bytes)] [model_hash: 8 bytes] [vector: float32[dim]]

These tests are EXPECTED RED until T102 (Wave 11) implements pack/unpack.

Run to confirm RED:
    uv run pytest tests/unit/clustering/test_centroid_reuse.py -v
"""

from __future__ import annotations

import struct
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# BLOB helpers (mirror what T102 will implement)
# ---------------------------------------------------------------------------

def _pack_centroid(vec: np.ndarray, model_hash: bytes) -> bytes:
    """Pack a centroid vector into the R-9 BLOB format."""
    assert len(model_hash) == 8, "model_hash must be exactly 8 bytes"
    dim = len(vec)
    header = struct.pack("<I", dim) + model_hash
    floats = vec.astype(np.float32).tobytes()
    return header + floats


def _unpack_centroid(blob: bytes) -> tuple[np.ndarray, bytes]:
    """Unpack a centroid BLOB into (vector, model_hash)."""
    dim = struct.unpack("<I", blob[:4])[0]
    model_hash = blob[4:12]
    vec = np.frombuffer(blob[12:], dtype=np.float32).copy()
    assert len(vec) == dim
    return vec, model_hash


# ---------------------------------------------------------------------------
# Test DB fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_with_patterns(tmp_path: Path):
    """Create an in-memory patterns table with centroid_embedding + centroid_model_version."""
    conn = sqlite3.connect(str(tmp_path / "sio.db"))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern_id TEXT NOT NULL UNIQUE,
            description TEXT,
            centroid_embedding BLOB,
            centroid_model_version TEXT,
            error_count INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# T101-1: centroid_embedding BLOB has correct format
# ---------------------------------------------------------------------------

def test_centroid_blob_format():
    """Packed BLOB must decode to same vector + model_hash."""
    model_hash = b"fastemb0"  # 8 bytes
    vec = np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32)

    blob = _pack_centroid(vec, model_hash)
    decoded_vec, decoded_hash = _unpack_centroid(blob)

    assert decoded_hash == model_hash
    np.testing.assert_array_almost_equal(decoded_vec, vec, decimal=6)


# ---------------------------------------------------------------------------
# T101-2: Re-cluster with same members reads BLOB, does NOT re-embed
# ---------------------------------------------------------------------------

def test_recluster_same_members_skips_embed(db_with_patterns):
    """When centroid_embedding is present and model_hash matches, embed_texts NOT called."""
    from sio.clustering.pattern_clusterer import cluster_errors  # noqa: PLC0415

    model_hash = b"fastemb0"
    dim = 64
    rng = np.random.default_rng(42)
    stored_vec = rng.random(dim).astype(np.float32)
    stored_vec /= np.linalg.norm(stored_vec) + 1e-9

    blob = _pack_centroid(stored_vec, model_hash)

    # Pre-insert pattern with centroid BLOB
    db_with_patterns.execute(
        "INSERT INTO patterns (pattern_id, description, centroid_embedding, centroid_model_version) "
        "VALUES (?, ?, ?, ?)",
        ("tool_failure_abc1234567", "tool_failure: Bash exit code 1", blob, "fastemb0"),
    )
    db_with_patterns.commit()

    errors = [
        {
            "id": 1,
            "session_id": "sess_001",
            "error_text": "tool_failure: Bash exit code 1",
            "tool_name": "Bash",
            "timestamp": "2026-04-20T10:00:00Z",
        }
    ]

    embed_spy = MagicMock(return_value=np.array([stored_vec]))

    # When cluster_errors respects stored centroids, embed_texts should NOT be called
    # for clusters that already have a matching BLOB in the DB.
    # This test is RED until T102 implements read-from-BLOB logic.
    with patch("sio.clustering.pattern_clusterer.FastEmbedBackend") as MockBackend:
        instance = MockBackend.return_value
        instance.encode = embed_spy
        cluster_errors(errors, threshold=0.50, db_conn=db_with_patterns)

    # The BLOB should have been read, NOT recomputed
    assert embed_spy.call_count == 0, (
        f"embed_texts was called {embed_spy.call_count} times — expected 0 "
        "(centroid should be read from BLOB)"
    )


# ---------------------------------------------------------------------------
# T101-3: model_hash mismatch → BLOB invalidated, centroid recomputed
# ---------------------------------------------------------------------------

def test_model_hash_mismatch_triggers_recompute(db_with_patterns):
    """When stored model_hash != current model_hash, centroid must be recomputed."""
    from sio.clustering.pattern_clusterer import cluster_errors  # noqa: PLC0415

    old_hash = b"oldmodel"
    dim = 64
    rng = np.random.default_rng(7)
    old_vec = rng.random(dim).astype(np.float32)
    old_vec /= np.linalg.norm(old_vec) + 1e-9

    blob = _pack_centroid(old_vec, old_hash)

    db_with_patterns.execute(
        "INSERT INTO patterns (pattern_id, description, centroid_embedding, centroid_model_version) "
        "VALUES (?, ?, ?, ?)",
        ("tool_failure_deadbeef12", "tool_failure: old model error", blob, "oldmodel"),
    )
    db_with_patterns.commit()

    errors = [
        {
            "id": 2,
            "session_id": "sess_002",
            "error_text": "tool_failure: old model error",
            "tool_name": "Bash",
            "timestamp": "2026-04-20T11:00:00Z",
        }
    ]

    fresh_vec = np.random.default_rng(99).random(dim).astype(np.float32)
    fresh_vec /= np.linalg.norm(fresh_vec) + 1e-9
    embed_spy = MagicMock(return_value=np.array([fresh_vec]))

    # Current model hash is different from old_hash — must trigger recompute
    with patch("sio.clustering.pattern_clusterer.FastEmbedBackend") as MockBackend:
        instance = MockBackend.return_value
        instance.encode = embed_spy
        # Patch current model hash identifier
        with patch(
            "sio.clustering.pattern_clusterer._current_model_hash",
            return_value=b"newmodel",
        ):
            cluster_errors(errors, threshold=0.50, db_conn=db_with_patterns)

    assert embed_spy.call_count >= 1, (
        "embed_texts was NOT called despite model_hash mismatch — expected recompute"
    )


# ---------------------------------------------------------------------------
# T101-4: New cluster (no BLOB) → centroid computed and stored
# ---------------------------------------------------------------------------

def test_new_cluster_computes_and_stores_centroid(db_with_patterns):
    """A new cluster with no existing BLOB must compute centroid and store it."""
    from sio.clustering.pattern_clusterer import cluster_errors  # noqa: PLC0415

    errors = [
        {
            "id": 5,
            "session_id": "sess_005",
            "error_text": "brand_new_error: never seen before",
            "tool_name": "Edit",
            "timestamp": "2026-04-21T09:00:00Z",
        }
    ]

    dim = 64
    fresh_vec = np.random.default_rng(55).random(dim).astype(np.float32)
    fresh_vec /= np.linalg.norm(fresh_vec) + 1e-9
    embed_spy = MagicMock(return_value=np.array([fresh_vec]))

    with patch("sio.clustering.pattern_clusterer.FastEmbedBackend") as MockBackend:
        instance = MockBackend.return_value
        instance.encode = embed_spy
        patterns = cluster_errors(errors, threshold=0.50, db_conn=db_with_patterns)

    # embed_texts must have been called (no stored BLOB to read)
    assert embed_spy.call_count >= 1, (
        "embed_texts was NOT called for a new cluster — expected computation"
    )

    # The resulting pattern should have the centroid stored back to DB
    if patterns:
        row = db_with_patterns.execute(
            "SELECT centroid_embedding FROM patterns WHERE pattern_id = ?",
            (patterns[0]["pattern_id"],),
        ).fetchone()
        assert row is not None and row[0] is not None, (
            "New cluster did not store centroid_embedding BLOB in patterns table"
        )


# ---------------------------------------------------------------------------
# T101-5: centroid_model_version column updated on write
# ---------------------------------------------------------------------------

def test_centroid_model_version_written_on_store(db_with_patterns):
    """centroid_model_version must be populated when centroid is stored."""
    from sio.clustering.pattern_clusterer import cluster_errors  # noqa: PLC0415

    errors = [
        {
            "id": 10,
            "session_id": "sess_010",
            "error_text": "version_test_error: checking column update",
            "tool_name": "Read",
            "timestamp": "2026-04-22T08:00:00Z",
        }
    ]

    dim = 64
    vec = np.random.default_rng(101).random(dim).astype(np.float32)
    vec /= np.linalg.norm(vec) + 1e-9

    with patch("sio.clustering.pattern_clusterer.FastEmbedBackend") as MockBackend:
        instance = MockBackend.return_value
        instance.encode = MagicMock(return_value=np.array([vec]))
        patterns = cluster_errors(errors, threshold=0.50, db_conn=db_with_patterns)

    if patterns:
        row = db_with_patterns.execute(
            "SELECT centroid_model_version FROM patterns WHERE pattern_id = ?",
            (patterns[0]["pattern_id"],),
        ).fetchone()
        assert row is not None and row[0] is not None, (
            "centroid_model_version was not set after storing new centroid"
        )
