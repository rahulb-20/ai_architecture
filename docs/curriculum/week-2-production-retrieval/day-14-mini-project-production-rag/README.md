# Day 14 — Mini-project: production-grade RAG

> Part of the [GenAI Architect study path](../../README.md). No interactive pack was published for this day. The boilerplate in this repository **is** the reference solution, so this page is the spec it satisfies.

## Goal
A RAG service with hybrid search, reranking hooks, ACL filtering, a cache and an evaluation report, which you can defend in an architecture review.

## Where each requirement lives

| Requirement | Implementation |
|---|---|
| Hybrid search (BM25 + dense, RRF) | `src/aiarch/services/retrieval.py` |
| Reranking (pluggable) | `HybridRetriever(reranker=...)` |
| Structure-aware chunking with versioned chunk IDs | `src/aiarch/services/chunking.py` |
| Tenant and ACL pre-filtering | `src/aiarch/adapters/vectorstore/memory.py` |
| Semantic cache with doc invalidation | `src/aiarch/adapters/cache/semantic.py` |
| Grounded prompt with citations and abstention | `src/aiarch/services/prompts.py`, `services/rag.py` |
| Eval report + gate | `src/aiarch/evaluation/runner.py`, `evals/golden.jsonl` |

## Acceptance criteria
- [ ] `python -m aiarch.evaluation.runner --gate` passes offline
- [ ] Swap in a real embedder (`AIARCH_EMBEDDER=sentence-transformers`) and Ollama, then record the new scores in `reports/`
- [ ] Add a cross-encoder reranker and show its effect on recall in the report
- [ ] Add 10 golden cases from your own domain, including 2 ACL-restricted and 1 injection case
- [ ] Write ADR-0009 explaining your chunk size and `k_final` choice, with the numbers

## Evaluation checklist
- [ ] Retrieval: recall@k and citation precision per case
- [ ] Safety: zero ACL leaks; injection blocked; out-of-scope abstains
- [ ] Operations: p95 latency and cost per query reported
- [ ] Regression: the gate runs in CI on every PR
