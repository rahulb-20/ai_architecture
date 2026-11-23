# Extending the boilerplate

Every extension follows the same pattern: implement a port, then wire it in `src/aiarch/container.py`. Services do not change.

## Add an LLM provider
- **OpenAI-compatible** (OpenAI, Azure OpenAI v1, Ollama, vLLM, TGI, LM Studio, LiteLLM/Portkey gateways fronting Anthropic, Gemini or watsonx.ai): no code needed. Add a route to `AIARCH_LLM_ROUTES` (see `.env.example`).
- **Native SDK** (for example the `anthropic` or `ibm-watsonx-ai` SDK): create `adapters/llm/<name>.py` with a class that has `name` and `generate(LLMRequest) -> LLMResponse`. Map SDK errors to `ProviderError(retryable=...)` / `RateLimited`, then add a `kind` branch in `container.build_llm`. Copy `tests/unit/test_openai_adapter.py` as the contract test.

## Add a vector database
Implement `upsert`, `delete_doc`, `search(vector, principal, k)` and `all_chunks(principal)` from `aiarch.ports.VectorStore`. Rules:
1. Filter by `principal.tenant_id` and ACL inside the query (namespace or partition per tenant, plus a metadata filter on `acl`).
2. If the store has native keyword search, return `[]` from `all_chunks` and add a sparse-search method the retriever can call.
3. Run `tests/unit/test_retrieval_and_store.py::test_acl_and_tenant_prefilter_never_leak` against your adapter.

## Add a reranker
Pass any callable `(query, list[ScoredChunk]) -> list[ScoredChunk]` as `HybridRetriever(reranker=...)`. A cross-encoder from `sentence-transformers` or a hosted rerank API both fit.

## Add a guardrail
Write a `Check` (`str -> GuardResult`) and append it to `InputGuard(extra_checks=[...])` in the container. For output policies, extend `OutputGuard`.

## Add a tool for the agent
Define a pydantic args model and a function `(args, principal) -> dict`, then register a `Tool(...)`. Set `side_effects=True` for anything that writes, which forces human approval, and set `allowed_groups` to restrict who can call it. The same schema can be exposed through an MCP server.

## Swap telemetry
Implement `Tracer.span(name, **attrs)` with the OpenTelemetry SDK, keeping the `gen_ai.*` attribute names, and export to Langfuse, Phoenix, Azure Monitor or Datadog.

## Add a prompt version
Register `PromptTemplate("grounded_qa", "3", ...)`. Activate it only after `--gate` passes with it. Answers record `grounded_qa@3`, so you can compare versions in traces.
