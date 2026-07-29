"""Unit tests for sio.core.embeddings — embedding backends and caching."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from sio.core.embeddings.api_model import ApiEmbedBackend
from sio.core.embeddings.local_model import FastEmbedBackend
from sio.core.embeddings.provider import EmbeddingBackend


@pytest.fixture
def fastembed_backend(tmp_path):
    return FastEmbedBackend(cache_dir=str(tmp_path))


class TestEmbeddingBackendABC:
    def test_embedding_backend_is_abstract(self):
        with pytest.raises(TypeError):
            EmbeddingBackend()


class TestFastEmbedEncode:
    def test_fastembed_encode_returns_ndarray(self, fastembed_backend):
        result = fastembed_backend.encode(["hello world", "test"])
        assert isinstance(result, np.ndarray)

    def test_fastembed_encode_single_returns_ndarray(self, fastembed_backend):
        result = fastembed_backend.encode_single("hello world")
        assert isinstance(result, np.ndarray)
        assert result.ndim == 1

    def test_fastembed_encode_shape(self, fastembed_backend):
        texts = ["one", "two", "three"]
        result = fastembed_backend.encode(texts)
        assert result.shape[0] == 3
        assert result.shape[1] == 384  # sentence-transformers/all-MiniLM-L6-v2

    def test_fastembed_encode_single_shape(self, fastembed_backend):
        result = fastembed_backend.encode_single("single")
        assert result.shape == (384,)


class TestFastEmbedCache:
    def test_fastembed_cache_hit(self, fastembed_backend):
        text = ["Cache me if you can."]
        t0 = time.perf_counter()
        first = fastembed_backend.encode(text)
        cold_ms = (time.perf_counter() - t0) * 1000

        t1 = time.perf_counter()
        second = fastembed_backend.encode(text)
        warm_ms = (time.perf_counter() - t1) * 1000

        np.testing.assert_array_equal(first, second)
        assert warm_ms < cold_ms

    def test_fastembed_cache_miss(self, fastembed_backend):
        emb_a = fastembed_backend.encode(["alpha sentence"])
        emb_b = fastembed_backend.encode(["completely different text"])
        assert not np.array_equal(emb_a, emb_b)

    @pytest.mark.network
    def test_fastembed_model_swap_invalidates_cache(self, tmp_path):
        # Downloads a SECOND ONNX model (paraphrase-multilingual-MiniLM-L12-v2),
        # so it needs network / a warm fastembed cache — opt in with --runnetwork.
        # The invalidation LOGIC itself is covered hermetically in
        # TestFastEmbedCacheInvalidation below.
        backend_v1 = FastEmbedBackend(
            model_name="sentence-transformers/all-MiniLM-L6-v2", cache_dir=str(tmp_path)
        )
        backend_v1.encode(["seed text for cache"])
        backend_v2 = FastEmbedBackend(
            model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            cache_dir=str(tmp_path),
        )
        result = backend_v2.encode(["seed text for cache"])
        assert isinstance(result, np.ndarray)


class TestApiEmbedBackend:
    def test_api_backend_with_mocked_http(self, tmp_path):
        fake_embedding = np.random.default_rng(42).random(384).tolist()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"embeddings": [fake_embedding]}
        config = {"api_url": "https://embeddings.example.com/v1/encode", "api_key": "test-key"}
        with patch("sio.core.embeddings.api_model.httpx.post", return_value=mock_response):
            backend = ApiEmbedBackend(config=config, cache_dir=str(tmp_path))
            result = backend.encode(["test input"])
        assert isinstance(result, np.ndarray)
        assert result.shape == (1, 384)

    def test_api_backend_fallback_to_fastembed(self, tmp_path):
        backend = ApiEmbedBackend(config=None, cache_dir=str(tmp_path))
        result = backend.encode(["fallback test"])
        assert isinstance(result, np.ndarray)
        assert result.shape[1] == 384


class TestFastEmbedCacheInvalidation:
    """The cache is keyed on (text_hash, model_name) — a model swap must MISS.

    Hermetic: ``TextEmbedding`` is mocked, so the invalidation contract is
    covered in the default suite without downloading a second ONNX model (the
    networked end-to-end version is the --runnetwork test above).
    """

    @staticmethod
    def _backend(tmp_path, model_name: str, fill: float) -> FastEmbedBackend:
        """A backend whose model always embeds to a constant vector of `fill`."""
        with patch("sio.core.embeddings.local_model.TextEmbedding") as mock_te:
            mock_te.return_value.embed.side_effect = lambda texts: [
                np.full(384, fill, dtype=np.float32) for _ in texts
            ]
            # self._model is the mock instance, which outlives this patch scope.
            return FastEmbedBackend(model_name=model_name, cache_dir=str(tmp_path))

    def test_model_swap_misses_cache_and_reencodes(self, tmp_path):
        v1 = self._backend(tmp_path, "model-a", 0.25)
        assert v1.encode(["seed text"])[0][0] == np.float32(0.25)

        v2 = self._backend(tmp_path, "model-b", 0.75)  # same cache dir, new model
        out = v2.encode(["seed text"])

        assert out[0][0] == np.float32(0.75), "stale model-a vector served after swap"
        v2._model.embed.assert_called_once()  # proves it really re-encoded

    def test_same_model_hits_cache_and_skips_the_model(self, tmp_path):
        first = self._backend(tmp_path, "model-a", 0.25)
        first.encode(["seed text"])

        second = self._backend(tmp_path, "model-a", 0.99)  # same name, new vector
        out = second.encode(["seed text"])

        assert out[0][0] == np.float32(0.25), "cache should have been served"
        second._model.embed.assert_not_called()

    def test_close_is_safe_on_a_half_built_object(self):
        """Regression: __init__ raising left __del__ -> close() with no _cache_conn.

        TextEmbedding() can raise (missing ONNX file, no network); the resulting
        AttributeError masked the real error as 'Exception ignored in __del__'.
        """
        obj = FastEmbedBackend.__new__(FastEmbedBackend)  # __init__ never ran
        obj.close()  # must not raise AttributeError
        obj.__del__()

    def test_constructor_failure_surfaces_the_real_error(self, tmp_path):
        with patch(
            "sio.core.embeddings.local_model.TextEmbedding",
            side_effect=RuntimeError("model file missing"),
        ):
            with pytest.raises(RuntimeError, match="model file missing"):
                FastEmbedBackend(cache_dir=str(tmp_path))
