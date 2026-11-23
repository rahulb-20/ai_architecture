"""
Day 4 - Grounded QA with layered hallucination control (runs locally on an RTX 3050 6GB).

Pipeline (each layer can force an abstention):
  L1 retrieval floor  -> no passage scores above RETRIEVAL_FLOOR  => abstain without calling the LLM
  L2 constrained gen  -> JSON-schema output: answerable flag + claims, each with passage_id + verbatim quote
  L3 quote check      -> quote must literally appear in the cited passage (deterministic, ~0 ms)
  L4 NLI check        -> cross-encoder must say the cited passage ENTAILS the claim
  Policy              -> 0 supported claims => abstain; some dropped => partial answer, flagged

Run:  python grounded_qa.py            (guarded pipeline)
      python grounded_qa.py --baseline (same questions, free-text answer, no guards - compare!)
"""

import argparse
import re
import time

import numpy as np
from ollama import chat
from pydantic import BaseModel, Field
from sentence_transformers import CrossEncoder, SentenceTransformer

LLM = "qwen3:4b"                     # fallback: "llama3.2:3b"
EMBED_MODEL = "BAAI/bge-small-en-v1.5"
NLI_MODEL = "cross-encoder/nli-deberta-v3-small"   # labels: [contradiction, entailment, neutral]
TOP_K = 3
RETRIEVAL_FLOOR = 0.55               # calibrate! print scores for in- vs out-of-domain questions
ENTAIL_MIN = 0.50

# A fictional company's policy corpus. Small on purpose, so you can see every decision.
CORPUS = {
    "TRV-01": "Employees travelling internationally receive a daily allowance of EUR 90 for meals. "
              "The allowance is reduced by 50% on days when the client provides lunch.",
    "TRV-02": "Economy class is mandatory for flights under 6 hours. Business class may be booked for "
              "flights of 6 hours or more with written approval from a Director.",
    "TRV-03": "Hotel bookings must be made through the Northwind travel portal. The nightly cap is "
              "EUR 180 in Tier-1 cities (London, Paris, New York) and EUR 120 elsewhere.",
    "EXP-01": "Expense claims must be submitted within 30 days of the trip end date. Claims submitted "
              "after 60 days are rejected automatically.",
    "EXP-02": "Receipts are required for any single expense above EUR 25. Card statements are not "
              "accepted as a substitute for itemised receipts.",
    "SEC-01": "Laptops taken abroad must have full-disk encryption enabled. Travel to sanctioned "
              "countries requires a loaner device issued by IT Security.",
}

QUESTIONS = [
    "What is the hotel cap in Paris?",                                   # answerable, 1 passage
    "Can I fly business class to Singapore on a 12-hour flight?",        # answerable, conditional
    "If the client buys me lunch abroad, what is my meal allowance?",    # needs arithmetic from text
    "What is the mileage rate for using my own car?",                    # NOT in corpus -> abstain
    "Why was the Paris hotel cap raised to EUR 250 last year?",          # false premise -> must not invent
]


class Claim(BaseModel):
    text: str = Field(description="One atomic factual statement answering part of the question")
    passage_id: str = Field(description="ID of the passage that supports this statement")
    quote: str = Field(description="Exact, verbatim span copied from that passage")


class GroundedAnswer(BaseModel):
    answerable: bool = Field(description="False if the passages do not contain the answer")
    claims: list[Claim]
    reason_if_unanswerable: str = ""


SYSTEM = (
    "You answer questions about company policy using ONLY the passages provided.\n"
    "Rules:\n"
    "1. Break the answer into short atomic claims. Every claim needs passage_id and a quote copied "
    "verbatim from that passage.\n"
    "2. If the passages do not contain the answer, set answerable=false and return no claims.\n"
    "3. If the question assumes something the passages do not say, do not accept the assumption; "
    "state what the passages actually say, or set answerable=false.\n"
    "4. Never use outside knowledge."
)


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


