"""
Day 11 - RAG evaluation harness (runs locally on Ollama, or any OpenAI-compatible endpoint).

Layers, cheapest first:
  1. Deterministic retrieval metrics  -> hit@k, MRR (needs labelled relevant chunk ids)
  2. Deterministic answer checks      -> citation validity, abstention correctness
  3. LLM-as-judge (Ragas 0.4)         -> faithfulness, context precision, context recall

Two retriever configs (k=1 vs k=3) are compared so you see the metrics move.
"""
import asyncio
import csv
import os
import re
import statistics as st
import time

os.environ.setdefault("RAGAS_DO_NOT_TRACK", "true")  # Ragas sends usage telemetry by default; off for enterprise use

from openai import AsyncOpenAI, OpenAI
from rank_bm25 import BM25Okapi
from ragas.llms import llm_factory
from ragas.metrics.collections import ContextPrecision, ContextRecall, Faithfulness

BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:11434/v1")  # Ollama's OpenAI-compatible API
API_KEY = os.getenv("LLM_API_KEY", "ollama")                       # Ollama ignores it
GEN_MODEL = os.getenv("GEN_MODEL", "llama3.2:3b")                  # the system under test
JUDGE_MODEL = os.getenv("JUDGE_MODEL", "qwen2.5:7b")               # different family -> less self-preference

# --- 1. A tiny, fictional policy corpus -------------------------------------------------
CORPUS = {
    "hr-01": "Employees accrue 1.75 days of paid leave per month, capped at 30 days carried over into the next calendar year.",
    "hr-02": "Unused leave above the 30-day cap lapses on 31 March. Leave encashment is allowed only at resignation or retirement.",
    "hr-03": "Parental leave is 26 weeks for the primary caregiver and 4 weeks for the secondary caregiver, available after 80 days of service.",
    "it-01": "Laptops must use full-disk encryption (BitLocker on Windows). Lost devices must be reported to the IT service desk within 4 hours.",
    "it-02": "Production database access requires a ticket approved by the data owner and expires automatically after 72 hours.",
    "it-03": "Passwords must be at least 14 characters. MFA is mandatory for VPN, email and all SaaS tools that support SSO.",
    "fin-01": "Travel claims must be submitted within 30 days of the trip with itemised receipts. Claims above INR 50,000 need VP approval.",
    "fin-02": "Domestic economy airfare is the default. Business class is allowed for flights over 6 hours with director approval.",
    "sec-01": "Customer PII may not be pasted into public AI tools. Approved internal assistants mask PII before any model call.",
    "sec-02": "Security incidents must be reported to the SOC hotline immediately; the SOC triages within 15 minutes for P1 events.",
}

# --- 2. Golden set: question, reference answer, relevant chunk ids, answerable? ------------
GOLDEN = [
    ("How many days of leave can I carry over into next year?", "Up to 30 days can be carried over.", ["hr-01"], True),
    ("When does leave above the carry-over cap lapse?", "Leave above the 30-day cap lapses on 31 March.", ["hr-02"], True),
    ("How long is parental leave for a secondary caregiver?", "4 weeks, after 80 days of service.", ["hr-03"], True),
    ("How quickly must I report a lost laptop?", "Within 4 hours, to the IT service desk.", ["it-01"], True),
    ("How long does production DB access last once approved?", "It expires automatically after 72 hours.", ["it-02"], True),
    ("Who approves a travel claim of INR 80,000?", "A VP must approve claims above INR 50,000.", ["fin-01"], True),
    ("Can I fly business class on a 7-hour flight?", "Yes, flights over 6 hours allow business class with director approval.", ["fin-02"], True),
    ("Can I paste customer data into ChatGPT?", "No. Customer PII may not be pasted into public AI tools.", ["sec-01"], True),
    # paraphrased on purpose: no keyword overlap, so BM25 should miss these (that's the lesson)
    ("If my notebook goes missing, who do I tell and how fast?", "The IT service desk, within 4 hours.", ["it-01"], True),
    ("Is two-factor needed to connect remotely?", "Yes, MFA is mandatory for VPN.", ["it-03"], True),
    ("What is the company's dental insurance limit?", "Not covered by the provided policies.", [], False),
    ("How many sick days do contractors get?", "Not covered by the provided policies.", [], False),
]

IDS = list(CORPUS)
tok = lambda s: re.findall(r"[a-z0-9]+", s.lower())
BM25 = BM25Okapi([tok(CORPUS[i]) for i in IDS])


def retrieve(q: str, k: int) -> list[str]:
    scores = BM25.get_scores(tok(q))
    return [IDS[i] for i in sorted(range(len(IDS)), key=lambda i: -scores[i])[:k]]


