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
