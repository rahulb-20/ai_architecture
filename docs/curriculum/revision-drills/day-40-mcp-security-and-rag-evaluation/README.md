# Day 40 — Revision Drill: MCP Security & RAG Evaluation

> Part of the [GenAI Architect study path](../../README.md) · Revision drill · [Original interactive pack](https://claude.ai/artifact/2xaWz7VTVxPzcKYTwA4EuV) · [Offline HTML](../../archive/html/day-40.html)


GenAI Architect prep · Day 40 · Revision drill · Wed 18 Nov 2026

# MCP security under a stateless protocol, and RAG evaluation you can defend

RAG evaluation (Day 11) has not been drilled yet. MCP (Day 20) comes back for a second pass, this time on approval flows, gateway trust and cache boundaries. The scenario joins them: how do you prove a tool-using assistant is safe to release?

MCP spec 2026-07-28mcp 2.3.0ragas 0.4.4deepeval 4.3.4

## Why this matters for the JD

The JD asks for evaluation (groundedness, relevance, faithfulness, latency and cost), MCP and tool calling, guardrails, and eval dashboards. Panels tend to probe the seams: who approved this tool call, what your judge misses, and which number stops a release.

## What to have current

### MCP

- Results carry a required `resultType`: `complete` or `input_required`.
- Streamable HTTP POSTs carry `MCP-Protocol-Version` and `Mcp-Method`, plus `Mcp-Name` on named calls.
- Tool parameters can be mirrored to headers with `x-mcp-header`.
- List results require `ttlMs` and `cacheScope`.

### Ragas

- New code imports from `ragas.metrics.collections`.
- The docs say the legacy metric API is due for removal in 1.0.
- Faithfulness needs no reference. `ContextPrecision` and `ContextRecall` do.
- The PyPI project now points to `vibrantlabsai/ragas` as its code home.

## Eight interview questions

Answer each aloud in under 90 seconds before opening the model answer.

Q1 · MCP

A tool that amends a customer record needs human approval. Walk through the flow on a stateless MCP server. How do you stop tampering, replay and double execution?

**Model answer**

The first `tools/call` returns an `InputRequiredResult` with an elicitation in `inputRequests` and a `requestState`. The client collects the input and retries the original call with a new JSON-RPC id, the `inputResponses` and the exact `requestState`. Any replica can handle the retry.

The spec treats `requestState` as attacker-controlled. Protect it with AEAD or HMAC, and bind it to the principal, a short expiry, and the method plus a digest of the parameters. Those bindings limit replay but do not guarantee single use, so the write needs a server-side ledger: an idempotency key with a unique constraint, committed in the same transaction as the change.

The design point most candidates miss: a form-mode answer arrives through the client, so a compromised or auto-approving client can fabricate it. For high-risk writes, use URL-mode elicitation to a server-owned page that authenticates the user and shows the exact change. Record approval against the parameter digest. The client learns the outcome by retrying.

Follow-up: the spec marks URL mode as liable to change. Keep the approval service behind your own interface so the protocol binding can move.

Q2 · MCP

Your gateway authorizes calls using the `Mcp-Name` header so it never parses bodies. A security reviewer calls this a bypass. Are they right?

**Model answer**

They are right if the backend can disagree with the gateway. A request can say `read_invoice` in the header and `delete_invoice` in the body: the gateway allows the first and the server runs the second. The spec closes this at the server, which must reject a mismatch with `400` and `HeaderMismatch` (`-32020`), after decoding any Base64-encoded values.

So header-based policy is sound only under conditions I can verify:

- Every backend passes a mismatch test in CI, including encoded names.
- The gateway checks `MCP-Protocol-Version` and rejects older or missing versions, as the spec recommends, because those requests carry no validation guarantee.
- Backends cannot be reached except through the gateway.
- The gateway handles coarse policy. The server still does object-level authorization.

For a small set of destructive tools I would also parse the body at the gateway. The latency cost is small and the blast radius justifies it.

Follow-up: keep secrets and personal data out of `x-mcp-header` parameters. Mirrored values land in places that log headers.

Q3 · MCP

Tool lists differ by role. You want the gateway to cache `tools/list` to cut latency. What can go wrong, and what is the correct cache design?

**Model answer**

If a role-filtered list is marked `public`, a shared cache may serve it across authorization contexts, so a junior user learns about admin tools, or loses tools they should have. Role-dependent lists must be `private`, which limits reuse to the same authorization context. Key the cache on that context, and use `public` only for lists that are identical for everyone.

Three further rules:

- A cached list is never an authorization decision. `tools/call` re-checks entitlement, because a revoked tool can stay listed until `ttlMs` expires.
- Hash tool definitions on each refresh. A changed description goes to review before it reaches the model, which also covers rug pulls.
- Keep tool order deterministic. The spec recommends it, and it protects the LLM prompt-cache prefix.

Follow-up: set the TTL from how long you can tolerate a stale list. Treat revocation latency as a call-time concern.

Q4 · MCP + evaluation

A team edits one MCP tool description and tool selection quietly degrades. How do you evaluate an MCP tool surface, and what belongs in the release gate?

**Model answer**

Tool descriptions are prompts, so a description change gets the same gate as a prompt change. Snapshot `tools/list`, version it, and run evaluation when its hash changes.

I score four layers against a labelled scenario set:

- **Selection:** correct tool, including "no tool" and "ask the user".
- **Arguments:** schema validity first, then semantic match on the fields that matter.
- **Trajectory:** required steps present, forbidden steps absent, and a step budget.
- **Outcome:** final state checked against a sandboxed system. This outranks the trajectory, since several paths may be valid.

Hard gates are zero forbidden writes and zero unapproved writes. Selection and outcome get non-inferiority margins. I also track the tokens the tool schemas consume, because a growing catalogue costs latency and hurts selection. Past a modest catalogue size, retrieve tools per request and evaluate that retrieval too.

Q5 · RAG evaluation

Faithfulness averages 0.96, yet users report wrong answers. How is that possible, and how do you find the cause?

**Model answer**

Faithfulness is the share of response claims supported by the *retrieved* context. It says nothing about whether that context was the right one. A clean answer drawn from a superseded policy scores perfectly.

I split the failure by pipeline stage:

- **Retrieval:** ID-based recall and precision against labelled evidence. No LLM involved, so it is cheap and stable.
- **Corpus:** version and effective-date checks on what was cited. Stale or conflicting sources show up here.
- **Generation:** correctness against a reference, plus citation support.
- **Behaviour:** abstention precision and recall on unanswerable questions.
- **Metric:** inspect claim extraction. If it drops the risky claim, the score never sees it.

Then I slice by intent and document type. An average of 0.96 can hide a failing segment, and complaints usually cluster.

Q6 · RAG evaluation

Your LLM judge agrees with human reviewers 88% of the time and gives the same answer on 97% of reruns. Would you trust it as a release gate?

**Model answer**

Not yet. With imbalanced labels, a judge that says "supported" nearly every time gets high agreement. I want Cohen's kappa and, above all, recall on the unsupported class, since that is the failure the gate exists to catch. Today's exercise shows 88% agreement with kappa near 0.32 and only about 28% of unsupported claims found.

Repeatability is a separate property. A 2026 study of 21 judges across about 541,000 judgments found exact match overstated agreement by 33 to 41 points against kappa on MT-Bench. Two judges had test-retest above 0.95 alongside position bias above 0.10, and rankings moved up to 14 places between benchmarks. Those were general judging benchmarks, so I take the method from it and measure on my own domain.

My protocol: 200 to 400 claims labelled by two subject experts, with disagreements adjudicated; report kappa and per-class recall with intervals; randomize order; pin the judge model and prompt; recalibrate when either changes. Deterministic checks run first, and the judge handles only what they cannot decide.

Q7 · RAG evaluation

The candidate's mean score is about 0.03 lower and its confidence interval overlaps the baseline's. The team says the difference is noise. What is your gate?

**Model answer**

Overlapping intervals are the wrong test when both versions ran on the same questions. Question difficulty dominates the variance, and pairing removes it. I bootstrap the per-question differences and apply a non-inferiority rule: the lower bound of the delta must stay above a margin the business agreed, such as −0.02.

Precision sets the golden-set size. For a half-width of 0.02 with a paired-difference SD of 0.10, n ≈ (1.96 × 0.10 / 0.02)² ≈ 96. A single pass/fail rate near 90% has an SD of 0.30, so the same precision needs about 860 cases. That is a good argument for paired, continuous scores.

Then gate by segment, because a small regulated segment can fail while the average looks acceptable. Segment gates multiply false alarms, so I keep a few pre-registered critical segments as blocking and treat the rest as warnings. Safety invariants such as access-control leaks get no margin: one failure blocks.

Q8 · RAG evaluation

How do you keep a golden set trustworthy for two years while the corpus, users and permissions keep changing?

**Model answer**

Treat it as a versioned product with an owner. Each case stores the question, intent, segment, persona, expected evidence by document ID and version, the reference answer, its reviewer and a review date.

- **Corpus change:** when a cited document changes, flag dependent cases for review. Run gates against a pinned corpus snapshot so a model change and a content change are not confounded.
- **Drift:** sample production traces by segment, with extra weight on low-confidence answers, negative feedback and escalations. Mask personal data before anyone reviews them.
- **Overfitting:** keep a holdout that prompt authors cannot see, and rotate it.
- **Synthetic data:** useful for coverage, but it stays out of blocking gates until a person has reviewed it.
- **Permissions:** run cases per persona. Add negative cases where the correct result is no restricted chunk retrieved, asserted deterministically.

The set itself holds sensitive content, so it gets the same access control and retention rules as its source data.

## Design scenario · 25 minutes on paper

**A general insurer wants a claims copilot for 12,000 staff.** It answers policy questions from 400,000 documents, reads claim records, and can draft and submit policy endorsements. Retrieval and business actions are exposed as MCP tools so other assistants can reuse them.

- p95 time to first token under 2.5 seconds for read-only questions; 99.9% availability in business hours.
- Personal data stays in India. Access follows branch, line of business and role.
- Policy wordings change weekly, and old versions still govern old claims.
- Every submitted endorsement needs a named human approver and a full audit trail.
- Prompts, tool descriptions, indexes and models can each change independently.

**Produce:** the architecture, the identity flow, the write path, the evaluation plan with release gates, and your top three risks.

**Reference design**

![Copilot calls an MCP gateway that routes to retrieval, claims and endorsement servers. Endorsement writes need out-of-band approval. Traces feed an evaluation pipeline that ends in a release gate.](diagrams/fig-1.svg)

**Identity.** The IdP issues a token with the gateway or target server as audience. Servers exchange it for narrower downstream credentials. Branch, line of business and role become retrieval filters applied in the server, never parameters the model chooses.

**Retrieval.** Hybrid search with reranking, with effective-date and policy-version metadata. The claim's inception date selects the governing wording, so "latest" is the wrong default.

**Write path.** The model drafts. The server validates the draft and stores it with a digest. MRTR sends the user to a server-owned approval page. On retry, the server verifies the signed state and approval record, then commits once through an idempotency ledger.

| Layer | Check | Gate |
| --- | --- | --- |
| Access control | Per-persona negative retrieval cases | Zero leaks |
| Write safety | Sandboxed attempts without approval, with replay and tampered state | Zero commits |
| Retrieval | ID recall@k, correct policy version | Paired non-inferiority by line of business |
| Answer | Faithfulness, correctness, abstention | Margins set with claims leadership |
| Tools | Selection, arguments, outcome | Margins, plus a step budget |
| Operations | p95 first token, cost per resolved query | Budget thresholds in a load test |

**Eval cost, as a sizing habit.** 400 cases × 4 judge calls × 3,000 tokens = 4.8M tokens per run. At 20 runs a month that is 96M tokens. Run deterministic checks on every change and the full judged suite only when a relevant artifact changes.

**Top risks.** Wrong policy version presented confidently; a compromised client forging approval; a lenient judge letting regressions through. The first is addressed by version-aware retrieval and tests, the second by out-of-band approval, the third by calibration against expert labels.

## Today's exercise · 20 minutes

Audit a simulated lenient judge, then run a paired release gate. It uses only the standard library, so it runs on your laptop with no GPU, API key, WSL2 or Docker.

```text
New-Item -ItemType Directory -Force day40-drill | Out-Null
Set-Location day40-drill
notepad judge_audit.py   # paste the script below and save
py -3.13 judge_audit.py
```
`lab/day40_lab.py`

```python
"""Day 40 drill: audit an LLM judge, then gate a release. Standard library only."""
import random
import statistics as st

rng = random.Random(40)

def judge_metrics(human, judge):
    n = len(human)
    observed = sum(h == j for h, j in zip(human, judge)) / n
    p_h, p_j = sum(human) / n, sum(judge) / n
    expected = p_h * p_j + (1 - p_h) * (1 - p_j)
    kappa = (observed - expected) / (1 - expected)
    bad = [j for h, j in zip(human, judge) if h == 0]
    caught = sum(j == 0 for j in bad) / len(bad)
    return observed, kappa, caught

def bootstrap_ci(values, rounds=4000):
    means = sorted(
        st.fmean(rng.choices(values, k=len(values))) for _ in range(rounds)
    )
    return means[int(0.025 * rounds)], means[int(0.975 * rounds)]

def clamp(x):
    return max(0.0, min(1.0, x))

# Part 1: 400 claims labelled by humans (1 = supported, 0 = unsupported).
# The simulated judge is lenient: it misses most unsupported claims.
human = [1 if rng.random() < 0.85 else 0 for _ in range(400)]
judge = [
    (1 if rng.random() < 0.97 else 0) if h else (0 if rng.random() < 0.35 else 1)
    for h in human
]
acc, kappa, caught = judge_metrics(human, judge)
print("JUDGE AUDIT (n=400 claims)")
print(f"  exact agreement          {acc:.3f}")
print(f"  Cohen's kappa            {kappa:.3f}")
print(f"  unsupported claims found {caught:.3f}")

# Part 2: 150 golden questions, same set scored on both releases.
# The candidate regresses only on the 'policy' segment (20% of traffic).
rows = []
for i in range(150):
    segment = "policy" if i % 5 == 0 else "general"
    base = clamp(rng.gauss(0.84, 0.12))
    drop = 0.12 if segment == "policy" else 0.0
    cand = clamp(base - drop + rng.gauss(0, 0.03))
    rows.append((segment, base, cand))

base = [r[1] for r in rows]
cand = [r[2] for r in rows]
diffs = [c - b for b, c in zip(base, cand)]
b_lo, b_hi = bootstrap_ci(base)
c_lo, c_hi = bootstrap_ci(cand)
d_lo, d_hi = bootstrap_ci(diffs)

print("\nRELEASE GATE (n=150 paired questions, margin -0.02)")
print(f"  baseline mean {st.fmean(base):.3f}  CI [{b_lo:.3f}, {b_hi:.3f}]")
print(f"  candidate mean {st.fmean(cand):.3f}  CI [{c_lo:.3f}, {c_hi:.3f}]")
print(f"  intervals overlap: {c_hi >= b_lo and b_hi >= c_lo}")
print(f"  paired delta {st.fmean(diffs):+.3f}  CI [{d_lo:+.3f}, {d_hi:+.3f}]")
print(f"  overall gate: {'PASS' if d_lo >= -0.02 else 'FAIL'}")

for name in ("general", "policy"):
    seg = [c - b for s, b, c in rows if s == name]
    lo, hi = bootstrap_ci(seg)
    verdict = "PASS" if lo >= -0.02 else "FAIL"
    print(f"  {name:<8} n={len(seg):<3} delta {st.fmean(seg):+.3f}  "
          f"CI [{lo:+.3f}, {hi:+.3f}]  {verdict}")
```

### Expected output

```text
JUDGE AUDIT (n=400 claims)
  exact agreement          0.882
  Cohen's kappa            0.318
  unsupported claims found 0.275

RELEASE GATE (n=150 paired questions, margin -0.02)
  baseline mean 0.834  CI [0.816, 0.852]
  candidate mean 0.807  CI [0.788, 0.826]
  intervals overlap: True
  paired delta -0.028  CI [-0.037, -0.019]
  overall gate: FAIL
  general  n=120 delta -0.002  CI [-0.006, +0.002]  PASS
  policy   n=30  delta -0.130  CI [-0.141, -0.120]  FAIL
```

### Then do this

1. Explain aloud why the intervals overlap while the gate fails.
2. Raise the judge's catch rate from `0.35` to `0.75`. Note how much kappa moves compared with exact agreement.
3. Stretch: replace the simulated labels with 30 claims you hand-label from your compliance bot's answers, and score them with your Granite judge prompt through Ollama.

## Read if you have time

- [MCP: Multi Round-Trip Requests](https://modelcontextprotocol.io/specification/2026-07-28/basic/patterns/mrtr). Read the server requirements on `requestState`.
- [MCP: Streamable HTTP](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http). Read the server validation section and the note for intermediaries.
- [Reliability without Validity](https://arxiv.org/abs/2606.19544) (Norman, Rivera, Hughes, 2026), on LLM judges.

## Tomorrow

Day 41 revision drill: multimodal RAG (Day 12) with LangGraph checkpointing and recovery (Day 17).

## Sources

- [MCP 2026-07-28 key changes](https://modelcontextprotocol.io/specification/2026-07-28/changelog)
- [MCP Multi Round-Trip Requests](https://modelcontextprotocol.io/specification/2026-07-28/basic/patterns/mrtr)
- [MCP Streamable HTTP](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)
- [MCP elicitation specification source](https://raw.githubusercontent.com/modelcontextprotocol/modelcontextprotocol/main/docs/specification/2026-07-28/client/elicitation.mdx)
- [MCP 2026-07-28 schema](https://raw.githubusercontent.com/modelcontextprotocol/modelcontextprotocol/main/schema/2026-07-28/schema.ts)
- [Ragas Faithfulness](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/faithfulness/)
- [Ragas Context Recall](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_recall/)
- [Ragas Context Precision](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_precision/)
- [Reliability without Validity (arXiv 2606.19544)](https://arxiv.org/abs/2606.19544)
- PyPI: [mcp](https://pypi.org/project/mcp/), [ragas](https://pypi.org/project/ragas/), [deepeval](https://pypi.org/project/deepeval/)

## Lab files in this folder

- [`lab/day40_lab.py`](lab/day40_lab.py)
