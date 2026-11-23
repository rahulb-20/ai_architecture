# ADR-0003: Hybrid retrieval (BM25 + dense) fused with RRF by default

- Status: Accepted
- Date: 2026-11-23

## Context
Enterprise questions mix paraphrase ("how do I get my VPN working again") with exact identifiers (policy codes, error codes, SKUs). Dense retrieval alone misses identifiers; BM25 alone misses paraphrase.

## Decision
`HybridRetriever` runs dense and BM25 retrieval and fuses them with Reciprocal Rank Fusion (k=60), which needs no score calibration. A reranker (cross-encoder or hosted rerank API) is an optional callable. Retrieval abstains when nothing clears `min_dense_score` and BM25 finds nothing.

## Consequences
- Plus: robust default, and the abstain path feeds the ABSTAINED status instead of a guess.
- Minus: the in-process BM25 index is rebuilt per query from the ACL-filtered view, which is O(N) and fine to roughly 50k chunks. Beyond that, use a store with native keyword search (Elastic/OpenSearch, Azure AI Search, Weaviate, pgvector + tsvector).
