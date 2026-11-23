# Day 15 — LangChain Core

> Part of the [GenAI Architect study path](../../README.md) · Agentic Systems · [Original interactive pack](https://claude.ai/artifact/TeJBU6XDsvmzTSWpq4TUzT) · [Offline HTML](../../archive/html/day-15.html)


Week 3 · Agentic systems · Day 15 of 30

# LangChain core: LCEL, runnables, retrievers and tools

What the LangChain 1.x stack actually is in late 2026, which parts earn a place in a production design, and which parts you should keep out of the critical path.

langchain 1.4.7 · langchain-core 1.7.075 min labRuns on Ollama, no Docker

## Why this matters for the JD

The JD lists **"agentic frameworks (LangChain, LangGraph, MCP, tool calling, memory, planning, multi-agent, guardrails)"** as a must-have and **"multi-provider LLM integration"** as preferred. In a review you will be asked two things: can your team build on LangChain fast, and can you stop it from becoming an unmaintainable abstraction layer? Today is about answering both.

## Concept brief

### The 1.x package map (this changed a lot in the last year)

- **`langchain-core`** is the stable contract: the `Runnable` protocol, messages, prompt templates, output parsers, the `BaseChatModel`/`Embeddings`/`VectorStore`/`BaseRetriever` interfaces, `@tool`, callbacks. LCEL lives here. This is the part with the strongest compatibility promise.
- **`langchain`** (1.x) is now slim. Its headline API is `create_agent`, a tool-calling agent loop that compiles to a LangGraph graph, plus **middleware** hooks (model/tool retry, model fallback, call limits, summarization, PII redaction, human-in-the-loop, LLM tool selection). `langchain[mcp]` now pulls in MCP support directly; the old `langchain-mcp-adapters` repo was archived in September 2026.
- **`langchain-classic`** holds the pre-1.0 chains (`RetrievalQA`, `ConversationalRetrievalChain`, the old `AgentExecutor`). Treat imports from it as tech debt in new designs.
- **Partner packages** (`langchain-openai`, `-anthropic`, `-ollama`, `-ibm` for watsonx, `-aws`, `-google-genai`…) version separately. `langchain-community` (0.4.x) is the long tail with weaker guarantees.
- **LangGraph** is the runtime underneath agents: state, checkpointing, interrupts, streaming. Tomorrow+1 (Day 17) goes deep on it.

### The Runnable protocol is the real abstraction

Every component (prompt, model, parser, retriever, tool, your own function) implements the same interface: `invoke / ainvoke`, `batch / abatch`, `stream / astream`, `astream_events`. Each call takes a `RunnableConfig` (callbacks, tags, metadata, `max_concurrency`, `run_name`, `configurable`), and that config propagates through the whole tree. LCEL's `|` builds a `RunnableSequence`; a dict literal becomes a `RunnableParallel`. Wrappers add NFR behaviour without touching business code: `.with_retry()`, `.with_fallbacks()`, `.with_config()`, `.with_structured_output()`, `.bind_tools()`, `configurable_alternatives()`.

![LCEL RAG pipeline on top, LangChain 1.x package layers below](diagrams/fig-1.svg)

Top: the chain you build in the lab. Bottom: the dependency layers. Design rule: business logic depends on `langchain-core` interfaces; everything else is replaceable.

### Retrievers and tools are just Runnables with a shape

A retriever is `str → list[Document]`. That uniformity is why you can swap FAISS for Azure AI Search or Milvus by changing one line, and why an `EnsembleRetriever` or a reranking step slots in as a pipe segment. A tool is a Runnable with a name, a description and a JSON schema (built from type hints and the docstring by `@tool`). The model never runs a tool: it emits `tool_calls`; your loop (or `create_agent`) executes them and feeds back `ToolMessage`s. The tool docstring is a prompt, so it gets versioned and reviewed like one.

### What to use, what to avoid

- **Use:** core interfaces, chat model wrappers (one place for provider differences), `with_structured_output`, `@tool`, retrievers, callbacks into your tracing stack, LCEL for short linear pipelines (≤ ~6 steps, no loops).
- **Use with care:** `create_agent` + middleware. It is good, but pin versions and wrap it behind your own service interface.
- **Avoid:** legacy chains from `langchain-classic`; deeply nested LCEL with branching and loops (move that to LangGraph, where state is explicit); `RunnableWithMessageHistory` for new agents (LangGraph checkpointers replace it); depending on `langchain-community` classes in the hot path without pinning.

**IBM mapping:** `langchain-ibm` gives you `ChatWatsonx` and `WatsonxEmbeddings`, so Granite on watsonx.ai drops into the same chain. watsonx Orchestrate and watsonx.ai's agent tooling sit at the layer above; LangChain/LangGraph agents can be deployed behind them. On AWS/Azure/GCP the equivalents are `ChatBedrockConverse`, `AzureChatOpenAI` and `ChatGoogleGenerativeAI`/Vertex.

## Trade-offs & decision table

| Option | Pick when | Failure modes | Cost / latency notes |
| --- | --- | --- | --- |
| Raw provider SDK | One provider, simple call, latency-critical path, strict dependency policy | Re-implementing retry, streaming, tool loops, tracing per provider; lock-in | Lowest overhead; most engineering time |
| langchain-core + LCEL | Linear RAG/extraction pipelines, multi-provider, need batch/stream/async for free | Opaque stack traces; nested lambdas become hard to read; dict-shape bugs at runtime | Framework overhead is milliseconds, negligible next to model latency |
| create\_agent + middleware | Standard tool-calling assistant with retries, limits, HITL, PII redaction | Hidden prompts/loops if not traced; behaviour shifts on minor upgrades | Unbounded loops cost tokens: set ModelCallLimit / ToolCallLimit |
| LangGraph directly | Branching, cycles, long-running, human approval, durable state | More code; graph design skills needed | Checkpointer storage and replay cost; best control of loops |
| LiteLLM / AI gateway + thin code | Platform team owns routing, quotas, keys across many apps | Duplicate retry/fallback at two layers | Central caching and budgets; extra network hop |
| Legacy chains (classic) | Only to keep an existing system running during migration | Deprecated paths, prompt logic hidden in library code | Hard to observe and tune |

**Review trap:** retries in three places (SDK, LangChain `with_retry`, gateway) multiply. A 3×3×3 stack turns one outage into 27 calls per request. Decide which layer owns retries and which owns fallbacks, and write it down.

## Hands-on lab · 75 min

**Goal:** build one RAG chain with LCEL, run it through `invoke`, `batch` and `astream`, add retry and fallback, get typed structured output, then hand the same retriever to a tool-calling agent built with `create_agent`. A custom callback prints per-stage latency. Everything runs natively on Windows with Ollama.

### Setup (PowerShell)

```text
# PowerShell — one-time setup (no Docker/WSL needed)
winget install --id Ollama.Ollama -e        # skip if Ollama is already installed
ollama pull qwen2.5:3b                      # ~1.9 GB, good tool calling for its size
ollama pull nomic-embed-text

mkdir day15; cd day15
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install "langchain>=1.4,<2" "langchain-core>=1.7,<2" "langchain-ollama>=1.1,<2" numpy
# save the script below as day15_lcel.py, then:
python day15_lcel.py --fake    # plumbing check, no models needed
python day15_lcel.py           # real run against Ollama
```

If tool calling is flaky with the 3B model, try `llama3.2:3b`, or `qwen3:4b` if VRAM allows (it will spill to CPU and slow down on a 6 GB card).

### day15\_lcel.py

`lab/day15_lcel.py`

```python
"""Day 15 - LangChain core (LCEL, runnables, retrievers, tools) on a laptop.

Run:  python day15_lcel.py            (uses local Ollama models)
      python day15_lcel.py --fake     (no Ollama; fake LLM + embeddings to check the plumbing)
"""
from __future__ import annotations

import asyncio
import sys
import time
from operator import itemgetter

from pydantic import BaseModel, Field
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import (
    RunnableConfig,
    RunnableLambda,
    RunnableParallel,
    RunnablePassthrough,
)
from langchain_core.tools import tool
from langchain_core.vectorstores import InMemoryVectorStore

FAKE = "--fake" in sys.argv
CHAT_MODEL = "qwen2.5:3b"          # ~1.9 GB, supports tool calling; fits a 6 GB RTX 3050
EMBED_MODEL = "nomic-embed-text"   # 768-dim, ~274 MB

# ---------------------------------------------------------------- 1. models
if FAKE:
    from langchain_core.embeddings import DeterministicFakeEmbedding
    from langchain_core.language_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage

    embeddings = DeterministicFakeEmbedding(size=256)

    def make_llm():
        return GenericFakeChatModel(
            messages=iter([AIMessage(content="Fake answer [doc-1].")] * 50)
        )
else:
    from langchain_ollama import ChatOllama, OllamaEmbeddings

    embeddings = OllamaEmbeddings(model=EMBED_MODEL)

    def make_llm(**kw):
        return ChatOllama(model=CHAT_MODEL, temperature=0, num_ctx=4096, **kw)

llm = make_llm()

# ---------------------------------------------------------------- 2. corpus + retriever
POLICIES = [
    ("doc-1", "hr", "Employees may work remotely up to 3 days per week with manager approval."),
    ("doc-2", "hr", "Annual leave is 24 working days; up to 8 days can be carried forward."),
    ("doc-3", "it", "Laptops must use BitLocker disk encryption and auto-lock after 5 minutes."),
    ("doc-4", "it", "Production secrets live in Vault; never commit them to Git."),
    ("doc-5", "finance", "Travel claims above INR 50,000 need VP approval within 30 days."),
    ("doc-6", "it", "GenAI tools may only be used with Confidential data via the approved gateway."),
]
docs = [Document(page_content=t, metadata={"id": i, "dept": d}) for i, d, t in POLICIES]
store = InMemoryVectorStore.from_documents(docs, embeddings)

# A retriever IS a Runnable: str -> list[Document]
retriever = store.as_retriever(search_kwargs={"k": 3})

def format_docs(ds: list[Document]) -> str:
    return "\n".join(f"[{d.metadata['id']}] {d.page_content}" for d in ds)

# ---------------------------------------------------------------- 3. LCEL RAG chain
prompt = ChatPromptTemplate.from_messages([
    ("system",
     "Answer ONLY from the context. Cite ids like [doc-1]. "
     "If the context is insufficient, say 'I don't know'.\n\nContext:\n{context}"),
    ("human", "{question}"),
])

# Return answer AND sources (architects want provenance, not just text)
rag_chain = (
    RunnableParallel(docs=retriever, question=RunnablePassthrough())
    | RunnablePassthrough.assign(context=itemgetter("docs") | RunnableLambda(format_docs))
    | RunnablePassthrough.assign(answer=prompt | llm | StrOutputParser())
    | RunnableLambda(lambda x: {"answer": x["answer"],
                                "sources": [d.metadata["id"] for d in x["docs"]]})
).with_config(run_name="policy_rag")

# ---------------------------------------------------------------- 4. latency callback
class StageTimer(BaseCallbackHandler):
    """Poor-man's tracing: wall time per LLM / retriever call (swap for Langfuse on Day 27)."""

    def __init__(self):
        self.t0: dict = {}
        self.rows: list[tuple[str, float]] = []

    def on_retriever_start(self, serialized, query, *, run_id, **kw):
        self.t0[run_id] = time.perf_counter()

    def on_retriever_end(self, documents, *, run_id, **kw):
        self.rows.append(("retriever", time.perf_counter() - self.t0.pop(run_id)))

    def on_chat_model_start(self, serialized, messages, *, run_id, **kw):
        self.t0[run_id] = time.perf_counter()

    def on_llm_end(self, response, *, run_id, **kw):
        if run_id in self.t0:
            self.rows.append(("chat_model", time.perf_counter() - self.t0.pop(run_id)))

# ---------------------------------------------------------------- 5. structured output
class Answer(BaseModel):
    answer: str = Field(description="Short answer grounded in context")
    citations: list[str] = Field(description="Doc ids used, e.g. ['doc-1']")
    confident: bool = Field(description="False if the context did not contain the answer")

# ---------------------------------------------------------------- 6. tools + agent
@tool
def leave_balance(employee_id: str) -> str:
    """Return remaining annual-leave days for an employee id like 'E123'."""
    return {"E123": "11 days", "E456": "3 days"}.get(employee_id, "unknown employee")

@tool
def search_policies(query: str) -> str:
    """Search company HR/IT/finance policies and return the most relevant passages."""
    return format_docs(retriever.invoke(query))

async def main():
    timer = StageTimer()
    cfg: RunnableConfig = {"callbacks": [timer], "tags": ["day15"], "metadata": {"user": "rahul"}}

    print("== 1. invoke")
    print(rag_chain.invoke("How many days can I work from home?", config=cfg))

    print("\n== 2. batch (max_concurrency=2)")
    qs = ["What disk encryption is required?", "Who approves big travel claims?",
          "What is the cafeteria menu?"]
    for q, r in zip(qs, rag_chain.batch(qs, config={**cfg, "max_concurrency": 2})):
        print(f"- {q} -> {r['answer'][:90]} {r['sources']}")

    print("\n== 3. astream (token streaming of the final answer only)")
    stream_chain = (
        {"context": retriever | format_docs, "question": RunnablePassthrough()}
        | prompt | llm | StrOutputParser()
    )
    async for chunk in stream_chain.astream("Where do production secrets live?"):
        print(chunk, end="", flush=True)
    print()

    print("\n== 4. retry + fallback (primary model name is deliberately wrong)")
    if FAKE:
        broken = RunnableLambda(lambda _: (_ for _ in ()).throw(RuntimeError("model down")))
    else:
        broken = ChatOllama(model="does-not-exist:1b")  # Ollama returns 404 -> exception
    resilient = broken.with_retry(stop_after_attempt=2).with_fallbacks([llm])
    print((prompt | resilient | StrOutputParser()).invoke(
        {"context": format_docs(docs[:2]), "question": "How much leave carries forward?"}))

    print("\n== 5. structured output")
    if not FAKE:
        structured = (
            {"context": retriever | format_docs, "question": RunnablePassthrough()}
            | prompt | llm.with_structured_output(Answer)
        )
        print(structured.invoke("Can I paste Confidential data into ChatGPT?"))
    else:
        print("(skipped in --fake mode)")

    print("\n== 6. tool-calling agent (langchain 1.x create_agent -> LangGraph under the hood)")
    if not FAKE:
        from langchain.agents import create_agent
        agent = create_agent(
            model=llm,
            tools=[leave_balance, search_policies],
            system_prompt="You are an HR assistant. Use tools; cite policy ids.",
        )
        out = agent.invoke({"messages": [{"role": "user", "content":
              "I'm E123. How many leave days do I have left, and how many can I carry forward?"}]})
        for m in out["messages"]:
            calls = getattr(m, "tool_calls", None)
            print(f"[{m.type}] {calls if calls else str(m.content)[:160]}")
    else:
        print("tool schema:", leave_balance.args_schema.model_json_schema()["properties"])

    print("\n== 7. stage timings")
    for name, secs in timer.rows:
        print(f"{name:<11} {secs*1000:8.1f} ms")

if __name__ == "__main__":
    asyncio.run(main())
```

### What to do, in order

1. **Fake run (5 min):** `--fake` swaps in `GenericFakeChatModel` and `DeterministicFakeEmbedding`. This is how you unit-test chains in CI without a model.
2. **Real run (15 min):** check that the cafeteria question returns "I don't know", that sources come back with every answer, and that the fallback in step 4 kicks in after the bad model fails.
3. **Read the agent trace (15 min):** section 6 prints every message. Confirm the model called `leave_balance` and `search_policies` (possibly in parallel), and that the final answer cites `doc-2`.
4. **Measure (15 min):** compare stage timings. Retrieval should be single-digit ms in memory; the chat model dominates. Change `max_concurrency` from 2 to 1 and note the batch wall time.
5. **Write-up (15 min):** where would you draw the line between LCEL and LangGraph for your watsonx compliance bot?

### Expected output (shape)

```text
== 1. invoke
{'answer': 'You can work remotely up to 3 days per week with manager approval [doc-1].', 'sources': ['doc-1', 'doc-2', 'doc-6']}

== 2. batch (max_concurrency=2)
- What disk encryption is required? -> Laptops must use BitLocker ... [doc-3] ['doc-3', 'doc-4', 'doc-6']
- Who approves big travel claims? -> Travel claims above INR 50,000 need VP approval [doc-5] [...]
- What is the cafeteria menu? -> I don't know. [...]

== 3. astream (token streaming of the final answer only)
Production secrets live in Vault and must never be committed to Git [doc-4].

== 4. retry + fallback (primary model name is deliberately wrong)
Up to 8 days of annual leave can be carried forward [doc-2].

== 5. structured output
answer='No. Confidential data may only be used via the approved gateway.' citations=['doc-6'] confident=True

== 6. tool-calling agent (langchain 1.x create_agent -> LangGraph under the hood)
[human] I'm E123. How many leave days do I have left, ...
[ai] [{'name': 'leave_balance', 'args': {'employee_id': 'E123'}, ...}, {'name': 'search_policies', ...}]
[tool] 11 days
[tool] [doc-2] Annual leave is 24 working days; ...
[ai] You have 11 days left, and up to 8 days can be carried forward [doc-2].

== 7. stage timings
retriever       35.2 ms     # includes the Ollama embedding call
chat_model    2140.7 ms
...
```

Exact wording and timings depend on the model; the first call is slower while Ollama loads the model into VRAM.

### Stretch goals

1. **Middleware:** pass `middleware=[ModelCallLimitMiddleware(run_limit=4), ToolCallLimitMiddleware(run_limit=6), PIIMiddleware("email", strategy="redact")]` (from `langchain.agents.middleware`) to `create_agent`. Ask a question containing an email address and confirm it never reaches the model.
2. **Provider swap without code changes:** wrap the model with `configurable_alternatives(ConfigurableField(id="llm"), default_key="ollama", watsonx=ChatWatsonx(...))` from `langchain-ibm` (free watsonx.ai trial or any OpenAI-compatible endpoint) and select it per request with `config={"configurable": {"llm": "watsonx"}}`. Run both through the same three questions and compare.

## GitHub repos to study

[langchain-ai/langchain](https://github.com/langchain-ai/langchain)Monorepo. Start with the partner packages under `libs/partners/` (the Ollama one is small: see how `ChatOllama` maps tool calls and structured output onto a provider API), then the `langchain.agents` package for `create_agent` and the middleware classes.

[langchain-ai/langgraph](https://github.com/langchain-ai/langgraph)The runtime under `create_agent`. Skim the prebuilt `ToolNode` to see how tool calls, errors and parallel calls are executed.

[langchain-ai/deepagents](https://github.com/langchain-ai/deepagents)A full agent harness built on `create_agent` + middleware (planning, filesystem, sub-agents). Good reference for how far middleware composition goes.

[langchain\_core/runnables/base.py](https://github.com/langchain-ai/langchain/blob/master/libs/core/langchain_core/runnables/base.py)One file, ~6.7k lines, worth an hour: how `|` builds a `RunnableSequence`, how `batch` fans out under `max_concurrency`, and how config and callbacks propagate to children.

**Archived in 2026:** `langchain-ai/langchain-mcp-adapters` (Sep 2026, use `langchain[mcp]`) and `langchain-ai/open_deep_research` (Aug 2026). Older tutorials still point to both.

## Read / watch

- [LangChain v1 migration guide](https://docs.langchain.com/oss/python/migrate/langchain-v1) — what moved to `langchain-classic`, the `create_agent` replacement for old agents, content blocks.
- [Middleware overview](https://docs.langchain.com/oss/python/langchain/middleware) — the built-in middleware list and the hook points (before/after model, wrap model/tool calls).
- [langchain-core runnables reference](https://reference.langchain.com/python/langchain-core/runnables) — the full Runnable API: `RunnableParallel`, `RunnablePassthrough.assign`, fallbacks, retry, configurable fields.
- [ChatOllama integration](https://docs.langchain.com/oss/python/integrations/chat/ollama) — tool calling and structured output support on local models.

## Interview questions

**Your team wants to standardise on LangChain. What's your position?**

Yes to `langchain-core` interfaces and partner model wrappers as the internal SDK: one Runnable contract, batch/stream/async and tracing for free, provider swaps in config. LangGraph for anything with loops, state or approvals. No to legacy chains, and no direct framework imports in domain code: wrap them behind our own ports (Retriever, Generator, Tool registry) so an upgrade or exit touches one module. Pin versions, track release notes in a monthly upgrade window, and run the eval suite on every bump.

**When does LCEL stop being the right tool?**

When you need cycles (retry-until-grounded, agent loops), branching on accumulated state, persistence across turns, human interrupts, or partial resumption after failure. LCEL composes a DAG of stateless steps; once you start passing dicts around to simulate state or nest `RunnableBranch` inside lambdas, move to LangGraph, where state, edges and checkpoints are explicit and observable.

**How do you make a tool-calling agent safe and cost-bounded?**

Least-privilege tools with narrow typed schemas and server-side authorisation (the tool checks the caller's identity, never trusts model-supplied IDs); call limits per run and per thread; timeouts and retries on tools only for idempotent operations; human approval for side-effecting tools; PII redaction before the model; tool results treated as untrusted input (prompt-injection channel); full tracing of every tool call with arguments for audit.

**How do you test LangChain pipelines in CI?**

Three layers. Unit tests with fake chat models and fake embeddings to check wiring, prompt variables and output shapes deterministically. Contract tests per tool schema and structured-output model. Then an eval stage on a golden set against the real model (faithfulness, correctness, tool-call accuracy, latency, cost) that gates the merge. Prompts and tool descriptions are versioned artifacts, so a change to a docstring triggers the eval gate too.

**Explain fallbacks vs retries, and where each belongs.**

Retry repeats the same call for transient faults (timeouts, 429, 5xx) with backoff and jitter, and only for idempotent work. Fallback switches to a different model, region or provider when the primary is down or over quota, accepting a quality or cost change. Put provider-level retry and cross-region failover in one layer (usually the AI gateway); keep app-level fallbacks for semantic reasons (structured-output parse failure → stronger model). Never stack retries at every layer.

## Architect's lens · ADR-015

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

## Lab files in this folder

- [`lab/day15_lcel.py`](lab/day15_lcel.py)
