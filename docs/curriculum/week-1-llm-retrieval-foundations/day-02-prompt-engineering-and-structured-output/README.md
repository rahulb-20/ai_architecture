# Day 2 — Prompt Engineering & Structured Output

> Part of the [GenAI Architect study path](../../README.md) · Llm Retrieval Foundations · [Original interactive pack](https://claude.ai/artifact/7tPunrvfJX8iDjgGKUwnoM) · [Offline HTML](../../archive/html/day-02.html)


Day 2 / 30Week 1 · LLM & retrieval foundationsSun 11 Oct 2026

# Prompt engineering & structured output

Treat the prompt as versioned code and the output as a typed contract. Today covers JSON schema, constrained decoding, few-shot, templates and versioning, and when each one actually holds up in production.

{ **"prompt"**: "triage@v2#878718ba", **"decoder"**: "json\_schema", **"validator"**: "pydantic 2.14", **"reask"**: 1 }

## Why this matters for the JD

The JD's first must-have names *prompting, token limits and hallucination control*. Every enterprise use case it lists (doc Q&A, copilots, workflow automation, support) ends with an LLM handing data to a system that expects a shape. Structured output is where an LLM becomes a dependable component. The "preferred" lines on *multi-provider integration* and *prompt versioning* come down to one question: how do you keep the same contract across OpenAI, Anthropic, Gemini, watsonx and open-source models?

## Concept brief

### The reliability ladder

There are four levels of guarantee. Each costs a little more and promises a little more:

1. **Prompt-only.** "Respond in JSON." This is a request, not a guarantee. Small models wrap the JSON in prose or ```` ```json ```` fences, drop keys, or invent enum values.
2. **JSON mode** (`format="json"`, `response_format={"type":"json_object"}`). The output is valid JSON with *no shape guarantee*. It removes parse errors but leaves schema errors.
3. **Schema-constrained decoding.** At each step the decoder masks out tokens that would break a grammar compiled from your JSON Schema. Required keys, types and enums are guaranteed *by construction*. Hosted examples: OpenAI `text.format` with `strict: true` and Anthropic `output_config.format`. Self-hosted: Ollama `format=<schema>`, and vLLM/SGLang with XGrammar as the default backend.
4. **Constrained decoding + semantic validation + re-ask.** A grammar can't express "summary ≤ 20 words", "end\_date after start\_date" or "the product must exist in the CMDB". Pydantic validators catch those cases, and the error goes back to the model for one bounded retry. Instructor and PydanticAI implement this pattern.

![Pipeline: versioned prompt template, LLM with grammar mask over logits, JSON parse, Pydantic validation, with a re-ask loop on validation failure.](diagrams/fig-1.svg)

Grammar for syntax, validators for semantics, evals for correctness. Three separate layers, and none of them replaces the others.

### What constrained decoding does not guarantee

- **Completion.** If the output hits `max_tokens`, the JSON is cut off. The mask keeps the prefix valid but cannot close it. Always check the stop reason.
- **Refusals.** OpenAI returns a separate `refusal` field. Other providers may return a stop reason or plain text. Treat a refusal as its own case and don't try to parse it.
- **Correct values.** A model under a strict schema will fill required fields with plausible values even when the input has nothing to support them. That is hallucination through the schema. Make "unknown" a legal value (nullable or an enum member) so the model can abstain. This links to Day 4.
- **Reasoning quality.** [Tam et al. 2024](https://arxiv.org/abs/2408.02442) reported reasoning drops under strict format rules. The [.txt rebuttal](https://blog.dottxt.ai/say-what-you-mean.html) showed most of the drop came from prompt mismatch and how answers were parsed. In practice: put a `reasoning` field *before* the label fields, since keys are generated in schema order, or think first and format in a second step.

### Prompt templates and versioning

In production a prompt is a deployable artifact, just like a model. The minimum discipline:

- **Immutable versions plus movable labels.** Use `triage@v2` with a content hash, and point a `production` label at a version. Rollback means moving the label, not redeploying. Langfuse, LangSmith and watsonx.ai prompt template assets (`PromptTemplateManager` in `ibm-watsonx-ai`) all follow this model.
- **Record the prompt version, model ID and schema hash on every trace.** Without all three you can't explain a regression.
- **Gate changes on a golden set.** A prompt edit is a code change and goes through the same eval CI (covered on Day 26).
- **Keep few-shot examples in the template, version-controlled.** Two or three examples covering the edge cases (the P1-vs-P2 boundary, a ticket with PII) usually help more than extra rules. Rotate or trim them with the token budget from Day 1 in mind.

**Provider map (Oct 2026).** OpenAI: `responses.parse(text_format=Model)` → `output_parsed`. Anthropic: `messages.parse(output_format=Model)` → `parsed_output`, sent on the wire as `output_config.format`; the older `output_format` parameter needs a beta header and is deprecated. Strict tool use is a separate `strict: true` flag. Ollama: `chat(format=Model.model_json_schema())`. watsonx.ai: prompt templates are governed assets; check per-model JSON/`response_format` support in the chat API before relying on it.

## Trade-offs & decision table

| Option | Pick when | Failure modes | Cost / latency |
| --- | --- | --- | --- |
| Prompt-only JSON | Prototyping, or a provider/model with no structured mode | Fences and prose around the JSON, missing keys, invented enums, silent drift after model upgrades | baseline Hidden cost: retries and parser code |
| JSON mode | Free-form objects, or when the schema is too dynamic to compile | Valid JSON in the wrong shape; can loop on whitespace if the prompt never mentions JSON | ≈ baseline |
| Strict schema (hosted) | Extraction, classification, routing and any machine-consumed output on OpenAI, Claude or Gemini | Schema-subset 400s (Claude rejects `minimum`/`maxLength`; OpenAI wants every key required); truncation; plausible-but-wrong values | low Schema tokens count as input; first request for a new schema may pay a grammar-compile delay |
| Grammar engine (self-hosted) | vLLM/SGLang/TGI or Ollama on-prem, regulated data, custom grammars (regex, EBNF, SQL subsets) | Slow compile on huge or deeply nested schemas; backend quirks across engines; tokenizer edge cases | low XGrammar mask overhead is near zero per token after compile |
| Tool / function calling | The model must *choose* among several actions, each with its own args | Wrong tool chosen; args valid but semantically wrong; no tool called | medium Tool definitions add to the prompt on every call |
| Validate + re-ask (Instructor, PydanticAI) | Cross-field and business rules a grammar can't express | Retry storms, latency spikes, a retry that "fixes" one field and breaks another | 1–3× on failure Cap retries, and alert on retry rate as an SLO |
| Two-pass (reason → format) | Hard reasoning where format pressure hurts accuracy | Twice the calls; the formatter can misread the reasoning | ≈ 2× Use a small, cheap model for the format pass |

**Review trap:** "We use structured outputs, so the data is clean." Structured outputs guarantee the *shape*. Field-level accuracy still needs a golden set and an eval number.

## Hands-on: the structured-output bench (≈75 min)

Build a helpdesk ticket-triage extractor and measure four strategies on the same golden set: prompt-only, JSON mode, schema-constrained, and schema + re-ask. You'll end with real numbers on parse rate, schema validity, field accuracy and latency on your own GPU. That is the evidence you'd bring to a design review.

**Runs on**RTX 3050 6 GB · 16 GB RAM · native Windows (no WSL/Docker)

**Models**`qwen3:4b` (2.5 GB) and `granite4:3b` (2.1 GB), both 4-bit and fully on GPU

**Libraries**ollama 0.6.3 · pydantic 2.14.0 (latest on PyPI today)

### Setup (PowerShell)

**powershell**

```powershell
# 1. Install Ollama (or use the installer from ollama.com/download)
winget install Ollama.Ollama
ollama pull qwen3:4b
ollama pull granite4:3b

# 2. Project + venv
mkdir day02-structured; cd day02-structured
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1      # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
python -m pip install --upgrade pip
python -m pip install "ollama>=0.6.3" "pydantic>=2.14"

# 3. Save the script below as triage_bench.py, then:
python triage_bench.py              # qwen3:4b
python triage_bench.py granite4:3b  # IBM Granite 4 micro
```

### Starter code: triage\_bench.py

**python 3.13** · saved as `lab/triage_bench.py`

```python
"""Day 2 - Structured output bench: prompt-only vs JSON mode vs schema-constrained vs schema+reask.

Run:  python triage_bench.py                 (default model qwen3:4b)
      python triage_bench.py granite4:3b     (compare IBM Granite 4 micro)
"""
import hashlib
from importlib.metadata import version
import json
import re
import statistics
import sys
import time
from typing import Literal

import ollama
from pydantic import BaseModel, Field, ValidationError, field_validator

MODEL = sys.argv[1] if len(sys.argv) > 1 else "qwen3:4b"
MAX_REASK = 2

# ---------- 1. The contract: one Pydantic model is the single source of truth ----------
class Ticket(BaseModel):
    category: Literal["billing", "access", "bug", "feature_request", "other"]
    priority: Literal["P1", "P2", "P3"]
    product: str = Field(description="System named in the ticket, or 'unknown'")
    pii_present: bool = Field(description="True if an email, phone number or employee ID appears")
    summary: str = Field(description="One sentence, at most 20 words")

    # Semantic rule a grammar cannot enforce -> caught by the validator, fixed by re-ask.
    @field_validator("summary")
    @classmethod
    def short_summary(cls, v: str) -> str:
        if len(v.split()) > 20:
            raise ValueError(f"summary has {len(v.split())} words; must be <= 20")
        return v

SCHEMA = Ticket.model_json_schema()

# ---------- 2. Versioned prompt templates (in real life: a registry, not a dict) ----------
PROMPTS = {
    "triage@v2": (
        "You are an L1 triage assistant for an enterprise IT helpdesk.\n"
        "Rules:\n"
        "- P1 = outage or security risk affecting many users; P2 = one user blocked; P3 = everything else.\n"
        "- category is one of: billing, access, bug, feature_request, other.\n"
        "- pii_present is true if the ticket contains an email, phone number or employee ID.\n"
        "- product is the system named, or \"unknown\".\n"
        "- summary is one sentence, at most 20 words.\n"
        "Respond ONLY with JSON matching this schema:\n{schema}\n\nTicket:\n{ticket}"
    ),
}
PROMPT_ID = "triage@v2"
PROMPT_HASH = hashlib.sha256(PROMPTS[PROMPT_ID].encode()).hexdigest()[:8]

# ---------- 3. A tiny golden set (expand to 30+ for anything you'd report) ----------
GOLDEN = [
    ("SAP Concur is down for the whole Pune office since 9am, nobody can submit expenses.",
     {"category": "bug", "priority": "P1", "pii_present": False}),
    ("I was charged twice for my Copilot licence this month. Ref emp ID E104522.",
     {"category": "billing", "priority": "P3", "pii_present": True}),
    ("Locked out of Okta after password reset, can't do any work. Call me on +91 98200 11223.",
     {"category": "access", "priority": "P2", "pii_present": True}),
    ("Would be great if the Jira dashboard could export to Excel.",
     {"category": "feature_request", "priority": "P3", "pii_present": False}),
    ("Suspicious login alerts on the VPN for 40+ users in finance, possible credential stuffing.",
     {"category": "access", "priority": "P1", "pii_present": False}),
    ("ServiceNow form throws a 500 error when I attach a PDF over 5 MB. priya.k@example.com",
     {"category": "bug", "priority": "P3", "pii_present": True}),
    ("Where do I find the holiday calendar for 2027?",
     {"category": "other", "priority": "P3", "pii_present": False}),
    ("Invoice INV-8831 from Snowflake shows the wrong cost centre, finance needs it fixed.",
     {"category": "billing", "priority": "P3", "pii_present": False}),
]

STRATEGIES = ["prompt_only", "json_mode", "schema", "schema+reask"]
THINK = False if MODEL.startswith("qwen3") else None  # turn off Qwen3 "thinking" for extraction

def call(messages, fmt):
    kwargs = dict(model=MODEL, messages=messages, format=fmt,
                  options={"temperature": 0, "num_ctx": 4096, "num_predict": 300})
    if THINK is not None:
        kwargs["think"] = THINK
    return ollama.chat(**kwargs).message.content

def strip_fences(text: str) -> str:
    m = re.search(r"\{.*\}", text, re.S)  # prompt-only output often wraps JSON in prose/```json
    return m.group(0) if m else text

def run_one(ticket: str, strategy: str):
    prompt = PROMPTS[PROMPT_ID].format(schema=json.dumps(SCHEMA), ticket=ticket)
    messages = [{"role": "user", "content": prompt}]
    fmt = {"prompt_only": None, "json_mode": "json"}.get(strategy, SCHEMA)
    attempts = MAX_REASK + 1 if strategy == "schema+reask" else 1
    parsed_json, err = False, None
    for i in range(attempts):
        raw = call(messages, fmt)
        try:
            data = json.loads(strip_fences(raw))
            parsed_json = True
            return Ticket.model_validate(data), parsed_json, i, None
        except (json.JSONDecodeError, ValidationError) as e:
            err = e
            # Re-ask: feed the validator's error back - the Instructor pattern, by hand.
            messages += [{"role": "assistant", "content": raw},
                         {"role": "user", "content": f"Your JSON failed validation:\n{e}\nReturn corrected JSON only."}]
    return None, parsed_json, attempts - 1, err

def main():
    print(f"model={MODEL}  prompt={PROMPT_ID}#{PROMPT_HASH}  ollama-python={version('ollama')}  pydantic={version('pydantic')}")
    call([{"role": "user", "content": "ping"}], None)  # warm-up: load weights into VRAM
    rows = []
    for strategy in STRATEGIES:
        lat, ok_json, ok_schema, retries, correct = [], 0, 0, 0, {"category": 0, "priority": 0, "pii_present": 0}
        for ticket, gold in GOLDEN:
            t0 = time.perf_counter()
            obj, parsed, n_retry, err = run_one(ticket, strategy)
            lat.append(time.perf_counter() - t0)
            ok_json += parsed
            retries += n_retry
            if obj:
                ok_schema += 1
                for k in correct:
                    correct[k] += getattr(obj, k) == gold[k]
            else:
                print(f"  [{strategy}] FAIL: {str(err).splitlines()[0][:90]}")
        n = len(GOLDEN)
        rows.append((strategy, ok_json / n, ok_schema / n, correct["category"] / n, correct["priority"] / n,
                     correct["pii_present"] / n, statistics.mean(lat), max(lat), retries))

    hdr = f"{'strategy':<14}{'json%':>7}{'valid%':>8}{'cat':>6}{'prio':>6}{'pii':>6}{'mean_s':>8}{'max_s':>7}{'reasks':>8}"
    print("\n" + hdr + "\n" + "-" * len(hdr))
    for r in rows:
        print(f"{r[0]:<14}{r[1]:>7.0%}{r[2]:>8.0%}{r[3]:>6.0%}{r[4]:>6.0%}{r[5]:>6.0%}{r[6]:>8.2f}{r[7]:>7.2f}{r[8]:>8}")

if __name__ == "__main__":
    main()
```

### Expected output

Exact numbers depend on the model, quantization and driver. The sample below is illustrative, not a measured result.

**illustrative**

```text
model=qwen3:4b  prompt=triage@v2#878718ba  ollama-python=0.6.3  pydantic=2.14.0
  [prompt_only] FAIL: Expecting value: line 1 column 1 (char 0)
  [json_mode] FAIL: 1 validation error for Ticket

strategy        json%  valid%   cat  prio   pii  mean_s  max_s  reasks
----------------------------------------------------------------------
prompt_only       88%     88%   75%   62%   88%    1.9    3.4       0
json_mode        100%     88%   75%   62%   88%    1.6    2.2       0
schema           100%    100%   88%   62%  100%    1.5    2.0       0
schema+reask     100%    100%   88%   62%  100%    1.7    3.1       1
```

Things to look for:

1. Valid JSON doesn't mean a valid object; compare `json%` with `valid%` in the JSON-mode row.
2. The schema row reaches 100% valid, but **priority accuracy barely changes**. Constrained decoding fixes shape, not judgment.
3. Re-ask only does something when a validator catches a *semantic* rule, such as the 20-word summary.
4. Granite 4 vs Qwen3: which one is better calibrated on P1 vs P2? Write one line on why that matters for a support SLA.

### Stretch goals

1. **Field-order experiment.** Add `reasoning: str` to `Ticket`, first as the first field and then as the last. Re-run and compare `prio`. Keys are generated in schema order, so reasoning-first should help the borderline P1/P2 tickets. Write up the result as evidence for or against the "Let Me Speak Freely" claim.
2. **Provider parity + eval gate.** Add a second `call()` using Anthropic `client.messages.parse(output_format=Ticket)` or OpenAI `client.responses.parse(text_format=Ticket)`. Note which schema features each provider rejects or rewrites. Then make the script `sys.exit(1)` if `valid% < 100` or `cat < 0.85`. That exit code is your first CI eval gate (Day 26).

## GitHub repos to study

- [567-labs/instructor](https://github.com/567-labs/instructor)~14k★ · v1.17.0 on PyPI, Sep 2026  
  Study the retry/re-ask loop and the `Mode` abstraction. It shows one way to map a single Pydantic contract onto tools, JSON mode and native structured output across providers.
- [dottxt-ai/outlines](https://github.com/dottxt-ai/outlines)~15.9k★ · v1.3.3, Aug 2026  
  Read how a JSON Schema becomes a regex/FSM, and the backend adapters (vLLM, Ollama, transformers, llama.cpp). This is the clearest code for "how constrained decoding works".
- [mlc-ai/xgrammar](https://github.com/mlc-ai/xgrammar)~1.9k★ · v0.2.8, Sep 2026 · XGrammar-2 in May 2026  
  The default structured-generation backend in vLLM, SGLang and TensorRT-LLM. Look at the pushdown-automaton design and the context-independent token cache, which explain why per-token overhead is near zero.
- [guidance-ai/guidance](https://github.com/guidance-ai/guidance)~21.8k★  
  Interleaved generation and control flow (select, regex, gen inside templates). It's a useful contrast to the "one schema per call" model when you design templated, multi-field prompts.

## Read / watch

- [Anthropic: Structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs). Read the supported/unsupported JSON Schema list; it is the most explicit description of a provider schema subset you'll find. Also covers `output_config.format` vs strict tool use.
- [OpenAI: Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs). Covers `text.format` with `strict`, `parse()` helpers, and refusal handling.
- ["Let Me Speak Freely?" (Tam et al.)](https://arxiv.org/abs/2408.02442) together with [.txt's "Say What You Mean" rebuttal](https://blog.dottxt.ai/say-what-you-mean.html). Read them as a pair; it's a good exercise in judging an eval methodology before you trust a headline claim.

Exercise reference: [Ollama structured outputs docs](https://docs.ollama.com/capabilities/structured-outputs) · [watsonx.ai PromptTemplateManager](https://ibm.github.io/watsonx-ai-python-sdk/v1.6.3/prompt_template_manager.html)

## Interview questions

**JSON mode, strict structured outputs and constrained decoding: what does each guarantee?**

JSON mode guarantees syntactically valid JSON and nothing more. Strict structured outputs guarantee the output matches a supplied JSON Schema (required keys, types, enums) within the provider's supported subset. Constrained decoding is the mechanism behind strict mode: the schema is compiled to a grammar and invalid tokens are masked at each step. None of them guarantee completion (truncation at max\_tokens), absence of refusals, or correct values. For those you need stop-reason checks, refusal handling, validators and evals.

**Does forcing JSON hurt model quality? How would you settle it for your use case?**

The evidence is mixed. Tam et al. reported drops on reasoning tasks, and the .txt rebuttal attributed most of that to prompt mismatch and how answers were parsed. I wouldn't rely on either; I'd measure. Run a golden-set A/B (free-text vs constrained) with identical prompts. Mitigations: put a reasoning field before the answer fields, keep schemas shallow, or split into reason-then-format with a cheap formatter model. For extraction and classification, constraints usually help because they remove the parsing failures.

**Our triage bot returns valid JSON 100% of the time, but 9% of tickets get the wrong priority. Walk me through it.**

It's a correctness problem, not a format problem. (1) Slice the errors with a confusion matrix: are they mostly on the P1/P2 boundary? (2) Check the label definitions in the prompt; vague rubrics produce vague labels. Add 2–3 few-shot examples right at the boundary. (3) Add a reasoning-first field so the model commits to evidence before the label. (4) Check that the golden labels are consistent; measure inter-annotator agreement. (5) If it's still above the SLA, route low-confidence cases (logprobs or self-reported confidence plus rules) to a human or a stronger model. Ship each change behind an eval gate with the prompt version on every trace.

**How do you version and roll out prompts across three providers?**

Templates live in a registry (Langfuse, LangSmith, watsonx prompt template assets, or git plus a hash). Versions are immutable, and labels like `staging`/`production` are movable. A prompt version is pinned to a model ID, because a prompt tuned for one model is a different artifact on another. Each change runs per-provider golden-set evals in CI, then a canary (5–10% of traffic) with online metrics: validity, retry rate, accuracy proxies, cost per call. Rollback means moving the label. Every trace carries the prompt version, model ID and schema hash.

**Design a multi-provider abstraction for structured output when each provider supports a different schema subset.**

Keep one canonical Pydantic model as the contract. Per-provider adapters derive a *wire schema*: they drop or relocate unsupported keywords (e.g. move `maxLength` into the description for Claude, make every key required plus nullable for OpenAI strict, emit the full schema to XGrammar on vLLM). After the response, always validate against the *full canonical* model, so constraints the provider couldn't enforce are still checked, with one bounded re-ask. Add contract tests per adapter in CI, and use a gateway (LiteLLM or similar, Day 23) for routing and fallback. Fallback should only go to providers that pass the same golden set.

## Architect's lens: ADR-007

Structured output strategy for the ticket-triage service · Status: Accepted

Context

The triage service feeds ServiceNow routing. It must run on Azure OpenAI (primary), Claude (secondary) and Granite on vLLM on-prem for regulated business units. Malformed output currently causes ~3% dead-letter tickets. Routing SLA: P1 misclassification under 2%.

Options

- A. Prompt-only JSON plus a tolerant parser
- B. Native strict structured output per provider
- C. B plus a canonical Pydantic contract, semantic validators and a single re-ask
- D. Tool calling for all extraction

Decision

**C.** One Pydantic model is the source of truth. Adapters emit each provider's supported schema subset (XGrammar via vLLM guided decoding on-prem). Full validation runs after every call, with at most one re-ask. Each enum includes `unknown` so the model can abstain. Prompts live in a registry and are pinned per model.

Consequences

- + Dead-letter rate from format errors drops to ~0; the remaining errors are semantic and measurable.
- + Provider swap = adapter + golden-set pass, not a rewrite.
- − Adapter maintenance as provider schema support changes; needs contract tests.
- − Re-ask adds tail latency, so p95 latency and retry rate become SLOs with alerts.
- Revisit if a provider ships full JSON Schema support or retry rate exceeds 5%.

**Tomorrow · Day 3**Fine-tuning vs RAG vs prompting: a decision framework, LoRA/QLoRA/PEFT, and when InstructLab-style tuning wins.

## Lab files in this folder

- [`lab/triage_bench.py`](lab/triage_bench.py)
