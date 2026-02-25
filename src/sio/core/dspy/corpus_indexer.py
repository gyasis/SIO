"""Corpus indexer — BM25 + embedding index over conversation history."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path


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

    def search_keyword(
        self, query: str, top_k: int = 5,
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
                path=c["path"], score=s,
                snippet=c["text"][:200],
            )
            for s, c in scored[:top_k]
        ]

    def search_embedding(
        self, query: str, top_k: int = 5,
    ) -> list[SearchResult]:
        """Search by embedding similarity.

        V0.1: Falls back to keyword search.
        Full implementation will use fastembed vectors.
        """
        return self.search_keyword(query, top_k)


def _chunk_markdown(text: str, path: str, chunk_size: int = 500) -> list[dict]:
    """Split markdown into chunks by headers or fixed size."""
    sections = re.split(r'\n(?=#{1,3}\s)', text)
    chunks = []
    for section in sections:
        section = section.strip()
        if not section:
            continue
        if len(section) <= chunk_size:
            chunks.append({"text": section, "path": path})
        else:
            for i in range(0, len(section), chunk_size):
                piece = section[i:i + chunk_size].strip()
                if piece:
                    chunks.append({"text": piece, "path": path})
    return chunks


def index_corpus(
    platform: str,
    history_dir: str | None = None,
) -> CorpusIndex:
    """Build BM25 + embedding index over conversation history.

    Args:
        platform: Platform name (e.g., 'claude-code').
        history_dir: Directory containing .md files.
            Defaults to ~/.specstory/history/ for claude-code.

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
