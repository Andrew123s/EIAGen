"""Embedding backends (CPU only).

* ``sentence_transformers`` - BAAI/bge-small-en-v1.5 (384-d) semantic embeddings.
* ``hashing`` - scikit-learn HashingVectorizer (lexical, fully offline, fast).
  Used automatically if sentence-transformers or the model is unavailable,
  and selected explicitly in the test suite for speed and determinism.
"""

from __future__ import annotations

import numpy as np

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger("embeddings")

_BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class EmbeddingService:
    def __init__(self, backend: str, model_name: str):
        self.requested_backend = backend
        self.model_name = model_name
        self._model = None
        self._vectorizer = None
        self.backend = backend
        if backend == "sentence_transformers":
            try:
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(model_name, device="cpu")
            except Exception as exc:  # model download blocked, package missing...
                log.warning("sentence-transformers unavailable (%s); using hashing embeddings", type(exc).__name__)
                self.backend = "hashing"
        if self.backend == "hashing":
            from sklearn.feature_extraction.text import HashingVectorizer

            self._vectorizer = HashingVectorizer(
                n_features=2 ** 12, ngram_range=(1, 2), alternate_sign=False,
                norm="l2", stop_words="english", lowercase=True)

    @property
    def name(self) -> str:
        return f"st:{self.model_name}" if self.backend == "sentence_transformers" else "hashing:4096"

    def _encode(self, texts: list[str]) -> np.ndarray:
        if self._model is not None:
            vecs = self._model.encode(texts, batch_size=32, normalize_embeddings=True,
                                      show_progress_bar=False, convert_to_numpy=True)
            return vecs.astype("float32")
        assert self._vectorizer is not None
        return self._vectorizer.transform(texts).toarray().astype("float32")

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 1), dtype="float32")
        return self._encode(texts)

    def embed_query(self, text: str) -> np.ndarray:
        if self._model is not None and "bge" in self.model_name.lower():
            text = _BGE_QUERY_PREFIX + text
        return self._encode([text])[0]


_service: EmbeddingService | None = None


def get_embedding_service() -> EmbeddingService:
    global _service
    settings = get_settings()
    if _service is None or _service.requested_backend != settings.embedding_backend \
            or _service.model_name != settings.embedding_model:
        _service = EmbeddingService(settings.embedding_backend, settings.embedding_model)
    return _service


def reset_embedding_service() -> None:
    global _service
    _service = None
