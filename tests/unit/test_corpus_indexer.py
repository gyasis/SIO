"""T047b [US4] Unit tests for corpus indexer."""

from __future__ import annotations

import pytest

from sio.core.dspy.corpus_indexer import index_corpus


@pytest.fixture
def corpus_dir(tmp_path):
    """Create a temp directory with markdown files."""
    (tmp_path / "session-001.md").write_text(
        "# Session 1\n\nUser asked to read foo.py.\n"
        "The Read tool returned file contents.\n"
    )
    (tmp_path / "session-002.md").write_text(
        "# Session 2\n\nUser ran tests with pytest.\n"
        "## Results\nAll 10 tests passed.\n"
    )
    (tmp_path / "session-003.md").write_text(
        "# Session 3\n\nUser debugged a Bash error.\n"
        "The error was a missing file path.\n"
    )
    return str(tmp_path)


class TestIndexCorpus:
    """index_corpus builds an index over markdown files."""

    def test_indexes_all_files(self, corpus_dir):
        idx = index_corpus("claude-code", history_dir=corpus_dir)
        assert idx.file_count == 3

    def test_creates_chunks(self, corpus_dir):
        idx = index_corpus("claude-code", history_dir=corpus_dir)
        assert idx.chunk_count > 0

    def test_empty_dir_returns_empty_index(self, tmp_path):
        idx = index_corpus("claude-code", history_dir=str(tmp_path))
        assert idx.file_count == 0
        assert idx.chunk_count == 0

    def test_nonexistent_dir_returns_empty_index(self):
        idx = index_corpus("claude-code", history_dir="/nonexistent/path")
        assert idx.file_count == 0
        assert idx.chunk_count == 0


class TestKeywordSearch:
    """search_keyword returns ranked results."""

    def test_finds_matching_content(self, corpus_dir):
        idx = index_corpus("claude-code", history_dir=corpus_dir)
        results = idx.search_keyword("pytest tests")
        assert len(results) >= 1
        assert any("pytest" in r.snippet.lower() for r in results)

    def test_returns_scored_results(self, corpus_dir):
        idx = index_corpus("claude-code", history_dir=corpus_dir)
        results = idx.search_keyword("Read foo")
        assert all(r.score > 0 for r in results)

    def test_respects_top_k(self, corpus_dir):
        idx = index_corpus("claude-code", history_dir=corpus_dir)
        results = idx.search_keyword("session", top_k=2)
        assert len(results) <= 2

    def test_no_match_returns_empty(self, corpus_dir):
        idx = index_corpus("claude-code", history_dir=corpus_dir)
        results = idx.search_keyword("zzzznonexistent")
        assert results == []


class TestEmbeddingSearch:
    """search_embedding returns semantically similar results."""

    def test_returns_results(self, corpus_dir):
        idx = index_corpus("claude-code", history_dir=corpus_dir)
        results = idx.search_embedding("read foo")
        assert len(results) >= 1

    def test_empty_index_returns_empty(self, tmp_path):
        idx = index_corpus("claude-code", history_dir=str(tmp_path))
        results = idx.search_embedding("anything")
        assert results == []
