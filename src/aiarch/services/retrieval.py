"""Hybrid retrieval: BM25 (sparse) + dense, fused with Reciprocal Rank Fusion.

Why hybrid by default: dense search misses exact identifiers (policy numbers, error
codes, product SKUs); BM25 misses paraphrases. RRF needs no score calibration
between the two, which is why it is the safe default fusion (ADR-0003).
A reranker is a pluggable callable so a cross-encoder can be added without
changing this module.

Abstaining is decided here, before any model is called. A query is "in scope" when
either signal is confident: the best dense score clears `min_dense_score`, or the
best chunk contains at least `min_term_coverage` of the query's content words.
Stopwords are ignored on the sparse side, otherwise "what is the ..." matches every
document and no question is ever out of scope.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from aiarch.domain.models import Chunk, Principal, ScoredChunk
from aiarch.ports import Embedder, Tracer, VectorStore

_TOKEN = re.compile(r"[a-z0-9]+")
Reranker = Callable[[str, list[ScoredChunk]], list[ScoredChunk]]


# Function words carry no topical signal. Keeping them in BM25 lets any question match any chunk.
STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "can", "could", "did", "do", "does", "for",
    "from", "had", "has", "have", "how", "i", "if", "in", "into", "is", "it", "its", "many", "may", "me",
    "much", "my", "no", "not", "of", "on", "or", "our", "should", "so", "than", "that", "the", "their", "them",
    "then", "there", "these", "they", "this", "those", "to", "was", "we", "were", "what", "when", "where",
    "which", "who", "whom", "why", "will", "with", "within", "would", "you", "your",
})


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def content_terms(text: str) -> list[str]:
    """Tokens with stopwords removed: what BM25 indexes and what term coverage is measured on."""
    return [t for t in tokenize(text) if t not in STOPWORDS]


class BM25:
    def __init__(self, chunks: Sequence[Chunk], k1: float = 1.5, b: float = 0.75) -> None:
        self.chunks = list(chunks)
        self.k1, self.b = k1, b
        self.docs = [Counter(content_terms(c.text)) for c in self.chunks]
        self.lens = [sum(d.values()) for d in self.docs]
        self.avg = (sum(self.lens) / len(self.lens)) if self.lens else 0.0
        df: Counter[str] = Counter()
        for d in self.docs:
            df.update(d.keys())
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def search(self, query: str, k: int) -> list[ScoredChunk]:
        q = content_terms(query)
        scores = []
        for i, d in enumerate(self.docs):
            s = 0.0
            for t in q:
                if t in d:
                    tf = d[t]
                    s += self.idf[t] * tf * (self.k1 + 1) / (
                        tf + self.k1 * (1 - self.b + self.b * self.lens[i] / (self.avg or 1)))
            if s > 0:
                scores.append((s, i))
        scores.sort(reverse=True)
        return [ScoredChunk(self.chunks[i], s, "sparse") for s, i in scores[:k]]


def term_coverage(query: str, hits: Sequence[ScoredChunk]) -> float:
    """Largest share of the query's content words found in any one of `hits` (0.0 to 1.0)."""
    q = set(content_terms(query))
    if not q or not hits:
        return 0.0
    return max(len(q & set(content_terms(h.chunk.text))) / len(q) for h in hits)


def rrf(result_lists: Sequence[Sequence[ScoredChunk]], k: int = 60) -> list[ScoredChunk]:
    fused: dict[str, float] = {}
    by_id: dict[str, Chunk] = {}
    for results in result_lists:
        for rank, sc in enumerate(results):
            fused[sc.chunk.chunk_id] = fused.get(sc.chunk.chunk_id, 0.0) + 1.0 / (k + rank + 1)
            by_id[sc.chunk.chunk_id] = sc.chunk
    return [ScoredChunk(by_id[cid], s, "hybrid")
            for cid, s in sorted(fused.items(), key=lambda x: -x[1])]


@dataclass
class RetrievalConfig:
    k_dense: int = 20
    k_sparse: int = 20
    k_final: int = 5
    # Abstain thresholds. A query is in scope if EITHER clears its bar; otherwise retrieve() returns [].
    min_dense_score: float = 0.30   # embedder-specific: calibrate on in-scope vs out-of-scope queries
    min_term_coverage: float = 0.6  # share of the query's content words present in the best sparse hit


class HybridRetriever:
    """BM25 index is built per-tenant on demand from the store's ACL-filtered view."""

    def __init__(self, store: VectorStore, embedder: Embedder, tracer: Tracer,
                 config: RetrievalConfig | None = None, reranker: Reranker | None = None) -> None:
        self.store, self.embedder, self.tracer = store, embedder, tracer
        self.cfg = config or RetrievalConfig()
        self.reranker = reranker

    def retrieve(self, query: str, principal: Principal) -> list[ScoredChunk]:
        with self.tracer.span("retrieval.hybrid", **{"retrieval.k_final": self.cfg.k_final}) as sp:
            qv = self.embedder.embed([query])[0]
            dense = self.store.search(qv, principal, self.cfg.k_dense)
            best_dense = dense[0].score if dense else 0.0
            # Boilerplate trade-off: BM25 is rebuilt from the ACL-filtered view per query.
            # O(N) and fine to ~50k chunks; beyond that use the store's native keyword index.
            visible = self.store.all_chunks(principal)
            sparse = BM25(visible).search(query, self.cfg.k_sparse) if visible else []
            sp.set("retrieval.dense_hits", len(dense))
            sp.set("retrieval.sparse_hits", len(sparse))
            coverage = term_coverage(query, sparse)
            sp.set("retrieval.best_dense", round(best_dense, 4))
            sp.set("retrieval.term_coverage", round(coverage, 4))
            if best_dense < self.cfg.min_dense_score and coverage < self.cfg.min_term_coverage:
                sp.set("retrieval.abstained", True)
                return []
            fused = rrf([dense, sparse])
            if self.reranker:
                fused = self.reranker(query, fused[: self.cfg.k_dense])
            return fused[: self.cfg.k_final]
