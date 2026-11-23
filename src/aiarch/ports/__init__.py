"""Ports: the interfaces the application layer depends on.

Adapters (OpenAI, Ollama, watsonx via a gateway, FAISS, Chroma, Redis, Langfuse...)
implement these Protocols. Swapping a vendor means writing one adapter and changing
one line in `aiarch.container`, never touching services. See ADR-0001.
"""
from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from aiarch.domain.models import Chunk, Principal, ScoredChunk, Usage


@dataclass(frozen=True, slots=True)
class Message:
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str


@dataclass(slots=True)
class LLMRequest:
    messages: list[Message]
    model: str | None = None
    temperature: float = 0.0
    max_output_tokens: int = 512
    json_schema: dict[str, Any] | None = None   # structured output when the provider supports it
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class LLMResponse:
    text: str
    model: str
    provider: str
    usage: Usage
    latency_ms: float


@runtime_checkable
class LLMClient(Protocol):
    name: str

    def generate(self, request: LLMRequest) -> LLMResponse: ...


@runtime_checkable
class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


@runtime_checkable
class VectorStore(Protocol):
    def upsert(self, chunks: Sequence[Chunk], vectors: Sequence[Sequence[float]]) -> None: ...

    def delete_doc(self, tenant_id: str, doc_id: str) -> int: ...

    def replace_doc(self, tenant_id: str, doc_id: str, chunks: Sequence[Chunk],
                    vectors: Sequence[Sequence[float]]) -> int:
        """Swap every chunk of one document for `chunks` in a single atomic step, so a reader
        never sees the document missing or half-written. Returns the number of chunks removed."""
        ...

    def doc_version(self, tenant_id: str, doc_id: str) -> int | None:
        """Highest indexed version of the document, or None if it is not indexed."""
        ...

    def search(self, vector: Sequence[float], principal: Principal, k: int) -> list[ScoredChunk]:
        """MUST apply tenant and ACL filtering inside the store (pre-filter), never after."""
        ...

    def all_chunks(self, principal: Principal) -> list[Chunk]:
        """ACL-filtered view used to build the in-process BM25 index.
        Stores with native keyword search (Elastic, Azure AI Search, OpenSearch) can
        instead implement sparse search directly and return [] here."""
        ...


@runtime_checkable
class AnswerCache(Protocol):
    def get(self, query: str, scope: str) -> tuple[Any, float] | None: ...

    def put(self, query: str, scope: str, value: Any, doc_ids: Sequence[str]) -> None: ...

    def invalidate_doc(self, doc_id: str) -> int: ...


class Span(Protocol):
    def set(self, key: str, value: Any) -> None: ...


@runtime_checkable
class Tracer(Protocol):
    def span(self, name: str, **attrs: Any) -> AbstractContextManager[Span]: ...

    def current_trace_id(self) -> str | None: ...


class TokenCounter(Protocol):
    def count(self, text: str) -> int: ...


def iter_batches(items: Sequence[Any], size: int) -> Iterator[Sequence[Any]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]
