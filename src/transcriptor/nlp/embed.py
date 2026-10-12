"""Embeddings de texto para búsqueda y relaciones.

- ``SentenceTransformerEmbedder``: BGE-M3 (extra ``[embeddings]``), multilingüe y preciso.
- ``HashingEmbedder``: n-gramas de caracteres con *hashing*; sin dependencias ni descargas.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Protocol

import numpy as np

from transcriptor.nlp.text import STOPWORDS, fold, words
from transcriptor.paths import models_dir

log = logging.getLogger(__name__)


class TextEmbedder(Protocol):
    name: str
    dim: int
    related_threshold: float  # similitud mínima para «clases relacionadas»
    same_topic_threshold: float  # similitud para unir dos nombres de tema

    def embed(self, texts: list[str]) -> np.ndarray: ...


class HashingEmbedder:
    name = "hashing-v1"
    dim = 1024
    related_threshold = 0.30
    same_topic_threshold = 0.80

    def _features(self, text: str) -> dict[int, float]:
        feats: dict[int, float] = {}
        toks = [w for w in (fold(x) for x in words(text)) if w not in STOPWORDS and len(w) > 2]
        grams: list[str] = []
        for tok in toks:
            padded = f"<{tok}>"
            grams.extend(padded[i : i + 4] for i in range(max(1, len(padded) - 3)))
            grams.append("w:" + tok[:7])  # raíz aproximada (integral/integrales)
        for a, b in zip(toks, toks[1:], strict=False):
            grams.append(f"b:{a[:6]}_{b[:6]}")
        for g in grams:
            h = int.from_bytes(hashlib.blake2b(g.encode(), digest_size=4).digest(), "little")
            idx = h % self.dim
            feats[idx] = feats.get(idx, 0.0) + (1.0 if h & 0x80000000 else -1.0) * (2.0 if g.startswith("w:") else 1.0)
        return feats

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, text in enumerate(texts):
            for idx, val in self._features(text).items():
                out[i, idx] = np.sign(val) * np.log1p(abs(val))
            norm = np.linalg.norm(out[i])
            if norm > 0:
                out[i] /= norm
        return out


class SentenceTransformerEmbedder:
    related_threshold = 0.55
    same_topic_threshold = 0.85

    def __init__(self, model: str = "BAAI/bge-m3") -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model, cache_folder=str(models_dir() / "embeddings"))
        self.name = model
        self.dim = int(self._model.get_sentence_embedding_dimension() or 1024)

    def embed(self, texts: list[str]) -> np.ndarray:
        vecs = self._model.encode(texts, normalize_embeddings=True, batch_size=16)
        return np.asarray(vecs, dtype=np.float32)


_default: TextEmbedder | None = None


def default_text_embedder() -> TextEmbedder:
    global _default
    if _default is None:
        try:
            _default = SentenceTransformerEmbedder()
        except Exception:
            log.info("BGE-M3 no disponible; se usa el embedder ligero local")
            _default = HashingEmbedder()
    return _default


def cosine_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    if a.size == 0 or b.size == 0:
        return np.zeros((a.shape[0] if a.ndim else 0, b.shape[0] if b.ndim else 0))
    return a @ b.T
