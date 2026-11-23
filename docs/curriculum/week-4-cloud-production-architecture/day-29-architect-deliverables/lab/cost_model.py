"""
Day 29 - Token-cost & capacity model for an enterprise GenAI assistant.
Stdlib + tiktoken only. Prices come from LiteLLM's public price map
(cached to prices.json), so the numbers move when providers change prices.

Usage:
    python cost_model.py                 # base case, prints report + writes cost_report.md
    python cost_model.py --users 50000 --adoption 0.35 --cache-hit 0.25
"""
from __future__ import annotations

import argparse
import json
import pathlib
import random
import statistics
import urllib.request
from dataclasses import dataclass, replace

import tiktoken

PRICE_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)
PRICE_CACHE = pathlib.Path(__file__).with_name("prices.json")

# Keys must exist in LiteLLM's price map. Run with --list granite to search.
MODELS = [
    "gpt-5-mini",
    "azure/gpt-4.1-mini",
    "claude-haiku-4-5",
    "claude-sonnet-4-5",
    "gemini/gemini-2.5-flash",
    "watsonx/ibm/granite-4-h-small",
]

SYSTEM_PROMPT = """You are the ACME internal knowledge assistant. Answer only from the
provided context. Cite sources as [doc_id]. If the context is insufficient,
say so and suggest who to contact. Never reveal personal data. Respond in the
user's language. Keep answers under 200 words unless asked for detail."""


@dataclass(frozen=True)
class Workload:
    users: int = 50_000
    adoption: float = 0.35          # share of employees active on a working day
    queries_per_user: float = 6.0   # per active user per day
    working_days: int = 22
    peak_factor: float = 4.0        # peak-hour QPS vs. average working-hour QPS
    working_hours: float = 9.0
    k_chunks: int = 6               # retrieved chunks per query
    chunk_tokens: int = 350
    history_tokens: int = 600       # condensed conversation memory
    query_tokens: int = 40
    output_tokens: int = 280
    rewrite_calls: float = 1.0      # extra small LLM calls (query rewrite, guard)
    rewrite_in: int = 300
    rewrite_out: int = 40
    semantic_cache_hit: float = 0.15   # answered without an LLM call
    prompt_cache_share: float = 0.0    # share of input billed at cached rate
    #  (system prompt is static -> cacheable; set e.g. 0.1 to model it)


def load_prices(refresh: bool = False) -> dict:
    if refresh or not PRICE_CACHE.exists():
        print(f"Downloading price map -> {PRICE_CACHE.name}")
        with urllib.request.urlopen(PRICE_URL, timeout=60) as r:
            PRICE_CACHE.write_bytes(r.read())
    return json.loads(PRICE_CACHE.read_text(encoding="utf-8"))


