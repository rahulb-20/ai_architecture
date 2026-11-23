# ADR-0005: Semantic cache is scoped, version-invalidated and off for agentic flows

- Status: Accepted
- Date: 2026-11-23

## Context
A semantic cache is the only cache layer that can return a *wrong* answer: a near-duplicate question with a different answer, another tenant's answer, or an answer from a superseded policy.

## Decision
- The scope key is `region|tenant|sorted(groups)`, and lookups never cross scopes.
- Entries record the doc_ids they cite; ingestion of a new document version calls `invalidate_doc`.
- TTL is a backstop (default 24h), and the threshold defaults high (0.92) and is chosen from a measured false-hit rate.
- The cache is used only by the single-turn RAG route; the agent route does not cache.

## Consequences
- Plus: bounded and auditable risk.
- Minus: lower hit rate than a global cache. Get the cost savings first from provider prefix caching and context trimming, which are correctness-neutral (`aiarch estimate` shows the effect).
