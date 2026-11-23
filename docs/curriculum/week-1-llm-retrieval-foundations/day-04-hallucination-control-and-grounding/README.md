# Day 4 — Hallucination Control & Grounding

> Part of the [GenAI Architect study path](../../README.md) · Llm Retrieval Foundations · [Original interactive pack](https://claude.ai/artifact/1qW5WPtnJuxZF5FyxmPhFA) · [Offline HTML](../../archive/html/day-04.html)


Week 1 · LLM & retrieval foundations · Day 4 of 30

# Hallucination control & grounding

Citations, abstention, self-checks and constrained answers, treated as an engineering control system with measurable layers rather than a line in the system prompt.

Tue 13 Oct 2026
~25 min read · 75 min lab
Runs on RTX 3050 6 GB

## Why this matters for the JD

The JD lists **hallucination control** under must-have LLM concepts and **groundedness / faithfulness** under evaluation, and every enterprise use case it names (doc Q&A, knowledge assistants, support) fails commercially on one confident wrong answer. Responsible-AI policy enforcement in the preferred skills also depends on it. In a design review you will be asked how the system knows when it doesn't know. Your answer should name layers, thresholds and metrics.

## Concept brief

### Two kinds of wrong

**Factuality** errors contradict the world. **Faithfulness** errors contradict, or go beyond, the context you supplied. In enterprise RAG you mostly own faithfulness: the corpus is the source of truth, even when it's stale. Faithfulness errors split further:

- **Intrinsic**: the answer contradicts the passage (passage says EUR 180, answer says EUR 200).
- **Extrinsic**: the answer adds facts the passage doesn't contain. These are often plausible and correct-sounding, and harder to catch.
- **Citation hallucination**: a correct-looking claim attached to the wrong source, or a quote that doesn't exist. This is the most damaging failure in a compliance context, because the citation creates false trust.

### Where RAG hallucinations actually come from

In post-mortems, the generator is usually not the first cause. Rough order of frequency in production systems:

1. **Retrieval miss**: the right chunk wasn't in the top-k, so the model fills the gap from parametric memory.
2. **Distractors and conflicts**: two versions of a policy, or a near-match chunk from another region or product.
3. **False-premise questions**: "Why was the cap raised to 250?" invites the model to explain a fact that isn't true.
4. **Derivation**: arithmetic, date logic, or multi-hop joins across chunks. Each step is a chance to drift.
5. **Positional neglect**: long contexts where the relevant span sits in the middle.
6. **Incentives**: models are trained and benchmarked in ways that reward guessing over saying "I don't know" (Kalai et al., 2025). Abstention has to be designed into the system, because the model won't default to it.

### The control loop: prevent → constrain → verify → respond

![Diagram 1](diagrams/fig-1.svg)

Every layer has an exit to a safe response. You tune each threshold separately on a golden set and log which layer caused each abstention.

- **Prevent**: retrieval quality is the largest lever (Days 6–10). Add a score floor so weak retrieval never reaches the generator. Apply ACL filtering before retrieval, never after generation.
- **Constrain**: a prompt contract (answer only from passages, say so when you can't), temperature 0, and structured output where every claim carries a `passage_id` and a verbatim quote. Constrained decoding (Day 2) guarantees the *shape* of the output, not its truth.
- **Verify**: start cheap and deterministic, then add models.
  - Check that each quote is a substring of the passage it cites. It runs in microseconds and catches fabricated citations.
  - Run an NLI or hallucination classifier (premise = passage, hypothesis = claim). Vectara HHEM-2.1-Open runs on CPU; LettuceDetect gives token-level spans.
  - Use an LLM-as-judge for nuanced or multi-hop cases. It's slower and costlier, and it brings its own bias.
  - Sampling consistency (SelfCheckGPT-style) suits cases with no context at all. It costs N extra generations.
- **Respond**: treat abstention as a product feature. You can drop unverified claims and return a partial answer with a visible flag. You can rephrase the question and retry retrieval once. You can route to a human or a ticket. Show citations so users can verify the answer themselves.

### Native citation features

Some providers return citations natively. **Anthropic Citations** chunks documents into sentences and returns `char_location` / `page_location` spans with `cited_text`, and that cited text doesn't count as output tokens. One catch for architects: on Claude, citations and structured outputs **can't be combined** (the API returns a 400). That forces a design choice between native spans and a strict JSON contract. Managed groundedness checks exist on each major cloud:

- **Azure**: AI Content Safety groundedness detection, with reasoning mode and a correction preview.
- **AWS**: Bedrock Guardrails contextual grounding check. It scores grounding and relevance against thresholds from 0 to 0.99. It doesn't support conversational QA.
- **IBM**: watsonx.governance faithfulness, answer relevance and "unsuccessful requests" metrics. On your InstructLab/Granite stack, this is where abstention rate becomes a governed KPI.

**The review-ready sentence.** "We treat hallucination as a precision/coverage trade-off. Each verification layer has a threshold tuned on a golden set to hit a target precision. We monitor abstention rate and verifier-fail rate per layer in production, and anything that fails verification is either dropped with a visible flag or escalated."

## Trade-offs & decision table

| Technique | Catches | Added latency / cost | Failure modes | Pick when |
| --- | --- | --- | --- | --- |
| Prompt contract only | Some extrinsic additions | ≈0 | Silently ignored under pressure; no signal that it failed | Prototypes only. It's a baseline, not a control. |
| Retrieval score floor | Out-of-scope questions, retrieval misses | ≈0 | Scores aren't calibrated across embedders or rerankers; one global threshold drifts | Always. Calibrate per index, and prefer reranker scores over raw cosine. |
| Structured claims + quote check | Fabricated citations, invented quotes | µs + ~20–40% more output tokens | Paraphrased quotes rejected (needs normalisation or fuzzy match); doesn't check claim vs quote | Compliance and regulated answers where every sentence must be traceable |
| Native citations (Anthropic) | Unattributed claims; gives span-level provenance | Low: cited text not billed as output | Can't combine with strict JSON on Claude; provider lock-in; a cited span can still be misread | Claude-based doc Q&A where UX needs click-to-source |
| NLI / HHEM classifier | Intrinsic contradictions, most extrinsic additions | 20–150 ms CPU per claim | Weak on arithmetic, multi-hop and tables; domain shift; length limits on older NLI models | Default runtime gate at scale, and air-gapped deployments |
| Span detector (LettuceDetect) | Which tokens are unsupported | Low on a GPU; ModernBERT long context | Trained on RAGTruth-style data; needs domain eval | UI highlighting, targeted regeneration |
| LLM-as-judge self-check | Nuanced, multi-hop and derived claims | +1 LLM call, often 0.5–3 s | Self-preference bias; non-deterministic; same blind spots as the generator | Offline eval, sampled online audits, escalation tier for borderline NLI scores |
| Sampling consistency (SelfCheckGPT) | Confabulation without context | N× generation | Cost; consistent-but-wrong answers pass | No retrieval context (open-domain), research settings |
| Managed groundedness (Azure / Bedrock / watsonx.gov) | Ungrounded responses; some offer correction | API hop + per-call fee | Input-size caps, regional availability, English-optimised; Bedrock excludes chat QA | Already on that cloud and you want a governed, auditable control fast |

**Cost math to quote:** at 100k answers/day with 4 claims each, a CPU NLI gate is about 400k small forward passes per day, which fits on a couple of vCPUs. An LLM judge on every answer roughly doubles your LLM spend and adds a second at p95. Run the classifier on everything, and send only the ambiguous band (for example, entailment between 0.4 and 0.7) to the judge.

## Hands-on: a 4-layer grounded QA pipeline

**Goal (75 min):** build retrieval floor → schema-constrained claims → quote check → NLI entailment over a small fictional travel-policy corpus. Then compare against an unguarded baseline on five questions: three answerable, one out-of-scope and one false-premise.

### Setup (PowerShell, Windows 11)

1. Install Ollama for Windows from [ollama.com/download](https://ollama.com/download). No WSL2 or Docker needed. It uses your RTX 3050 automatically.
2. Pull the generator. `qwen3:4b` is about 2.5 GB and fits in 6 GB of VRAM with headroom. If it's slow, use `llama3.2:3b`.
3. Create a venv and install. The embedder and the NLI model run on CPU, about 600 MB downloaded from Hugging Face on first run.

**PowerShell**

```powershell
ollama pull qwen3:4b
mkdir day04; cd day04
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install "ollama>=0.6" "sentence-transformers>=5" "pydantic>=2.10" numpy
# Optional: keep torch CPU-only (smaller) - install it BEFORE sentence-transformers:
# python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
```

If `Activate.ps1` is blocked, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

### Starter code: grounded\_qa.py

**grounded_qa.py** · `lab/grounded_qa.py`

```python
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
```

### Run it

**PowerShell**

```powershell
python grounded_qa.py --baseline   # unguarded answers first; note what it invents
python grounded_qa.py              # guarded pipeline
```

### What you should see

Exact scores and wording will vary by model and version. The shape should look like this:

**expected output (illustrative)**

```text
Q: What is the hotel cap in Paris?
   retrieval: TRV-03=0.7x, ...
   -> GROUNDED  (~1500 ms)
      OK  [TRV-03] The nightly hotel cap in Paris is EUR 180.   (entail=0.9x)

Q: If the client buys me lunch abroad, what is my meal allowance?
   -> PARTIAL or GROUNDED
      OK   [TRV-01] The international daily meal allowance is EUR 90.
      DROP [TRV-01] My allowance is EUR 45.  <- not entailed (p=0.3x)   # derived claim!

Q: What is the mileage rate for using my own car?
   -> ABSTAIN  retrieval floor (top=0.4x)   # or model: not answerable

Q: Why was the Paris hotel cap raised to EUR 250 last year?
   -> GROUNDED with a corrective claim (cap is EUR 180), or ABSTAIN
      # the baseline often explains the fictional raise instead

Summary: {'GROUNDED': 2-3, 'PARTIAL': 0-1, 'ABSTAIN': 1-2}
```

**Things to notice and write down:**

- The derived EUR 45 claim is *correct* but often fails NLI. Verifiers punish derivation. That's a design decision, not a bug.
- Print the top retrieval score for all five questions and set `RETRIEVAL_FLOOR` between the in-domain and out-of-domain clusters. That's threshold calibration in miniature.
- Time each layer. NLI should be tens of milliseconds on CPU; the LLM dominates.

### Stretch goals

1. **Swap the verifier for HHEM-2.1-Open** (`AutoModelForSequenceClassification.from_pretrained("vectara/hallucination_evaluation_model", trust_remote_code=True)`, then `model.predict([(premise, hypothesis)])`). Compare its decisions with the DeBERTa NLI model on 20 hand-labelled claims and compute precision/recall for "hallucinated".
2. **Add a derivation lane:** extend `Claim` with `kind: Literal["extracted","derived"]` and `inputs: list[str]`. Verify extracted claims with NLI, and verify derived claims by re-computing them in Python from the cited numbers. That's the pattern for policy math such as allowances, SLAs and dates.

## GitHub repos to study

[vectara/hallucination-leaderboard](https://github.com/vectara/hallucination-leaderboard)

Read the methodology section: how the summarisation prompt is fixed and how hallucination rate and answer rate are reported together. Answer rate is the abstention trade-off in one number.

~3.3k ★ · updated May 2026 · HHEM-2.3 scoring

[KRLabsOrg/LettuceDetect](https://github.com/KRLabsOrg/LettuceDetect)

Look at `lettucedetect/models/inference.py` for the span-level output format and the RAGTruth training setup. It's a good template for a token-level verifier service.

~600 ★ · v0.2.x releases Jun–Jul 2026 · ModernBERT/EuroBERT

[confident-ai/deepeval](https://github.com/confident-ai/deepeval)

Compare the Faithfulness and Hallucination metric implementations. Both extract claims, then judge them against context, so you'll see the claim-decomposition prompts you'd otherwise write yourself.

~17.5k ★ · PyPI 4.2.x

[NVIDIA/NeMo-Guardrails](https://github.com/NVIDIA/NeMo-Guardrails)

Study the "self check facts" and "self check hallucination" output rails and the AlignScore integration. This is how grounding becomes declarative policy (Day 21 builds on it).

~7.3k ★ · v0.24.1

## Read / watch

- [Kalai et al., "Why Language Models Hallucinate" (2025)](https://arxiv.org/abs/2509.04664). The argument is that accuracy-only evals reward guessing, so abstention must be scored explicitly. Use it to justify why your eval scorecard penalises confident errors more than abstentions.
- [Anthropic: Citations](https://platform.claude.com/docs/en/build-with-claude/citations). Covers the request shape, the three citation location types, cost behaviour, and the incompatibility with structured outputs. A good model of provider-native grounding.
- [Azure AI Content Safety: Groundedness detection](https://learn.microsoft.com/en-us/azure/ai-services/content-safety/concepts/groundedness), read alongside the [Bedrock contextual grounding check](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-contextual-grounding-check.html). Compare the threshold semantics and limits; it's useful for a multi-cloud answer.

## Interview questions

**A compliance bot gave a wrong answer with a real-looking citation. Walk me through your triage.**

Pull the trace and classify the failure. (1) Was the correct chunk in the corpus? If not, it's a content gap. (2) Was it retrieved in the top-k? If not, it's a retrieval problem: check context recall, filters and chunking. (3) Was it retrieved but the answer contradicts it? That's a faithfulness problem: check the verifier score and why the threshold let it through. (4) Is the cited passage the wrong one? That's citation hallucination: add quote verification. Turn the case into a golden-set regression test, then fix the layer that failed rather than editing the prompt.

**How do you choose an abstention threshold, and how do you defend it to the business?**

Frame it as cost: a wrong answer in a regulated workflow might cost 50× an "I don't know" that routes to a human. On a labelled golden set, plot precision against coverage as the threshold varies, pick the point that meets the target precision (for example, ≥ 97% on answered questions), and report the coverage that leaves. Set thresholds per domain if risk differs. In production, track abstention rate and verifier-fail rate. A rising abstention rate is an early warning of corpus drift or retrieval regression.

**NLI/HHEM classifier or LLM-as-judge for runtime groundedness?**

Use both, in tiers. The classifier is cheap (tens of ms on CPU), deterministic, can run air-gapped and is easy to calibrate, but it's weak on arithmetic, tables and multi-hop reasoning. The judge handles nuance but adds a second or more, costs another LLM call, is non-deterministic, and may favour its own family's outputs. Gate everything with the classifier and send only the uncertain band to the judge. Use the judge offline for eval and calibration, and validate the judge itself against human labels.

**Claude citations and structured outputs can't be used together. How do you design around that in a multi-provider system?**

Define a provider-neutral answer contract (claims with source spans) in your gateway. For Claude, either use native citations and map `char_location` into your contract, or use structured outputs with quote fields and verify the quotes yourself. For OpenAI, Gemini and open-source models, use JSON-schema output plus your own quote and NLI verification. Downstream consumers see one schema. Record which path produced each answer for audit, and run the same eval suite across providers.

**Will fine-tuning or a bigger context window fix hallucinations?**

Not by itself. Fine-tuning on facts the model doesn't already know can increase its tendency to hallucinate (Gekhman et al., 2024). Fine-tune for behaviour, such as format, refusal style and domain tone, and keep facts in retrieval. Long context reduces retrieval misses but adds distractors and positional neglect, plus cost and latency. Neither replaces measurement: you still need faithfulness evals and runtime verification.

## Architect's lens: ADR-014 · Runtime grounding verification

Status
:   Accepted · Day 4 study ADR

Context
:   Internal policy assistant for about 20k users on watsonx.ai with Granite, plus a fallback provider. Wrong answers carry regulatory exposure. Target p95 latency ≤ 4 s and precision on answered questions ≥ 97%. One region must run air-gapped.

Options
:   **A.** Prompt contract only. **B.** LLM-as-judge on every answer. **C.** Tiered: retrieval floor, then claims with quotes, a deterministic quote check, a CPU NLI/HHEM gate, and an LLM judge only for the uncertain band. **D.** Managed cloud groundedness API.

Decision
:   **C.** A has no measurable control. B doubles LLM cost and breaks the latency budget. D fails the air-gapped requirement and ties the control to one cloud. C runs everywhere, costs about 50–150 ms for most traffic, and produces per-layer signals for watsonx.governance dashboards.

Consequences
:   - About 20–40% more output tokens because of quotes.
    - Correct derived answers (calculations) may be dropped until a derivation lane exists.
    - Thresholds need recalibration whenever the embedder, reranker or verifier changes, so this is added to the CI eval gate (Day 26).
    - Abstentions route to an HR or Compliance queue, which needs an owner and an SLA.

## Tomorrow

**Day 5: Embeddings deep dive.** Model choice with MTEB, dimensions, Matryoshka truncation, similarity metrics and domain adaptation. Today's retrieval floor depends directly on it.

## — Sources

- <https://platform.claude.com/docs/en/build-with-claude/citations>
- <https://arxiv.org/abs/2509.04664>
- <https://learn.microsoft.com/en-us/azure/ai-services/content-safety/concepts/groundedness>
- <https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-contextual-grounding-check.html>
- <https://www.ibm.com/docs/en/DSXDOC/wsj/model/wos-evaluation-metrics.html>
- <https://docs.ollama.com/capabilities/structured-outputs>
- <https://ollama.com/library/qwen3>
- <https://huggingface.co/vectara/hallucination_evaluation_model>
- <https://huggingface.co/cross-encoder/nli-deberta-v3-small>
- <https://github.com/vectara/hallucination-leaderboard>
- <https://github.com/KRLabsOrg/LettuceDetect>
- <https://github.com/confident-ai/deepeval>
- <https://github.com/NVIDIA/NeMo-Guardrails>

## Lab files in this folder

- [`lab/grounded_qa.py`](lab/grounded_qa.py)