SYSTEM = (
    "Answer ONLY from the context. Cite every fact with its chunk id in square brackets, e.g. [hr-01]. "
    "If the context does not contain the answer, reply exactly: I don't know."
)
gen_client = OpenAI(base_url=BASE_URL, api_key=API_KEY)


def generate(q: str, ids: list[str]) -> tuple[str, float]:
    ctx = "\n".join(f"[{i}] {CORPUS[i]}" for i in ids)
    t0 = time.perf_counter()
    r = gen_client.chat.completions.create(
        model=GEN_MODEL,
        temperature=0,
        messages=[{"role": "system", "content": SYSTEM},
                  {"role": "user", "content": f"Context:\n{ctx}\n\nQuestion: {q}"}],
    )
    return r.choices[0].message.content.strip(), time.perf_counter() - t0


# --- 3. Deterministic metrics ------------------------------------------------------------
def hit_and_rr(retrieved: list[str], relevant: list[str]) -> tuple[float, float]:
    for rank, cid in enumerate(retrieved, 1):
        if cid in relevant:
            return 1.0, 1.0 / rank
    return 0.0, 0.0


def citation_ok(answer: str, retrieved: list[str]) -> bool:
    cited = set(re.findall(r"\[([a-z]+-\d+)\]", answer))
    return cited <= set(retrieved)  # never cite a chunk the model was not shown


def abstained(answer: str) -> bool:
    return "i don't know" in answer.lower() or "i do not know" in answer.lower()


# --- 4. LLM-as-judge (Ragas 0.4 collections API) ------------------------------------------
judge = llm_factory(JUDGE_MODEL, client=AsyncOpenAI(base_url=BASE_URL, api_key=API_KEY),
                    temperature=0, max_tokens=2048)
faith, cprec, crec = Faithfulness(llm=judge), ContextPrecision(llm=judge), ContextRecall(llm=judge)


async def judge_row(q, ref, ans, ctxs, answerable):
    out = {"faithfulness": None, "context_precision": None, "context_recall": None}
    if abstained(ans):
        return out  # faithfulness is undefined for an abstention; score abstention separately
    try:
        out["faithfulness"] = (await faith.ascore(user_input=q, response=ans, retrieved_contexts=ctxs)).value
        if answerable:
            out["context_precision"] = (await cprec.ascore(user_input=q, reference=ref, retrieved_contexts=ctxs)).value
            out["context_recall"] = (await crec.ascore(user_input=q, retrieved_contexts=ctxs, reference=ref)).value
    except Exception as e:  # small local judges sometimes emit invalid JSON - count it, don't crash
        print(f"   judge error on '{q[:40]}...': {type(e).__name__}")
    return out


def mean(xs):
    xs = [x for x in xs if x is not None]
    return round(st.mean(xs), 3) if xs else float("nan")


async def run_config(k: int) -> list[dict]:
    rows = []
    for q, ref, rel, answerable in GOLDEN:
        ids = retrieve(q, k)
        ans, lat = generate(q, ids)
        hit, rr = hit_and_rr(ids, rel) if answerable else (None, None)
        scores = await judge_row(q, ref, ans, [CORPUS[i] for i in ids], answerable)
        rows.append({"k": k, "question": q, "retrieved": "|".join(ids), "answer": ans,
                     "answerable": answerable, "hit": hit, "rr": rr,
                     "citation_ok": citation_ok(ans, ids),
                     "abstain_correct": abstained(ans) == (not answerable),
                     "latency_s": round(lat, 2), **scores})
        print(f"k={k} | {q[:45]:45} | hit={hit} | {ans[:60]!r}")
    return rows


async def main():
    all_rows = []
    for k in (1, 3):
        all_rows += await run_config(k)

    print("\n=== Summary ===")
    print(f"{'k':>2} {'hit@k':>6} {'MRR':>6} {'cite':>6} {'abst':>6} {'faith':>6} {'cprec':>6} {'crec':>6} {'p50 s':>6}")
    for k in (1, 3):
        r = [x for x in all_rows if x["k"] == k]
        print(f"{k:>2} {mean([x['hit'] for x in r]):>6} {mean([x['rr'] for x in r]):>6} "
              f"{mean([float(x['citation_ok']) for x in r]):>6} {mean([float(x['abstain_correct']) for x in r]):>6} "
              f"{mean([x['faithfulness'] for x in r]):>6} {mean([x['context_precision'] for x in r]):>6} "
              f"{mean([x['context_recall'] for x in r]):>6} {st.median([x['latency_s'] for x in r]):>6}")

    with open("eval_results.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(all_rows[0]) + ["human_faithful"])
        w.writeheader()
        for row in all_rows:
            w.writerow({**row, "human_faithful": ""})  # fill 1/0 by hand for the calibration step
    print("\nWrote eval_results.csv")


if __name__ == "__main__":
    asyncio.run(main())
