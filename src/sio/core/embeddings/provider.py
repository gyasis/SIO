"""Abstract embedding backend interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np


class EmbeddingBackend(ABC):
    """Abstract base class for embedding providers."""

    @abstractmethod
    def encode(self, texts: list[str]) -> np.ndarray:
        """Encode multiple texts into embeddings.

        Args:
            texts: List of strings to encode.

        Returns:
            numpy ndarray of shape (len(texts), embedding_dim).
        """
        ...

    @abstractmethod
    def encode_single(self, text: str) -> np.ndarray:
        """Encode a single text into an embedding vector.

        Args:
            text: String to encode.

        Returns:
            numpy ndarray of shape (embedding_dim,).
        """
        ...
