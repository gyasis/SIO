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


class TestRealEmbeddingSearch:
    """Tests for real fastembed-based vector search."""

    def test_embedding_search_uses_vectors_when_available(self, corpus_dir):
        """When fastembed is available, search_embedding uses vector similarity."""
        from unittest.mock import MagicMock

        idx = index_corpus("claude-code", history_dir=corpus_dir)

        # Create a mock backend that returns deterministic embeddings
        mock_backend = MagicMock()
        # Give each chunk a unique embedding; make the query closest to chunk 0
        import numpy as np

        n_chunks = len(idx._chunks)
        dim = 8
        # Create embeddings: chunk i gets a vector with 1.0 at position i % dim
        chunk_embeddings = np.zeros((n_chunks, dim), dtype=np.float32)
        for i in range(n_chunks):
            chunk_embeddings[i, i % dim] = 1.0

        # Query embedding matches chunk 0 exactly
        query_emb = np.zeros(dim, dtype=np.float32)
        query_emb[0] = 1.0

        mock_backend.encode.return_value = chunk_embeddings
        mock_backend.encode_single.return_value = query_emb

        # Inject the mock backend and trigger embedding computation
        idx._backend = mock_backend
        idx._embeddings = chunk_embeddings

        results = idx.search_embedding("read foo", top_k=3)
        assert len(results) >= 1
        # The top result should be chunk 0 (closest to query vector)
        assert results[0].score > 0
        mock_backend.encode_single.assert_called_once_with("read foo")

    def test_embedding_search_different_from_keyword(self, corpus_dir):
        """Embedding search can rank differently than keyword search."""
        from unittest.mock import MagicMock

        import numpy as np

        idx = index_corpus("claude-code", history_dir=corpus_dir)

        n_chunks = len(idx._chunks)
        dim = 8

        # Make embedding search favor the LAST chunk (opposite of keyword order)
        chunk_embeddings = np.random.default_rng(42).random(
            (n_chunks, dim),
        ).astype(np.float32)
        # Normalize
        norms = np.linalg.norm(chunk_embeddings, axis=1, keepdims=True)
        chunk_embeddings = chunk_embeddings / np.maximum(norms, 1e-10)

        # Query = last chunk embedding (so last chunk ranks first)
        query_emb = chunk_embeddings[-1].copy()

        mock_backend = MagicMock()
        mock_backend.encode.return_value = chunk_embeddings
        mock_backend.encode_single.return_value = query_emb

        idx._backend = mock_backend
        idx._embeddings = chunk_embeddings

        emb_results = idx.search_embedding("session", top_k=3)
        kw_results = idx.search_keyword("session", top_k=3)

        # Both return results but ordering can differ
        assert len(emb_results) >= 1
        assert len(kw_results) >= 1
        # The top embedding result should be the last chunk
        assert emb_results[0].path == idx._chunks[-1]["path"]

    def test_fastembed_unavailable_falls_back_to_keyword(self, corpus_dir):
        """When fastembed import fails, search_embedding falls back to keyword."""
        from unittest.mock import patch

        idx = index_corpus("claude-code", history_dir=corpus_dir)
        # Ensure no embeddings are cached
        idx._embeddings = None
        idx._backend = None

        # Patch the import inside _ensure_embeddings to fail
        with patch(
            "sio.core.dspy.corpus_indexer._fastembed_available", False,
        ):
            results = idx.search_embedding("pytest tests")
            # Should still return results via keyword fallback
            assert len(results) >= 1
            assert any("pytest" in r.snippet.lower() for r in results)

    def test_ensure_embeddings_lazy_init(self, corpus_dir):
        """_ensure_embeddings only initializes once."""
        from unittest.mock import MagicMock, patch

        import numpy as np

        idx = index_corpus("claude-code", history_dir=corpus_dir)
        assert idx._embeddings is None  # Not yet computed

        mock_backend_cls = MagicMock()
        n_chunks = len(idx._chunks)
        mock_backend_cls.return_value.encode.return_value = np.ones(
            (n_chunks, 8), dtype=np.float32,
        )

        with patch(
            "sio.core.dspy.corpus_indexer._fastembed_available", True,
        ), patch(
            "sio.core.dspy.corpus_indexer.FastEmbedBackend", mock_backend_cls,
        ):
            idx._ensure_embeddings()
            assert idx._embeddings is not None
            first_call_count = mock_backend_cls.call_count

            # Second call should not re-initialize
            idx._ensure_embeddings()
            assert mock_backend_cls.call_count == first_call_count
