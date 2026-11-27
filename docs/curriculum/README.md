# GenAI Architect study path

Background reading for the boilerplate: one folder per day, each with the concept brief, trade-off table, lab code (`lab/`), interview questions and an ADR-style decision. The **Code** column points at the part of `src/aiarch` that puts the topic into practice.

Offline copies of the original interactive packs are in [`archive/html/`](archive/html/).


## Week 1 · LLM & retrieval foundations

| Day | Topic | Lab | Code |
|---|---|---|---|
| 1 | [LLM internals for architects](week-1-llm-retrieval-foundations/day-01-llm-internals-for-architects/README.md) | Hands-on lab: Token & latency lab (≈75 min) | `services/cost.py` |
| 2 | [Prompt Engineering & Structured Output](week-1-llm-retrieval-foundations/day-02-prompt-engineering-and-structured-output/README.md) | Hands-on: the structured-output bench (≈75 min) | `services/prompts.py` |
| 3 | [Fine-tuning vs RAG vs Prompting](week-1-llm-retrieval-foundations/day-03-fine-tuning-vs-rag-vs-prompting/README.md) | Hands-on lab: same model, three levers |  |
| 4 | [Hallucination Control & Grounding](week-1-llm-retrieval-foundations/day-04-hallucination-control-and-grounding/README.md) | Hands-on: a 4-layer grounded QA pipeline | `services/guardrails.py (OutputGuard), services/rag.py` |
| 5 | [Embeddings Deep Dive](week-1-llm-retrieval-foundations/day-05-embeddings-deep-dive/README.md) | Hands-on: embedding bake-off on BEIR SciFact | `adapters/embeddings/` |
| 6 | [Chunking Strategies](week-1-llm-retrieval-foundations/day-06-chunking-strategies/README.md) | Hands-on lab: the chunking bake-off (60–90 min) | `services/chunking.py` |
| 7 | [Vector Search Internals](week-1-llm-retrieval-foundations/day-07-vector-search-internals/README.md) | Hands-on lab: benchmark four index families | `adapters/vectorstore/memory.py` |

## Week 2 · Production retrieval

| Day | Topic | Lab | Code |
|---|---|---|---|
| 8 | [Vector DB Selection](week-2-production-retrieval/day-08-vector-db-selection/README.md) | Hands-on: multi-tenant filtered search, measured | `adapters/vectorstore/memory.py (tenant/ACL)` |
| 9 | [Hybrid Search & Reranking](week-2-production-retrieval/day-09-hybrid-search-and-reranking/README.md) | Hands-on: measure what hybrid and reranking buy you (60–90 min) | `services/retrieval.py` |
| 10 | [Advanced RAG](week-2-production-retrieval/day-10-advanced-rag/README.md) | Hands-on: a query-side bake-off on your laptop (60–90 min) | `services/retrieval.py` |
| 11 | [RAG Evaluation](week-2-production-retrieval/day-11-rag-evaluation/README.md) | Hands-on: a layered eval harness on your laptop | `evaluation/runner.py` |
| 12 | [Multimodal RAG](week-2-production-retrieval/day-12-multimodal-rag/README.md) | Hands-on lab · 75 min |  |
| 13 | [Feedback Loops & Caching](week-2-production-retrieval/day-13-feedback-loops-and-caching/README.md) | Hands-on lab · 75 min | `adapters/cache/semantic.py` |
| 14 | [Mini-project: production-grade RAG](week-2-production-retrieval/day-14-mini-project-production-rag/README.md) | Project spec (this repo is the reference solution) | whole repo |

## Week 3 · Agentic systems

| Day | Topic | Lab | Code |
|---|---|---|---|
| 15 | [LangChain Core](week-3-agentic-systems/day-15-langchain-core/README.md) | Hands-on lab · 75 min | `ports/__init__.py` |
| 16 | [Tool Calling](week-3-agentic-systems/day-16-tool-calling/README.md) | Hands-on: a provider-neutral tool harness | `services/agents/tools.py` |
| 17 | [LangGraph](week-3-agentic-systems/day-17-langgraph/README.md) | Hands-on: a refund-approval agent that survives restarts | `services/agents/agent.py` |
| 18 | [Multi-agent patterns](week-3-agentic-systems/day-18-multi-agent-patterns/README.md) | Hands-on: benchmark three topologies (60–90 min) | `services/agents/agent.py` |
| 19 | [Agent Memory](week-3-agentic-systems/day-19-agent-memory/README.md) | Hands-on lab · 75 minutes | — |
| 20 | [MCP](week-3-agentic-systems/day-20-mcp/README.md) | Hands-on lab · 75 minutes | `services/agents/tools.py (MCP-shaped schema)` |
| 21 | [Guardrails & Prompt-Injection Defense](week-3-agentic-systems/day-21-guardrails-and-prompt-injection-defense/README.md) | Mini-project: guarded LangGraph agent + MCP tool | `services/guardrails.py` |

## Week 4 · Cloud, production & architecture

