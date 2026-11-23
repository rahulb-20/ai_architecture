"""
Day 13 lab: semantic cache + prompt-prefix reuse + feedback loop, fully local (Ollama).
Run:  python semcache_lab.py sweep      -> threshold calibration on paraphrase vs near-miss pairs
      python semcache_lab.py serve      -> cached RAG answers, latency + prefix-reuse stats
      python semcache_lab.py feedback   -> thumbs-down -> invalidate cache + grow golden set
"""
import json
import re
import sys
import time
import uuid
import pathlib

import numpy as np
import ollama

EMBED_MODEL = "nomic-embed-text"   # 137M params, runs fine on CPU or RTX 3050
CHAT_MODEL = "llama3.2:3b"         # ~2 GB Q4, fits in 6 GB VRAM
CORPUS_VERSION = "kb-2026-10-22"   # bump when the index is rebuilt -> old cache entries die
GOLDEN = pathlib.Path("golden_set.jsonl")
FEEDBACK = pathlib.Path("feedback.jsonl")

# ---------------------------------------------------------------- embeddings
def embed(texts: list[str]) -> np.ndarray:
    # nomic-embed-text expects a task prefix; use the same one on both sides (symmetric match)
    r = ollama.embed(model=EMBED_MODEL, input=[f"search_query: {t}" for t in texts])
    v = np.asarray(r.embeddings, dtype=np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)

# Cheap "entity guard": numbers and discriminating words must match exactly.
GUARD_WORDS = {"india", "us", "uk", "international", "domestic", "annual", "sick",
               "casual", "email", "vpn", "encash", "carry", "contractor", "manager"}

def entities(q: str) -> frozenset[str]:
    toks = re.findall(r"[a-z]+|\d[\d,]*", q.lower())
    nums = {t.replace(",", "") for t in toks if t[0].isdigit()}
    return frozenset(nums | (set(toks) & GUARD_WORDS))

# ---------------------------------------------------------------- semantic cache
class SemanticCache:
    def __init__(self, threshold: float = 0.90, ttl_s: int = 3600, guard: bool = True):
        self.threshold, self.ttl, self.guard = threshold, ttl_s, guard
        self.entries: list[dict] = []
        self.stats = {"hit": 0, "miss": 0, "guard_block": 0}

    @staticmethod
    def scope(tenant: str, acl_group: str) -> str:
        # never share answers across tenants, permission sets, or corpus versions
        return f"{tenant}|{acl_group}|{CORPUS_VERSION}"

    def lookup(self, query: str, vec: np.ndarray, scope: str):
        now, best, best_s = time.time(), None, -1.0
        for e in self.entries:
            if e["scope"] != scope or e["bad"] or now - e["ts"] > self.ttl:
                continue
            s = float(vec @ e["vec"])
            if s > best_s:
                best, best_s = e, s
        if best is not None and best_s >= self.threshold:
            if self.guard and entities(query) != best["ents"]:
                self.stats["guard_block"] += 1
            else:
                self.stats["hit"] += 1
                return best, best_s
        self.stats["miss"] += 1
        return None, best_s

    def store(self, query, vec, scope, answer) -> str:
        eid = uuid.uuid4().hex[:8]
        self.entries.append({"id": eid, "query": query, "vec": vec, "scope": scope,
                             "answer": answer, "ents": entities(query),
                             "ts": time.time(), "bad": False})
        return eid

    def invalidate(self, eid: str):
        for e in self.entries:
            if e["id"] == eid:
                e["bad"] = True

# ---------------------------------------------------------------- part A: calibration
PAIRS = [  # (cached query, new query, should_hit)
    ("How many days of annual leave do I get?", "What is my yearly leave entitlement?", True),
    ("How do I reset my VPN password?", "Steps to change my VPN password", True),
    ("What is the travel reimbursement limit for domestic trips?", "Domestic travel claim cap?", True),
    ("Can I carry forward unused leave?", "Do unused leave days carry over to next year?", True),
    ("How do I raise an IT ticket?", "Where do I log a helpdesk request?", True),
    ("Who approves expenses above 50,000 INR?", "Who signs off on expenses over 50,000 INR?", True),
    ("What is the travel reimbursement limit for domestic trips?", "What is the travel reimbursement limit for international trips?", False),
    ("How many days of annual leave do I get?", "How many days of sick leave do I get?", False),
    ("How do I reset my VPN password?", "How do I reset my email password?", False),
    ("Can I carry forward unused leave?", "Can I encash unused leave?", False),
    ("What is the notice period for employees in India?", "What is the notice period for employees in the US?", False),
    ("Who approves expenses above 50,000 INR?", "Who approves expenses above 5,000 INR?", False),
]

