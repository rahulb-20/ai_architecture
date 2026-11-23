"""In-memory vector store with tenant partitioning and ACL pre-filtering.

Exact (brute-force) cosine search with numpy: fine up to ~100k chunks on a laptop.
The security property matters more than the index type: a principal can never
receive a chunk from another tenant or outside their ACL groups, because filtering
happens before ranking (ADR-0004). Production adapters (pgvector, Azure AI Search,
Milvus, Elastic) must keep that contract.
"""
from __future__ import annotations

import threading
from collections.abc import Sequence

import numpy as np

from aiarch.domain.models import Chunk, Principal, ScoredChunk


class InMemoryVectorStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._by_tenant: dict[str, tuple[list[Chunk], np.ndarray]] = {}

    def upsert(self, chunks: Sequence[Chunk], vectors: Sequence[Sequence[float]]) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("chunks and vectors length mismatch")
        with self._lock:
            for tenant in {c.tenant_id for c in chunks}:
                items = [(c, v) for c, v in zip(chunks, vectors, strict=True) if c.tenant_id == tenant]
                new_ids = {c.chunk_id for c, _ in items}
                old_chunks, old_mat = self._by_tenant.get(tenant, ([], np.zeros((0, len(vectors[0])))))
                keep = [i for i, c in enumerate(old_chunks) if c.chunk_id not in new_ids]
                chunks_out = [old_chunks[i] for i in keep] + [c for c, _ in items]
                mat = np.vstack([old_mat[keep]] + [np.asarray([v for _, v in items], dtype=np.float32)])
                self._by_tenant[tenant] = (chunks_out, mat)

    def delete_doc(self, tenant_id: str, doc_id: str) -> int:
        with self._lock:
            if tenant_id not in self._by_tenant:
                return 0
            chunks, mat = self._by_tenant[tenant_id]
            keep = [i for i, c in enumerate(chunks) if c.doc_id != doc_id]
            self._by_tenant[tenant_id] = ([chunks[i] for i in keep], mat[keep])
            return len(chunks) - len(keep)

    def acl_index(self) -> dict[str, frozenset[str]]:
        """doc_id -> ACL, for audits and the eval leak check."""
        with self._lock:
            return {c.doc_id: c.acl for chunks, _ in self._by_tenant.values() for c in chunks}

    def all_chunks(self, principal: Principal) -> list[Chunk]:
        with self._lock:
            chunks, _ = self._by_tenant.get(principal.tenant_id, ([], None))
            return [c for c in chunks if principal.can_read(c.acl)]

    def search(self, vector: Sequence[float], principal: Principal, k: int) -> list[ScoredChunk]:
        with self._lock:
            if principal.tenant_id not in self._by_tenant:
                return []
            chunks, mat = self._by_tenant[principal.tenant_id]
            allowed = np.array([principal.can_read(c.acl) for c in chunks], dtype=bool)
            if not allowed.any():
                return []
            sims = mat @ np.asarray(vector, dtype=np.float32)
            sims = np.where(allowed, sims, -np.inf)          # pre-filter: denied rows can never rank
            top = np.argsort(-sims)[: min(k, int(allowed.sum()))]
            return [ScoredChunk(chunks[i], float(sims[i]), "dense") for i in top]