class Pipeline:
    def __init__(self) -> None:
        t = time.perf_counter()
        self.ids = list(CORPUS)
        self.embedder = SentenceTransformer(EMBED_MODEL)
        self.doc_vecs = self.embedder.encode(list(CORPUS.values()), normalize_embeddings=True)
        self.nli = CrossEncoder(NLI_MODEL)
        print(f"[init] models loaded in {time.perf_counter() - t:.1f}s")

    def retrieve(self, q: str) -> list[tuple[str, float]]:
        qv = self.embedder.encode([q], normalize_embeddings=True)[0]
        sims = self.doc_vecs @ qv
        order = np.argsort(-sims)[:TOP_K]
        return [(self.ids[i], float(sims[i])) for i in order]

    def generate(self, q: str, hits: list[tuple[str, float]]) -> GroundedAnswer:
        ctx = "\n".join(f"[{pid}] {CORPUS[pid]}" for pid, _ in hits)
        resp = chat(
            model=LLM,
            messages=[{"role": "system", "content": SYSTEM},
                      {"role": "user", "content": f"Passages:\n{ctx}\n\nQuestion: {q}"}],
            format=GroundedAnswer.model_json_schema(),   # constrained decoding (L2)
            options={"temperature": 0},
            think=False,                                 # qwen3: skip reasoning tokens for latency
        )
        return GroundedAnswer.model_validate_json(resp.message.content)

    def entailment(self, premise: str, hypothesis: str) -> float:
        probs = self.nli.predict([(premise, hypothesis)], apply_softmax=True)[0]
        return float(probs[1])

    def answer(self, q: str) -> dict:
        t0 = time.perf_counter()
        hits = self.retrieve(q)
        top = hits[0][1]
        if top < RETRIEVAL_FLOOR:                                          # L1
            return {"status": "ABSTAIN", "why": f"retrieval floor (top={top:.2f})",
                    "hits": hits, "ms": (time.perf_counter() - t0) * 1000}

        ga = self.generate(q, hits)                                        # L2
        if not ga.answerable or not ga.claims:
            return {"status": "ABSTAIN", "why": f"model: {ga.reason_if_unanswerable or 'not answerable'}",
                    "hits": hits, "ms": (time.perf_counter() - t0) * 1000}

        allowed = {pid for pid, _ in hits}
        kept, dropped = [], []
        for c in ga.claims:
            src = CORPUS.get(c.passage_id, "")
            if c.passage_id not in allowed or norm(c.quote) not in norm(src):   # L3
                dropped.append((c, "quote not found in cited passage"))
                continue
            p = self.entailment(src, c.text)                                   # L4
            if p < ENTAIL_MIN:
                dropped.append((c, f"not entailed (p={p:.2f})"))
                continue
            kept.append((c, p))

        status = "ABSTAIN" if not kept else ("PARTIAL" if dropped else "GROUNDED")
        return {"status": status, "kept": kept, "dropped": dropped, "hits": hits,
                "why": "all claims failed verification" if not kept else "",
                "ms": (time.perf_counter() - t0) * 1000}


def baseline(q: str) -> str:
    ctx = "\n".join(f"[{k}] {v}" for k, v in CORPUS.items())
    r = chat(model=LLM, messages=[{"role": "user", "content": f"{ctx}\n\nQuestion: {q}"}],
             options={"temperature": 0}, think=False)
    return r.message.content.strip()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", action="store_true")
    args = ap.parse_args()

    if args.baseline:
        for q in QUESTIONS:
            print(f"\nQ: {q}\nA: {baseline(q)}")
        return

    pipe = Pipeline()
    tally: dict[str, int] = {}
    for q in QUESTIONS:
        r = pipe.answer(q)
        tally[r["status"]] = tally.get(r["status"], 0) + 1
        print(f"\nQ: {q}")
        print("   retrieval:", ", ".join(f"{pid}={s:.2f}" for pid, s in r["hits"]))
        print(f"   -> {r['status']}  ({r['ms']:.0f} ms) {r['why']}")
        for c, p in r.get("kept", []):
            print(f"      OK  [{c.passage_id}] {c.text}   (entail={p:.2f})")
        for c, why in r.get("dropped", []):
            print(f"      DROP [{c.passage_id}] {c.text}   <- {why}")
    print("\nSummary:", tally)


if __name__ == "__main__":
    main()
