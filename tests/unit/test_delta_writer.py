"""Tests for delta-based writing in sio.applier.writer.

Covers T036: merge vs append based on similarity, delta_type tracking.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import numpy as np

from sio.core.config import SIOConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_approved_suggestion(
    conn: sqlite3.Connection, **overrides
) -> int:
    """Insert one approved suggestion and return its ID."""
    defaults = {
        "pattern_id": 1,
        "dataset_id": 1,
        "description": "test suggestion",
        "confidence": 0.85,
        "proposed_change": "## Rule: Test\n\nAlways test before committing.",
        "target_file": "CLAUDE.md",
        "change_type": "claude_md_rule",
        "status": "approved",
    }
    defaults.update(overrides)
    cur = conn.execute(
        "INSERT INTO suggestions "
        "(pattern_id, dataset_id, description, confidence, proposed_change, "
        " target_file, change_type, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
        (
            defaults["pattern_id"],
            defaults["dataset_id"],
            defaults["description"],
            defaults["confidence"],
            defaults["proposed_change"],
            defaults["target_file"],
            defaults["change_type"],
            defaults["status"],
        ),
    )
    conn.commit()
    return cur.lastrowid


class _FakeBackend:
    """Fake embedding backend that returns controlled embeddings."""

    def __init__(self, embedding_map: dict[str, np.ndarray]):
        self._map = embedding_map
        self._rng = np.random.default_rng(99)

    def encode(self, texts: list[str]) -> np.ndarray:
        result = []
        for t in texts:
            matched = False
            for key, emb in self._map.items():
                if key in t:
                    result.append(emb)
                    matched = True
                    break
            if not matched:
                result.append(
                    self._rng.random(384).astype(np.float32)
                )
        return np.stack(result)


def _make_similar_embeddings() -> dict[str, np.ndarray]:
    """Create two very similar vectors and one dissimilar."""
    rng = np.random.default_rng(42)
    base = rng.random(384).astype(np.float32)
    base /= np.linalg.norm(base)

    # Tiny perturbation -> sim > 0.95
    similar = base + rng.normal(0, 0.005, 384).astype(np.float32)
    similar /= np.linalg.norm(similar)

    # Completely different
    different = rng.random(384).astype(np.float32)
    different /= np.linalg.norm(different)

    return {
        "Never use SELECT *": base,
        "Avoid SELECT star in queries": similar,
        "Always run tests before committing": different,
    }


# =========================================================================
# TestDeltaMerge
# =========================================================================


class TestDeltaMerge:
    """When new rule >80% similar to existing -> merge (in-place update)."""

    def test_merge_when_similar(self, v2_db, tmp_path: Path):
        from sio.applier.writer import apply_change

        existing_content = (
            "# Rules\n"
            "\n"
            "Never use SELECT * in SQL queries\n"
        )
        target = tmp_path / "CLAUDE.md"
        target.write_text(existing_content)

        sid = _seed_approved_suggestion(
            v2_db,
            target_file=str(target),
            proposed_change="Avoid SELECT star in queries -- use explicit columns",
        )

        embs = _make_similar_embeddings()
        fake = _FakeBackend(embs)
        config = SIOConfig(similarity_threshold=0.80)

        with patch("sio.applier.writer._get_backend", return_value=fake):
            result = apply_change(v2_db, sid, config=config)

        assert result["success"] is True
        assert result["delta_type"] == "merge"

        # The file should NOT have a second copy appended at the end;
        # instead the existing block is updated in place.
        content = target.read_text()
        assert "# Rules" in content
        # The merged content should include the existing text.
        assert "SELECT" in content

    def test_merge_updates_in_place_not_append(self, v2_db, tmp_path: Path):
        """After merge, file should have same number of blocks (not extra)."""
        from sio.applier.writer import apply_change

        existing = "Block one: Never use SELECT *\n\nBlock two: Run tests always\n"
        target = tmp_path / "CLAUDE.md"
        target.write_text(existing)

        sid = _seed_approved_suggestion(
            v2_db,
            target_file=str(target),
            proposed_change="Avoid SELECT star in queries",
        )

        embs = _make_similar_embeddings()
        fake = _FakeBackend(embs)
        config = SIOConfig(similarity_threshold=0.80)

        with patch("sio.applier.writer._get_backend", return_value=fake):
            result = apply_change(v2_db, sid, config=config)

        assert result["delta_type"] == "merge"
        content = target.read_text()
        # "Block two" should still be present (not lost by merge).
        assert "Block two" in content or "Run tests always" in content


# =========================================================================
# TestDeltaAppend
# =========================================================================


class TestDeltaAppend:
    """When new rule <80% similar to existing -> append (new block)."""

    def test_append_when_dissimilar(self, v2_db, tmp_path: Path):
        from sio.applier.writer import apply_change

        existing = "Never use SELECT * in SQL queries\n"
        target = tmp_path / "CLAUDE.md"
        target.write_text(existing)

        sid = _seed_approved_suggestion(
            v2_db,
            target_file=str(target),
            proposed_change="Always run tests before committing",
        )

        embs = _make_similar_embeddings()
        fake = _FakeBackend(embs)
        config = SIOConfig(similarity_threshold=0.80)

        with patch("sio.applier.writer._get_backend", return_value=fake):
            result = apply_change(v2_db, sid, config=config)

        assert result["success"] is True
        assert result["delta_type"] == "append"

        content = target.read_text()
        # Both rules should be present.
        assert "SELECT" in content
        assert "tests before committing" in content

    def test_append_to_empty_file(self, v2_db, tmp_path: Path):
        """Empty file -> always append (no existing blocks to compare)."""
        from sio.applier.writer import apply_change

        target = tmp_path / "CLAUDE.md"
        target.write_text("")

        sid = _seed_approved_suggestion(
            v2_db,
            target_file=str(target),
            proposed_change="New rule here",
        )

        config = SIOConfig(similarity_threshold=0.80)
        result = apply_change(v2_db, sid, config=config)

        assert result["success"] is True
        assert result["delta_type"] == "append"
        assert "New rule here" in target.read_text()

    def test_append_without_config(self, v2_db, tmp_path: Path):
        """When config is None, writer uses original append-only behavior."""
        from sio.applier.writer import apply_change

        target = tmp_path / "CLAUDE.md"
        target.write_text("Existing content\n")

        sid = _seed_approved_suggestion(
            v2_db,
            target_file=str(target),
            proposed_change="New appended rule",
        )

        result = apply_change(v2_db, sid)  # No config

        assert result["success"] is True
        assert result["delta_type"] == "append"
        content = target.read_text()
        assert "Existing content" in content
        assert "New appended rule" in content


# =========================================================================
# TestDeltaTypeTracking
# =========================================================================


class TestDeltaTypeTracking:
    """Verify delta_type is recorded in applied_changes table."""

    def test_merge_recorded_in_db(self, v2_db, tmp_path: Path):
        from sio.applier.writer import apply_change

        target = tmp_path / "CLAUDE.md"
        target.write_text("Never use SELECT * in SQL queries\n")

        sid = _seed_approved_suggestion(
            v2_db,
            target_file=str(target),
            proposed_change="Avoid SELECT star in queries",
        )

        embs = _make_similar_embeddings()
        fake = _FakeBackend(embs)
        config = SIOConfig(similarity_threshold=0.80)

        with patch("sio.applier.writer._get_backend", return_value=fake):
            result = apply_change(v2_db, sid, config=config)

        assert result["delta_type"] == "merge"

        row = v2_db.execute(
            "SELECT delta_type FROM applied_changes WHERE suggestion_id = ?",
            (sid,),
        ).fetchone()
        assert row is not None
        assert row[0] == "merge"

    def test_append_recorded_in_db(self, v2_db, tmp_path: Path):
        from sio.applier.writer import apply_change

        target = tmp_path / "CLAUDE.md"
        target.write_text("Existing rule about SQL\n")

        sid = _seed_approved_suggestion(
            v2_db,
            target_file=str(target),
            proposed_change="Always run tests before committing",
        )

        embs = _make_similar_embeddings()
        fake = _FakeBackend(embs)
        config = SIOConfig(similarity_threshold=0.80)

        with patch("sio.applier.writer._get_backend", return_value=fake):
            result = apply_change(v2_db, sid, config=config)

        assert result["delta_type"] == "append"

        row = v2_db.execute(
            "SELECT delta_type FROM applied_changes WHERE suggestion_id = ?",
            (sid,),
        ).fetchone()
        assert row is not None
        assert row[0] == "append"

    def test_delta_type_in_result_dict(self, v2_db, tmp_path: Path):
        """apply_change always returns delta_type in the result dict."""
        from sio.applier.writer import apply_change

        target = tmp_path / "CLAUDE.md"
        target.write_text("")

        sid = _seed_approved_suggestion(
            v2_db, target_file=str(target)
        )
        result = apply_change(v2_db, sid)

        assert "delta_type" in result
        assert result["delta_type"] in ("merge", "append")


# =========================================================================
# TestBackwardCompatibility
# =========================================================================


class TestBackwardCompatibility:
    """Existing writer behavior is preserved when config is not provided."""

    def test_old_tests_still_pass_without_config(
        self, v2_db, tmp_path: Path
    ):
        """apply_change without config behaves like the original writer."""
        from sio.applier.writer import apply_change

        target = tmp_path / "CLAUDE.md"
        target.write_text("# Existing Rules\n\nDo not delete.\n")

        sid = _seed_approved_suggestion(
            v2_db,
            target_file=str(target),
            proposed_change="## New Rule\n\nNew content.",
        )
        result = apply_change(v2_db, sid)

        content = target.read_text()
        assert "# Existing Rules" in content
        assert "Do not delete." in content
        assert "## New Rule" in content
        assert result["success"] is True

    def test_creates_file_if_missing_without_config(
        self, v2_db, tmp_path: Path
    ):
        from sio.applier.writer import apply_change

        target = tmp_path / "CLAUDE.md"
        sid = _seed_approved_suggestion(
            v2_db,
            target_file=str(target),
            proposed_change="## First Rule\n\nContent.",
        )
        result = apply_change(v2_db, sid)
        assert target.exists()
        assert "## First Rule" in target.read_text()
        assert result["success"] is True
