"""Tests for sio.applier.deduplicator -- find and merge duplicate rules."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np

from sio.applier.deduplicator import (
    DuplicatePair,
    find_duplicates,
    propose_merge,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_md(path: Path, content: str) -> Path:
    """Write *content* to a file and return the path."""
    path.write_text(content, encoding="utf-8")
    return path


class _FakeBackend:
    """Fake embedding backend with controlled embeddings per text fragment."""

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
                result.append(self._rng.random(384).astype(np.float32))
        return np.stack(result)


def _make_near_identical_pair() -> dict[str, np.ndarray]:
    """Two very similar embeddings (sim ~0.99) and one dissimilar."""
    rng = np.random.default_rng(42)
    base = rng.random(384).astype(np.float32)
    base /= np.linalg.norm(base)

    # Tiny perturbation -> high similarity (~0.99).
    similar = base + rng.normal(0, 0.005, 384).astype(np.float32)
    similar /= np.linalg.norm(similar)

    # Completely different direction -> low similarity.
    different = rng.random(384).astype(np.float32)
    different /= np.linalg.norm(different)

    return {
        "Never use SELECT * in queries": base,
        "Avoid SELECT star": similar,
        "Always run tests before committing": different,
    }


def _make_moderate_similarity() -> dict[str, np.ndarray]:
    """Two embeddings with moderate similarity (~0.70-0.80)."""
    rng = np.random.default_rng(42)
    base = rng.random(384).astype(np.float32)
    base /= np.linalg.norm(base)

    # Larger perturbation -> moderate similarity.
    moderate = base + rng.normal(0, 0.3, 384).astype(np.float32)
    moderate /= np.linalg.norm(moderate)

    return {
        "Use explicit column lists in SQL": base,
        "SQL queries should list columns": moderate,
    }


# =========================================================================
# TestFindDuplicates
# =========================================================================


class TestFindDuplicates:
    """find_duplicates detects near-identical rules across files."""

    def test_finds_near_identical_pair(self, tmp_path: Path):
        """Two rules at ~0.99 similarity should be found at 0.85 threshold."""
        fa = _write_md(
            tmp_path / "file_a.md",
            "Never use SELECT * in queries\n",
        )
        fb = _write_md(
            tmp_path / "file_b.md",
            "Avoid SELECT star -- use explicit columns\n",
        )

        embs = _make_near_identical_pair()
        fake = _FakeBackend(embs)

        with patch("sio.applier.deduplicator._get_backend", return_value=fake):
            pairs = find_duplicates([fa, fb], threshold=0.85)

        assert len(pairs) >= 1
        pair = pairs[0]
        assert isinstance(pair, DuplicatePair)
        assert pair.similarity >= 0.85

    def test_respects_threshold_high(self, tmp_path: Path):
        """Rules at 0.90 similarity are found with threshold=0.85."""
        rng = np.random.default_rng(42)
        base = rng.random(384).astype(np.float32)
        base /= np.linalg.norm(base)

        # Create vectors with known ~0.90 similarity.
        noise = rng.normal(0, 0.08, 384).astype(np.float32)
        similar = base + noise
        similar /= np.linalg.norm(similar)

        # Verify the actual similarity.
        actual_sim = float(
            np.dot(base, similar) / (np.linalg.norm(base) * np.linalg.norm(similar))
        )

        fa = _write_md(tmp_path / "a.md", "Rule about SQL formatting\n")
        fb = _write_md(tmp_path / "b.md", "Rule about code formatting\n")

        embs = {
            "Rule about SQL formatting": base,
            "Rule about code formatting": similar,
        }
        fake = _FakeBackend(embs)

        with patch("sio.applier.deduplicator._get_backend", return_value=fake):
            pairs = find_duplicates([fa, fb], threshold=0.85)

        if actual_sim >= 0.85:
            assert len(pairs) >= 1
            assert pairs[0].similarity >= 0.85
        else:
            assert len(pairs) == 0

    def test_does_not_find_dissimilar_at_070(self, tmp_path: Path):
        """Rules at ~0.70 similarity should NOT be found at 0.85 threshold."""
        embs = _make_moderate_similarity()
        fa = _write_md(tmp_path / "a.md", "Use explicit column lists in SQL\n")
        fb = _write_md(tmp_path / "b.md", "SQL queries should list columns\n")

        fake = _FakeBackend(embs)

        with patch("sio.applier.deduplicator._get_backend", return_value=fake):
            pairs = find_duplicates([fa, fb], threshold=0.85)

        # Moderate similarity should be below 0.85 threshold.
        for pair in pairs:
            assert pair.similarity >= 0.85  # only pairs above threshold returned

    def test_finds_pairs_within_same_file(self, tmp_path: Path):
        """Duplicates within a single file are detected."""
        content = "Never use SELECT * in queries\n\nAvoid SELECT star -- use explicit columns\n"
        f = _write_md(tmp_path / "rules.md", content)
        embs = _make_near_identical_pair()
        fake = _FakeBackend(embs)

        with patch("sio.applier.deduplicator._get_backend", return_value=fake):
            pairs = find_duplicates([f], threshold=0.85)

        assert len(pairs) >= 1
        assert pairs[0].file_a == pairs[0].file_b  # same file

    def test_finds_pairs_across_multiple_files(self, tmp_path: Path):
        """Duplicates across three files are all detected."""
        fa = _write_md(tmp_path / "a.md", "Never use SELECT * in queries\n")
        fb = _write_md(tmp_path / "b.md", "Avoid SELECT star\n")
        fc = _write_md(tmp_path / "c.md", "Always run tests before committing\n")

        embs = _make_near_identical_pair()
        fake = _FakeBackend(embs)

        with patch("sio.applier.deduplicator._get_backend", return_value=fake):
            pairs = find_duplicates([fa, fb, fc], threshold=0.85)

        # Should find the SELECT-related pair, not a pair with "tests".
        assert len(pairs) >= 1
        texts = {pairs[0].text_a, pairs[0].text_b}
        assert any("SELECT" in t or "select" in t.lower() for t in texts)

    def test_sorted_by_similarity_descending(self, tmp_path: Path):
        """Returned pairs are sorted by similarity (highest first)."""
        rng = np.random.default_rng(42)
        base = rng.random(384).astype(np.float32)
        base /= np.linalg.norm(base)

        very_similar = base + rng.normal(0, 0.003, 384).astype(np.float32)
        very_similar /= np.linalg.norm(very_similar)

        somewhat_similar = base + rng.normal(0, 0.05, 384).astype(np.float32)
        somewhat_similar /= np.linalg.norm(somewhat_similar)

        content = "Rule alpha about SQL\n\nRule beta about SQL\n\nRule gamma about SQL\n"
        f = _write_md(tmp_path / "rules.md", content)
        embs = {
            "Rule alpha about SQL": base,
            "Rule beta about SQL": very_similar,
            "Rule gamma about SQL": somewhat_similar,
        }
        fake = _FakeBackend(embs)

        with patch("sio.applier.deduplicator._get_backend", return_value=fake):
            pairs = find_duplicates([f], threshold=0.85)

        if len(pairs) >= 2:
            assert pairs[0].similarity >= pairs[1].similarity

    def test_empty_file_returns_empty(self, tmp_path: Path):
        f = _write_md(tmp_path / "empty.md", "")
        with patch(
            "sio.applier.deduplicator._get_backend",
            return_value=_FakeBackend({}),
        ):
            pairs = find_duplicates([f], threshold=0.85)
        assert pairs == []

    def test_missing_file_skipped(self, tmp_path: Path):
        missing = tmp_path / "nonexistent.md"
        fa = _write_md(tmp_path / "a.md", "Some rule\n")
        with patch(
            "sio.applier.deduplicator._get_backend",
            return_value=_FakeBackend({}),
        ):
            pairs = find_duplicates([missing, fa], threshold=0.85)
        assert pairs == []  # Only one block total, no pairs possible


# =========================================================================
# TestProposeMerge
# =========================================================================


class TestProposeMerge:
    """propose_merge generates consolidated text from duplicate pairs."""

    def test_keeps_longer_as_base(self):
        pair = DuplicatePair(
            file_a="a.md",
            line_a=1,
            text_a="Never use SELECT * in SQL queries -- always list columns",
            file_b="b.md",
            line_b=1,
            text_b="Avoid SELECT *",
            similarity=0.92,
        )
        merged = propose_merge(pair)
        # Longer text is the base.
        assert "Never use SELECT * in SQL queries" in merged

    def test_incorporates_unique_lines_from_shorter(self):
        pair = DuplicatePair(
            file_a="a.md",
            line_a=1,
            text_a="Never use SELECT *",
            file_b="b.md",
            line_b=1,
            text_b="Never use SELECT *\nUse explicit column lists instead",
            similarity=0.90,
        )
        merged = propose_merge(pair)
        assert "explicit column lists" in merged

    def test_no_duplication_of_identical_lines(self):
        pair = DuplicatePair(
            file_a="a.md",
            line_a=1,
            text_a="Always run tests\nBefore committing code",
            file_b="b.md",
            line_b=1,
            text_b="Always run tests\nBefore committing code",
            similarity=1.0,
        )
        merged = propose_merge(pair)
        # Should be identical to the input (no duplication).
        assert merged.count("Always run tests") == 1

    def test_merge_returns_string(self):
        pair = DuplicatePair(
            file_a="a.md",
            line_a=1,
            text_a="Rule A",
            file_b="b.md",
            line_b=1,
            text_b="Rule B",
            similarity=0.88,
        )
        merged = propose_merge(pair)
        assert isinstance(merged, str)
        assert len(merged) > 0
