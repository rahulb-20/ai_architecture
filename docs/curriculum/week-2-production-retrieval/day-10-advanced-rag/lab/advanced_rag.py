"""
Day 10 lab: which query-side RAG technique fixes which failure?
Baseline dense vs multi-query+RRF vs HyDE vs self-query (metadata filter) vs corrective (grade -> rewrite -> abstain).
Runs fully local on Ollama: qwen3:4b (~2.5 GB, fits a 6 GB GPU) + nomic-embed-text.
"""
import argparse, statistics, time
from typing import Literal, Optional

import numpy as np
import ollama
from pydantic import BaseModel

LLM, EMB, K = "qwen3:4b", "nomic-embed-text", 3

# --- 1. A small policy KB with metadata and deliberate traps -----------------------
# Traps: vocabulary mismatch (wifi vs broadband), versioned policies (2023 vs 2025),
# colloquial phrasing (left my laptop in a cab), and one out-of-scope question.
DOCS = [
 {"id": "D01", "dept": "travel",     "year": 2023, "text": "Travel policy 2023: economy class for flights under 6 hours; business class above 6 hours with VP approval. Domestic per diem in India is INR 3,500 per day."},
 {"id": "D02", "dept": "travel",     "year": 2025, "text": "Travel policy 2025 revision: business class only for flights longer than 8 hours. Domestic per diem in India is INR 4,500 per day. Hotels must be booked through Concur."},
 {"id": "D03", "dept": "hr",         "year": 2024, "text": "Remote-work connectivity allowance: employees on hybrid contracts may claim up to INR 1,500 per month towards residential broadband by uploading the ISP invoice."},
 {"id": "D04", "dept": "hr",         "year": 2025, "text": "Parental leave: primary caregivers receive 26 weeks of paid leave; secondary caregivers receive 4 weeks within the first year."},
 {"id": "D05", "dept": "security",   "year": 2025, "text": "Lost or stolen endpoint devices must be reported to the Security Operations Centre within 4 hours through ServiceNow so the device can be remotely wiped."},
 {"id": "D06", "dept": "security",   "year": 2024, "text": "Credential standard: passphrases of at least 14 characters, MFA is mandatory, and periodic rotation is not forced unless a compromise is suspected."},
 {"id": "D07", "dept": "it",         "year": 2025, "text": "Generative AI acceptable use: only approved tools (watsonx.ai, the internal Copilot) may process client material. Client data must never be entered into public chatbots. Prompts are logged for 90 days."},
 {"id": "D08", "dept": "finance",    "year": 2025, "text": "Expense claims must be filed within 30 days of the transaction. Receipts are required above INR 500. Late claims need CFO approval."},
 {"id": "D09", "dept": "hr",         "year": 2025, "text": "Notice period: 60 days for band L5 and above, 30 days below L5. Garden leave is at the manager's discretion."},
 {"id": "D10", "dept": "it",         "year": 2024, "text": "Remote access: the Zscaler client is required when off the corporate network; split tunnelling is disabled for client projects."},
 {"id": "D11", "dept": "facilities", "year": 2025, "text": "Hot-desking: book a desk in the workplace app at least 24 hours ahead; lockers are released every day at 8 pm."},
 {"id": "D12", "dept": "security",   "year": 2025, "text": "Data classification has four levels: Public, Internal, Confidential and Restricted. Restricted data may not leave the India region, in line with the DPDP Act."},
 {"id": "D13", "dept": "hr",         "year": 2023, "text": "Learning budget: up to INR 50,000 per year for professional certifications, subject to a 12-month retention clause."},
 {"id": "D14", "dept": "finance",    "year": 2024, "text": "Corporate cards: personal use is prohibited and statements must be reconciled by the 5th of each month."},
]
TEXT = {d["id"]: d["text"] for d in DOCS}
GOLD = [  # (question, expected doc id or None for out-of-scope)
 ("Can I get reimbursed for my home wifi?", "D03"),
 ("What was the India per diem under the 2023 travel rules?", "D01"),
 ("Under the 2025 travel policy, when can I fly business class?", "D02"),
 ("I left my laptop in a cab, what should I do?", "D05"),
 ("Can I paste a client's contract into ChatGPT?", "D07"),
 ("How long do I have to submit a taxi receipt?", "D08"),
 ("Will the company pay for my AWS certification?", "D13"),
 ("What is on the cafeteria menu on Fridays?", None),
]

# --- 2. Model helpers ----------------------------------------------------------------
CALLS = {"llm": 0}

def embed(texts, kind):
    # nomic-embed-text is asymmetric: it expects task prefixes. Forgetting them costs recall.
    prefix = "search_document: " if kind == "doc" else "search_query: "
    r = ollama.embed(model=EMB, input=[prefix + t for t in texts])
    v = np.asarray(r.embeddings, dtype=np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)

