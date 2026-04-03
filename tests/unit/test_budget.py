"""Tests for sio.applier.budget -- instruction budget management."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np

from sio.applier.budget import (
    BudgetResult,
    check_budget,
    count_meaningful_lines,
    trigger_consolidation,
)
from sio.core.config import SIOConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_md(path: Path, content: str) -> Path:
    """Write *content* to a markdown file and return the path."""
    path.write_text(content, encoding="utf-8")
    return path


# =========================================================================
# TestCountMeaningfulLines
# =========================================================================


class TestCountMeaningfulLines:
    """count_meaningful_lines excludes blanks and HTML comments."""

    def test_counts_non_blank_lines(self, tmp_path: Path):
        f = _write_md(tmp_path / "rules.md", "Line one\nLine two\nLine three\n")
        assert count_meaningful_lines(f) == 3

    def test_excludes_blank_lines(self, tmp_path: Path):
        f = _write_md(
            tmp_path / "rules.md",
            "Line one\n\n\nLine two\n\n",
        )
        assert count_meaningful_lines(f) == 2

    def test_excludes_single_line_html_comments(self, tmp_path: Path):
        f = _write_md(
            tmp_path / "rules.md",
            "Real line\n<!-- this is a comment -->\nAnother real line\n",
        )
        assert count_meaningful_lines(f) == 2

    def test_excludes_multiline_html_comments(self, tmp_path: Path):
        content = (
            "Line before\n"
            "<!--\n"
            "This entire\n"
            "block is\n"
            "a comment\n"
            "-->\n"
            "Line after\n"
        )
        f = _write_md(tmp_path / "rules.md", content)
        assert count_meaningful_lines(f) == 2

    def test_mixed_blanks_and_comments(self, tmp_path: Path):
        content = (
            "# Title\n"
            "\n"
            "Real rule one\n"
            "<!-- comment -->\n"
            "\n"
            "Real rule two\n"
            "   \n"
            "Real rule three\n"
        )
        f = _write_md(tmp_path / "rules.md", content)
        assert count_meaningful_lines(f) == 4  # Title + 3 rules

    def test_returns_zero_for_missing_file(self, tmp_path: Path):
        assert count_meaningful_lines(tmp_path / "nonexistent.md") == 0

    def test_returns_zero_for_empty_file(self, tmp_path: Path):
        f = _write_md(tmp_path / "empty.md", "")
        assert count_meaningful_lines(f) == 0

    def test_whitespace_only_lines_excluded(self, tmp_path: Path):
        f = _write_md(tmp_path / "rules.md", "   \n\t\n  \t  \nReal line\n")
        assert count_meaningful_lines(f) == 1

    def test_inline_html_comment_removed(self, tmp_path: Path):
        """A line that is entirely an HTML comment should not count."""
        f = _write_md(
            tmp_path / "rules.md",
            "Real line\n<!-- hidden -->\n",
        )
        assert count_meaningful_lines(f) == 1

    def test_partial_inline_comment_still_counts(self, tmp_path: Path):
        """A line with text AND a comment still counts (text remains)."""
        f = _write_md(
            tmp_path / "rules.md",
            "Real text <!-- comment --> more text\n",
        )
        assert count_meaningful_lines(f) == 1


# =========================================================================
# TestCheckBudget
# =========================================================================


class TestCheckBudget:
    """check_budget returns correct status for various utilization levels."""

    def _make_config(self, primary: int = 100, supplementary: int = 50) -> SIOConfig:
        return SIOConfig(
            budget_cap_primary=primary,
            budget_cap_supplementary=supplementary,
        )

    def test_ok_when_under_budget(self, tmp_path: Path):
        """File at 50/100 lines adding 5 -> ok."""
        lines = "\n".join(f"Rule line {i}" for i in range(50))
        f = _write_md(tmp_path / "CLAUDE.md", lines + "\n")
        config = self._make_config(primary=100)
        result = check_budget(f, new_rule_lines=5, config=config)
        assert result.status == "ok"
        assert result.current_lines == 50
        assert result.cap == 100

    def test_ok_near_cap(self, tmp_path: Path):
        """File at 91/100 lines adding 5 -> ok (fits, but near cap)."""
        lines = "\n".join(f"Rule line {i}" for i in range(91))
        f = _write_md(tmp_path / "CLAUDE.md", lines + "\n")
        config = self._make_config(primary=100)
        result = check_budget(f, new_rule_lines=5, config=config)
        assert result.status == "ok"
        assert result.current_lines == 91

    def test_consolidate_when_exceeds_cap(self, tmp_path: Path):
        """File at 95/100 lines adding 8 -> consolidate."""
        lines = "\n".join(f"Rule line {i}" for i in range(95))
        f = _write_md(tmp_path / "CLAUDE.md", lines + "\n")
        config = self._make_config(primary=100)
        result = check_budget(f, new_rule_lines=8, config=config)
        assert result.status == "consolidate"
        assert result.current_lines == 95
        assert result.cap == 100

    def test_blocked_at_exact_cap(self, tmp_path: Path):
        """File at 100/100 lines adding 1 -> blocked (already at capacity)."""
        lines = "\n".join(f"Rule line {i}" for i in range(100))
        f = _write_md(tmp_path / "CLAUDE.md", lines + "\n")
        config = self._make_config(primary=100)
        result = check_budget(f, new_rule_lines=1, config=config)
        assert result.status == "blocked"

    def test_uses_supplementary_cap_for_non_claude_md(self, tmp_path: Path):
        """Non-CLAUDE.md files use budget_cap_supplementary."""
        lines = "\n".join(f"Rule line {i}" for i in range(10))
        f = _write_md(tmp_path / "rules.md", lines + "\n")
        config = self._make_config(supplementary=50)
        result = check_budget(f, new_rule_lines=5, config=config)
        assert result.cap == 50
        assert result.status == "ok"

    def test_budget_result_is_namedtuple(self, tmp_path: Path):
        f = _write_md(tmp_path / "CLAUDE.md", "One line\n")
        config = self._make_config()
        result = check_budget(f, new_rule_lines=1, config=config)
        assert isinstance(result, BudgetResult)
        assert hasattr(result, "status")
        assert hasattr(result, "current_lines")
        assert hasattr(result, "cap")
        assert hasattr(result, "message")


# =========================================================================
# TestTriggerConsolidation
# =========================================================================


class _FakeBackend:
    """A fake embedding backend that returns controlled embeddings."""

    def __init__(self, embeddings: dict[str, np.ndarray]):
        self._embeddings = embeddings
        self._default = np.random.default_rng(42).random(384).astype(np.float32)

    def encode(self, texts: list[str]) -> np.ndarray:
        result = []
        for t in texts:
            # Try to match against known texts by checking if known text
            # is contained in the provided text (handles merged texts).
            matched = False
            for key, emb in self._embeddings.items():
                if key in t:
                    result.append(emb)
                    matched = True
                    break
            if not matched:
                result.append(self._default.copy())
        return np.stack(result)


class TestTriggerConsolidation:
    """trigger_consolidation merges similar blocks and rewrites the file."""

    def _make_similar_embeddings(self) -> dict[str, np.ndarray]:
        """Create embeddings where block A and B are near-identical."""
        rng = np.random.default_rng(42)
        base = rng.random(384).astype(np.float32)
        base /= np.linalg.norm(base)

        similar = base + rng.normal(0, 0.01, 384).astype(np.float32)
        similar /= np.linalg.norm(similar)

        different = rng.random(384).astype(np.float32)
        different /= np.linalg.norm(different)

        return {
            "Never use SELECT *": base,
            "Avoid SELECT * in queries": similar,
            "Always run tests before committing": different,
        }

    def test_merges_similar_blocks(self, tmp_path: Path):
        content = (
            "Never use SELECT * in any SQL query\n"
            "\n"
            "Avoid SELECT * in queries -- use explicit columns\n"
            "\n"
            "Always run tests before committing\n"
        )
        f = _write_md(tmp_path / "CLAUDE.md", content)
        config = SIOConfig(dedup_threshold=0.85)
        embs = self._make_similar_embeddings()
        fake = _FakeBackend(embs)

        with patch("sio.applier.budget._get_backend", return_value=fake):
            result = trigger_consolidation(f, config)

        assert result is True
        new_content = f.read_text()
        # Should have fewer blocks now.
        assert "Always run tests before committing" in new_content

    def test_no_merge_when_blocks_dissimilar(self, tmp_path: Path):
        content = (
            "Never use SELECT *\n"
            "\n"
            "Always run tests before committing\n"
        )
        f = _write_md(tmp_path / "CLAUDE.md", content)
        config = SIOConfig(dedup_threshold=0.99)  # Very high threshold

        rng = np.random.default_rng(42)
        embs = {
            "Never use SELECT *": rng.random(384).astype(np.float32),
            "Always run tests before committing": rng.random(384).astype(
                np.float32
            ),
        }
        fake = _FakeBackend(embs)

        with patch("sio.applier.budget._get_backend", return_value=fake):
            result = trigger_consolidation(f, config)

        assert result is False

    def test_returns_false_for_missing_file(self, tmp_path: Path):
        config = SIOConfig()
        result = trigger_consolidation(tmp_path / "missing.md", config)
        assert result is False

    def test_returns_false_for_single_block(self, tmp_path: Path):
        f = _write_md(tmp_path / "CLAUDE.md", "Only one block\n")
        config = SIOConfig()
        result = trigger_consolidation(f, config)
        assert result is False


# =========================================================================
# TestConsolidationTriggersAndBlocking (integration scenarios)
# =========================================================================


class TestConsolidationTriggersAndBlocking:
    """Scenario: file at 95/100 lines, adding 8 lines -> consolidate.
    Scenario: file at 100/100, consolidation finds no candidates -> blocked.
    """

    def test_consolidation_trigger_at_95_percent(self, tmp_path: Path):
        """File at 95 lines, adding 8 -> check says consolidate."""
        lines = "\n".join(f"Rule line {i}" for i in range(95))
        f = _write_md(tmp_path / "CLAUDE.md", lines + "\n")
        config = SIOConfig(budget_cap_primary=100)
        result = check_budget(f, new_rule_lines=8, config=config)
        assert result.status == "consolidate"
        assert result.current_lines == 95
        assert "exceeds cap" in result.message

    def test_blocked_when_consolidation_finds_no_candidates(
        self, tmp_path: Path
    ):
        """File at 100 lines, consolidation fails -> effectively blocked.

        We simulate consolidation returning False (no merges found),
        meaning the caller should treat this as 'blocked'.
        """
        lines = "\n".join(f"Unique rule {i}" for i in range(100))
        f = _write_md(tmp_path / "CLAUDE.md", lines + "\n")
        config = SIOConfig(budget_cap_primary=100, dedup_threshold=0.99)

        budget = check_budget(f, new_rule_lines=1, config=config)
        assert budget.status == "blocked"

        # Consolidation with very high threshold should find nothing.
        rng = np.random.default_rng(42)
        embs = {
            f"Unique rule {i}": rng.random(384).astype(np.float32)
            for i in range(100)
        }
        fake = _FakeBackend(embs)

        with patch("sio.applier.budget._get_backend", return_value=fake):
            merged = trigger_consolidation(f, config)

        # No merges => caller knows it's "blocked".
        assert merged is False
