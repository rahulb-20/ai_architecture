# Decision log (from the daily packs)

The ADR-style decision from each day. The ADRs that govern this codebase are in [`docs/architecture/adr/`](../architecture/adr/).

## Day 1 — [LLM internals for architects](week-1-llm-retrieval-foundations/day-01-llm-internals-for-architects/README.md)

Title
:   Default context strategy for the enterprise knowledge assistant

Status
:   Proposed · 2026-10-11

Context
:   50k employees, ~8.8M requests a month, permissioned and changing corpus, citation requirement, p95 TTFT target under 2 s. Candidate models offer 128k–1M windows. Indic-language users increase token counts.

Options
:   (A) Long-context stuffing per department. (B) Bounded RAG with an ≤8k input budget. (C) B plus prompt caching of the stable prefix, with a gated long-context route for whole-document tasks.

Decision
:   **C.** RAG is the default path with explicit per-slot token budgets and a cached system/tool prefix. A separate "analyse this document" route allows up to 100k tokens, with its own rate limit and model.

Consequences
:   + Predictable cost and TTFT; ACLs enforced at retrieval; citations by design. − We own retrieval quality, so this requires the eval harness from Day 11. Two routes means two SLOs and two cost centres. Revisit if long-context price per token falls about 10× or effective-context benchmarks close the gap.

## Day 2 — [Prompt Engineering & Structured Output](week-1-llm-retrieval-foundations/day-02-prompt-engineering-and-structured-output/README.md)

Structured output strategy for the ticket-triage service · Status: Accepted

Context

The triage service feeds ServiceNow routing. It must run on Azure OpenAI (primary), Claude (secondary) and Granite on vLLM on-prem for regulated business units. Malformed output currently causes ~3% dead-letter tickets. Routing SLA: P1 misclassification under 2%.

Options

- A. Prompt-only JSON plus a tolerant parser
- B. Native strict structured output per provider
- C. B plus a canonical Pydantic contract, semantic validators and a single re-ask
- D. Tool calling for all extraction

Decision

**C.** One Pydantic model is the source of truth. Adapters emit each provider's supported schema subset (XGrammar via vLLM guided decoding on-prem). Full validation runs after every call, with at most one re-ask. Each enum includes `unknown` so the model can abstain. Prompts live in a registry and are pinned per model.

Consequences

- + Dead-letter rate from format errors drops to ~0; the remaining errors are semantic and measurable.
- + Provider swap = adapter + golden-set pass, not a rewrite.
- − Adapter maintenance as provider schema support changes; needs contract tests.
- − Re-ask adds tail latency, so p95 latency and retry rate become SLOs with alerts.
- Revisit if a provider ships full JSON Schema support or retry rate exceeds 5%.

**Tomorrow · Day 3**Fine-tuning vs RAG vs prompting: a decision framework, LoRA/QLoRA/PEFT, and when InstructLab-style tuning wins.

## Day 3 — [Fine-tuning vs RAG vs Prompting](week-1-llm-retrieval-foundations/day-03-fine-tuning-vs-rag-vs-prompting/README.md)

Title
:   Customisation strategy for the internal compliance assistant

Status
:   Accepted

Context
:   Assistant answers employee questions on around 3,000 policy documents updated weekly. Answers must cite the clause, respect document-level entitlements and return a fixed JSON envelope (answer, citations, confidence, escalate flag) for the workflow engine. Volume is about 1.5M requests a month. Data must stay in-region.

Options
:   (a) Prompting only with a long system prompt and few-shot examples. (b) RAG with hybrid retrieval and ACL filters on a general instruct model. (c) Fine-tune the facts into a model. (d) RAG plus a LoRA adapter trained for the JSON envelope, citation style and abstention behaviour.

Decision
:   Option (b) now, with (d) as a gated follow-up if evals show envelope validity below 99% or abstention errors above target. Option (c) is rejected: weekly policy changes, citation and entitlement requirements cannot be met by weights.

Consequences
:   Positive: fresh answers, auditable citations, no training pipeline on day one. Negative: retrieval quality becomes the main risk, so invest in chunking, hybrid search and a golden set (Days 6–11). If (d) is triggered, the team owns adapter versioning, re-tuning on base-model upgrades, and an eval gate in CI (Day 26).

**Tomorrow, Day 4:** Hallucination control and grounding: citations, abstention, self-check and constrained answers.

Versions checked against PyPI on 12 Oct 2026: peft 0.21.2, trl 1.15.0, transformers 5.19.0, datasets 5.1.0, torch 2.14.1. Repo status checked the same day.

## Day 4 — [Hallucination Control & Grounding](week-1-llm-retrieval-foundations/day-04-hallucination-control-and-grounding/README.md)

Status
:   Accepted · Day 4 study ADR

Context
:   Internal policy assistant for about 20k users on watsonx.ai with Granite, plus a fallback provider. Wrong answers carry regulatory exposure. Target p95 latency ≤ 4 s and precision on answered questions ≥ 97%. One region must run air-gapped.

Options
:   **A.** Prompt contract only. **B.** LLM-as-judge on every answer. **C.** Tiered: retrieval floor, then claims with quotes, a deterministic quote check, a CPU NLI/HHEM gate, and an LLM judge only for the uncertain band. **D.** Managed cloud groundedness API.

Decision
:   **C.** A has no measurable control. B doubles LLM cost and breaks the latency budget. D fails the air-gapped requirement and ties the control to one cloud. C runs everywhere, costs about 50–150 ms for most traffic, and produces per-layer signals for watsonx.governance dashboards.

Consequences
:   - About 20–40% more output tokens because of quotes.
    - Correct derived answers (calculations) may be dropped until a derivation lane exists.
    - Thresholds need recalibration whenever the embedder, reranker or verifier changes, so this is added to the CI eval gate (Day 26).
    - Abstentions route to an HR or Compliance queue, which needs an owner and an SLA.

## Day 5 — [Embeddings Deep Dive](week-1-llm-retrieval-foundations/day-05-embeddings-deep-dive/README.md)

Status
:   Accepted · Day 5 study ADR (continues ADR-014)

Context
:   The same policy assistant: about 20k users, roughly 6M chunks in 9 languages, on watsonx.ai with one air-gapped region. Chunks up to about 1,000 tokens. Target p95 retrieval ≤ 150 ms. Vector-store RAM budget per region: 16 GiB.

Options
:   **A.** watsonx-hosted granite-embedding-278m-multilingual (768-d, 512-token limit). **B.** Self-hosted granite-embedding-311m-multilingual-r2 (768-d MRL, 32K context, Apache-2.0). **C.** Managed API embedder (text-embedding-3-large, 3072-d). **D.** Qwen3-Embedding-8B self-hosted.

Decision
:   **B**, storing a 256-d int8 vector for stage 1 (6M × 256 B ≈ 1.4 GiB plus HNSW overhead) and 768-d fp32 on disk for rescoring. A truncates our 1,000-token chunks. C fails the air-gapped requirement and its 3072-d vectors break the RAM budget at fp32. D needs a GPU per replica and makes re-indexing slow. B won the 300-query multilingual golden set within 1.5 nDCG points of D.

