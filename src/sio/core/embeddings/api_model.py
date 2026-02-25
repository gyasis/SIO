"""External API embedding backend with fastembed fallback (FR-024)."""

from __future__ import annotations

import numpy as np

try:
    import httpx
except ImportError:
    httpx = None

from sio.core.embeddings.local_model import FastEmbedBackend
from sio.core.embeddings.provider import EmbeddingBackend


class ApiEmbedBackend(EmbeddingBackend):
    """Embedding backend that calls an external API.

    Falls back to local fastembed if no config provided.
    """

    def __init__(
        self,
        config: dict | None = None,
        cache_dir: str | None = None,
    ):
        self._config = config
        self._fallback = FastEmbedBackend(cache_dir=cache_dir) if cache_dir else FastEmbedBackend()

        if config and config.get("api_url"):
            self._use_api = True
            self._api_url = config["api_url"]
            self._api_key = config.get("api_key", "")
        else:
            self._use_api = False

    def encode(self, texts: list[str]) -> np.ndarray:
        if not self._use_api:
            return self._fallback.encode(texts)

        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        response = httpx.post(
            self._api_url,
            json={"texts": texts},
            headers=headers,
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()
        embeddings = data.get("embeddings", data.get("data", []))
        return np.array(embeddings, dtype=np.float32)

    def encode_single(self, text: str) -> np.ndarray:
        return self.encode([text])[0]
