"""FastEmbed-based local embedding backend with SQLite caching."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import numpy as np
from fastembed import TextEmbedding

from sio.core.embeddings.provider import EmbeddingBackend


class FastEmbedBackend(EmbeddingBackend):
    """Local embedding backend using fastembed (ONNX-based).

    Uses sentence-transformers/all-MiniLM-L6-v2 by default (384 dimensions).
    Caches embeddings in a SQLite database keyed on (sha256(text), model_name).
    """

    def __init__(
        self,
        model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
        cache_dir: str | None = None,
    ):
        self.model_name = model_name
        self._model = TextEmbedding(model_name=model_name)
        self._cache_conn = None
        if cache_dir:
            cache_path = Path(cache_dir) / "embedding_cache.db"
            self._cache_conn = sqlite3.connect(str(cache_path))
            self._cache_conn.execute(
                "CREATE TABLE IF NOT EXISTS cache "
                "(text_hash TEXT, model_name TEXT, embedding BLOB, "
                "PRIMARY KEY (text_hash, model_name))"
            )
            self._cache_conn.commit()

    def _text_hash(self, text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _cache_get(self, text: str) -> np.ndarray | None:
        if not self._cache_conn:
            return None
        row = self._cache_conn.execute(
            "SELECT embedding FROM cache WHERE text_hash = ? AND model_name = ?",
            (self._text_hash(text), self.model_name),
        ).fetchone()
        if row:
            return np.frombuffer(row[0], dtype=np.float32)
        return None

    def _cache_put(self, text: str, embedding: np.ndarray) -> None:
        if not self._cache_conn:
            return
        self._cache_conn.execute(
            "INSERT OR REPLACE INTO cache (text_hash, model_name, embedding) VALUES (?, ?, ?)",
            (self._text_hash(text), self.model_name, embedding.astype(np.float32).tobytes()),
        )
        self._cache_conn.commit()

    def encode(self, texts: list[str]) -> np.ndarray:
        results = []
        texts_to_encode = []
        indices_to_encode = []

        for i, text in enumerate(texts):
            cached = self._cache_get(text)
            if cached is not None:
                results.append((i, cached))
            else:
                texts_to_encode.append(text)
                indices_to_encode.append(i)

        if texts_to_encode:
            embeddings = list(self._model.embed(texts_to_encode))
            for idx, text, emb in zip(indices_to_encode, texts_to_encode, embeddings):
                emb_array = np.array(emb, dtype=np.float32)
                self._cache_put(text, emb_array)
                results.append((idx, emb_array))

        results.sort(key=lambda x: x[0])
        return np.stack([r[1] for r in results])

    def encode_single(self, text: str) -> np.ndarray:
        return self.encode([text])[0]
