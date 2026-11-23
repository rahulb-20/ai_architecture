# Day 13 — Feedback Loops & Caching

> Part of the [GenAI Architect study path](../../README.md) · Production Retrieval · [Original interactive pack](https://claude.ai/artifact/KwkESUASD6fHTzRnqKjUYQ) · [Offline HTML](../../archive/html/day-13.html)


Week 2 · Production retrieval · Day 13 of 30

# Feedback loops, semantic caching and prompt caching

How a RAG system gets better every week instead of decaying, and how to cut its latency and token bill without serving wrong answers faster.

~25 min read75 min labRuns on Ollama, no Docker

## Why this matters for the JD

The JD's preferred skills name **"RAG optimization … feedback loops"** and the must-haves include **"evaluation (latency/cost)"** and **"productionization … cost controls"**. In a design review, this is where you show the system has a plan for month three: who reads thumbs-down, how failures become regression tests, and which cache layer pays for the GPU bill.

## Concept brief

Treat a production RAG system as two loops running at different speeds.

- **Fast loop (milliseconds):** the request path. Caches remove work at three different layers: whole answers (exact and semantic cache), shared prompt prefixes (provider prompt caching, vLLM automatic prefix caching), and intermediate artifacts (query embeddings, retrieval results, rerank scores).
- **Slow loop (days):** the improvement path. Signals are captured, triaged into a failure taxonomy, fixed at the right layer (index, chunking, prompt, model, guardrail), and frozen into the golden set so the fix can't regress.

![Request path with three cache layers and a feedback loop back into the golden set and index](diagrams/fig-1.svg)

Three cache layers on the request path; the feedback path ends in two places — the golden set (so fixes stick) and cache invalidation (so bad answers stop being replayed).

### Prompt caching is about layout, not a feature flag

Provider prompt caches (Anthropic, OpenAI, Gemini, Bedrock, Azure OpenAI) and self-hosted KV prefix reuse (vLLM's Automatic Prefix Caching) all match on an *exact token prefix*. One changed token early in the prompt invalidates everything after it. So the architecture rule is: **stable → volatile**. Tool schemas, system instructions, policy text and few-shot examples first; retrieved chunks next; the user question last. Timestamps, user names or request IDs in the system prompt silently destroy the hit rate. Sort tool definitions deterministically.

Current pricing shapes (confirm per model before committing numbers): Anthropic charges 1.25× base input for a 5-minute cache write, 2× for a 1-hour write, and about 0.1× for a cache read; you mark up to 4 breakpoints with `cache_control`, or set one top-level `cache_control` for automatic caching. OpenAI caches automatically above 1,024 tokens and reports `cached_tokens` in usage; `prompt_cache_key` influences routing but does not guarantee a hit. Gemini 2.5+ does implicit caching by default, with explicit context caches as a separate beta API. vLLM APC only cuts *prefill* time, never decode time.

Break-even for a cached prefix of P tokens reused n times (Anthropic multipliers):
5-min TTL: 1.25P + 0.1P·n vs P·(1+n) → pays off from the 1st reuse
1-hour TTL: 2.00P + 0.1P·n vs P·(1+n) → pays off from the 2nd reuse
Worked example: 6,000-token prompt = 4,000 static prefix + 2,000 retrieved chunks + question
uncached input = 6,000 units
cached (steady state) = 0.1 × 4,000 + 2,000 = 2,400 units → −60% input tokens
+ 20% semantic-cache hit rate on FAQ intents → those calls cost ≈ one embedding

### Semantic caching: the hit rate is easy, the precision is hard

A semantic cache embeds the query and returns a stored answer when cosine similarity clears a threshold. The failure is that the most dangerous queries are the most similar: "domestic travel limit" and "international travel limit" can score above 0.94 with general-purpose embedders. Raising the threshold kills recall long before it removes those false hits. What works in practice:

- **Scope the key:** tenant + permission set + corpus version. A cache shared across ACL groups is a data-leak path, not a performance feature.
- **Guard on discriminators:** numbers, regions, product names, dates must match exactly (the lab does this with a regex; production systems use NER or a small reranker as a verifier).
- **Cache by intent:** enable it only for FAQ-class intents with stable answers; never for personalised, time-sensitive or tool-calling turns.
- **Calibrate on near-misses:** the threshold comes from a labelled set of paraphrases *and* near-miss pairs, with a target false-hit rate, not from a blog post's "0.9".
- **Only cache good answers:** admit entries that passed grounding checks; evict on thumbs-down, on TTL and on corpus re-index.

### Feedback signals, ranked by trust

Explicit thumbs are sparse and skew negative; use them, but don't optimise to them alone. Implicit signals are denser: user re-asks within a minute, copies the answer, clicks a citation, escalates to a human agent or raises a ticket. Online LLM-as-judge (faithfulness, answer relevance) scores a sample of every day's traffic. Human annotation queues review the disagreements. Each confirmed failure gets a class — `retrieval_miss`, `chunking`, `stale_doc`, `generation`, `out_of_scope`, `policy` — because the class decides which team and which layer fixes it. Then it lands in the golden set that the CI eval gate runs on every prompt, model or index change.

**IBM mapping:** watsonx.governance covers the monitoring and evaluation side (quality and drift metrics, feedback data); for self-hosted Granite on Red Hat OpenShift AI, the vLLM runtime gives you prefix caching; Azure API Management offers a semantic-cache policy in front of Azure OpenAI; Bedrock and Vertex both expose provider-side prompt/context caching.

## Trade-offs & decision table

| Lever | Saves | Pick when | Failure modes | Cost / latency notes |
| --- | --- | --- | --- | --- |
| Exact-match cache | Whole call | Repeated canned prompts, batch jobs, eval reruns | Near-zero hit rate on free text; stale after re-index | Key = hash(model, prompt version, normalised query, scope). Cheapest win. |
| Semantic cache | Retrieval + generation | High-volume FAQ intents (HR, IT helpdesk, support) | Confident wrong answers on near-misses; cross-ACL leakage; staleness | Hit ≈ one embedding + vector lookup (tens of ms). Needs calibration set and invalidation. |
| Provider prompt cache | Prefill cost + TTFT on shared prefix | Long system prompts, tool schemas, few-shot, long docs, agents | Volatile tokens in prefix → 0% hits; TTL expiry on low traffic; min-token thresholds | Reads ≈ 0.1× input; writes 1.25–2×. Track cached-token ratio as an SLI. |
| vLLM prefix caching | Prefill GPU time | Self-hosted models, multi-turn chat, shared RAG instructions | KV memory pressure evicts blocks; routing across replicas breaks locality | Use prefix-aware routing in the gateway for multi-replica deployments. |
| Embedding / retrieval cache | Embed + ANN + rerank | Re-ask patterns, multi-query expansion, agent loops | Stale results after index updates | Key on corpus version; reranker is often the costliest retrieval hop. |
| Model routing / cascade | Per-token price | Mixed difficulty traffic | Router misclassifies hard queries; quality variance | Gate with eval on each route; fall back up the cascade on low confidence. |
| Streaming + trimming | Perceived latency, output tokens | Every chat UI | Guardrails that need the full answer conflict with streaming | TTFT is the SLO users feel; cap max\_tokens; trim top-k after rerank. |

**Review trap:** quoting a semantic-cache hit rate without a false-hit rate. Ask for both, measured on a near-miss set, before approving the design.

## Hands-on lab · 75 min

**Goal:** build a scoped semantic cache in front of a local RAG answerer, prove why the threshold alone can't protect you, measure prefix reuse, and wire a thumbs-down into cache invalidation and the golden set. Everything runs in native Windows via Ollama: no Redis, Docker or WSL2.

### Setup (PowerShell)

```text
# PowerShell — one-time setup (no Docker/WSL needed)
winget install --id Ollama.Ollama -e        # or the installer from ollama.com/download
ollama pull nomic-embed-text
ollama pull llama3.2:3b

mkdir day13; cd day13
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install "ollama>=0.6" "numpy>=2.1"
# save the script below as semcache_lab.py, then:
python semcache_lab.py sweep
python semcache_lab.py serve
python semcache_lab.py feedback
```

Model footprint: `llama3.2:3b` is about 2 GB at Q4 and fits in the 3050's 6 GB VRAM; `nomic-embed-text` is small and can share it. If VRAM is tight, set `$env:OLLAMA_MAX_LOADED_MODELS=2` before starting Ollama, or swap the chat model for `qwen2.5:1.5b`.

### semcache\_lab.py

`lab/semcache_lab.py`

```python
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
```

### What to do, in order

1. **sweep (15 min):** read the similarity column. Note how high the near-miss pairs score. Find the threshold where guard-off false hits reach 0 and see what recall is left.
2. **serve (20 min):** watch latency on HIT vs MISS, and `prompt_tokens`/`prefill_ms` on consecutive misses. If prefix reuse kicks in, later misses evaluate far fewer prompt tokens. Then put `time.ctime()` at the start of `SYSTEM` and rerun: the reuse disappears.
3. **feedback (15 min):** confirm the downvoted entry misses on replay and `golden_set.jsonl` gains a case with a failure class.
4. **write-up (15 min):** one paragraph: chosen threshold, false-hit rate, latency saved per hit, and which intents you'd allow in the cache.

### Expected output (shape)

```text
# sweep (illustrative; exact sims depend on the embedding model)
   sim  label  pair
 0.912  HIT    'How many days of annual leave do I get' ~ 'What is my yearly leave entitlement?'
 0.947  MISS   'What is the travel reimbursement limit' ~ 'What is the travel reimbursement limit'
 0.931  MISS   'Who approves expenses above 50,000 INR' ~ 'Who approves expenses above 5,000 INR?'
 ...
threshold | guard | true hits | false hits (wrong answer served)
   0.85   | off  |   5/6     |   6/6     <- every near-miss is served a wrong answer
   0.93   | off  |   2/6     |   3/6     <- raising the threshold kills recall first
   0.85   | on   |   5/6     |   0/6     <- the guard, not the threshold, buys precision

# serve
MISS    3400 ms  best=-1.000  id=3f1a9c2e  'How many days of annual leave do I get?'  {'prompt_tokens': 1630, 'prefill_ms': 2100, ...}
HIT       45 ms  sim=0.912  'What is my yearly leave entitlement?'
MISS     900 ms  best=0.951  id=...       'How many days of sick leave do I get?'  {'prompt_tokens': 14, 'prefill_ms': 30, ...}
cache stats: {'hit': 2, 'miss': 5, 'guard_block': 2}
```

Your similarity values will differ by model and version; the pattern to look for is that near-miss pairs score close to or above true paraphrases, and that the entity guard removes false hits without costing recall.

### Stretch goals

1. **Verifier instead of regex:** replace `entities()` with a cross-encoder check (e.g. a bge reranker via `sentence-transformers`) that must score the (cached query, new query) pair above a cut-off. Compare false hits and added latency.
2. **Redis-backed cache:** swap the in-memory class for RedisVL's `SemanticCache` (needs Redis 8 / Redis Stack, so Docker Desktop or WSL2). Add `filterable_fields` for tenant and ACL so scope is enforced in the query, not in Python.

## GitHub repos to study

[redis/redis-vl-python](https://github.com/redis/redis-vl-python)Read the `SemanticCache` and `EmbeddingsCache` extensions: TTL, distance threshold and filterable fields for scoping.

[vllm-project/vllm](https://github.com/vllm-project/vllm)Study the prefix-caching design (block hashing, LRU eviction) and how it interacts with KV memory under load.

[langfuse/langfuse](https://github.com/langfuse/langfuse)Scores, annotation queues and datasets: the reference shape for "trace → feedback → dataset → experiment" loops.

[vllm-project/semantic-router](https://github.com/vllm-project/semantic-router)Policy-based model routing on cost, latency and quality: the cascade lever from the table, in code.

**Skip for new builds:** zilliztech/GPTCache is still public, but its README says it no longer adds support for new APIs or models. Read its get/set design for ideas; don't adopt it.

## Read / watch

- [Anthropic · Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) — breakpoints, TTLs, automatic caching, per-model minimums, usage fields.
- [OpenAI · Prompt caching guide](https://developers.openai.com/api/docs/guides/prompt-caching) — automatic caching, retention options, `prompt_cache_key`; then the [Prompt Caching 201 cookbook](https://developers.openai.com/cookbook/examples/prompt_caching_201).
- [Langfuse · Evaluation overview](https://langfuse.com/docs/evaluation/overview) — online LLM-as-judge, annotation queues, datasets and CI experiments in one loop.
- [vLLM · Automatic Prefix Caching](https://docs.vllm.ai/en/latest/features/automatic_prefix_caching.html) — what it speeds up and what it doesn't.

## Interview questions

**Your semantic cache served "domestic travel limit" for an "international" question. How do you fix it without killing the hit rate?**

Raising the threshold won't do it: near-misses often score above paraphrases. Add an exact-match guard on discriminating entities (numbers, regions, product names) or a cross-encoder verifier on the query pair; restrict caching to FAQ intents; calibrate the threshold on a labelled near-miss set with a target false-hit rate (e.g. under 0.5%); and monitor false hits through feedback on cache-served answers specifically.

**How do you design prompts so provider prompt caching actually hits?**

Order content stable → volatile: tools, system instructions, policy and few-shot, then retrieved chunks, then the question. Keep timestamps, user IDs and A/B flags out of the prefix; serialise tool schemas deterministically; place breakpoints at the end of each stable segment. Choose TTL from traffic: a 5-minute write pays back on the first reuse, a 1-hour write on the second. Track cached-token ratio per route as an SLI and alert when it drops after a deploy.

**Thumbs-down rate is 3%. How do you turn that into improvements rather than noise?**

Join feedback to traces, add implicit signals (re-asks, escalations), and run an online judge on a sample to catch silent failures. Route disagreements to an annotation queue. Classify each confirmed failure by layer (retrieval miss, chunking, stale doc, generation, policy) and fix it there. Every fixed case goes into the golden set; the CI eval gate blocks prompt, model or index changes that regress it. Report weekly by failure class, not by raw thumbs.

**A semantic cache in a multi-tenant assistant: what are the security and compliance risks?**

Cross-tenant or cross-role leakage if the cache key ignores ACLs; cached PII outliving its retention policy; inability to honour erasure requests under DPDP or GDPR. Mitigate with keys scoped by tenant, permission-set hash and corpus version; no caching of personalised or PII-bearing answers (check with Presidio before admit); per-entry TTLs; purge APIs by user and document; encryption at rest; and audit logs of cache-served responses.

**p95 is 4.2 s against a 2.5 s SLO. Walk through how you'd get there.**

Break the trace down: embedding, ANN, rerank, prefill, decode, guardrails. Typical moves: stream to cut perceived latency (TTFT); move the static prefix into the prompt cache to cut prefill; shrink rerank candidates or use a smaller reranker; trim context to the top chunks after rerank; cap max tokens; route simple intents to a smaller model; run retrieval branches in parallel; serve FAQ intents from the semantic cache. Re-run the eval set after each change so quality doesn't pay for the speed.

## Architect's lens · ADR-013

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

## Lab files in this folder

- [`lab/semcache_lab.py`](lab/semcache_lab.py)
