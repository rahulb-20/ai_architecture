"""Domain model: plain data, no I/O, no framework imports.

Everything else in the codebase depends on these types; these types depend on
nothing. That is the first rule of the architecture (see ADR-0001).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


@dataclass(frozen=True, slots=True)
class Principal:
    """Who is asking. Carried through every layer so authorisation is never an afterthought."""

    user_id: str
    tenant_id: str
    groups: frozenset[str] = frozenset({"all"})
    region: str = "in"

    def can_read(self, acl: frozenset[str]) -> bool:
        return bool(self.groups & acl)


@dataclass(frozen=True, slots=True)
class Document:
    doc_id: str
    text: str
    tenant_id: str
    acl: frozenset[str] = frozenset({"all"})
    version: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Chunk:
    chunk_id: str
    doc_id: str
    text: str
    tenant_id: str
    acl: frozenset[str]
    doc_version: int
    position: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ScoredChunk:
    chunk: Chunk
    score: float
    source: str  # "dense" | "sparse" | "hybrid" | "rerank"


@dataclass(frozen=True, slots=True)
class Citation:
    doc_id: str
    chunk_id: str
    doc_version: int


class AnswerStatus(StrEnum):
    ANSWERED = "answered"
    ABSTAINED = "abstained"          # retrieval found nothing good enough: say so, do not guess
    BLOCKED = "blocked"              # a guardrail refused the request
    DEGRADED = "degraded"            # all models failed: sources returned without generation


@dataclass(slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cached_input_tokens + other.cached_input_tokens,
        )


@dataclass(slots=True)
class Answer:
    status: AnswerStatus
    text: str
    citations: list[Citation] = field(default_factory=list)
    model: str | None = None
    prompt_version: str | None = None
    usage: Usage = field(default_factory=Usage)
    cost_usd: float = 0.0
    cache_hit: bool = False
    trace_id: str | None = None
    reasons: list[str] = field(default_factory=list)
