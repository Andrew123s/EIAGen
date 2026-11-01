"""FAISS vector store keyed by regulatory chunk id.

The database is the source of truth. The index holds embeddings of active
chunks only and is rebuilt whenever the active chunk set or the embedding
backend changes (tracked with a fingerprint), so superseded and withdrawn
documents never surface in retrieval.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from app.core.logging import get_logger

log = get_logger("vector_store")

try:
    import faiss  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    faiss = None


def fingerprint(chunk_ids: list[int], backend_name: str) -> str:
    h = hashlib.sha1(backend_name.encode())
    h.update(",".join(map(str, sorted(chunk_ids))).encode())
    return h.hexdigest()[:16]


class VectorStore:
    def __init__(self, directory: Path):
        self.directory = directory
        self.fingerprint: str | None = None
        self._index = None
        self._ids: np.ndarray = np.zeros(0, dtype="int64")
        self._matrix: np.ndarray | None = None   # numpy fallback

    @property
    def size(self) -> int:
        return int(self._ids.shape[0])

    def build(self, ids: list[int], vectors: np.ndarray, fp: str) -> None:
        self._ids = np.asarray(ids, dtype="int64")
        vectors = np.ascontiguousarray(vectors, dtype="float32")
        if faiss is not None and len(ids):
            index = faiss.IndexIDMap(faiss.IndexFlatIP(vectors.shape[1]))
            index.add_with_ids(vectors, self._ids)
            self._index = index
            self._matrix = None
        else:
            self._index = None
            self._matrix = vectors
        self.fingerprint = fp
        self.save()

    def search(self, query: np.ndarray, k: int) -> list[tuple[int, float]]:
        if self.size == 0:
            return []
        k = min(k, self.size)
        q = np.ascontiguousarray(query.reshape(1, -1), dtype="float32")
        if self._index is not None:
            scores, ids = self._index.search(q, k)
            return [(int(i), float(s)) for i, s in zip(ids[0], scores[0]) if i != -1]
        assert self._matrix is not None
        sims = self._matrix @ q[0]
        order = np.argsort(-sims)[:k]
        return [(int(self._ids[i]), float(sims[i])) for i in order]

    # ------------------------------------------------------------ persistence

    def save(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        meta = {"fingerprint": self.fingerprint, "ids": self._ids.tolist(),
                "backend": "faiss" if self._index is not None else "numpy"}
        (self.directory / "index_meta.json").write_text(json.dumps(meta), encoding="utf-8")
        if self._index is not None:
            faiss.write_index(self._index, str(self.directory / "index.faiss"))
        elif self._matrix is not None:
            np.save(self.directory / "matrix.npy", self._matrix)

    def load(self) -> bool:
        meta_path = self.directory / "index_meta.json"
        if not meta_path.exists():
            return False
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            self._ids = np.asarray(meta["ids"], dtype="int64")
            if meta["backend"] == "faiss" and faiss is not None and (self.directory / "index.faiss").exists():
                self._index = faiss.read_index(str(self.directory / "index.faiss"))
            elif (self.directory / "matrix.npy").exists():
                self._matrix = np.load(self.directory / "matrix.npy")
            else:
                return False
            self.fingerprint = meta["fingerprint"]
            return True
        except Exception as exc:  # corrupt index: rebuild
            log.warning("Could not load vector index (%s); it will be rebuilt", exc)
            return False