Consequences
:   - We operate an embedding service (GPU or CPU pool) with its own SLO.
    - Every vector carries `emb_model`, `emb_version`, `dim` and `precision`.
    - An embedder upgrade is a planned re-index with shadow evaluation and threshold recalibration (retrieval floor from ADR-014).
    - Re-check against watsonx's hosted catalogue each quarter. If R2 becomes hosted in-region, revisit A vs B for ops cost.

## Day 6 — [Chunking Strategies](week-1-llm-retrieval-foundations/day-06-chunking-strategies/README.md)

### ADR-006: Default chunking for an enterprise policy & procedures assistant

Status
:   Proposed, pending golden-set evaluation

Context
:   ~40k policy PDFs and DOCX files with heading hierarchies and tables; per-document ACLs; questions split between precise lookups ("retention period for backups in EU") and procedures. Embedder max length 512 tokens. Target: evidence hit@5 ≥ 0.90 and ≤ 3k context tokens per query at p95.

Options
:   (A) Vendor default fixed 800/400. (B) Recursive 400/0. (C) Structure-aware via Docling HybridChunker, heading breadcrumb in the embedded text, parent-child with section parents ≤1024 tokens. (D) Semantic or LLM chunking. (E) Late chunking with a self-hosted long-context embedder.

Decision
:   C as the default; B as the fallback for documents without usable structure. Contextual retrieval is held back for the top two high-value collections and enabled only if it lifts hit@5 by ≥3 points on their golden sets.

Consequences
:   **+** Citations map to real sections; tables stay intact; ACLs inherited per section; recall improves without larger k. **−** A layout-parsing dependency and GPU cost at ingest; two stores (vectors + parent text); chunker version must be tracked and re-indexing planned. A rejected because the token cost per answer is about 4× with no recall gain in published evals; D and E rejected for ingest cost and hosted-API constraints respectively.

Revisit when
:   hit@5 drops below 0.85 on the monthly eval, the embedder changes, or a long-context embedder becomes available on the chosen platform.

09

## Day 7 — [Vector Search Internals](week-1-llm-retrieval-foundations/day-07-vector-search-internals/README.md)

ADR-007 · Title
:   ANN index type for the enterprise policy assistant (8M chunks, 768-d, 40 tenants, 50 QPS peak, p95 ≤ 50 ms retrieval)

Context
:   Corpus grows ~5% a month with daily incremental loads and occasional deletes for retention. Tenant isolation is a compliance requirement. Budget favours a single 64 GB node per replica.

Options
:   (a) HNSW,Flat: ~27 GB per replica, best recall/latency, slow rebuild. (b) HNSW + SQ8 with 3× oversample and full-precision rescoring: ~8 GB vectors in RAM, full vectors on disk. (c) IVF-PQ + refine: smallest footprint, needs periodic re-training, recall ceiling risk. (d) Managed (Azure AI Search / watsonx.data Milvus) with the same choice inside.

Decision
:   (b) HNSW over SQ8 with rescoring, M=32, efConstruction=200, efSearch tuned to recall@10 ≥ 0.97 on the golden set; tenant as partition key, never a free-form filter.

Consequences
:   Fits the 64 GB node with room for a parallel rebuild. Recall loss vs (a) measured at <1 pt. Requires a weekly compaction job for deletes and a recall canary (1% shadow exact search). Re-evaluate at 50M chunks, when DiskANN or IVF-PQ becomes cheaper.

**Tomorrow · Day 8:** Vector DB selection — Pinecone vs Weaviate vs Chroma vs Milvus vs Azure AI Search vs Elastic vs pgvector; metadata filtering and multi-tenancy.

## Day 8 — [Vector DB Selection](week-2-production-retrieval/day-08-vector-db-selection/README.md)

Title

Vector store and tenancy model for the multi-BU knowledge assistant

Context

12 business units (2 regulated), about 25M chunks growing 40% a year, Azure landing zone, Postgres already operated by the platform team, India data residency, p95 retrieval ≤ 150 ms, filtered recall@10 ≥ 0.95 on the golden set, per-BU deletion within 30 days on request.

Options

(1) Pinecone serverless with namespace per BU. (2) Azure AI Search with an index per regulated BU and a security-filter field for the rest. (3) pgvector on Azure PostgreSQL Flexible Server with RLS + a tenant column, and a separate database for regulated BUs.

Decision

Option 2. It meets residency and private-endpoint needs natively, provides hybrid + semantic ranking without extra services, and its fixed search-unit pricing fits the finance team's budgeting. Regulated BUs get silo indexes with their own CMK; the rest share a pooled index with server-side injected filters.

Consequences

+ One managed service, Entra RBAC, audit via Azure Monitor. − Schema changes need reindex pipelines; index-count tier limits must be watched; cost is fixed even at low traffic. Revisit if the corpus passes 100M chunks or recall under filters drops below the NFR. Option 3 remains the fallback if cost pressure grows.

## Day 9 — [Hybrid Search & Reranking](week-2-production-retrieval/day-09-hybrid-search-and-reranking/README.md)

**ADR-009 · Retrieval ranking strategy for the enterprise knowledge assistant** · Accepted

Context
:   Corpus of ~2M chunks (policies, runbooks, tickets) in English and Hindi. Queries mix exact identifiers (error codes, policy IDs) and natural-language questions. Target: retrieval + rerank p95 ≤ 300 ms at 50 QPS; data must stay in-region; document-level ACLs.

Options
:   (A) Dense only. (B) Hybrid BM25 + dense with RRF, no reranker. (C) Hybrid + managed rerank API. (D) Hybrid + self-hosted multilingual cross-encoder. (E) Hybrid + LLM listwise rerank.

Decision
:   **D.** BM25 and dense run in parallel with ACL pre-filters, top 50 each; RRF with k=60 and equal weights; bge-reranker-v2-m3 reranks the top 40 on a 2-GPU pool behind the AI gateway; top 6 go to the LLM; reranker score below a tuned threshold triggers abstention. Reranker sits behind an interface so C remains a drop-in fallback.

Rationale
:   On the 400-query golden set, B beat A by a wide margin on identifier queries; D added further nDCG@10 over B. C met quality but sends chunk text out of the boundary per query and has per-search pricing that scales with N. E failed the latency budget.

Consequences
:   We operate a GPU pool (autoscaling, patching, model versioning). Two indexes must stay consistent, so ingestion writes both in one job with a reconciliation check. Any embedding or reranker change goes through the eval gate (Day 26). Revisit fusion weights if query-type mix shifts by more than 15 percentage points in monthly log analysis.

**Tomorrow · Day 10**Advanced RAG: query rewriting, HyDE, multi-query, self-query, GraphRAG, and agentic / corrective RAG.

Prepared for Rahul Bhave · GenAI Architect 30-day plan · library versions checked on PyPI on 18 Oct 2026.

## Day 10 — [Advanced RAG](week-2-production-retrieval/day-10-advanced-rag/README.md)

**ADR-010 · Query transformation and corrective-retrieval policy for the enterprise knowledge assistant** · Accepted

Context
:   Builds on ADR-009 (hybrid + self-hosted rerank). Pilot logs show ~25% of failed answers come from vocabulary mismatch or versioned documents, and ~5% of questions are out of scope but still get answered. Budget: p95 end-to-end ≤ 4 s, median cost per query must not double. A small set of leadership users asks thematic questions over incident reports.

