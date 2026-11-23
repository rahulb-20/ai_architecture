# Day 42 — Revision Drill: Fine-tuning vs RAG & Model Serving

> Part of the [GenAI Architect study path](../../README.md) · Revision drill · [Original interactive pack](https://claude.ai/artifact/8SGFoCS6s3cTYp3mPmr7UU) · [Offline HTML](../../archive/html/day-42.html)


Revision drill · Day 42 · Fri 20 Nov 2026

# Fine-tuning vs RAG & Model Serving / Gateways

Today revisits Day 3 (fine-tuning vs RAG vs prompting, LoRA/QLoRA/PEFT, InstructLab) and Day 23 (vLLM, TGI, Ollama, LiteLLM, quotas, fallback routing). The two belong together. Once you decide to tune, you have to *serve* the tuned weights, and the gateway in front of them decides what each tenant can use.

JD: fine-tuning vs RAGJD: model endpoints · scaling · cost controlsPreferred: multi-provider LLM integration

## What changed since you studied these

### Re-check these before an interview

- **InstructLab's main repo was archived on 23 Apr 2026** and is read-only now. Your compliance bot was built on it, so expect the question "what's your exit plan?" (Q4 and the ADR below cover it).
- **TGI is in maintenance mode.** Hugging Face's README now points users to vLLM and SGLang, or to llama.cpp/MLX for local use. Stop proposing TGI for new builds.
- **LiteLLM had two supply-chain compromises this year.** In March, PyPI 1.82.7/1.82.8 were live for about 40 minutes. On 21 Oct, the hijacked release pipeline pushed a credential stealer as 1.107.0 to PyPI and to the Docker `latest`/`main-stable` tags. Current PyPI release: 1.110.1. LiteLLM images are signed with keyless cosign from v1.111.0-dev.3 onward.
- **vLLM v0.34.0** shipped 16 Nov 2026, two weeks after v0.33.0. At that pace, pin versions and re-benchmark on every upgrade. **PEFT** is at 0.22.0 on PyPI.

## Mental model in one picture

![Request flow: tenants call a gateway that checks auth, quota and cache, then routes to a self-hosted vLLM base model with LoRA adapters, or falls back to a managed provider; RAG context is added before the call.](diagrams/fig-1.svg)

Retrieval supplies the facts, a LoRA adapter shapes the behaviour, and the gateway holds the policy. Fine-tuning changes how the model answers; RAG changes what it knows at request time.

| Lever | Fixes | Doesn't fix | Serving cost shape |
| --- | --- | --- | --- |
| Prompting / few-shot | Format, tone, simple policy | Long-tail domain style; long prompts cost every call | Input tokens × every request |
| RAG | Fresh facts, citations, per-user ACLs | Output format discipline, domain reasoning style | Retrieval latency + context tokens |
| LoRA / QLoRA | Behaviour, schema adherence, tool-use, distilling a big model into a small one | Facts that change; access control; "unlearning" | One-time training + eval; adapters are tens of MB, so many can share one base |
| Full fine-tune | Deep domain shift, new language/modality | Same as LoRA, plus catastrophic forgetting risk | A dedicated deployment per model |

## hard interview questions

Answer aloud in 90 seconds first, then open the model answer.

**Q1 The client says: "Fine-tune Granite on our 40,000 policy documents so it knows them." Defend your counter-proposal.**

Separate **knowledge** from **behaviour**. Policies change, need citations, and are access-controlled per business unit. Weights can't hold an ACL. They can't cite a clause version, and they can't forget a withdrawn policy without retraining. So facts go through RAG: chunked by clause with metadata for version, effective date, and the BU that owns it, filtered by the caller's entitlements.

Tune only where prompting can't get you there. Examples: the house answer format (clause → interpretation → action), refusal style, or a small model that matches a large model's groundedness at a third of the latency. If you do tune, use RAFT-style data: questions paired with retrieved passages that include distractors, so the model learns to read context rather than memorise it.

The close: "We measure. RAG baseline first on a 200-question golden set. Tuning has to beat it on faithfulness and format adherence by an agreed margin before we pay for the training and retraining loop."

**Q2 Show me the break-even math where fine-tuning beats prompting on cost.**

Take a 2,500-token few-shot system prompt at 3M requests a month. That is 7.5B input tokens a month spent re-teaching the format. A LoRA that internalises those instructions cuts the prompt to about 300 tokens, a saving of 6.6B tokens. Prompt caching narrows the gap: cached input is often billed at a fraction of the normal rate. So compare against the *cached* price, not the list price.

Tuning costs: data curation (the expensive part), training runs, an eval suite, and a retraining cadence every time the base model or the policy changes. Add the operational cost of self-hosting, or the provider's premium for hosted custom models.

The bigger payoff is usually **distillation**. If a tuned 8B model replaces a frontier model on 70% of traffic, you cut cost per token and latency at the same time. Rule of thumb: tune for cost when the volume is high, the task is narrow and stable, and you have a golden set to prove there's no regression.

**Q3 LoRA vs QLoRA vs DoRA vs full fine-tune. Which do you pick, at what rank, and how do you prove you didn't break the model?**

**LoRA** trains low-rank matrices next to frozen weights. It is cheap, swappable, and can be served many-to-one base. **QLoRA** trains LoRA on a 4-bit NF4-quantised base, so 7–8B models fit on a single consumer or mid-range GPU. You pay with slower steps and a small quality gap, and you should evaluate the adapter on the precision you will actually serve. **DoRA** splits weights into magnitude and direction and often closes part of the gap to full fine-tuning at the same rank. **Full FT** is for deep domain or language shift only, and it brings the most forgetting and the heaviest ops.

Rank: start at r=8–16 on the attention projections and go higher only if the eval curve says so. Higher rank adds capacity and also overfitting. To prove no regression, keep three suites: domain golden set (task gain), a general capability probe such as instruction-following and safety prompts (forgetting), and format/tool-call validity rate. Every adapter version goes through the same CI eval gate as prompts.

**Q4 Your InstructLab-based compliance bot depends on a repo that was archived in April 2026. What do you do?**

First, pin down the blast radius. The **artifacts** you produced (the taxonomy, the synthetic training data, the tuned checkpoints) are still yours and still run. What you lost is upstream fixes for the **pipeline** (SDG, training, CLI). There is no outage. You have a maintenance-risk problem.

Actions: freeze and mirror the exact versions you depend on (wheels, container digests). Record an ADR. Move the parts you need onto maintained primitives: the taxonomy and SDG prompts become a versioned dataset, and training moves to PEFT/TRL (or the platform's tuning service on watsonx.ai). Re-run your golden set on the re-trained adapter before switching over.

The architectural lesson: **keep the tuning recipe portable.** Data, eval suite, and hyperparameters should be the durable assets. The tool that runs them is replaceable.

**Q5 Ten business units each want their own tuned model. How do you serve that without ten GPU deployments?**

One base model, ten LoRA adapters, served by vLLM with `--enable-lora`, `--lora-modules name=path`, `--max-loras` (adapters resident on the GPU at once), and `--max-lora-rank` set to the highest rank you ship. Continuous batching mixes requests for different adapters in the same batch. The client picks the adapter by passing its name as `model` on the OpenAI-compatible API, and the gateway maps tenant to adapter, so clients never choose one themselves.

Trade-offs: a cold adapter must load (CPU→GPU) on first use, so size `--max-loras` for the hot set. Mixed-adapter batches carry some throughput overhead compared with a merged single model. Runtime loading (`/v1/load_lora_adapter`, gated by `VLLM_ALLOW_RUNTIME_LORA_UPDATING`) is convenient, but the docs flag it as risky in production. Prefer immutable deployments that list their adapters, with rollout by canary. Merge an adapter into its own deployment only when one tenant's traffic justifies dedicated capacity or isolation.

**Q6 A team runs six TGI endpoints in production. TGI is now in maintenance mode. Give me the migration plan.**

There's no fire. Maintenance mode still gets minor fixes, so plan the move instead of rushing it. Target vLLM (or SGLang for structured or agentic-heavy workloads). Both expose OpenAI-compatible APIs, so put the gateway in front first and make the backend swappable.

1. **Parity inventory:** chat templates, stop tokens, grammar/JSON mode, quantisation format, max context, and any adapters.
2. **Benchmark on your own traffic shape:** TTFT, inter-token latency, throughput at p95 concurrency, GPU memory headroom.
3. **Shadow traffic** through the gateway. Diff outputs on the golden set. Tokeniser or template drift is the usual cause of silent quality changes.
4. **Canary** at 5% → 25% → 100%, with automatic rollback on error rate or eval-score SLOs.
5. Decommission, and record the ADR.

**Q7 Design the quota and fallback logic for a multi-tenant gateway. Where does naive fallback go wrong?**

**Quotas are measured in tokens, not requests.** One request can cost 100 tokens or 100,000. Before the call, check against a pessimistic estimate (prompt tokens + max\_tokens). After it, charge the actual usage. Keep a rolling TPM limit for fairness and a monthly budget for cost, with soft alerts at 80%.

Where naive fallback breaks:

- **Retry storms.** Every client retries, the gateway retries, and the SDK retries too. Use one retry owner, jittered backoff, and a circuit breaker per route.
- **Semantic drift.** The fallback model has different prompt sensitivity, JSON quirks, and a different tokenizer. Keep per-model prompt variants and run the fallback route through the eval gate too.
- **Mid-stream failure.** If a stream dies after tokens reach the user, you can't transparently restart. Fail the request clearly, or fall back only before the first token.
- **Compliance.** A fallback to another region or provider can break data residency (DPDP, the EU AI Act's record-keeping duties). Fallback chains must stay inside approved boundaries.
- **Cache leaks.** The cache key must include tenant and ACL scope.

**Q8 After the LiteLLM 1.107.0 compromise, what supply-chain controls do you require for your AI gateway?**

The gateway holds every provider key, so treat it as a tier-0 asset. Controls:

- **Pin with hashes** (`pip install --require-hashes`), install from a private mirror, and never use floating Docker tags like `latest` or `main-stable`. Deploy by digest.
- **Verify signatures** (cosign) and keep an SBOM in CI. Hold new versions for a quarantine window of a few days before promoting them.
- **Default-deny egress:** the gateway pod reaches only the provider endpoints, so an infostealer can't phone home.
- **Short-lived credentials:** workload identity instead of static keys, secrets pulled from a vault, and narrowly scoped IAM roles.
- **Detection:** watch for unexpected `.pth`/`sitecustomize.py` files and unknown processes. Alert on credential use from new IPs (the incident post says to check CloudTrail for `GetCallerIdentity`).
- **Runbook:** if a bad version ran, rebuild from scratch rather than uninstalling, and rotate every key the gateway could read.

## Design scenario (30 min, whiteboard it)

**Brief.** An Indian private bank wants a shared GenAI platform for 12 business units. Requirements: data stays in India (DPDP Act plus RBI outsourcing expectations). Three units want "their own tuned model" (credit memos, collections scripts, HR policy). Budget: 4 × L40S on-prem plus one managed provider for overflow. Peak load: 150 concurrent users. p95 TTFT under 1.5 s for chat.

**Deliver:** serving topology, tuning vs RAG decision per unit, gateway policy, fallback rules, capacity estimate, and the two riskiest assumptions.

**→ Model solution outline**

1. **Default to RAG and prompting for all 12 units.** Only credit memos get a LoRA, for format discipline and domain reasoning style. Collections and HR are tone and policy problems: system prompts plus RAG over versioned policies. Tune later only if the golden set says so.
2. **Serving:** 2 GPUs run an 8B-class instruct base on vLLM with multi-LoRA (credit memo adapter, later others). 1 GPU runs the embedding model and reranker. 1 GPU is headroom and canary. For the 8B base in BF16, about 16 GB of the 48 GB per L40S goes to weights and the rest to KV cache. Run a load test to find whether 150 concurrent sessions meet the TTFT target at your average context length. If they don't, quantise to FP8 or reduce max context.
3. **Gateway:** one virtual key per unit, token quotas, PII masking before any external route, tenant-scoped semantic cache, OTel GenAI traces into a cost ledger. Deploy pinned images by digest with egress restricted (see Q8).
4. **Fallback:** on-prem first. The managed provider must be in an India region with zero data retention contractually confirmed. Fall back only for units whose data is classified as allowed, and only before the first token. Credit memos never leave the bank's own infrastructure. If on-prem is down, they queue.
5. **Riskiest assumptions:** (a) the managed provider's India-region offering covers the model you need, so validate it with procurement; (b) 150 concurrent users at your real context length fit in KV cache, so prove it with a load test before sign-off.

**watsonx mapping:** watsonx.ai on-prem (Cloud Pak for Data / Software Hub) for tuning and hosting custom foundation models; watsonx.governance for the model inventory and evaluation evidence the bank's auditors will ask for.

## Optional exercise (45–60 min): a gateway you can reason about

Build a one-file gateway in front of Ollama with token quotas per tenant, an ordered fallback chain with a circuit breaker, a tenant-scoped cache, and p50/p95 latency per route. The first route points at a model that doesn't exist, so you watch the breaker trip and the fallback take over. It runs on your RTX 3050 laptop: 3B and 1.5B models, no Docker, no WSL2. It uses plain `httpx` rather than a gateway library. You can read every line, and given this year's incidents, the dependency footprint is part of the design.

**PowerShell · setup**

```powershell
# PowerShell — Ollama for Windows installed, service running on :11434
ollama pull llama3.2:3b
ollama pull qwen2.5:1.5b
mkdir gw-lite; cd gw-lite
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install "httpx==0.28.1"
# save gateway_lite.py here, then:
python gateway_lite.py
```
**gateway_lite.py** · `lab/gateway_lite.py`

```python
"""gateway_lite.py - a 150-line model gateway you can reason about.

Routes OpenAI-compatible chat calls to Ollama with:
  * per-tenant token budgets (TPM-style, sliding 60 s window)
  * ordered fallback chain with a circuit breaker per route
  * exact-match response cache
  * latency + token accounting, printed as a p50/p95 table
Run:  python gateway_lite.py
"""
from __future__ import annotations

import hashlib
import json
import statistics
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

import httpx

OLLAMA = "http://localhost:11434/v1/chat/completions"

# Route chain: first entry is deliberately broken to prove fallback works.
ROUTES = [
    {"name": "primary-missing", "model": "does-not-exist:1b"},
    {"name": "llama3.2-3b", "model": "llama3.2:3b"},
    {"name": "qwen2.5-1.5b", "model": "qwen2.5:1.5b"},
]
TENANT_TPM = {"finance": 4000, "hr": 400}   # tokens per rolling minute

@dataclass
class Breaker:
    fails: int = 0
    open_until: float = 0.0
    threshold: int = 2
    cooldown_s: float = 30.0

    def allow(self) -> bool:
        return time.monotonic() >= self.open_until

    def record(self, ok: bool) -> None:
        if ok:
            self.fails = 0
            return
        self.fails += 1
        if self.fails >= self.threshold:
            self.open_until = time.monotonic() + self.cooldown_s

@dataclass
class Gateway:
    breakers: dict = field(default_factory=lambda: defaultdict(Breaker))
    usage: dict = field(default_factory=lambda: defaultdict(deque))  # tenant -> (ts, tokens)
    cache: dict = field(default_factory=dict)
    stats: dict = field(default_factory=lambda: defaultdict(list))   # route -> latencies
    events: list = field(default_factory=list)

    def _tokens_used(self, tenant: str) -> int:
        q, now = self.usage[tenant], time.monotonic()
        while q and now - q[0][0] > 60:
            q.popleft()
        return sum(t for _, t in q)

    def chat(self, tenant: str, messages: list[dict], max_tokens: int = 120) -> dict:
        # 1. Quota check uses a *pessimistic* estimate: prompt chars/4 + max_tokens.
        est = sum(len(m["content"]) for m in messages) // 4 + max_tokens
        if self._tokens_used(tenant) + est > TENANT_TPM[tenant]:
            self.events.append((tenant, "429 quota"))
            return {"status": 429, "error": f"{tenant} over {TENANT_TPM[tenant]} TPM"}

        # 2. Cache (exact match on tenant + messages). Tenant is in the key: never share across tenants.
        key = hashlib.sha256(json.dumps([tenant, messages], sort_keys=True).encode()).hexdigest()
        if key in self.cache:
            self.events.append((tenant, "cache hit"))
            return {**self.cache[key], "cached": True}

        # 3. Walk the fallback chain.
        for r in ROUTES:
            br = self.breakers[r["name"]]
            if not br.allow():
                self.events.append((tenant, f"skip {r['name']} (breaker open)"))
                continue
            t0 = time.perf_counter()
            try:
                resp = httpx.post(OLLAMA, timeout=60, json={
                    "model": r["model"], "messages": messages,
                    "max_tokens": max_tokens, "temperature": 0,
                })
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                br.record(False)
                self.events.append((tenant, f"fail {r['name']}: {type(exc).__name__}"))
                continue
            dt = time.perf_counter() - t0
            br.record(True)
            body = resp.json()
            used = body.get("usage", {}).get("total_tokens", est)
            self.usage[tenant].append((time.monotonic(), used))   # charge actual tokens
            self.stats[r["name"]].append(dt)
            out = {"status": 200, "route": r["name"], "tokens": used, "latency_s": round(dt, 2),
                   "text": body["choices"][0]["message"]["content"].strip()}
            self.cache[key] = out
            return out
        return {"status": 503, "error": "all routes failed"}

    def report(self) -> None:
        print("\nroute            n   p50(s)  p95(s)")
        for name, lat in self.stats.items():
            lat = sorted(lat)
            p95 = lat[min(len(lat) - 1, int(0.95 * len(lat)))]
            print(f"{name:<15}{len(lat):>3}  {statistics.median(lat):>6.2f}  {p95:>6.2f}")
        for t in TENANT_TPM:
            print(f"tenant {t:<8} tokens in window: {self._tokens_used(t)} / {TENANT_TPM[t]}")
        print("\nevents:")
        for e in self.events:
            print("  ", *e)

if __name__ == "__main__":
    gw = Gateway()
    qs = [
        ("finance", "In one sentence: what is a LoRA adapter?"),
        ("finance", "In one sentence: when is RAG better than fine-tuning?"),
        ("finance", "In one sentence: what is a LoRA adapter?"),          # cache hit
        ("hr", "Summarise in 2 lines why token-based quotas beat request-based ones."),
        ("hr", "List 5 risks of running an AI gateway with every provider key in it."),
        ("hr", "Explain continuous batching to a CFO in 3 sentences."),   # likely 429
    ]
    for tenant, q in qs:
        r = gw.chat(tenant, [{"role": "user", "content": q}])
        print(tenant, r.get("status"), r.get("route", ""), r.get("latency_s", ""),
              "| cached" if r.get("cached") else "", "|", (r.get("text") or r.get("error"))[:90])
    gw.report()
```
**Expected output (latencies and text will vary)**

```text
finance 200 llama3.2-3b 2.41  | A LoRA adapter is a small set of low-rank weight ...
finance 200 llama3.2-3b 1.37  | RAG is better when knowledge changes often or must ...
finance 200 llama3.2-3b 1.37 | cached | A LoRA adapter is a small set of low-rank ...
hr 200 llama3.2-3b 1.92  | Token quotas track actual cost, since one request ...
hr 200 llama3.2-3b 2.80  | 1. Single point of compromise for all keys ...
hr 429    | hr over 400 TPM

route            n   p50(s)  p95(s)
llama3.2-3b      4    1.92    2.80
tenant finance  tokens in window: ~400 / 4000
tenant hr       tokens in window: ~330 / 400

events:
   finance fail primary-missing: HTTPStatusError
   finance fail primary-missing: HTTPStatusError
   finance cache hit
   hr skip primary-missing (breaker open)
   ...
```

### Stretch goals

1. **Fallback before first token only.** Switch to `"stream": true` and fall back only if the failure happens before the first chunk. If the stream dies mid-answer, return a clear error. Then explain to yourself why this is the only safe behaviour (Q7).
2. **Adapter routing.** Create two Ollama variants of the same base with different `SYSTEM` prompts in Modelfiles (`ollama create fin-style -f Modelfile.fin`). Map tenant → variant in the gateway. This stands in for vLLM multi-LoRA routing. Then write the three-line vLLM `serve` command you would use in production instead.

## Architect's lens: ADR-042

Title
:   Serve tenant-specific behaviour as LoRA adapters on a shared vLLM base, behind a self-operated gateway

Context
:   Several business units want customised behaviour. GPU capacity is fixed. TGI is in maintenance mode, and InstructLab's upstream is archived. Two compromises of a popular open-source gateway this year show that the gateway is a high-value target.

Options
:   (A) One merged model per tenant on dedicated GPUs. (B) Shared base plus multi-LoRA on vLLM. (C) Managed provider custom models only. (D) Prompt-only customisation.

Decision
:   D by default. B where an adapter beats D on the golden set by an agreed margin. C only as a residency-compliant overflow for data classified as allowed. The gateway runs pinned, signed images by digest, with egress restricted.

Consequences
:   + One GPU pool serves many tenants, and adapters are cheap to version and roll back. − There is cold-load latency for adapters outside the hot set. A noisy tenant can affect shared capacity, which quotas and priority classes mitigate. Each vLLM upgrade needs a re-benchmark. The tuning recipe (data, eval, hyperparameters) is kept tool-agnostic so the training stack can be replaced.

## Tomorrow

Day 43 revision drill: the next two topics in the rotation, again weighted toward weeks 3–4.

### Sources

- [TGI README: maintenance-mode notice](https://raw.githubusercontent.com/huggingface/text-generation-inference/main/README.md)
- [LiteLLM: security incident, 21 Oct 2026](https://docs.litellm.ai/blog/security-incident-oct-21-2026.md) · [security update, March 2026](https://docs.litellm.ai/blog/security-update-march-2026)
- [vLLM docs: LoRA adapters](https://docs.vllm.ai/en/v0.15.0/features/lora/) · [vLLM release tracker (v0.34.0)](https://releasealert.dev/github/vllm-project/vllm)
- Repos: [vllm-project/vllm](https://github.com/vllm-project/vllm) · [sgl-project/sglang](https://github.com/sgl-project/sglang) · [huggingface/peft](https://github.com/huggingface/peft) · [BerriAI/litellm](https://github.com/BerriAI/litellm) · [instructlab/instructlab (archived)](https://github.com/instructlab/instructlab)
- Papers: [LoRA (Hu et al.)](https://arxiv.org/abs/2106.09685) · [QLoRA (Dettmers et al.)](https://arxiv.org/abs/2305.14314)
- Package versions checked on PyPI on 20 Nov 2026: vllm 0.34.0, litellm 1.110.1, peft 0.22.0, httpx 0.28.1.

## Lab files in this folder

- [`lab/gateway_lite.py`](lab/gateway_lite.py)
