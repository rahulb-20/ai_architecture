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
