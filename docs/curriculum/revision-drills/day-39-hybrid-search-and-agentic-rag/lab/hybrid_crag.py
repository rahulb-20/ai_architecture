"""Day 39 drill: hybrid retrieval (BM25 + dense, RRF) -> cross-encoder rerank -> LangGraph corrective loop.
Runs on CPU in ~1-2 GB RAM. Optional: Ollama for the final answer (falls back to printing context).
"""
from __future__ import annotations

import re
import time
from typing import TypedDict

import numpy as np
import torch
from rank_bm25 import BM25Okapi
from sentence_transformers import CrossEncoder, SentenceTransformer
from langgraph.graph import END, START, StateGraph

# ---------------------------------------------------------------- corpus
DOCS = [
    {"id": "HR-4.1", "dept": "hr", "text": "Employees accrue 1.75 days of paid leave per month; unused leave above 30 days lapses on 31 March."},
    {"id": "HR-4.3", "dept": "hr", "text": "Parental leave is 26 weeks for the primary caregiver and 4 weeks for the secondary caregiver."},
    {"id": "HR-6.2", "dept": "hr", "text": "Remote work requires manager approval; a maximum of 3 remote days per week applies to client-facing roles."},
    {"id": "IT-2.7", "dept": "it", "text": "Error VPN-809 means the device certificate has expired; re-enrol the laptop through the Company Portal."},
    {"id": "IT-2.9", "dept": "it", "text": "Error VPN-812 indicates MFA push timeout; approve the prompt within 60 seconds or use a TOTP code."},
    {"id": "IT-3.1", "dept": "it", "text": "Personal USB storage is blocked on corporate laptops; request an exception via ticket category SEC-USB."},
    {"id": "IT-5.4", "dept": "it", "text": "Laptops are refreshed every 4 years; engineers with GPU workloads may request an RTX-class workstation."},
    {"id": "FIN-1.2", "dept": "finance", "text": "Travel claims must be filed within 15 days of return with itemised receipts above INR 500."},
    {"id": "FIN-1.5", "dept": "finance", "text": "International per diem for the US is USD 75 per day, covering meals and incidentals."},
    {"id": "FIN-3.3", "dept": "finance", "text": "Purchase orders above INR 5 lakh need CFO approval and three competing vendor quotes."},
    {"id": "SEC-1.1", "dept": "security", "text": "Customer PII must never be pasted into public AI chatbots; use the approved internal assistant only."},
    {"id": "SEC-2.4", "dept": "security", "text": "Production secrets are stored in the vault and rotated every 90 days; never commit them to git."},
]

def tok(s: str) -> list[str]:
    # keep codes like vpn-809 and 1.75 intact: exact-match tokens are where BM25 beats dense
    return re.findall(r"[a-z0-9]+(?:[-.][a-z0-9]+)*", s.lower())

print("Loading models (first run downloads ~200 MB)...")
EMB = SentenceTransformer("BAAI/bge-small-en-v1.5", device="cpu")
RERANK = CrossEncoder("cross-encoder/ms-marco-MiniLM-L6-v2", device="cpu",
                      activation_fn=torch.nn.Sigmoid())  # explicit: scores in 0..1 so a threshold means something
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "  # bge v1.5 query instruction

BM25 = BM25Okapi([tok(d["text"]) for d in DOCS])
DOC_VECS = EMB.encode([d["text"] for d in DOCS], normalize_embeddings=True)

# ---------------------------------------------------------------- retrieval primitives
def bm25_rank(q: str, allowed: list[int], k: int) -> list[int]:
    s = BM25.get_scores(tok(q))
    return sorted(allowed, key=lambda i: -s[i])[:k]

def dense_rank(q: str, allowed: list[int], k: int) -> list[int]:
    qv = EMB.encode(QUERY_PREFIX + q, normalize_embeddings=True)
    sims = DOC_VECS @ qv
    return sorted(allowed, key=lambda i: -sims[i])[:k]

