"""memory_lab.py - Day 43 revision lab (runs fully local on Ollama).

Part A: tenant-isolated retrieval in ONE Qdrant collection (payload partitioning),
        with a retriever that cannot be called without a tenant, plus a leak test.
Part B: agent long-term memory with the LangGraph store: namespaces per
        (tenant, user), semantic recall, and the "stale fact" problem.

Run:  python memory_lab.py
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

import ollama
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.runtime import Runtime
from langgraph.store.memory import InMemoryStore
from qdrant_client import QdrantClient, models

EMBED_MODEL = "nomic-embed-text"   # 768-d, ~270 MB, fine on CPU or the 3050
CHAT_MODEL = "llama3.2:3b"
DIM = 768
COLLECTION = "kb"


def embed(texts: list[str], kind: str = "document") -> list[list[float]]:
    # nomic-embed-text is trained with task prefixes; skipping them costs recall.
    prefix = "search_document: " if kind == "document" else "search_query: "
    return ollama.embed(model=EMBED_MODEL, input=[prefix + t for t in texts]).embeddings


# --------------------------------------------------------------------------- A
DOCS = [
    ("acme", "Acme travel policy: economy class for flights under 6 hours; hotel cap INR 9,000/night."),
    ("acme", "Acme laptop refresh happens every 36 months via the IT portal."),
    ("globex", "Globex travel policy: business class allowed above 4 hours; hotel cap INR 15,000/night."),
    ("globex", "Globex CONFIDENTIAL: Project Falcon acquisition closes in Q1; do not disclose."),
]


class TenantScopedRetriever:
    """The only door to the collection. Callers cannot omit or override the tenant."""

    def __init__(self, client: QdrantClient, collection: str):
        self.client, self.collection = client, collection

    def search(self, tenant_id: str, query: str, k: int = 2):
        if not tenant_id:
            raise PermissionError("tenant_id is mandatory")
        flt = models.Filter(must=[models.FieldCondition(
            key="tenant_id", match=models.MatchValue(value=tenant_id))])
        return self.client.query_points(
            self.collection, query=embed([query], "query")[0],
            query_filter=flt, limit=k, with_payload=True).points


def part_a() -> None:
    print("=== Part A: one collection, many tenants ===")
    client = QdrantClient(path="./qdrant_data")          # embedded local mode, no server
    if client.collection_exists(COLLECTION):
        client.delete_collection(COLLECTION)
    client.create_collection(
        COLLECTION,
        vectors_config=models.VectorParams(size=DIM, distance=models.Distance.COSINE),
        # Multitenant recipe from the Qdrant docs: skip the global graph (m=0),
        # build per-tenant graphs (payload_m). Local mode ignores it; a server honours it.
        hnsw_config=models.HnswConfigDiff(payload_m=16, m=0),
    )
    client.create_payload_index(
        COLLECTION, "tenant_id",
        field_schema=models.KeywordIndexParams(type=models.KeywordIndexType.KEYWORD, is_tenant=True),
    )
    vecs = embed([text for _, text in DOCS])
    client.upsert(COLLECTION, points=[
        models.PointStruct(id=str(uuid.uuid4()), vector=v, payload={"tenant_id": t, "text": text})
        for (t, text), v in zip(DOCS, vecs)
    ])

    retr = TenantScopedRetriever(client, COLLECTION)
    q = "What is the hotel limit per night?"
    for tenant in ("acme", "globex"):
        top = retr.search(tenant, q, k=1)[0]
        print(f"[{tenant}] {top.score:.3f}  {top.payload['text'][:70]}")

    # Naive path: same query, no filter -> whichever tenant is closest wins.
    naive = client.query_points(COLLECTION, query=embed(["Project Falcon acquisition"], "query")[0], limit=1).points[0]
    print(f"[NO FILTER] acme user asking about Falcon gets -> tenant={naive.payload['tenant_id']}  <-- leak")

    # Leak test: every tenant fires every other tenant's documents verbatim as queries.
    leaks = 0
    for tenant in ("acme", "globex"):
        for owner, text in DOCS:
            if owner == tenant:
                continue
            hits = retr.search(tenant, text, k=3)
            leaks += sum(h.payload["tenant_id"] != tenant for h in hits)
    print(f"Leak test: {leaks} foreign chunks returned across {len(DOCS) * 2 - len(DOCS)} probes -> "
          f"{'PASS' if leaks == 0 else 'FAIL'}")
    client.close()


# --------------------------------------------------------------------------- B
@dataclass
class Ctx:
    tenant_id: str
    user_id: str


def build_store() -> InMemoryStore:
    # Swap for PostgresStore (langgraph-checkpoint-postgres) in production.
    return InMemoryStore(index={"dims": DIM, "embed": lambda xs: embed(list(xs)), "fields": ["text"]})


def answer(state: MessagesState, runtime: Runtime[Ctx]) -> dict:
    ns = ("memories", runtime.context.tenant_id, runtime.context.user_id)
    question = state["messages"][-1].content
    mems = runtime.store.search(ns, query=question, limit=3)
    recalled = "\n".join(f"- {m.value['text']} (score {m.score:.2f})" for m in mems) or "- (none)"
    print(f"  recalled for {runtime.context.user_id}:\n{recalled}")
    reply = ollama.chat(model=CHAT_MODEL, options={"temperature": 0}, messages=[
        {"role": "system", "content": "Answer in one sentence using ONLY these user memories. "
                                      "If they conflict, say so.\n" + recalled},
        {"role": "user", "content": question},
    ])
    return {"messages": [{"role": "assistant", "content": reply.message.content}]}


def part_b() -> None:
    print("\n=== Part B: long-term memory with the LangGraph store ===")
    store = build_store()
    rahul = ("memories", "acme", "rahul")
    priya = ("memories", "acme", "priya")

    # Append-only memory: every fact gets a fresh key.
    store.put(rahul, str(uuid.uuid4()), {"text": "Rahul lives in Bengaluru."})
    store.put(rahul, str(uuid.uuid4()), {"text": "Rahul prefers answers with a decision table."})
    store.put(rahul, str(uuid.uuid4()), {"text": "Rahul moved to Pune last month."})   # contradicts #1
    store.put(priya, str(uuid.uuid4()), {"text": "Priya is on the payments team."})

    graph = (StateGraph(MessagesState, context_schema=Ctx)
             .add_node("answer", answer)
             .add_edge(START, "answer").add_edge("answer", END)
             .compile(store=store))

    ask = lambda user, q: graph.invoke({"messages": [{"role": "user", "content": q}]},
                                       context=Ctx("acme", user))["messages"][-1].content
    print("Q (rahul): Where do I live?")
    print("  ->", ask("rahul", "Where do I live?"))
    print("Q (priya): Where does Rahul live?")
    print("  ->", ask("priya", "Where does Rahul live?"))

    # Fix the stale-fact problem: semantic slot keys turn append into upsert.
    # (A real system would have the LLM classify each fact into a slot at write time.)
    for item in store.search(rahul, limit=50):          # no query = list the namespace
        if any(w in item.value["text"] for w in ("lives in", "moved to")):
            store.delete(rahul, item.key)
    store.put(rahul, "profile.home_city", {"text": "Rahul lives in Pune (moved from Bengaluru)."})
    print("After slot upsert, Q (rahul): Where do I live?")
    print("  ->", ask("rahul", "Where do I live?"))
    print("Namespaces:", store.list_namespaces(prefix=("memories", "acme")))


if __name__ == "__main__":
    part_a()
    part_b()
