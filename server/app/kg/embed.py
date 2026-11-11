"""Title embeddings for the semantic-similarity stage.

* FastEmbedder: sentence-transformers/all-MiniLM-L6-v2 via fastembed (ONNX, no PyTorch). Loaded lazily.
* HashEmbedder: character-trigram hashing. No model download; used by tests and as a fallback.
Vectors are L2-normalized, so cosine similarity = dot product.
"""
from __future__ import annotations

import hashlib
import logging
import threading
from pathlib import Path

import numpy as np

log = logging.getLogger("ht.kg.embed")


class HashEmbedder:
    dim = 512
    name = "hash-trigram"

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            s = f"  {t.lower()}  "
            for j in range(len(s) - 2):
                h = int.from_bytes(hashlib.blake2b(s[j:j + 3].encode(), digest_size=4).digest(), "little")
                out[i, h % self.dim] += 1.0
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.maximum(norms, 1e-9)


class FastEmbedder:
    def __init__(self, model: str, cache_dir: Path):
        self.name = model
        self._model_name = model
        self._cache_dir = cache_dir
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        with self._lock:
            if self._model is None:
                from fastembed import TextEmbedding
                self._cache_dir.mkdir(parents=True, exist_ok=True)
                log.info("loading embedding model %s", self._model_name)
                self._model = TextEmbedding(self._model_name, cache_dir=str(self._cache_dir))
        return self._model

    def embed(self, texts: list[str]) -> np.ndarray:
        vecs = np.array(list(self._load().embed(texts)), dtype=np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        return vecs / np.maximum(norms, 1e-9)


def to_blob(v: np.ndarray) -> bytes:
    return np.asarray(v, dtype=np.float32).tobytes()


def from_blob(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.float32)