Options
:   (A) Keep single-shot hybrid + rerank. (B) Always run multi-query + HyDE + grading. (C) Gated: conversational rewrite always; self-query when the question names a version, date or department; multi-query only when top-1 rerank score is below threshold; one batched grading call; max one retry, then abstain. (D) Full agentic RAG with tool choice. (E) GraphRAG over the whole corpus.

Decision
:   **C** for the main assistant, built as a LangGraph graph with a hard budget. A separate, opt-in "insights" path uses LazyGraphRAG/LightRAG over the incident-report corpus only, which has a single permission level.

Rationale
:   On the golden set, C recovers most of B's gain on mismatch and version questions while about 70% of queries take the zero-extra-call path, so median latency and cost barely move. Abstention on out-of-scope questions comes from the grader + budget. B doubles cost for every query; D failed p95 and is hard to evaluate; E has unacceptable index cost and ACL blending across the whole corpus.

Consequences
:   Router threshold and grader prompt become versioned artefacts under the eval gate (Day 26). We must monitor escalation rate and abstention rate as SLIs; a jump in either signals drift or an index problem. Self-query field enums must be regenerated when metadata taxonomies change. Revisit if escalation rate exceeds 40% or abstention exceeds 10% for two weeks.

**Tomorrow · Day 11**RAG evaluation: RAGAS / DeepEval / TruLens, faithfulness, context precision and recall, golden sets, and LLM-as-judge pitfalls.

Prepared for Rahul Bhave · GenAI Architect 30-day plan · library versions checked on PyPI on 19 Oct 2026.

## Day 11 — [RAG Evaluation](week-2-production-retrieval/day-11-rag-evaluation/README.md)

Title

**Layered evaluation with a calibrated judge as the release gate for the enterprise knowledge assistant**

Context

The RAG assistant serves HR, IT and finance policy to around 20k users. Teams change chunking, embeddings and prompts weekly, and two regressions reached production last quarter unnoticed. Data residency rules out sending documents to external judge APIs for some business units. The eval budget is under 5% of serving cost.

Options

A) Manual spot checks before each release. B) A managed platform evaluator (watsonx.governance / Azure AI Foundry) only. C) Layered: deterministic IR and format checks, then a Ragas/DeepEval judge on a versioned golden set in CI, then a reference-free triad on 2% of sampled production traces, with a self-hosted judge from a different family than the generator, calibrated quarterly against SME labels.

Decision

**C.** The CI gate blocks a merge if faithfulness drops by more than 3 pp or hit@5 by more than 2 pp against main, paired on a 250-item golden set. The judge is pinned by model version and prompt hash, and must hold κ ≥ 0.6 against 100 SME-labelled items to stay the gate. Production failures flow into the golden set every month.

Consequences

Positive: regressions get caught pre-merge, metrics map directly to the fix (retriever versus generator), and it stays residency-compliant. Negative: SME labelling effort (~2 days per quarter), GPU capacity for the judge, and a golden-set curation owner is required. Risks: the judge drifts on upgrade (mitigated by re-calibrating before any judge change), and over-fitting to the golden set (mitigated by a hidden hold-out slice). Managed evaluators remain an option for business units without residency limits.

## Day 12 — [Multimodal RAG](week-2-production-retrieval/day-12-multimodal-rag/README.md)

Title
:   Ingestion representation for a regulated BFSI document assistant (policies, rate tables, scanned annexures)

Context
:   About 400k pages, about 30% of answer-bearing content in tables, about 10% scanned. India data residency (DPDP), citations required for auditors, existing watsonx.ai tenancy, and a 3-person platform team.

Options
:   (1) pypdf text only. (2) Docling layout parse + table summaries + VLM figure captions (multi-vector). (3) ColQwen page-image retrieval only. (4) Managed cloud document AI.

Decision
:   Option 2, self-hosted in-region on the existing OpenShift GPU pool. Tables are atomic elements and aggregate queries route to SQL. Option 3 runs as a secondary index only for the slide-deck collection, fused with RRF.

Why not
:   (1) fails the table questions in the pilot golden set. (3) alone gives page-level citations, has no BM25 and adds about 200 GB of index. (4) is blocked by residency for some documents and raises per-page cost at re-ingest.

Consequences
:   GPU capacity is needed at ingest. Parser and model versions are pinned and recorded per element, and upgrades go through a re-parse plus an evaluation gate. Captioning cost is budgeted and cached by content hash. The golden set is stratified by modality and owned by the business SME.

## Day 13 — [Feedback Loops & Caching](week-2-production-retrieval/day-13-feedback-loops-and-caching/README.md)

Title
:   Layered caching for the enterprise policy assistant

Context
:   ~60% of traffic is FAQ-class HR/IT questions. The prompt carries a ~4k-token static prefix. Answers are permission-scoped, and the corpus re-indexes nightly. Wrong policy answers carry compliance risk.

Options
:   (A) No caching. (B) Provider prompt caching only. (C) B + a global semantic cache. (D) B + exact cache + a scoped, guarded semantic cache for allow-listed intents.

Decision
:   **D.** Prompt caching everywhere with a stable-first prompt layout. Semantic cache keyed by tenant, ACL hash and corpus version; entity guard; admission only after grounding checks pass; launch gated on a false-hit rate under 0.5% on a 300-pair near-miss set.

Consequences
:   Lower input cost and TTFT; FAQ hits served in tens of ms. We take on a calibration set to maintain, cache-specific monitoring (false-hit feedback, hit rate by intent), purge APIs for DPDP erasure, and nightly invalidation on re-index. C was rejected for ACL leakage risk; A for cost.

**Tomorrow · Day 14:** Mini-project — a production-grade RAG with hybrid search, reranking and an evaluation report. Keep today's cache and golden set; you'll plug them in.

## Day 15 — [LangChain Core](week-3-agentic-systems/day-15-langchain-core/README.md)

Title
:   Orchestration framework boundary for the GenAI platform

Context
:   Four teams are building RAG and agent apps across watsonx.ai (Granite), Azure OpenAI and a local Ollama dev loop. Each team currently writes its own provider adapters, retries and tracing. LangChain releases roughly weekly; the 1.0 split moved many APIs.

Options
:   (A) Raw SDKs per provider. (B) Full LangChain including legacy chains. (C) `langchain-core` + partner packages behind internal ports; LangGraph for stateful flows; `create_agent` allowed for simple tool assistants. (D) In-house framework.

Decision
:   **C.** Domain code depends on our ports, implemented with core Runnables. Versions pinned in a shared constraints file; upgrades monthly behind the eval gate. `langchain-classic` and unpinned `langchain-community` imports blocked by a lint rule. Retries and provider failover owned by the AI gateway; app code does only semantic fallbacks.

Consequences
:   Faster onboarding and uniform tracing; provider swaps become config changes. We accept framework churn (mitigated by pinning and the port layer) and the cost of owning a small adapter library. A was rejected for duplicated effort, B for deprecated surface area, D for maintenance cost.

**Tomorrow · Day 16:** Tool and function calling across OpenAI, Anthropic, Gemini and open-source models: parallel tools, schemas and error handling. Keep today's two tools; you'll run them against several providers.

## Day 16 — [Tool Calling](week-3-agentic-systems/day-16-tool-calling/README.md)

