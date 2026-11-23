# Day 11 — RAG Evaluation

> Part of the [GenAI Architect study path](../../README.md) · Production Retrieval · [Original interactive pack](https://claude.ai/artifact/YV5TWFmF8JUsrkCfNAvxYn) · [Offline HTML](../../archive/html/day-11.html)


Week 2 · Production retrieval · Day 11 of 30

# RAG evaluation: proving the system works, and catching it when it stops

Faithfulness, context precision and recall, golden sets, and why an LLM judge is a measuring instrument you have to calibrate before you trust it.

Tue 20 Oct 2026ragas 0.4.3deepeval 4.3.1trulens 2.16.0~75 min hands-on

## Why this matters for the JD

The JD names **evaluation (groundedness, relevance, faithfulness, latency/cost)** as a must-have, and lists **RAG optimisation with feedback loops**, **eval dashboards** and **testing in CI/CD** as preferred. Every one of those depends on a metric you can defend. Without an eval harness, a chunking or reranker change is an opinion. With one, it is a measured decision you can put in an ADR.

## Concept brief

### Evaluate the stages separately, then end to end

A RAG answer can fail in two places: the retriever brought the wrong evidence, or the generator misused good evidence. One end-to-end score can't tell you which, so it can't tell you what to fix. Put metrics on each edge of the pipeline.

![RAG pipeline with metrics attached to each edge](diagrams/fig-1.svg)

Blue, top: reference-free metrics (the TruLens “RAG triad”). These can run on sampled production traffic. Amber, bottom: reference-based metrics. These need a golden set, so they run offline and in CI.

### The metrics, and what each one actually computes

- **hit@k / recall@k / MRR / nDCG**: deterministic, cheap, and repeatable. They need labelled relevant documents. Run these first, since they cost nothing per run.
- **Context precision** (Ragas): an LLM marks each retrieved chunk as useful or not against the reference, then takes a rank-weighted average. It tells you whether the good evidence sits at the top, which matters because models attend unevenly to long contexts.
- **Context recall**: the reference answer is split into claims, and each claim is checked for support in the retrieved contexts. This is your “did retrieval bring everything needed” metric.
- **Faithfulness**: the response is split into atomic claims, an NLI-style verdict is made per claim against the contexts, and the score is supported ÷ total. This is the hallucination metric. It ignores whether the answer is *correct*, only whether it is *supported*.
- **Answer relevancy**: the judge writes questions the answer would satisfy, then measures their embedding similarity to the real question. It catches evasive or off-topic answers.
- **Answer correctness / factual correctness**: the answer is compared with the reference by claim overlap. It needs a golden set.

Common failure

A faithful answer can still be wrong (the retriever fetched a superseded policy and the model quoted it accurately). A correct answer can still be unfaithful (the model answered from its parametric memory). You need both axes. Abstentions (“I don't know”) make faithfulness undefined, so score them separately as abstention accuracy on the unanswerable slice.

### Golden sets that survive contact with production

- **Size and stratification.** Aim for 150–300 items stratified by intent, document type, difficulty (single-hop, multi-hop, comparison, numeric) and user role. Include 10–15% unanswerable questions and a few adversarial ones (prompt injection inside documents).
- **Source.** Start from real query logs and SME-written questions. Synthetic generation (Ragas testset, DeepEval Synthesizer) helps coverage, but an SME must review every synthetic item, or you end up measuring the generator's own blind spots.
- **Label relevance at document/span level, not chunk ID.** Chunk IDs change every time you re-chunk. Store `doc_id + char offsets` and map to chunks at eval time, so retrieval labels survive Day 6-style chunking experiments.
- **Version it like code.** Freeze it, hash it, keep a hold-out slice nobody tunes against, and refresh it quarterly from production failures. Production failures are the feedback loop the JD asks for.
- **Know the noise floor.** With 100 items and a score of 0.85, the 95% CI is about ±7 pp (1.96·√(0.85·0.15/100)). Compare configs *paired* on the same items with a bootstrap, and never gate a release on a 1–2 pp delta.

### LLM-as-judge: a measuring instrument with known biases

| Bias / pitfall | What happens | Mitigation |
| --- | --- | --- |
| Position bias | In pairwise comparisons the judge prefers the first (or second) option | Score both orders; count only consistent verdicts |
| Verbosity bias | Longer answers score higher | Claim-level metrics; length-controlled rubrics |
| Self-preference | A judge rates outputs from its own model family higher | Use a judge from a different family than the generator |
| Scale drift | 1–10 scores are unstable across runs and judge versions | Binary pass/fail per claim or criterion; pin the judge model version |
| Leniency | Partial support counted as support | Few-shot rubric with borderline examples; calibrate against human labels |
| Non-determinism | Same input, different score | temperature 0, response caching, report variance over 3 runs |
| Cost | Faithfulness is roughly 2–3 judge calls per answer | Sample production traffic (1–5%); cheap deterministic checks first |

The rule that holds this together: **a judge earns the right to gate a release only after it agrees with humans**. Have SMEs label 50–100 items, compute Cohen's κ, and target ≥ 0.6. Raw agreement alone misleads. In this exercise, a judge that passes everything can show 70% agreement and κ = 0.

### Tools, and the platform equivalents

| Tool | Shape | Strength | Watch out for |
| --- | --- | --- | --- |
| Ragas 0.4 | Metric library | Research-backed RAG metrics; new `ragas.metrics.collections` API with `llm_factory` | The 0.3 → 0.4 migration broke APIs; telemetry is on by default (`RAGAS_DO_NOT_TRACK=true`); dependency pins (see the exercise) |
| DeepEval | pytest-style test framework | `assert_test` in CI, G-Eval custom metrics, synthesizer, red-teaming | Pushes you toward the Confident AI cloud for dashboards |
| TruLens | Tracing + feedback functions | RAG triad on live traces, OpenTelemetry-based, Snowflake-backed | Heavier footprint; best paired with its own tracing |
| Arize Phoenix | Observability + evals | OTel/OpenInference tracing, eval dashboards, self-hostable | A platform to run, not just a library |
| Cloud-native | Managed | watsonx.governance (faithfulness, answer/context relevance), Azure AI Foundry RAG evaluators, Bedrock RAG evaluation, Vertex Gen AI eval | Lock-in; judge prompts are opaque, so check whether you can calibrate them |

## Trade-offs & decision table

| Approach | Pick when | Failure modes | Cost / latency |
| --- | --- | --- | --- |
| Deterministic IR metrics | Always, as the first layer; tuning chunking, k, embeddings, reranker | Needs relevance labels; blind to generation quality | ~free; milliseconds |
| String / regex checks | Citation format, abstention phrase, PII leaks, JSON validity | Brittle to wording changes | ~free |
| Reference-based LLM judge | Offline regression in CI against a golden set | Golden set staleness; judge bias | 2–4 judge calls per item; minutes per run |
| Reference-free LLM judge | Online monitoring on sampled traffic | Can't detect “confidently wrong but grounded” | Pay per sampled trace; run async, off the request path |
| Small/local judge (7–8B) | Dev loop, data-residency constraints, high volume | Invalid JSON, low κ on nuanced claims | No per-token cost; GPU-bound |
| Frontier judge | Release gates, calibration baseline | Cost; data leaving the boundary | Higher per-token cost; rate limits |
| Fine-tuned evaluator / Granite Guardian-style model | Very high volume, stable domain | Needs labelled data; drifts with the domain | Cheap at inference; up-front training cost |
| Human review | Calibration sets, high-risk domains (legal, medical, compliance) | Slow; inter-annotator disagreement | Most expensive; days |

## Hands-on: a layered eval harness on your laptop

**Goal (75 min):** evaluate a small BM25 RAG over 10 policy chunks with a 12-item golden set (two paraphrased traps, two unanswerable). Compare `k=1` and `k=3` using deterministic metrics plus Ragas faithfulness, context precision and context recall, judged by a *different* local model family. Then calibrate the judge against your own labels.

### Setup (PowerShell, no WSL needed)

**PowerShell**

```powershell
# 1. Install Ollama for Windows from https://ollama.com/download, then pull the models
ollama pull llama3.2:3b      # generator (system under test)
ollama pull qwen2.5:7b       # judge - deliberately a different model family

# 2. Project + venv (Python 3.13)
mkdir day11-rag-eval; cd day11-rag-eval
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1   # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
python -m pip install --upgrade pip
python -m pip install "ragas==0.4.3" "langchain-community==0.4.1" rank-bm25 openai

# 3. Optional: make sure Ollama is serving
curl.exe http://localhost:11434/v1/models
```

Why the pin

I tested this on 20 Oct 2026: `ragas 0.4.3` fails to import with the newest `langchain-community 0.4.2` (`No module named 'langchain_community.chat_models.vertexai'`). Pinning `0.4.1` fixes it. Fragile eval-tool dependencies are themselves an architecture concern, so isolate the eval toolchain in its own environment or container, separate from the serving image.

VRAM: `qwen2.5:7b` at Q4 is about 4.7 GB and fits your 6 GB RTX 3050. Ollama swaps models between the generate and judge steps, which costs a few seconds per row. If it's too slow, set `$env:JUDGE_MODEL="qwen2.5:3b"` and watch what that does to κ in stretch 1. To use a free-tier API instead, set `LLM_BASE_URL`, `LLM_API_KEY` and the model names to any OpenAI-compatible endpoint.

### rag\_eval.py

**rag_eval.py** · `lab/rag_eval.py`

```python
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
```

### Run it

**PowerShell**

```powershell
python rag_eval.py
```

**Expected output** (your numbers will differ; the shape will look like this):

k=1 | How many days of leave can I carry over into | hit=1.0 | 'You can carry over up to 30 days [hr-01].'
...
k=3 | If my notebook goes missing, who do I tell an | hit=0.0 | "I don't know."
k=3 | What is the company's dental insurance limit? | hit=None | "I don't know."
=== Summary ===
k hit@k MRR cite abst faith cprec crec p50 s
1 0.8 0.8 ~0.9 ~1.0 ~0.9 ~0.8 ~0.8 ~1.5
3 0.8 0.8 ~0.9 ~0.9 ~0.8 ~0.6 ~0.8 ~2.0
Wrote eval\_results.csv

**What to notice.** BM25 gets hit@k = 0.8 at both k values, because the two paraphrased questions (“notebook goes missing”, “two-factor… remotely”) share no keywords with the chunks. Raising k does not fix a vocabulary gap. Context precision should *drop* at k=3, since more noise sits in the window, while recall stays flat. That is the precision/recall trade-off reranking addresses on Day 9. Look at any row where faithfulness is below 1.0 and decide for yourself whether the judge was right.

### Stretch goals

1. **Calibrate the judge.** Open `eval_results.csv`, fill `human_faithful` with 1 or 0 for every answered row by your own reading, then run the script below. If κ < 0.6, try the 3B judge and a frontier free-tier judge, and record which one you would let gate CI.
   **calibrate.py** · `lab/calibrate.py`

```python
"""Stretch 1 - is your judge trustworthy? Compare Ragas faithfulness with your own 1/0 labels."""
import csv

rows = [r for r in csv.DictReader(open("eval_results.csv", encoding="utf-8"))
        if r["faithfulness"] and r["human_faithful"] in ("0", "1")]
judge = [1 if float(r["faithfulness"]) >= 0.8 else 0 for r in rows]   # threshold = your release bar
human = [int(r["human_faithful"]) for r in rows]

n = len(rows)
po = sum(j == h for j, h in zip(judge, human)) / n                    # observed agreement
pj, ph = sum(judge) / n, sum(human) / n
pe = pj * ph + (1 - pj) * (1 - ph)                                    # agreement expected by chance
kappa = (po - pe) / (1 - pe) if pe < 1 else float("nan")
fp = sum(j == 1 and h == 0 for j, h in zip(judge, human))             # judge passed a hallucination

print(f"n={n}  agreement={po:.2f}  Cohen's kappa={kappa:.2f}  judge-passed-but-human-failed={fp}")
print("Rule of thumb: kappa >= 0.6 before you let this judge gate a release.")
```
2. **Fix the misses and prove it.** Run `ollama pull nomic-embed-text`, add a dense retriever via `client.embeddings.create(model="nomic-embed-text", input=[...])`, fuse it with BM25 using RRF (score = Σ 1/(60 + rank)), and re-run. The paraphrase rows should flip to hit = 1. Then add a paired-bootstrap delta (1,000 resamples) so you can say whether the improvement is real at n = 12. At this size it usually isn't, which is the lesson about golden-set size.

## GitHub repos to study

- [vibrantlabsai/ragas](https://github.com/vibrantlabsai/ragas)  
  Formerly explodinggradients/ragas. Read `src/ragas/metrics/collections/faithfulness` to see the claim-decomposition and NLI prompts you are trusting, and the testset generator.
- [confident-ai/deepeval](https://github.com/confident-ai/deepeval)  
  Look at the pytest integration (`assert_test`, `deepeval test run`) and G-Eval. This is the pattern for eval gates on Day 26.
- [truera/trulens](https://github.com/truera/trulens)  
  Study the RAG-triad feedback functions and how they attach to OpenTelemetry spans. This is online evaluation on real traces.
- [Arize-ai/phoenix](https://github.com/Arize-ai/phoenix)  
  Look at `phoenix.evals` and the experiments/datasets feature: versioned golden sets plus eval runs in a dashboard (Day 27 preview).

## Read / watch

- [Ragas: migrate from v0.3 to v0.4](https://docs.ragas.io/en/latest/howtos/migrations/migrate_from_v03_to_v04/): the current metric API (collections, `llm_factory`, `ascore`). Read this before trusting any older tutorial.
- [A Survey on LLM-as-a-Judge](https://arxiv.org/abs/2411.15594) (Gu et al.): the bias taxonomy and reliability strategies. Pair it with [Self-Preference Bias in LLM-as-a-Judge](https://arxiv.org/abs/2410.21819).
- [Azure AI Foundry: RAG evaluators](https://learn.microsoft.com/azure/foundry/concepts/evaluation-evaluators/rag-evaluators) and [AWS: Bedrock knowledge base evaluation](https://aws.amazon.com/blogs/machine-learning/evaluating-rag-applications-with-amazon-bedrock-knowledge-base-evaluation/): how the hyperscalers package the same metrics. Useful for client conversations about managed versus open-source evaluation.

## Interview questions

**Your faithfulness score is 0.94 but users still complain about wrong answers. What's going on and how do you diagnose it?**

Faithfulness only measures *support by the retrieved context*, not correctness. A high score with wrong answers means the retriever is fetching wrong-but-plausible evidence: stale policy versions, the wrong region's document, or a near-duplicate. Check context recall and answer correctness on a golden set, slice by document freshness and metadata, and check whether ACL or metadata filters are missing. The fix is usually in retrieval (filters, dedup, versioning), not the prompt.

**How do you build a golden set for a new domain with no query logs?**

Seed it with SME-written questions per document type and intent, augment with synthetic generation (Ragas testset / DeepEval Synthesizer) for coverage of multi-hop and comparison cases, and have SMEs review every synthetic item. Include 10–15% unanswerable and some adversarial items. Label relevance at doc + span level so it survives re-chunking. Version and freeze it, keep a hidden hold-out, and replace synthetic items with real failures once traffic starts.

**Would you let an LLM judge gate production releases? Defend it.**

Yes, conditionally. The judge is an instrument and has to be calibrated: binary per-claim verdicts, a different model family from the generator, a pinned model version and prompt hash, temperature 0, and κ ≥ 0.6 against 50–100 human labels, re-checked whenever the judge changes. Gate on *paired deltas* against main with a threshold above the noise floor, not on absolute scores. Keep deterministic checks as the first gate and humans in the loop for high-risk domains.

**Offline vs online evaluation: what runs where, and why?**

Offline: reference-based metrics (hit@k, context precision/recall, correctness) on a versioned golden set in CI. It's deterministic in scope, comparable across commits, and acts as a merge gate. Online: reference-free metrics (groundedness, context and answer relevance) computed asynchronously on 1–5% of sampled traces, plus user feedback and escalation signals. That catches drift and new query types the golden set doesn't cover. Online failures are triaged into the golden set, which closes the loop.

**Retrieval changes improved context recall by 6 pp on 80 questions. Do you ship?**

Not on that number alone. At n = 80 the 95% CI on a proportion near 0.8 is about ±9 pp, so run a paired bootstrap on per-item scores to see whether the delta's interval excludes zero. Also check that faithfulness, latency p95 and cost per query didn't regress, and look at per-slice results, since an aggregate gain can hide a loss on one business unit. If it's still ambiguous, grow the golden set or run a shadow / A-B test with online metrics.

## Architect's lens: ADR-011

Title

**Layered evaluation with a calibrated judge as the release gate for the enterprise knowledge assistant**

Context

The RAG assistant serves HR, IT and finance policy to around 20k users. Teams change chunking, embeddings and prompts weekly, and two regressions reached production last quarter unnoticed. Data residency rules out sending documents to external judge APIs for some business units. The eval budget is under 5% of serving cost.

Options

A) Manual spot checks before each release. B) A managed platform evaluator (watsonx.governance / Azure AI Foundry) only. C) Layered: deterministic IR and format checks, then a Ragas/DeepEval judge on a versioned golden set in CI, then a reference-free triad on 2% of sampled production traces, with a self-hosted judge from a different family than the generator, calibrated quarterly against SME labels.

Decision

**C.** The CI gate blocks a merge if faithfulness drops by more than 3 pp or hit@5 by more than 2 pp against main, paired on a 250-item golden set. The judge is pinned by model version and prompt hash, and must hold κ ≥ 0.6 against 100 SME-labelled items to stay the gate. Production failures flow into the golden set every month.

Consequences

Positive: regressions get caught pre-merge, metrics map directly to the fix (retriever versus generator), and it stays residency-compliant. Negative: SME labelling effort (~2 days per quarter), GPU capacity for the judge, and a golden-set curation owner is required. Risks: the judge drifts on upgrade (mitigated by re-calibrating before any judge change), and over-fitting to the golden set (mitigated by a hidden hold-out slice). Managed evaluators remain an option for business units without residency limits.

## Tomorrow

Day 12 · Multimodal RAG: tables, images, scanned docs, vision LLMs, and document parsers (Docling, Unstructured).

## Lab files in this folder

- [`lab/rag_eval.py`](lab/rag_eval.py)
- [`lab/calibrate.py`](lab/calibrate.py)
