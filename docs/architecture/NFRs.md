# Non-functional requirements

These are targets for a 50k-employee deployment. The "Verified by" column says where the boilerplate measures or enforces each one.

| ID | Requirement | Target | Verified by |
|---|---|---|---|
| NFR-01 | Time to first token (p95) | ≤ 1.5 s | trace span `gen_ai.chat` duration; load test |
| NFR-02 | End-to-end answer latency (p95) | ≤ 5 s | eval gate `p95_latency_ms_max` |
| NFR-03 | Availability (monthly) | 99.9% with defined degraded mode | `/readyz` breaker state; DEGRADED status path |
| NFR-04 | Groundedness | ≥ 95% of answered cases cite a valid source | `OutputGuard` + eval `citation_precision` |
| NFR-05 | Retrieval quality | recall ≥ 0.8 on golden set | eval `recall_at_k` |
| NFR-06 | Access control | **0** cross-tenant / cross-ACL leaks | vector store pre-filter + eval `acl_leaks` |
| NFR-07 | Prompt-injection resistance | known patterns blocked; retrieved text framed as data | `InputGuard`, prompt rule 3, eval `injection` case |
| NFR-08 | PII | no PII leaves the trust boundary unmasked | `mask_pii` on input and output; span redaction |
| NFR-09 | Cost | ≤ budgeted cost per query; per-request token cap | `PriceBook`, `max_input_tokens`, `aiarch estimate` |
| NFR-10 | Traceability | every answer has trace_id, model, prompt version | `Answer` fields; OTel GenAI span attributes |
| NFR-11 | Data residency | prompts, logs, caches and indexes stay in region | `Principal.region` in cache scope; deploy one stack per region |
| NFR-12 | Recoverability | RPO 24h for index (rebuildable), RTO 30 min | re-ingestion is idempotent per doc version; fallback routes |
| NFR-13 | Change safety | no prompt/model/chunking change ships without passing the gate | CI `Eval gate` step |