ADR-016 · Tool execution layer for the support copilot

Context
:   The copilot calls 12 internal tools, 3 of which write (refunds, ticket updates, address change). The primary model is on Azure OpenAI, failover goes to Anthropic, and the on-prem Granite path runs on watsonx.ai. Audit requires a trace for every write. The p95 latency target is 6 s.

Options
:   (A) Provider SDK auto-execution per provider. (B) A framework's tool abstraction only (LangChain/LangGraph tools). (C) A canonical tool registry plus our own executor, with thin provider adapters; LiteLLM is allowed for transport.

Decision
:   **C.** The executor owns validation, RBAC scope checks, approval for writes over ₹5,000, idempotency keys, retries, timeouts and tracing. Strict mode is on wherever the provider supports it. Parallel calls are allowed for reads, and writes are serialized. Budgets are 6 model steps and 3 identical calls. LangGraph nodes (Day 17) call this executor rather than raw tools.

Consequences
:   Plus: provider switching becomes a config change proven by contract tests, one audit trail covers every write, and write behaviour is deterministic. Minus: we own about 500 lines of infra code, adapters lag new provider features (tool search, programmatic calling) until we flag them in, and the team needs a golden tool-call eval in CI (Day 26). We revisit this ADR if one provider's hosted agent runtime becomes the standard.

09 · Tomorrow  
**Day 17: LangGraph.** State, nodes, conditional edges, checkpointing, human-in-the-loop and streaming. Today's executor becomes a graph node with a real interrupt for approval.

## Day 17 — [LangGraph](week-3-agentic-systems/day-17-langgraph/README.md)

**Orchestration and persistence for the refund / claims assistant** · Status: Accepted

Context
:   Customer-support assistant on watsonx.ai (Granite) with Azure OpenAI as fallback. Refunds above ₹5,000 need a supervisor's approval, which can take up to 72 h. Auditors require a per-ticket decision trail. Expected 20k tickets/day across 4 API replicas.

Options
:   (A) ReAct agent with an "ask\_human" tool and in-memory state. (B) LangGraph StateGraph with `interrupt()` + PostgresSaver. (C) LangGraph for the LLM steps, Temporal or Step Functions for the long-running approval workflow.

Decision
:   B. Routing is deterministic code; approval is a durable interrupt; the checkpoint history is the audit trail (exported to the audit store nightly). C was rejected for now because it adds a second orchestration runtime for one approval step; revisit if cross-system sagas (payments, ERP) appear.

Consequences
:   + Restart-safe pauses, replay for incident analysis, unit-testable routers. − Graph and state schema need versioning; paused threads pin a graph version. − Postgres growth: checkpoint TTL 30 days, documents stored by reference. − Side effects (refund API) live only in a post-approval node with an idempotency key.

**Tomorrow · Day 18:** Multi-agent patterns — supervisor, hierarchical, swarm/handoffs, ReAct vs plan-and-execute, and when not to go multi-agent.

## Day 18 — [Multi-agent patterns](week-3-agentic-systems/day-18-multi-agent-patterns/README.md)

Title
:   Agent topology for the enterprise employee assistant (HR + IT + Finance)

Status
:   Accepted

Context
:   About 40 tools across three domains; payroll data needs separate RBAC; 80% of traffic is single-intent FAQ; p95 target 6 s; budget ≤ ₹2 per conversation; audit needs one accountable answer path.

Options
:   (1) One agent with all tools. (2) Router workflow to per-domain agents. (3) Supervisor with domain sub-agents as tools. (4) Peer handoffs/swarm.

Decision
:   (2) as the front door: a structured-output classifier routes single-intent requests to a domain agent. Cross-domain requests ("both") go to (3), a supervisor that calls domain agents as tools. Reject (4) for now: no single guardrail owner, and shared history crosses the payroll RBAC boundary. Payroll runs as its own sub-agent with its own credentials, so isolation is enforced by identity rather than prompts.

Consequences
:   + Predictable cost and latency on 80% of traffic; each route testable in CI; clean RBAC boundary. − Misroutes are silent, so log the classifier confidence and run a weekly misroute review. − Supervisor briefs can be lossy, so require a typed brief schema (facts, constraints, expected output). Revisit if cross-domain traffic exceeds 30% or the eval shows the router capping quality.

## Day 19 — [Agent Memory](week-3-agentic-systems/day-19-agent-memory/README.md)

ADR
:   **ADR-019: Long-term memory for the internal engineering copilot** (status: proposed)

Context
:   12k engineers across 30 client accounts. Users want the copilot to remember stack, preferences and ongoing work across sessions. Constraints: per-client data isolation, DPDP erasure within 30 days of request, p95 first-token latency under 2 s, already running Postgres for app data.

Options
:   (A) No long-term memory, rolling summary per thread only. (B) LangGraph `PostgresStore` with pgvector, namespaced `(client, user, kind)`, background extraction with consolidation. (C) Managed Mem0 platform. (D) Graphiti on Neo4j.

Decision
:   B. It reuses Postgres operations, backup and encryption; keeps data in our VPC; namespace prefixes are enforced in a store wrapper fed from the auth token; extraction runs async with a 3B-class model and ADD/UPDATE/DELETE consolidation. A profile document holds structured fields (role, client, stack). Revisit D if the "facts change over time" defect rate exceeds 5% in evals.

Consequences
:   Positive: no new vendor, cheap reads, clean erasure (delete by namespace prefix plus thread checkpoints). Negative: we own consolidation quality and the poisoning guard; up to 60 s eventual consistency for new facts; pgvector index tuning becomes our job as memory counts grow. Requires a memory eval set (recall, contradiction, poisoning) as a CI gate.

**Tomorrow · Day 20:** MCP: protocol, servers and clients, tools/resources/prompts, auth, and building and securing a server.

## Day 20 — [MCP](week-3-agentic-systems/day-20-mcp/README.md)

ADR
:   **ADR-020: How enterprise agents reach internal systems** (status: proposed)

Context
:   Six teams want their systems (HR policies, Snowflake, ServiceNow, Jira, SharePoint, an internal pricing API) available to three agent hosts: watsonx Orchestrate, an internal LangGraph copilot, and developer IDEs. Requirements: per-user authorization, audit for compliance, DPDP data residency in India regions, and no direct internet exposure.

Options
:   (A) Native function calling inside each host. (B) Each team runs its own remote MCP server with its own auth. (C) Teams run remote MCP servers behind a shared MCP gateway (IBM ContextForge, self-hosted on our OpenShift) with Entra ID as the authorization server. (D) A managed cloud gateway (AgentCore Gateway or Azure API Management).

Decision
:   C. Teams own their servers and tool design; the gateway owns the allow-list, tool pinning, rate limits, OpenTelemetry traces and audit. Tokens are audience-bound per server; downstream calls use on-behalf-of exchange. All traffic stays on private networking. Local stdio servers are allowed only for developer IDEs, from an internal package mirror.

Consequences
:   Positive: one integration per system, consistent security and audit, works across all three hosts. Negative: the gateway is a tier-1 service that needs HA and an on-call owner; one extra hop (target under 10 ms p95); teams must follow a tool-design guide and pass tool-selection evals before onboarding. Revisit D if we standardise on a single cloud.

