# ADR-0001: Ports and adapters with a single composition root

- Status: Accepted
- Date: 2026-11-23

## Context
GenAI vendors, models and vector databases change every quarter. Teams that call provider SDKs directly from business logic end up rewriting services for every switch (OpenAI → Azure OpenAI → watsonx.ai → self-hosted vLLM), and they can't test without network access.

## Options
1. Call SDKs directly from services (fastest to start, highest coupling).
2. Use a framework abstraction everywhere (for example LangChain runnables), which couples us to the framework instead of the vendor.
3. Define our own narrow ports (`LLMClient`, `Embedder`, `VectorStore`, `AnswerCache`, `Tracer`) and wire adapters in one composition root.

## Decision
Option 3. `aiarch.domain` has no dependencies. `aiarch.ports` defines Protocols. `aiarch.services` depends only on ports. `aiarch.container` is the only module that imports concrete adapters. Frameworks such as LangChain/LangGraph are allowed *inside* an adapter or a service, never across the port boundary.

## Consequences
- Plus: swapping a provider is one adapter and one line in `container.py`. Every service is unit-testable with fakes, and the whole system runs offline.
- Minus: we maintain thin adapters ourselves, and new provider features (for example a new caching API) need an explicit port change before services can use them.