def rrf(rankings: list[list[int]], k: int = 60) -> list[int]:
    """Reciprocal Rank Fusion: score(d) = sum 1/(k + rank). Rank-based, so no score normalisation needed."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for r, i in enumerate(ranking, start=1):
            scores[i] = scores.get(i, 0.0) + 1.0 / (k + r)
    return sorted(scores, key=lambda i: -scores[i])

def hybrid(q: str, dept: str | None, k_each: int = 6) -> list[int]:
    allowed = [i for i, d in enumerate(DOCS) if dept is None or d["dept"] == dept]  # pre-filter, like a metadata filter
    return rrf([bm25_rank(q, allowed, k_each), dense_rank(q, allowed, k_each)])

def rerank(q: str, cand: list[int], top_n: int = 3) -> list[tuple[int, float]]:
    scores = RERANK.predict([(q, DOCS[i]["text"]) for i in cand])
    order = np.argsort(-scores)[:top_n]
    return [(cand[j], float(scores[j])) for j in order]

# ---------------------------------------------------------------- LangGraph corrective loop
class S(TypedDict, total=False):
    question: str
    query: str
    dept: str | None
    hits: list[tuple[int, float]]
    attempts: int
    answer: str

THRESHOLD = 0.30   # tune on a golden set, never by eye
MAX_ATTEMPTS = 2

def retrieve(state: S) -> S:
    q = state.get("query") or state["question"]
    cand = hybrid(q, state.get("dept"))
    return {"query": q, "hits": rerank(q, cand), "attempts": state.get("attempts", 0) + 1}

def route(state: S) -> str:
    best = state["hits"][0][1] if state["hits"] else 0.0
    if best >= THRESHOLD:
        return "generate"
    return "rewrite" if state["attempts"] < MAX_ATTEMPTS else "abstain"

def rewrite(state: S) -> S:
    # cheap deterministic rewrite: drop the metadata filter (maybe the router guessed wrong) and strip filler words.
    # Stretch goal: replace with an LLM rewrite via Ollama.
    stop = {"what", "is", "the", "how", "do", "i", "my", "a", "an", "for", "of", "to", "can", "does"}
    q = " ".join(w for w in tok(state["question"]) if w not in stop)
    return {"query": q, "dept": None}

def generate(state: S) -> S:
    ctx = "\n".join(f"[{DOCS[i]['id']}] {DOCS[i]['text']}" for i, _ in state["hits"])
    try:
        from langchain_ollama import ChatOllama
        llm = ChatOllama(model="qwen3:1.7b", temperature=0)
        msg = llm.invoke("Answer ONLY from the context and cite ids in [brackets]. "
                         "If the context does not contain the answer, say you don't know.\n\n"
                         f"Context:\n{ctx}\n\nQuestion: {state['question']}")
        return {"answer": msg.content}
    except Exception as e:  # no Ollama running -> still show the grounded context
        return {"answer": f"(LLM unavailable: {type(e).__name__}) Top context:\n{ctx}"}

def abstain(state: S) -> S:
    return {"answer": "I couldn't find this in the policy corpus. Raise a ticket with HR/IT."}

g = StateGraph(S)
for name, fn in [("retrieve", retrieve), ("rewrite", rewrite), ("generate", generate), ("abstain", abstain)]:
    g.add_node(name, fn)
g.add_edge(START, "retrieve")
g.add_conditional_edges("retrieve", route, {"generate": "generate", "rewrite": "rewrite", "abstain": "abstain"})
g.add_edge("rewrite", "retrieve")
g.add_edge("generate", END)
g.add_edge("abstain", END)
app = g.compile()

# ---------------------------------------------------------------- demo
if __name__ == "__main__":
    tests = [
        ("What does error VPN-812 mean?", "it"),            # exact code: BM25 carries it
        ("How long can new mothers take off work?", "hr"),   # paraphrase: dense carries it
        ("What is the US daily allowance?", "hr"),           # wrong filter -> rewrite drops it
        ("What is the canteen menu on Friday?", None),       # not in corpus -> abstain
    ]
    for q, dept in tests:
        print(f"\nQ: {q}  (filter={dept})")
        print(f"  bm25 : {[DOCS[i]['id'] for i in bm25_rank(q, list(range(len(DOCS))), 3)]}")
        print(f"  dense: {[DOCS[i]['id'] for i in dense_rank(q, list(range(len(DOCS))), 3)]}")
        t = time.perf_counter()
        out = app.invoke({"question": q, "dept": dept})
        ms = (time.perf_counter() - t) * 1000
        print(f"  reranked: {[(DOCS[i]['id'], round(s, 3)) for i, s in out['hits']]}  attempts={out['attempts']}  {ms:.0f} ms")
        print("  ->", out["answer"].replace("\n", "\n     "))
