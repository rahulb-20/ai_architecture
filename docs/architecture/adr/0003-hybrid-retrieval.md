# ADR-0003: Hybrid retrieval (BM25 + dense) fused with RRF by default

- Status: Accepted
- Date: 2026-11-23

## Context
Enterprise questions mix paraphrase ("how do I get my VPN working again") with exact identifiers (policy codes, error codes, SKUs). Dense retrieval alone misses identifiers; BM25 alone misses paraphrase.

## Decision
`HybridRetriever` runs dense and BM25 retrieval and fuses them with Reciprocal Rank Fusion (k=60), which needs no score calibration. A reranker (cross-encoder or hosted rerank API) is an optional callable. Retrieval abstains, before any model call, when the best dense score is below `min_dense_score` and no chunk contains at least `min_term_coverage` of the query's content words. BM25 ignores stopwords, so function words alone never count as a match.

## Consequences
- Plus: robust default, and the abstain path feeds the ABSTAINED status instead of a guess.
- Minus: `min_dense_score` depends on the embedder. The default suits the offline hashing embedder; recalibrate it on in-scope and out-of-scope queries when you switch models.
- Minus: the in-process BM25 index is rebuilt per query from the ACL-filtered view, which is O(N) and fine to roughly 50k chunks. Beyond that, use a store with native keyword search (Elastic/OpenSearch, Azure AI Search, Weaviate, pgvector + tsvector).