**Tomorrow · Day 21:** Guardrails and prompt-injection defense (NeMo Guardrails, Guardrails AI, Llama Guard, Presidio), plus the Week 3 mini-project: a LangGraph agent with an MCP tool and guardrails.

## Day 21 — [Guardrails & Prompt-Injection Defense](week-3-agentic-systems/day-21-guardrails-and-prompt-injection-defense/README.md)

#### ADR-021: Deterministic tool policy is the security boundary for agents; classifiers are advisory

Status
:   Accepted

Context
:   Enterprise HR/IT assistant on LangGraph. Reads internal KB and email via MCP; can create tickets and send email. Content is partly user-authored (wiki), so indirect injection is in scope. India DPDP and internal infosec require PII minimisation and an audit trail. p95 latency target 4 s.

Options
:   (A) Managed guardrail classifier on input and output only. (B) A plus NeMo self-check rails. (C) Deterministic tool policy with taint tracking and human approval as the primary control, plus a lightweight classifier and Presidio as secondary layers. (D) Full CaMeL-style plan/execute separation.

Decision
:   C now, with D evaluated for the payments workflow. A and B leave the outcome dependent on a classifier being right, and an attacker gets unlimited tries. D is the strongest but cuts flexibility for open-ended questions.

Consequences
:   Positive: worst case of a successful injection is a refused or approval-gated action, which is auditable. Guard overhead stays under ~300 ms. Negative: approval UX must be designed to avoid fatigue; policy rules need an owner and versioning (YAML in Git with review). Requires a durable checkpointer (Postgres) for paused threads. Red-team ASR becomes a release gate in CI (ties to Day 26).

## Day 22 — [Cloud Reference Architectures for GenAI](week-4-cloud-production-architecture/day-22-cloud-reference-architectures-for-genai/README.md)

Title

Model-plane processing locality for personal-data GenAI workloads (India)

Status

Accepted

Context

A BFSI client, regulated in India, is putting five GenAI workloads on Azure, AWS and watsonx. Its risk policy requires in-India processing for personal data. Peak load is 3× the average. Global SKUs are cheaper and have more quota.

Options

A. Global SKUs everywhere plus contractual assurances. B. Data-zone / geo profiles (APAC). C. In-geo regional deployments + provisioned throughput for the base load, routed by data class at the gateway. D. Self-host open-weight models on OpenShift in the client DC.

Decision

**C**, with D kept for any workload that later requires an air gap. Public-class traffic may use Global capacity through the same gateway.

Consequences

+ Passes the residency audit; enforced in CI (policy-as-code), in the cloud (Azure Policy / SCP) and at runtime (gateway allow-list). − Roughly 10–20% higher unit cost than Global; new models arrive later; PTU is committed per region, so DR doubles the commitment. Revisit when India regions get the target models at Global quota, or when the client's policy relaxes to DPDP baseline.

## Day 23 — [Model Serving & Gateways](week-4-cloud-production-architecture/day-23-model-serving-and-gateways/README.md)

Title
:   Centralise all LLM access through a self-hosted AI gateway

Context
:   Six product teams call Azure OpenAI, watsonx.ai and a self-hosted vLLM pool directly with their own keys. There is no chargeback, keys sit in app configs, 429 incidents cascade, and every team writes its own retry logic.

Options
:   (A) Shared SDK wrapper library. (B) Cloud-native APIM AI-gateway policies. (C) LiteLLM proxy (self-hosted, HA, Postgres + Redis). (D) Agent Router on the existing Envoy/K8s platform.

Decision
:   **C now, with D re-evaluated in 2 quarters.** LiteLLM covers every provider in the estate including watsonx, ships virtual keys, budgets and fallbacks today, and keeps the OpenAI-compatible contract, so apps stay portable. Deploy it as 3+ stateless replicas behind an internal load balancer on private endpoints. Pin the image digest and verify hashes. Provider keys live only in Key Vault or Secrets Manager, accessed via workload identity.

Consequences
:   + Central quotas, chargeback, one place for guardrails and OTel. + Provider swaps become config. − A new tier-1 dependency (needs an SLO, on-call and DR), 5–20 ms added latency to verify in load tests, and a concentrated supply-chain and secret risk. − Fallback models must pass the same eval gate in CI (Day 26). Revisit if self-hosted traffic exceeds 50% and needs inference-aware K8s routing.

**Tomorrow · Day 24** — Containerizing GenAI services: FastAPI + streaming + Docker best practices (multi-stage builds, secrets, health checks). Today's gateway becomes the thing you containerize.

## Day 24 — [Containerizing GenAI Services](week-4-cloud-production-architecture/day-24-containerizing-genai-services/README.md)

Status

Proposed · 2026-11-02

Context

Chat UI and two internal consumers need token streaming. Traffic passes through corporate APIM and a WAF. The backend is a self-hosted vLLM pool with about 48 concurrent sequences at target latency, plus Azure OpenAI as overflow. Peak demand is about 3× capacity for 20-minute windows (Monday 10:00 IST). Users abandon roughly 15% of answers mid-stream.

Options

(A) WebSockets with an unbounded server queue. (B) SSE over HTTP, with a per-replica semaphore, 429 + Retry-After, and overflow to Azure OpenAI. (C) Async jobs: POST returns a job id, the client polls or subscribes.

Decision

**B.** SSE passes APIM and WAF unchanged and reuses existing HTTP auth and logging. Admission control is enforced before the stream opens. Upstream calls are cancelled on disconnect. Overflow routing lives in the gateway (LiteLLM-style fallback) rather than in clients. C is kept for batch summarisation jobs over 60 s.

Consequences

+ Bounded TTFT under burst; about 15% GPU time recovered from abandoned streams; no new protocol through security review. − Clients must handle 429 and in-band `error` events (SDK work). Per-replica caps need a shared limiter (Redis) once there are more than 3 replicas. Output guardrails must run incrementally or on the final buffer, not before the first token. Overflow to Azure OpenAI adds per-token cost, so track it as a budget KPI.

Tomorrow · Day 25 — Kubernetes for GenAI: HPA/KEDA autoscaling, GPU scheduling, KServe, Helm and canary releases.

## Day 26 — [CI/CD for GenAI](week-4-cloud-production-architecture/day-26-ci-cd-for-genai/README.md)

**ADR-026: Prompt release and rollback mechanism for the enterprise knowledge assistant** · Status: Accepted

Context

Prompts change weekly and are edited by both engineers and domain SMEs. Each rebuild and redeploy of the app takes about 25 minutes. A bad prompt last quarter caused 40 minutes of ungrounded answers before the rollback finished. Compliance requires an audit trail of every production prompt.

Options

**A.** Prompts in code, shipped with the image (canary deploy). **B.** Registry-only, edited in the UI, alias resolved at runtime. **C.** Hybrid: git is the source of truth, CI runs the eval gate and syncs approved versions to the registry; the app resolves the `production` alias with a 30 s TTL.

Decision

**C.** PR checks run deterministic and replayed evals. Merging creates a registry version labelled `candidate`. Promotion to `production` needs the nightly live eval to pass and approval in a GitHub protected environment. Rollback is moving the alias back, which is available to on-call without a deploy.

Consequences