def llm(prompt, schema=None, temperature=0.0):
    """One chat call. With a pydantic schema, Ollama constrains decoding to that JSON schema."""
    CALLS["llm"] += 1
    r = ollama.chat(
        model=LLM,
        messages=[{"role": "user", "content": prompt}],
        format=schema.model_json_schema() if schema else None,
        think=False,                       # qwen3 is a thinking model; we want short, cheap outputs
        options={"temperature": temperature, "num_ctx": 4096},
    )
    out = r.message.content
    return schema.model_validate_json(out) if schema else out.strip()

DOC_VECS = None

def dense(qvec, k=K, allowed=None):
    scores = DOC_VECS @ qvec
    hits = [(DOCS[i]["id"], float(scores[i])) for i in np.argsort(-scores)
            if allowed is None or DOCS[i]["id"] in allowed]
    return hits[:k]

def rrf(lists, k=60):
    fused = {}
    for lst in lists:
        for rank, (doc, _) in enumerate(lst, start=1):
            fused[doc] = fused.get(doc, 0.0) + 1.0 / (k + rank)
    return sorted(fused.items(), key=lambda x: x[1], reverse=True)

# --- 3. Strategies -------------------------------------------------------------------
def baseline(q):
    return dense(embed([q], "query")[0])

class Rewrites(BaseModel):
    queries: list[str]

def multi_query(q, n=3):
    rw = llm(f"Rewrite this employee question as {n} different search queries for a corporate "
             f"policy knowledge base. Use formal policy vocabulary and synonyms.\nQuestion: {q}",
             Rewrites).queries[:n]
    vecs = embed([q] + rw, "query")            # keep the original query in the fan-out
    return rrf([dense(v, k=10) for v in vecs])[:K]

def hyde(q):
    fake = llm(f"Write two sentences from a corporate policy document that would answer: {q}\n"
               f"Invent plausible details. Output only the excerpt.")
    v = embed([fake], "doc")[0]               # it is document-shaped text, so embed it as a document
    return dense(v)

class Filter(BaseModel):
    search_text: str
    dept: Optional[Literal["travel", "hr", "security", "it", "finance", "facilities"]] = None
    year: Optional[int] = None

def self_query(q):
    f = llm("Turn the question into a search query plus an optional metadata filter. "
            "Set 'year' ONLY if the question names a year explicitly. Set 'dept' only if obvious. "
            f"Departments: travel, hr, security, it, finance, facilities.\nQuestion: {q}", Filter)
    allowed = {d["id"] for d in DOCS
               if (f.dept is None or d["dept"] == f.dept) and (f.year is None or d["year"] == f.year)}
    # Relevance filter, not a security filter: fail OPEN if it matches nothing.
    # (An ACL filter must fail CLOSED. Never let an LLM write your ACL filter.)
    return dense(embed([f.search_text], "query")[0], allowed=allowed or None)

class Grade(BaseModel):
    relevant: bool

def corrective(q, retries=1):
    """CRAG-style: retrieve -> grade each chunk -> rewrite and retry -> abstain (empty list)."""
    query = q
    for _ in range(retries + 1):
        hits = baseline(query)
        kept = [(d, s) for d, s in hits
                if llm(f"Does the passage contain information that answers the question? "
                       f"Answer strictly.\nQuestion: {q}\nPassage: {TEXT[d]}", Grade).relevant]
        if kept:
            return kept
        query = llm("A search for this question found nothing relevant. Rewrite it as one keyword-rich "
                    f"search query for a corporate policy knowledge base. Output only the query.\nQuestion: {q}")
    return []

STRATEGIES = {"baseline": baseline, "multi_query": multi_query, "hyde": hyde,
              "self_query": self_query, "corrective": corrective}

# --- 4. Evaluate ---------------------------------------------------------------------
def main():
    global DOC_VECS
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=list(STRATEGIES))
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    DOC_VECS = embed([d["text"] for d in DOCS], "doc")
    llm("Say OK.")                                 # warm up: load the model into VRAM
    CALLS["llm"] = 0
    answerable = [g for g in GOLD if g[1]]
    print(f"docs={len(DOCS)} questions={len(GOLD)} (answerable={len(answerable)}) llm={LLM} emb={EMB}\n")
    print(f"{'strategy':<12}{'hit@3':>7}{'MRR':>7}{'p50 s':>8}{'LLM calls':>11}  abstained on OOS?")
    for name in args.only:
        fn = STRATEGIES[name]
        c0, hits, rr, lat, oos = CALLS["llm"], 0, 0.0, [], None
        for q, exp in GOLD:
            t0 = time.perf_counter(); res = fn(q); lat.append(time.perf_counter() - t0)
            ids = [d for d, _ in res]
            if exp is None:
                oos = len(ids) == 0
                continue
            if exp in ids:
                hits += 1; rr += 1 / (ids.index(exp) + 1)
            elif args.verbose:
                print(f"   miss [{name}] {q!r} -> {ids}")
        n = len(answerable)
        print(f"{name:<12}{hits:>4}/{n}{rr / n:>7.2f}{statistics.median(lat):>8.2f}"
              f"{CALLS['llm'] - c0:>11}  {'yes' if oos else 'no'}")

if __name__ == "__main__":
    main()