def count_tokens(text: str) -> int:
    # o200k_base is the GPT-4o/4.1/5 tokenizer. Other vendors differ by
    # roughly 10-25%; treat this as an estimate and calibrate from real logs.
    try:
        return len(tiktoken.get_encoding("o200k_base").encode(text))
    except Exception as e:  # first run downloads the BPE file; corp proxies block it
        print(f"[warn] tiktoken unavailable ({type(e).__name__}); using chars/4 heuristic")
        return max(1, len(text) // 4)


def per_query_tokens(w: Workload, system_tokens: int) -> tuple[int, int]:
    main_in = system_tokens + w.k_chunks * w.chunk_tokens + w.history_tokens + w.query_tokens
    tin = main_in + int(w.rewrite_calls * w.rewrite_in)
    tout = w.output_tokens + int(w.rewrite_calls * w.rewrite_out)
    return tin, tout


def monthly(w: Workload, price: dict, system_tokens: int) -> dict:
    tin, tout = per_query_tokens(w, system_tokens)
    q_month = w.users * w.adoption * w.queries_per_user * w.working_days
    llm_q = q_month * (1 - w.semantic_cache_hit)
    p_in = price.get("input_cost_per_token") or 0.0
    p_out = price.get("output_cost_per_token") or 0.0
    p_cached = price.get("cache_read_input_token_cost") or p_in
    in_cost_q = tin * ((1 - w.prompt_cache_share) * p_in + w.prompt_cache_share * p_cached)
    cost = llm_q * (in_cost_q + tout * p_out)
    # capacity: peak tokens-per-minute is what you size quotas/PTUs/GPUs on
    avg_qps = q_month / w.working_days / (w.working_hours * 3600)
    peak_qps = avg_qps * w.peak_factor * (1 - w.semantic_cache_hit)
    peak_tpm = peak_qps * 60 * (tin + tout)
    return {
        "queries": q_month,
        "tin": tin,
        "tout": tout,
        "cost": cost,
        "per_query": cost / q_month,
        "per_user": cost / (w.users * w.adoption),
        "peak_qps": peak_qps,
        "peak_tpm": peak_tpm,
        "fits_ctx": tin <= (price.get("max_input_tokens") or 10**9),
    }


def monte_carlo(w: Workload, price: dict, sys_t: int, n: int = 2000) -> tuple[float, float]:
    rng = random.Random(42)
    out = []
    for _ in range(n):
        s = replace(
            w,
            adoption=min(1.0, max(0.05, rng.gauss(w.adoption, w.adoption * 0.3))),
            queries_per_user=max(1.0, rng.lognormvariate(0, 0.4) * w.queries_per_user),
            output_tokens=int(max(50, rng.gauss(w.output_tokens, w.output_tokens * 0.35))),
            k_chunks=rng.choice([max(1, w.k_chunks - 2), w.k_chunks, w.k_chunks + 2]),
        )
        out.append(monthly(s, price, sys_t)["cost"])
    out.sort()
    return statistics.median(out), out[int(0.9 * n)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--users", type=int, default=50_000)
    ap.add_argument("--adoption", type=float, default=0.35)
    ap.add_argument("--cache-hit", type=float, default=0.15)
    ap.add_argument("--prompt-cache", type=float, default=0.0)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--list", metavar="TEXT", help="search model keys and exit")
    a = ap.parse_args()

    prices = load_prices(a.refresh)
    if a.list:
        print("\n".join(k for k in prices if a.list.lower() in k.lower()))
        return

    w = Workload(users=a.users, adoption=a.adoption,
                 semantic_cache_hit=a.cache_hit, prompt_cache_share=a.prompt_cache)
    sys_t = count_tokens(SYSTEM_PROMPT)
    tin, tout = per_query_tokens(w, sys_t)

    lines = [
        "# Token-cost model",
        f"Users {w.users:,} | adoption {w.adoption:.0%} | {w.queries_per_user} q/user/day | "
        f"semantic-cache hit {w.semantic_cache_hit:.0%}",
        f"System prompt = {sys_t} tokens (o200k_base or heuristic). "
        f"Per query: {tin:,} in / {tout:,} out",
        "",
        "| Model | $/month | $/query | $/active user | P50 | P90 | peak TPM |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for m in MODELS:
        p = prices.get(m)
        if not p:
            lines.append(f"| {m} | not in price map (try --list) | | | | | |")
            continue
        r = monthly(w, p, sys_t)
        p50, p90 = monte_carlo(w, p, sys_t)
        flag = "" if r["fits_ctx"] else " ⚠ ctx"
        lines.append(
            f"| {m}{flag} | {r['cost']:,.0f} | {r['per_query']:.5f} | {r['per_user']:.2f} | "
            f"{p50:,.0f} | {p90:,.0f} | {r['peak_tpm']:,.0f} |"
        )

    # Sensitivity: which lever moves cost most? (on the first model)
    base_p = prices[MODELS[0]]
    base = monthly(w, base_p, sys_t)["cost"]
    lines += ["", f"## Sensitivity on {MODELS[0]} (each lever -25%)", "| Lever | Δ cost |", "|---|---:|"]
    for field in ["k_chunks", "chunk_tokens", "output_tokens", "history_tokens", "queries_per_user"]:
        v = getattr(w, field)
        nv = type(v)(v * 0.75) if isinstance(v, float) else max(1, int(v * 0.75))
        c = monthly(replace(w, **{field: nv}), base_p, sys_t)["cost"]
        lines.append(f"| {field} {v}→{nv} | {(c - base) / base:+.1%} |")

    report = "\n".join(lines)
    print(report)
    pathlib.Path("cost_report.md").write_text(report, encoding="utf-8")
    print("\nWrote cost_report.md")


if __name__ == "__main__":
    main()
