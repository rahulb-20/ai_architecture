"""Composition root: the ONLY place that knows concrete adapter classes.

Services receive ports through their constructors. Tests build their own container
with fakes; production builds this one from Settings. Adding a vendor = one adapter
+ one branch here (see docs/guides/extending.md).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from aiarch.adapters.cache.semantic import NullCache, SemanticCache
from aiarch.adapters.embeddings.hashing import HashingEmbedder
from aiarch.adapters.llm.fake import FakeLLM
from aiarch.adapters.llm.openai_compatible import OpenAICompatibleLLM
from aiarch.adapters.telemetry.tracer import JsonTracer
from aiarch.adapters.vectorstore.memory import InMemoryVectorStore
from aiarch.config import LLMRouteConfig, Settings
from aiarch.domain.models import Principal
from aiarch.ports import AnswerCache, Embedder, LLMClient, Tracer
from aiarch.services.agents.agent import Agent, RulePlanner
from aiarch.services.agents.tools import ToolRegistry, calculator_tool, kb_search_tool, ticket_tool
from aiarch.services.cost import PriceBook
from aiarch.services.gateway import ModelGateway, Route
from aiarch.services.guardrails import InputGuard, OutputGuard
from aiarch.services.ingestion import IngestionService, load_markdown_folder
from aiarch.services.prompts import default_registry
from aiarch.services.rag import RagService
from aiarch.services.retrieval import HybridRetriever, RetrievalConfig


def build_llm(cfg: LLMRouteConfig) -> LLMClient:
    if cfg.kind == "fake":
        return FakeLLM(name=cfg.name)
    if cfg.kind == "openai_compatible":
        if not cfg.base_url or not cfg.model:
            raise ValueError(f"route {cfg.name}: base_url and model are required")
        key = os.environ.get(cfg.api_key_env) if cfg.api_key_env else None
        return OpenAICompatibleLLM(cfg.name, cfg.base_url, cfg.model, api_key=key, timeout_s=cfg.timeout_s)
    raise ValueError(f"unknown LLM kind: {cfg.kind}")


def build_embedder(s: Settings) -> Embedder:
    if s.embedder == "sentence-transformers":
        from aiarch.adapters.embeddings.hashing import SentenceTransformerEmbedder
        return SentenceTransformerEmbedder(s.embedder_model)
    return HashingEmbedder()


@dataclass
class Container:
    settings: Settings
    tracer: Tracer
    embedder: Embedder
    store: InMemoryVectorStore
    cache: AnswerCache
    gateway: ModelGateway
    ingestion: IngestionService
    rag: RagService
    agent: Agent
    tickets: list[dict[str, Any]] = field(default_factory=list)


def build_container(settings: Settings | None = None, *, tracer: Tracer | None = None,
                    llm_clients: list[LLMClient] | None = None, seed: bool = True) -> Container:
    s = settings or Settings.from_env()
    tracer = tracer or JsonTracer()
    embedder = build_embedder(s)
    store = InMemoryVectorStore()
    cache: AnswerCache = (SemanticCache(embedder, s.cache_threshold, s.cache_ttl_s)
                          if s.semantic_cache else NullCache())
    prices = PriceBook.from_file(s.prices_file) if s.prices_file.exists() else PriceBook()
    clients = llm_clients or [build_llm(r) for r in s.llm_routes]
    retries = {r.name: r.max_retries for r in s.llm_routes}
    gateway = ModelGateway([Route(c, max_retries=retries.get(c.name, 1)) for c in clients],
                           tracer, prices, max_input_tokens=s.max_input_tokens)
    retriever = HybridRetriever(store, embedder, tracer,
                                RetrievalConfig(k_final=s.k_final, min_dense_score=s.min_dense_score,
                                                min_term_coverage=s.min_term_coverage))
    rag = RagService(retriever, gateway, default_registry(), cache, tracer, InputGuard(), OutputGuard())
    ingestion = IngestionService(store, embedder, cache, tracer, s.chunk_tokens, s.chunk_overlap)
    rag.output_guard.keep_source_values = s.output_keep_source_pii   # policy switch, see ADR-0006

    tickets: list[dict[str, Any]] = []
    registry = ToolRegistry()
    registry.register(calculator_tool())
    registry.register(ticket_tool(tickets))

    def _search(q: str, p: Principal) -> list[dict[str, Any]]:
        return [{"doc_id": h.chunk.doc_id, "text": h.chunk.text[:300]} for h in retriever.retrieve(q, p)[:3]]

    registry.register(kb_search_tool(_search))
    agent = Agent(registry, RulePlanner(), tracer, input_guard=rag.input_guard)   # same guard as /v1/ask

    c = Container(s, tracer, embedder, store, cache, gateway, ingestion, rag, agent, tickets)
    if seed and s.seed_folder and s.seed_folder.exists():
        for doc in load_markdown_folder(s.seed_folder, s.seed_tenant):
            ingestion.ingest(doc)
    return c
