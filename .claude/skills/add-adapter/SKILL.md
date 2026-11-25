---
name: add-adapter
description: Add or replace an adapter behind one of this repo's ports (LLM client, embedder, vector store, answer cache, tracer). Use whenever a task integrates a vendor, database, SDK or service, for example pgvector, Qdrant, Redis, OpenTelemetry, Anthropic or watsonx. Covers where the code goes, the optional dependency, container wiring, offline contract tests and the docs to update.
---

# Add an adapter

An adapter is the only code that may know a vendor. Everything else depends on the port in `src/aiarch/ports/__init__.py`. Follow these steps in order; each has a rule that has already caught a real mistake in this repo.

## 1. Find the port and read it

| Integrating | Port | Existing adapter to copy | Wired in |
|---|---|---|---|
| Model provider | `LLMClient` | `adapters/llm/openai_compatible.py` | `container.build_llm` (branch on `LLMRouteConfig.kind`) |
| Embedding model | `Embedder` | `adapters/embeddings/hashing.py` | `container.build_embedder` |
| Vector database | `VectorStore` | `adapters/vectorstore/memory.py` | `build_container` |
| Answer cache | `AnswerCache` | `adapters/cache/semantic.py` | `build_container` |
| Tracing backend | `Tracer` | `adapters/telemetry/tracer.py` | `build_container` |

Read the port's docstrings. They carry obligations that the type signature does not:

- `VectorStore.search` must filter by tenant and ACL **inside the store query**. Filtering after the search is a leak and a failed review.
- `VectorStore.replace_doc` must be atomic for readers: one transaction, or write the new version and then delete the old one.
- `LLMClient` errors must be mapped to `ProviderError(retryable=...)` or `RateLimited(retry_after_s=...)` from `domain/errors.py`. The gateway's retry, fallback and circuit breaker act on those types only; a raw SDK exception bypasses all three.
- `AnswerCache` entries are scoped by tenant and groups (`scope`) and must be removable by document (`invalidate_doc`).

If the port cannot express what the vendor needs, stop. Changing a port is a design decision: it goes in the design brief and needs an ADR. Do not add a vendor-specific method and call it from a service.

## 2. Write the adapter

- One file: `src/aiarch/adapters/<kind>/<vendor>.py`. The class satisfies the port by structure; no base class.
- Import the vendor SDK **inside** `__init__` or the method that needs it, never at module top level, so the default install and the offline checks never need it. `SentenceTransformerEmbedder` shows the pattern.
- Accept an already-built client as an optional constructor argument (as `OpenAICompatibleLLM(client=...)` does). Tests inject a fake through it.
- Secrets come from the environment by variable name (`api_key_env`), never as a literal, a default value or a setting that holds the secret itself.
- Set timeouts explicitly. No retries inside an LLM adapter: the gateway owns them.
- Keep the `gen_ai.*` attribute names if the adapter emits spans.

## 3. Declare the dependency as optional

Add an extra in `pyproject.toml` under `[project.optional-dependencies]` (for example `redis = ["redis>=5"]`). Never add a vendor package to `dependencies` or to `dev`. A fresh clone with only `.[dev]` installed must still pass all four checks.

## 4. Add a setting and one branch in the container

- Add the selector to `Settings` in `config.py` as a `Literal[...]` whose default is the current offline behaviour. An unknown value must fail at start-up, not fall back silently.
- Add the branch in `container.py`. That file is the only place a concrete adapter class is named.
- Check the `Container` dataclass field type. `store` is annotated `InMemoryVectorStore`, and `evaluation/runner.py` calls `c.store.acl_index()`, which is not on the port. A second vector store means widening that annotation to the port and giving the eval runner a port-level way to get what it needs. Say so in the design brief; do not paper over it with a cast.

## 5. Test it offline

The checks run with no network, no keys and no Docker, so the adapter is tested against a fake of the vendor, not the vendor.

- **HTTP vendors:** `httpx.MockTransport`, as in `tests/unit/test_openai_adapter.py`.
- **SDK vendors:** a small in-test fake client passed through the constructor seam from step 2.
- **Contract tests:** run the port's shared contract suite against the new adapter if one exists in `tests/`. If it does not exist yet, run the port-level tests the guide names (`docs/guides/extending.md`) against it, and say in your report that there is no shared suite.
- **Must be covered:** tenant isolation and ACL for stores and caches; error mapping, including rate limits and timeouts, for model clients; the optional dependency being absent (selecting the adapter without its extra installed gives a clear error that names the extra).
- **Selected by setting:** one test that the setting builds the new adapter and the default still builds the old one.

A test that needs the real service may exist only if it is skipped unless an explicit environment variable is set, with the reason in the skip message. It never counts towards an acceptance criterion.

## 6. Update the docs in the same change

- `docs/guides/extending.md`: the section for that port.
- `.env.example`: the new setting, commented, with the offline default.
- `README.md`: only if the quick start or configuration section is affected.
- `docs/guides/production-checklist.md`: tick or reword the line the adapter satisfies (for example the one about moving the store and cache out of process).
- An ADR if the adapter replaces a default or changes a trade-off recorded in an existing ADR.

## 7. Before you call it done

- [ ] `grep -rn "<vendor package>" src/aiarch` shows it only under `adapters/`.
- [ ] No concrete adapter class is named outside `container.py` and its own module.
- [ ] `python -m pip install -e ".[dev]"` in a clean environment, then all four checks from `CLAUDE.md`, pass without the new extra installed.
- [ ] The eval gate numbers with the default configuration are unchanged from `main`.
- [ ] State plainly what was **not** verified against the real service. Offline fakes prove the adapter's logic, not the vendor's behaviour.