Rollback drops from ~25 min to under 1 min (TTL-bound). The app now depends at runtime on the registry, so we keep a last-known-good prompt bundled in the image as a fallback. UI edits are disabled in production projects so the two sources can't drift. Retrieval-config changes still go through image deploys and are out of scope for this mechanism. We'll revisit that when index aliases are in place.

## Day 27 — [GenAI Observability](week-4-cloud-production-architecture/day-27-genai-observability/README.md)

Title
:   Vendor-neutral GenAI telemetry through OpenTelemetry, with a self-hosted LLM observability backend

Status
:   Accepted · Nov 2026

Context
:   Our enterprise knowledge assistant runs on Azure OpenAI and watsonx.ai, behind a LiteLLM gateway, with LangGraph agents and MCP tools. We need trace-level debugging, prompt-version attribution, online eval scores and per-team cost, while meeting DPDP data-residency rules and keeping existing on-call tooling (Instana).

Options
:   (A) LangSmith SaaS with its SDK. (B) Vendor SDK per tool (Langfuse SDK, Phoenix register). (C) OTel SDK plus GenAI instrumentors → OTel Collector (redact, tail-sample, fan out) → self-hosted Langfuse + Instana + COS archive. (D) APM only.

Decision
:   Option C. Instrumentation uses only OTel APIs and pinned instrumentors. Request-scoped attributes (`tenant`, `app.prompt.version`, `feature`) are propagated to all spans. Content capture is redacted and sampled. Langfuse is the system of record for prompts, datasets and scores.

Consequences
:   + The backend can be swapped through exporter config. + One redaction choke point for audit. + Exact cost metrics alongside sampled traces. − We own semconv drift while `gen_ai.*` is in Development (mitigation: lockfile pins and a contract test asserting required attributes). − We run Langfuse's ClickHouse/Postgres/Redis/S3 stack (mitigation: managed Postgres and Redis, ClickHouse sizing by monthly span volume). − Two UIs for engineers (Langfuse for quality, Instana for SLOs), joined on trace ID.

**Tomorrow · Day 28:** Security, governance & responsible AI: OWASP Top 10 for LLM apps, RBAC, secrets, encryption, network segmentation, audit, EU AI Act and India's DPDP Act.

## Day 28 — [Security, Governance & Responsible AI](week-4-cloud-production-architecture/day-28-security-governance-and-responsible-ai/README.md)

Title
:   Enforce document authorization at the retrieval index with end-user identity; guardrails as defense in depth

Status
:   Accepted

Context
:   Enterprise knowledge assistant for about 50k employees over SharePoint, Confluence and HR systems with heterogeneous ACLs. Regulated content (HR, finance, health benefits). Requirements: no cross-permission disclosure, auditable access decisions, under 2.5 s p95 end to end, DPDP erasure within the statutory window.

Options
:   (1) Prompt instructions plus output classifier. (2) Post-retrieval ACL filter in the app. (3) ACL pre-filter in the vector index using the user's group claims via OBO token. (4) Separate index per sensitivity tier and BU.

Decision
:   Option 3 as the control of record, plus option 4 only for the "restricted" tier (HR case files, M&A). Chunks carry `acl_groups`, `classification`, `source_doc_id` and `subject_ids`. Missing ACL fails closed. Llama Guard / Granite Guardian and Presidio sit on input and output as defense in depth. The semantic cache is keyed on a hash of the user's effective permission set.

Consequences
:   + Access decisions are deterministic and testable in CI with personas. + Erasure is a metadata delete. − Requires an event-driven ACL sync pipeline with a sync-lag SLO (target under 15 min; revocations pushed immediately). − Large group counts need group flattening and filter-aware ANN; benchmark recall at real filter selectivity. − A second index tier adds ops cost, accepted for the restricted data's risk.

**Tomorrow, Day 29:** Architect deliverables: HLD/LLD, NFRs, ADRs and decision logs, a token-cost estimation model, DR and resilience for GenAI, and explaining it all to stakeholders.

## Day 29 — [Architect deliverables](week-4-cloud-production-architecture/day-29-architect-deliverables/README.md)

### ADR-0012 · Resilience posture for the enterprise knowledge assistant Accepted

Context
:   Internal assistant for 50k employees across India and the EU. The business owner has accepted a 99.9% availability target. Data must stay in-region. The primary model is served pay-as-you-go from one provider region. Budget does not allow doubling index and GPU capacity.

Options
:   (A) Degraded mode only. (B) Multi-region within the same provider and data zone, plus degraded mode. (C) Multi-provider fallback. (D) Active-active multi-region with a replicated index.

Decision
:   **B.** The gateway fails over to the paired region within the same data zone. If both fail, the UI falls back to search-only mode. The vector index is snapshotted every 6 hours and can be rebuilt from source in under 8 hours (measured). Embedding model version is pinned, and its retirement date is tracked.

Consequences
:   Covers regional outages and quota exhaustion at roughly 10–15% extra cost (reserved quota in the second region). It does not cover a provider-wide outage, which we accept and mitigate with search-only mode. C becomes the decision if the assistant moves into a customer-facing workflow.

Revisit when
:   Provider-wide incident longer than 1 h · assistant used in a regulated workflow · second-region quota unavailable.

## Day 30 — [Capstone System Design](week-4-cloud-production-architecture/day-30-capstone-system-design/README.md)

Title
:   Enforce document permissions by early binding (ACLs in the index, filtered at query time)

Status
:   Accepted · capstone design · 2026-11-08

Context
:   50k employees across HR, finance, engineering and leadership. Sources have per-document ACLs and around 2M documents. A single leaked salary or reorg document is a reportable incident under internal policy and a DPDP exposure. Retrieval must stay under ~200 ms at 25 QPS.

Options
:   **A. Early binding:** ACL principals indexed as filterable fields on every chunk, pre-filtered inside the vector and keyword search. **B. Late binding:** retrieve top-k unfiltered, then check each result against the source system's permission API. **C. Index per group or per BU:** physical separation.

Decision
:   A, plus a post-retrieval re-check and a revocation fast-path. B adds a per-result call to SharePoint or Confluence (seconds of latency, throttling) and returns empty pages when most of the top-k is filtered out. C explodes the index count, because group membership overlaps heavily, and duplicates shared documents. C is kept only for the leadership tier, which gets a separate index with stricter network access.

Consequences
:   Positive: fast, complete results; leaks become testable in CI. Negative: ACL sync is now a tier-1 pipeline with its own SLO (revocation ≤ 5 min) and a nightly reconciliation job; nested-group expansion and Entra group overage must be handled; semantic-cache keys must include an entitlement hash. Review trigger: a source that supports only late-bound permission checks, or a regulator asking for physical separation.

## Day 35 — [Revision Drill: Agent Memory & Cloud Architectures](revision-drills/day-35-agent-memory-and-cloud-architectures/README.md)

### ADR-035: Agent memory on self-managed Postgres + pgvector, behind a MemoryStore interface

**Context.** The RM copilot needs per-RM and per-customer memory with India residency, per-read audit, structured commitments, erasure within 7 days, and a 15-minute RPO. Azure is the strategic cloud; the bank wants to avoid lock-in on customer data.

**Options.** (A) Foundry Agent Service threads and memory (managed, but memory is preview and data lives in service-shaped stores); (B) Mem0 self-hosted (good extraction and consolidation, an extra component to secure); (C) Azure Database for PostgreSQL with pgvector, accessed through LangGraph's `PostgresStore` and a thin MemoryStore interface; (D) Cosmos DB with vector search.