def sweep():
    a = embed([p[0] for p in PAIRS])
    b = embed([p[1] for p in PAIRS])
    sims = (a * b).sum(axis=1)
    print(f"{'sim':>6}  label  pair")
    for s, (x, y, lab) in zip(sims, PAIRS):
        print(f"{s:6.3f}  {'HIT ' if lab else 'MISS'}   {x[:38]!r} ~ {y[:38]!r}")
    print("\nthreshold | guard | true hits | false hits (wrong answer served)")
    for guard in (False, True):
        for t in (0.80, 0.85, 0.88, 0.90, 0.93, 0.95):
            tp = fp = 0
            for s, (x, y, lab) in zip(sims, PAIRS):
                hit = s >= t and (not guard or entities(x) == entities(y))
                tp += hit and lab
                fp += hit and not lab
            print(f"   {t:.2f}   | {'on ' if guard else 'off'}  |   {tp}/6     |   {fp}/6")

# ---------------------------------------------------------------- part B: cached RAG
KB = """[HR-12] Annual leave: 24 working days per calendar year, accrued monthly.
[HR-13] Sick leave: 12 days per year, not encashable.
[HR-14] Up to 10 unused annual leave days carry forward; the rest lapse on 31 March.
[HR-15] Leave encashment: allowed only at exit, max 30 days.
[FIN-03] Domestic travel claims capped at INR 15,000 per trip; international at USD 1,500.
[FIN-07] Expenses above INR 50,000 need VP approval; above INR 5,000 need manager approval.
[IT-02] Reset VPN password at https://selfservice.example.internal/vpn (MFA required).
[IT-05] Raise IT tickets in ServiceDesk, category 'End-user computing'.
"""
# Static prefix FIRST (instructions + KB) so the server can reuse its KV cache; the
# per-request question goes LAST. Do not put timestamps or user ids in the prefix.
SYSTEM = ("You are an internal policy assistant. Answer ONLY from the policy text below, "
          "cite the [ID], and reply 'Not in policy' if absent. Be brief.\n\n" + KB * 6)

def answer_llm(q: str) -> tuple[str, dict]:
    r = ollama.chat(model=CHAT_MODEL, keep_alive="10m",
                    options={"temperature": 0, "num_ctx": 4096},
                    messages=[{"role": "system", "content": SYSTEM},
                              {"role": "user", "content": q}])
    meta = {"prompt_tokens": r.prompt_eval_count,
            "prefill_ms": round((r.prompt_eval_duration or 0) / 1e6),
            "total_ms": round((r.total_duration or 0) / 1e6)}
    return r.message.content.strip(), meta

STREAM = ["How many days of annual leave do I get?",
          "What is my yearly leave entitlement?",
          "How many days of sick leave do I get?",
          "Domestic travel claim cap?",
          "What is the travel reimbursement limit for international trips?",
          "What's the domestic travel reimbursement limit?",
          "How do I reset my VPN password?"]

def serve(cache: SemanticCache | None = None, tenant="ltim", acl="all-staff"):
    cache = cache or SemanticCache(threshold=0.88)
    scope = cache.scope(tenant, acl)
    for q in STREAM:
        t0 = time.perf_counter()
        vec = embed([q])[0]
        hit, s = cache.lookup(q, vec, scope)
        if hit:
            ms = (time.perf_counter() - t0) * 1000
            print(f"HIT  {ms:7.0f} ms  sim={s:.3f}  {q!r}\n      -> {hit['answer'][:90]}")
            continue
        ans, meta = answer_llm(q)
        eid = cache.store(q, vec, scope, ans)
        ms = (time.perf_counter() - t0) * 1000
        print(f"MISS {ms:7.0f} ms  best={s:.3f}  id={eid}  {q!r}  {meta}\n      -> {ans[:90]}")
    print("\ncache stats:", cache.stats)
    return cache

# ---------------------------------------------------------------- part C: feedback loop
def record_feedback(cache: SemanticCache, eid: str, rating: int, reason: str, expected: str = ""):
    e = next(x for x in cache.entries if x["id"] == eid)
    FEEDBACK.open("a", encoding="utf-8").write(json.dumps({
        "ts": time.time(), "entry": eid, "query": e["query"], "answer": e["answer"],
        "rating": rating, "reason": reason, "corpus": CORPUS_VERSION}) + "\n")
    if rating < 0:
        cache.invalidate(eid)  # stop serving it immediately
        GOLDEN.open("a", encoding="utf-8").write(json.dumps({
            "question": e["query"], "expected": expected, "failure_class": reason,
            "source": "prod-feedback"}) + "\n")  # becomes a regression test

def feedback_demo():
    cache = serve()
    target = cache.entries[0]
    print(f"\nUser gives thumbs-down on {target['id']} ({target['query']!r})")
    record_feedback(cache, target["id"], -1, "generation:missing_accrual_detail",
                    expected="24 working days per year, accrued monthly [HR-12]")
    print("Replaying the stream; that entry must now MISS and regenerate:\n")
    cache.stats = {"hit": 0, "miss": 0, "guard_block": 0}
    serve(cache)
    print(f"\n{GOLDEN} now has {sum(1 for _ in GOLDEN.open(encoding='utf-8'))} case(s)")

if __name__ == "__main__":
    {"sweep": sweep, "serve": serve, "feedback": feedback_demo}[sys.argv[1] if len(sys.argv) > 1 else "sweep"]()
