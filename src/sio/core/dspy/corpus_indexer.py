"""Corpus indexer — BM25 + embedding index over conversation history."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# Lazy import guard for fastembed
try:
    from sio.core.embeddings.local_model import FastEmbedBackend

    _fastembed_available = True
except ImportError:
    FastEmbedBackend = None  # type: ignore[assignment,misc]
    _fastembed_available = False


@dataclass
class SearchResult:
    """Single search result from corpus index."""

    path: str
    score: float
    snippet: str


@dataclass
class CorpusIndex:
    """Index over a corpus of markdown files."""

    file_count: int
    chunk_count: int
    _chunks: list[dict] = field(default_factory=list, repr=False)
    _embeddings: np.ndarray | None = field(default=None, repr=False)
    _backend: object | None = field(default=None, repr=False)

    def search_keyword(
        self,
        query: str,
        top_k: int = 5,
    ) -> list[SearchResult]:
        """Search by keyword (BM25-style term matching)."""
        query_terms = set(query.lower().split())
        scored = []
        for chunk in self._chunks:
            text_lower = chunk["text"].lower()
            hits = sum(1 for t in query_terms if t in text_lower)
            if hits > 0:
                score = hits / max(len(query_terms), 1)
                scored.append((score, chunk))
        scored.sort(key=lambda x: -x[0])
        return [
            SearchResult(
                path=c["path"],
                score=s,
                snippet=c["text"][:200],
            )
            for s, c in scored[:top_k]
        ]

    def _ensure_embeddings(self) -> None:
        """Lazily compute embeddings for all chunks using fastembed.

        Only initializes once. No-op if fastembed is unavailable or chunks are empty.
        """
        if self._embeddings is not None:
            return
        if not self._chunks:
            return
        if not _fastembed_available:
            return

        self._backend = FastEmbedBackend()
        texts = [c["text"] for c in self._chunks]
        self._embeddings = self._backend.encode(texts)

    def search_embedding(
        self,
        query: str,
        top_k: int = 5,
        min_similarity: float = 0.3,
    ) -> list[SearchResult]:
        """Search by embedding (cosine similarity via fastembed vectors).

        Falls back to keyword search when fastembed is unavailable or
        embeddings cannot be computed.

        Args:
            query: Search query string.
            top_k: Maximum number of results to return.
            min_similarity: Minimum cosine similarity threshold for results.
        """
        self._ensure_embeddings()

        if self._embeddings is None or len(self._chunks) == 0:
            # Fallback to keyword search
            return self.search_keyword(query, top_k)

        query_emb = np.asarray(self._backend.encode_single(query)).flatten()

        # Cosine similarity: dot(A, q) / (||A|| * ||q||)
        norms = np.linalg.norm(self._embeddings, axis=1) * np.linalg.norm(query_emb)
        sims = np.dot(self._embeddings, query_emb) / np.maximum(norms, 1e-10)

        top_idx = np.argsort(-sims)[:top_k]
        return [
            SearchResult(
                path=self._chunks[i]["path"],
                score=float(sims[i]),
                snippet=self._chunks[i]["text"][:200],
            )
            for i in top_idx
            if float(sims[i]) >= min_similarity
        ]


def _chunk_markdown(text: str, path: str, chunk_size: int = 500) -> list[dict]:
    """Split markdown into chunks by headers or fixed size."""
    sections = re.split(r"\n(?=#{1,3}\s)", text)
    chunks = []
    for section in sections:
        section = section.strip()
        if not section:
            continue
        if len(section) <= chunk_size:
            chunks.append({"text": section, "path": path})
        else:
            for i in range(0, len(section), chunk_size):
                piece = section[i : i + chunk_size].strip()
                if piece:
                    chunks.append({"text": piece, "path": path})
    return chunks


def index_corpus(
    platform: str,
    history_dir: str | None = None,
) -> CorpusIndex:
    """Build BM25 + embedding index over conversation history.

    Args:
        platform: Platform name (e.g., the value of DEFAULT_PLATFORM).
        history_dir: Directory containing .md files.
            Defaults to ~/.specstory/history/ for the default platform.

    Returns:
        CorpusIndex with keyword and embedding search.
    """
    if history_dir is None:
        history_dir = os.path.expanduser("~/.specstory/history/")

    md_files = []
    history_path = Path(history_dir)

    if history_path.exists():
        md_files = sorted(history_path.glob("*.md"))

    all_chunks: list[dict] = []
    for md_file in md_files:
        text = md_file.read_text(errors="replace")
        chunks = _chunk_markdown(text, str(md_file))
        all_chunks.extend(chunks)

    return CorpusIndex(
        file_count=len(md_files),
        chunk_count=len(all_chunks),
        _chunks=all_chunks,
    )