**Decision.** C. It is GA, in-region with an asynchronous cross-region replica, supports row-level security and customer-managed keys, holds structured commitments and vectors in one database, and the same Postgres already holds LangGraph checkpoints, so one erasure job covers both.

**Consequences.** We build extraction and consolidation ourselves (borrowing prompts from LangMem and Mem0). We own vacuum, index tuning and capacity. The interface lets us move to a managed memory service later if it becomes GA with the controls we need; the exit cost is a data migration, written down here. Revisit in 6 months or when Foundry memory goes GA.

## Day 36 — [Revision Drill: Tool Calling & Security Governance](revision-drills/day-36-tool-calling-and-security-governance/README.md)

### ADR-036: Enforce tool policy in a central tool gateway, with object-level checks kept in each back end

**Context.** Many teams are building tool-calling agents on LangGraph, watsonx Orchestrate and MCP, against several model providers. Prompt injection cannot be prevented reliably at the model. The insurer needs tiered approvals, egress control and a seven-year audit trail that is consistent across agents.

**Options.** (A) Rules in the system prompt; (B) policy in framework middleware per agent (LangGraph interrupts, callbacks); (C) a central tool gateway on the call path (MCP gateway or API Management) with a tool registry; (D) leave it to each back-end service.

**Decision.** C, plus D for object-level authorisation. A is not a control. B depends on each team implementing it the same way and is skipped by tools called outside the framework. The gateway gives one place for schema validation, tier policy, credential injection and audit, independent of the framework and the model provider. Back ends keep their own authorisation so the gateway is not a single lock.

**Consequences.** The gateway is on the critical path: it needs a latency budget (≤ 50 ms p95), high availability across zones and fail-closed behaviour for tier 2–3. A platform team must own the registry and onboarding, which slows the first tool for each team by a few days. Agent frameworks still handle HITL interaction, but the approval check is done by the gateway. Revisit when OWASP's Agent Control Standard has framework support, to replace custom hooks with the standard ones.

## Day 37 — [Revision Drill: LangChain Core & Containerizing](revision-drills/day-37-langchain-core-and-containerizing/README.md)

Title
:   LangChain usage boundary and streaming transport for GenAI APIs

Context
:   Multiple teams ship LLM APIs. LangChain v1 changes fast and LangServe is archived. Traffic crosses a corporate API gateway; users expect token streaming.

Options
:   (a) LangChain end to end incl. classic chains; (b) `langchain-core` LCEL + provider packages at the model edge, own FastAPI service; (c) raw provider SDKs with a thin in-house abstraction.

Decision
:   **(b).** LCEL for prompt → model → parser, fallbacks and tracing; LangGraph only where state or human approval is needed. FastAPI native SSE as the streaming contract. One Uvicorn process per pod, hardened multi-stage image, liveness without dependency calls.

Consequences
:   + Provider swap (watsonx ↔ Azure OpenAI ↔ Ollama) is a config change; + contract tests with fake models make upgrades safe. − Weekly dependency churn needs Renovate + an eval gate; − teams must hand-write serving code LangServe used to give them (mitigated by a shared service template).

## Day 38 — [Revision Drill: Caching & Architect Deliverables](revision-drills/day-38-caching-and-architect-deliverables/README.md)

Title
:   Layered caching policy for the enterprise knowledge assistant

Context
:   Cost is 2× budget. Traffic is roughly 40% repeated FAQ intents. The system is multi-tenant with ACL-filtered retrieval and spans two residency regions. Answers must cite current policy.

Options
:   (A) Global semantic cache on all traffic. (B) Provider prefix caching plus context trimming only. (C) B plus a scoped semantic cache for whitelisted intents, behind a feature flag.

Decision
:   **C.** Prefix caching and trimming are on everywhere. The semantic cache is limited to intents with a measured false-hit rate ≤0.5% at the chosen τ, partitioned by region/ACL/locale, with keys that include the source-document version, and it is disabled for multi-turn and agentic flows.

Consequences
:   + Most of the saving comes from correctness-neutral levers, and cache risk is bounded and measurable. − More moving parts: intent classifier, invalidation hooks on re-index, a cache-hit dashboard. The false-hit eval must run in CI whenever the embedding model or τ changes. Revisit in 90 days with production hit/false-hit telemetry.

## Day 39 — [Revision Drill: Hybrid Search & Agentic RAG](revision-drills/day-39-hybrid-search-and-agentic-rag/README.md)

|  |  |
| --- | --- |
| Context | A multilingual helpdesk corpus with error codes and in-region residency. p95 TTFT target under 3 s. Retrieval misses are the top cause of wrong answers in pilot traces. |
| Options | (a) Dense only. (b) Hybrid with RRF and no rerank. (c) Hybrid with RRF, a self-hosted cross-encoder and a score gate. (d) Hybrid with a managed reranker. |
| Decision | (c): engine-native hybrid with RRF (k recorded in config), bge-reranker-v2-m3 on an in-region GPU pool, a top-40 → top-6 rerank, τ derived from the golden set, and one bounded rewrite before abstaining. |
| Consequences | + Exact-code recall and residency. + A deterministic, testable gate. − A GPU pool to run and capacity-plan. − τ must be re-derived on every reranker upgrade. Fallback to fused order on rerank timeout. Revisit if eval shows managed reranking at least 3 nDCG points better and residency allows it. |

## Day 42 — [Revision Drill: Fine-tuning vs RAG & Model Serving](revision-drills/day-42-fine-tuning-vs-rag-and-model-serving/README.md)

Title
:   Serve tenant-specific behaviour as LoRA adapters on a shared vLLM base, behind a self-operated gateway

Context
:   Several business units want customised behaviour. GPU capacity is fixed. TGI is in maintenance mode, and InstructLab's upstream is archived. Two compromises of a popular open-source gateway this year show that the gateway is a high-value target.

Options
:   (A) One merged model per tenant on dedicated GPUs. (B) Shared base plus multi-LoRA on vLLM. (C) Managed provider custom models only. (D) Prompt-only customisation.

Decision
:   D by default. B where an adapter beats D on the golden set by an agreed margin. C only as a residency-compliant overflow for data classified as allowed. The gateway runs pinned, signed images by digest, with egress restricted.

Consequences
:   + One GPU pool serves many tenants, and adapters are cheap to version and roll back. − There is cold-load latency for adapters outside the hot set. A noisy tenant can affect shared capacity, which quotas and priority classes mitigate. Each vLLM upgrade needs a re-benchmark. The tuning recipe (data, eval, hyperparameters) is kept tool-agnostic so the training stack can be replaced.

## Day 43 — [Revision Drill: Multi-tenant Vector DBs & Agent Memory](revision-drills/day-43-multi-tenant-vector-dbs-and-agent-memory/README.md)

Title
:   Tiered tenant isolation for knowledge, and a Postgres-backed namespaced store for agent memory

Context
:   A multi-tenant copilot with a long tail of small tenants, a few large or regulated ones, per-user long-term memory, DPDP erasure obligations, and a security review that will ask how cross-tenant leakage is prevented.