| Day | Topic | Lab | Code |
|---|---|---|---|
| 22 | [Cloud Reference Architectures for GenAI](week-4-cloud-production-architecture/day-22-cloud-reference-architectures-for-genai/README.md) | Hands-on: residency & network policy-as-code | `docs/architecture/HLD.md` |
| 23 | [Model Serving & Gateways](week-4-cloud-production-architecture/day-23-model-serving-and-gateways/README.md) | Hands-on: build and break a mini AI gateway | `services/gateway.py` |
| 24 | [Containerizing GenAI Services](week-4-cloud-production-architecture/day-24-containerizing-genai-services/README.md) | Drill + lab files | `Dockerfile, api/app.py` |
| 25 | [Kubernetes for GenAI](week-4-cloud-production-architecture/day-25-kubernetes-for-genai/README.md) | Hands-on lab: queue-depth autoscaling with KEDA on kind | `docs/architecture/HLD.md (deployment view)` |
| 26 | [CI/CD for GenAI](week-4-cloud-production-architecture/day-26-ci-cd-for-genai/README.md) | Hands-on exercise · 75–90 min | `.github/workflows/ci.yml` |
| 27 | [GenAI Observability](week-4-cloud-production-architecture/day-27-genai-observability/README.md) | Hands-on lab · 60–90 min | `adapters/telemetry/tracer.py` |
| 28 | [Security, Governance & Responsible AI](week-4-cloud-production-architecture/day-28-security-governance-and-responsible-ai/README.md) | Hands-on lab: a policy-enforcing request pipeline (75 min) | `services/guardrails.py, docs/guides/production-checklist.md` |
| 29 | [Architect deliverables](week-4-cloud-production-architecture/day-29-architect-deliverables/README.md) | Hands-on: cost & capacity model for 50k employees | `docs/architecture/ (NFRs, ADRs), services/cost.py` |
| 30 | [Capstone System Design](week-4-cloud-production-architecture/day-30-capstone-system-design/README.md) | Capstone project: kestrel-assist | whole repo |

## Revision drills (Day 31+)

| Day | Topic | Lab | Code |
|---|---|---|---|
| 31 | [Revision Drill: MCP & Model Gateways](revision-drills/day-31-mcp-and-model-gateways/README.md) |  |  |
| 32 | [Revision Drill: LangGraph HITL & Eval Gates](revision-drills/day-32-langgraph-hitl-and-eval-gates/README.md) | Drill + lab files |  |
| 33 | [Revision Drill: Guardrails & Observability](revision-drills/day-33-guardrails-and-observability/README.md) | Drill + lab files |  |
| 34 | [Revision Drill: Multi-Agent & Kubernetes](revision-drills/day-34-multi-agent-and-kubernetes/README.md) | Drill + lab files |  |
| 35 | [Revision Drill: Agent Memory & Cloud Architectures](revision-drills/day-35-agent-memory-and-cloud-architectures/README.md) | Drill + lab files |  |
| 36 | [Revision Drill: Tool Calling & Security Governance](revision-drills/day-36-tool-calling-and-security-governance/README.md) | Drill + lab files |  |
| 37 | [Revision Drill: LangChain Core & Containerizing](revision-drills/day-37-langchain-core-and-containerizing/README.md) | Hands-on drill (60–75 min) |  |
| 38 | [Revision Drill: Caching & Architect Deliverables](revision-drills/day-38-caching-and-architect-deliverables/README.md) | Hands-on lab (~75 min) |  |
| 39 | [Revision Drill: Hybrid Search & Agentic RAG](revision-drills/day-39-hybrid-search-and-agentic-rag/README.md) | Drill + lab files |  |
| 40 | [Revision Drill: MCP Security & RAG Evaluation](revision-drills/day-40-mcp-security-and-rag-evaluation/README.md) | Today's exercise · 20 minutes |  |
| 41 | [Revision Drill: Multimodal RAG & LangGraph Recovery](revision-drills/day-41-multimodal-rag-and-langgraph-recovery/README.md) | Today's exercise · 25 minutes |  |
| 42 | [Revision Drill: Fine-tuning vs RAG & Model Serving](revision-drills/day-42-fine-tuning-vs-rag-and-model-serving/README.md) | Optional exercise (45–60 min): a gateway you can reason about |  |
| 43 | [Revision Drill: Multi-tenant Vector DBs & Agent Memory](revision-drills/day-43-multi-tenant-vector-dbs-and-agent-memory/README.md) | Exercise (60–75 min): isolation you can prove, memory you can fix |  |
| 44 | [Revision: K8s + CI/CD for GenAI](revision-drills/day-44-k8s-and-ci-cd-for-genai/README.md) | Drill + lab files |  |
| 46 | [Revision Drill: Multi-Agent & GenAI Observability](revision-drills/day-46-multi-agent-and-genai-observability/README.md) | Lab (60 min): find the money in a multi-agent trace |  |
| 47 | [Revision Drill: Guardrails & Cloud Architectures](revision-drills/day-47-guardrails-and-cloud-architectures/README.md) | Lab (60 min): build the enforcement point, then switch half of it off |  |
| 48 | [Revision Drill: Security & Governance + Containerizing](revision-drills/day-48-security-governance-and-containerizing/README.md) | Lab (60 min): pass the review, then switch the controls off |  |
| 49 | [Revision Drill: Tool Calling & LangChain Core](revision-drills/day-49-tool-calling-and-langchain-core/README.md) | Lab (60–75 min): measure retries and duplicate writes, then build the loop |  |

## Cross-day indexes

- [Interview question bank](interview-bank.md)
- [Decision records from the daily packs](decision-log.md)
