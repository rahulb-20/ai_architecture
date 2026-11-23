"""Scoped semantic answer cache.

Three rules from ADR-0005 are enforced here, not left to callers:
1. Lookups only ever compare against entries in the same scope (tenant + ACL groups + locale).
2. Every entry records the doc_ids it was grounded on, so a document update invalidates it.
3. Entries expire (TTL) as a backstop.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from aiarch.ports import Embedder


@dataclass
class _Entry:
    vec: np.ndarray
    value: Any
    doc_ids: frozenset[str]
    expires_at: float


class SemanticCache:
    def __init__(self, embedder: Embedder, threshold: float = 0.92, ttl_s: float = 86_400,
                 max_entries_per_scope: int = 5_000) -> None:
        self._emb = embedder
        self.threshold = threshold
        self.ttl_s = ttl_s
        self._max = max_entries_per_scope
        self._scopes: dict[str, list[_Entry]] = {}
        self._lock = threading.RLock()

    def _vec(self, text: str) -> np.ndarray:
        return np.asarray(self._emb.embed([text])[0], dtype=np.float32)

    def get(self, query: str, scope: str) -> tuple[Any, float] | None:
        now = time.time()
        with self._lock:
            entries = [e for e in self._scopes.get(scope, []) if e.expires_at > now]
            self._scopes[scope] = entries
            if not entries:
                return None
            sims = np.vstack([e.vec for e in entries]) @ self._vec(query)
            i = int(np.argmax(sims))
            return (entries[i].value, float(sims[i])) if sims[i] >= self.threshold else None

    def put(self, query: str, scope: str, value: Any, doc_ids: Sequence[str]) -> None:
        with self._lock:
            bucket = self._scopes.setdefault(scope, [])
            bucket.append(_Entry(self._vec(query), value, frozenset(doc_ids), time.time() + self.ttl_s))
            if len(bucket) > self._max:
                del bucket[0 : len(bucket) - self._max]

    def invalidate_doc(self, doc_id: str) -> int:
        removed = 0
        with self._lock:
            for scope, bucket in self._scopes.items():
                keep = [e for e in bucket if doc_id not in e.doc_ids]
                removed += len(bucket) - len(keep)
                self._scopes[scope] = keep
        return removed


class NullCache:
    """Use when caching is disabled (e.g. agentic or multi-turn routes)."""

    def get(self, query: str, scope: str) -> None:
        return None

    def put(self, query: str, scope: str, value: Any, doc_ids: Sequence[str]) -> None:
        return None

    def invalidate_doc(self, doc_id: str) -> int:
        return 0