Options
:   (A) A collection per tenant for everything. (B) One shared collection with an app-level filter only. (C) Tiered: a shared collection with a tenant index, dedicated shards or collections for large and regulated tenants, and server-side scoping. Memory either (M1) in the same vector DB or (M2) in Postgres via LangGraph `PostgresStore`.

Decision
:   C + M2. Tenant identity comes only from the verified token. A single retriever adds the filter, and the DB enforces it again. Memory is namespaced (tenant, user), uses slot keys for facts, extracts from user turns only, and has TTLs on episodic memories. Leak probes run as a CI gate.

Consequences
:   + Low cost for the long tail, contractual isolation where it's needed, and erasure that is provable per store. Memory shares the transactional, backup and RLS story with checkpoints. − Two storage technologies to operate. Promotion between tiers needs tooling and monitoring. Shared-tier erasure depends on compaction timing. The extraction model's accuracy has to be evaluated continuously.

## Day 46 — [Revision Drill: Multi-Agent & GenAI Observability](revision-drills/day-46-multi-agent-and-genai-observability/README.md)

Title
:   Trace schema and cost control for agent teams

Status
:   Proposed · 2026-11-24

Context
:   Several teams are building agent workflows on different frameworks (LangGraph, OpenAI Agents SDK, watsonx Orchestrate). Finance wants cost per conversation and per agent. Compliance forbids prompt and response text in the monitoring platform. The OpenTelemetry GenAI conventions are in Development status and attribute names have changed before.

Options
:   (A) Each team uses its framework's native tracing and backend. (B) One schema: OpenTelemetry GenAI conventions at a pinned version, emitted through a shared instrumentation module, normalised in the Collector, backend replaceable. (C) Structured logs only, with cost computed from provider invoices.

Decision
:   B. Every model call sits under exactly one `invoke_agent` span and every run under one `invoke_workflow` span that carries conversation ID, tenant and outcome. Our own attributes use the `app.` namespace. Content capture is off; content goes to a regional store and spans hold references. Metrics come from 100% of spans, traces are tail-sampled. Token and handoff budgets are enforced in the orchestrator, and a CI check fails any scenario trace with an unattributed model call, a repeated edge above the threshold or a cost above budget.

Consequences
:   + One cost and reliability view across frameworks, a backend that can be swapped, and a compliance position that holds by default. Loops are bounded by design and detected by test. − We own an instrumentation module and Collector transforms, and must track spec changes until the conventions stabilise. Framework auto-instrumentation will not match our schema exactly, so some spans need mapping. Debugging without content is slower until the reference store and its access process exist.

## Day 47 — [Revision Drill: Guardrails & Cloud Architectures](revision-drills/day-47-guardrails-and-cloud-architectures/README.md)

Title
:   Guardrails as a platform enforcement point with provenance-based taint

Status
:   Proposed · 2026-11-25

Context
:   Several teams are shipping agents that read outside content (email, web, uploads) and call tools. Each team has added its own detector library. Residency rules differ by tenant and data class, and model capacity is cheaper and larger on wider processing scopes. A guardrail library we depend on has just deprecated its policy language.

Options
:   (A) Each application embeds a guardrail library and its own rules. (B) A shared enforcement point in the gateway and orchestrator: classification and masking, residency routing by data class, provenance tagging of context, a tool gate with an egress allowlist and approval on tainted runs, and an output rail; detectors are pluggable inputs to it. (C) Rely on the cloud provider's managed guardrail service and network controls alone.

Decision
:   B. Policy lives in versioned data and code we own, keyed by tenant and data class. A run is tainted by the source of its context, not by a detector verdict. Authorisation, routing and egress rules fail closed; when no compliant deployment exists the request queues or is refused and never falls back to a wider scope. Detectors (Prompt Guard, managed services, Granite Guardian) are replaceable and are measured on our own adversarial set in CI.

Consequences
:   + One place to audit, one adversarial test set, and security properties that do not depend on a classifier's miss rate. Library or vendor changes touch a plug-in, not policy. Residency becomes a routing rule that also optimises cost. − The platform team owns a critical path component with its own latency budget and on-call. Tainted runs need an approval experience, and approval fatigue is a real risk if too many actions are held. Some requests will be refused for lack of compliant capacity, which needs a product answer. Applications lose the freedom to tune rules locally.

## Day 48 — [Revision Drill: Security & Governance + Containerizing](revision-drills/day-48-security-governance-and-containerizing/README.md)

Title
:   Intake policy for third-party code and images in GenAI services

Status
:   Proposed · 2026-11-26

Context
:   Our services depend on fast-moving open-source AI libraries and on public base images. In 2026 a widely used gateway library shipped malicious releases twice, and a widely used scanner action was repointed to malicious code. Each bad artifact was withdrawn within hours. Teams currently install the latest versions at build time and deploy images by tag.

Options
:   (A) Keep tracking latest and rely on vulnerability scanning in CI. (B) Controlled intake: lockfiles with hashes, a cooldown on new releases, an internal mirror, CI actions pinned by SHA, images deployed by digest, provenance verified at admission, and a daily bot that proposes updates. (C) Vendor or fork critical dependencies and build everything from reviewed source in an isolated environment.

Decision
:   B for all services, with C reserved for the model gateway if a second review shows we use a small part of it. The cooldown is three days by default and security can waive it for an urgent fix. Scan and test jobs carry no publishing or cloud credentials.

Consequences
:   + Short-lived malicious releases do not reach builds, and a moved tag cannot change what runs. We can answer "are we affected" from SBOMs in minutes. Updates become small, reviewed changes. − Fixes arrive up to three days later unless waived, and someone owns the waiver process. The mirror and admission policy are new platform components with their own availability needs. None of this detects a malicious release that stays hidden for longer than the cooldown, so egress limits and scoped keys remain necessary.

## Day 49 — [Revision Drill: Tool Calling & LangChain Core](revision-drills/day-49-tool-calling-and-langchain-core/README.md)

Title
:   Retries and idempotency for agent tool calls

Status
:   Proposed · 2026-11-27

Context
:   Our agents call back-end tools that write: refunds, emails, ticket updates. Tool calls time out, and some timeouts happen after the write. Today the model sees the error and decides whether to call again. We have had duplicate writes, and our eval counts a run as passed when the agent finishes.

Options
:   (A) Leave retries to the model and tell it in the prompt not to repeat writes. (B) A tool contract registry declares each tool's effect class. The executor owns timeouts and retries. Every write carries an idempotency key derived from task, tool and arguments, and back ends deduplicate on it. (C) Run every multi-step write flow in a durable workflow engine with compensation steps, and let the agent only choose which workflow to start.

Decision
:   B for all agent tools. C for flows that move money across more than one system, where partial completion needs compensation. Retries apply to reads and keyed writes only. When retries run out on a write, the result is "unknown" and goes to reconciliation. The eval definition of success changes to: finished, and no duplicate or orphaned write.

Consequences
:   + a lost reply no longer causes a second write. Retry behaviour is the same for every model and provider, so failover does not change it. The contract table becomes a reviewable LLD artifact. − every back end that accepts writes must store keys for the task-resume window, and some need changes to do it. Tools that cannot be made idempotent stay behind human approval. The registry is a new thing to own. Not solved: a valid call with the wrong meaning. That stays with outcome evals and approvals.
