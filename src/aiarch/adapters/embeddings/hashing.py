"""Offline embedders.

`HashingEmbedder` needs no model download: it hashes word unigrams and character
trigrams into a fixed-size, L2-normalised vector. Retrieval quality is far below a
real model, but it is deterministic and fast, which makes it the right default for
tests, CI and first-run. Use `SentenceTransformerEmbedder` (optional extra) or an
API embedder for real workloads.
"""
from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence

_WORD = re.compile(r"[a-z0-9]+")


class HashingEmbedder:
    def __init__(self, dim: int = 512) -> None:
        self.name = f"hashing-{dim}"
        self.dim = dim

    def _features(self, text: str) -> list[str]:
        words = _WORD.findall(text.lower())
        feats = [f"w:{w}" for w in words]
        for w in words:
            padded = f"#{w}#"
            feats += [f"c:{padded[i:i + 3]}" for i in range(len(padded) - 2)]
        return feats

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        out = []
        for t in texts:
            v = [0.0] * self.dim
            for f in self._features(t):
                h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "little")
                v[h % self.dim] += 1.0 if (h >> 63) == 0 else -1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
        return out


class SentenceTransformerEmbedder:
    """Install with `python -m pip install -e ".[local-models]"`."""

    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2") -> None:
        from sentence_transformers import SentenceTransformer  # optional dependency

        self._model = SentenceTransformer(model_name)
        self.name = model_name
        self.dim = int(self._model.get_sentence_embedding_dimension() or 0)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return self._model.encode(list(texts), normalize_embeddings=True).tolist()
