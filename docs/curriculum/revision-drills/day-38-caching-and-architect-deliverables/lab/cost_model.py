"""Token-cost model for a RAG assistant, with prompt caching and a semantic cache.
Prices below are ILLUSTRATIVE placeholders (USD per 1M tokens). Replace with your
provider's current price sheet before using the numbers in a review."""
from dataclasses import dataclass, replace

@dataclass
class Workload:
    users: int = 50_000
    active_ratio: float = 0.30          # share of users active on a working day
    queries_per_active_user: float = 4
    working_days: int = 22
    llm_calls_per_query: float = 1.6    # rewrite + answer, occasional retry/agent step
    static_prefix_tok: int = 2_500      # system prompt + tool schemas + few-shot
    retrieved_tok: int = 3_000          # top-k chunks after rerank
    user_tok: int = 120                 # question + short history
    output_tok: int = 350

@dataclass
class Pricing:
    input_per_m: float = 3.00
    output_per_m: float = 15.00
    cache_read_mult: float = 0.10       # e.g. 0.1x base input on a prefix-cache hit
    cache_write_mult: float = 1.25      # e.g. 1.25x for a 5-minute cache write

def monthly_cost(w: Workload, p: Pricing, prefix_hit: float = 0.0, sem_hit: float = 0.0):
    queries = w.users * w.active_ratio * w.queries_per_active_user * w.working_days
    calls = queries * (1 - sem_hit) * w.llm_calls_per_query
    static_eff = w.static_prefix_tok * (prefix_hit * p.cache_read_mult
                                        + (1 - prefix_hit) * (p.cache_write_mult if prefix_hit else 1.0))
    in_tok = calls * (static_eff + w.retrieved_tok + w.user_tok)
    out_tok = calls * w.output_tok
    cost = in_tok / 1e6 * p.input_per_m + out_tok / 1e6 * p.output_per_m
    return queries, cost

if __name__ == "__main__":
    w, p = Workload(), Pricing()
    scenarios = [("Baseline (no caching)", 0.0, 0.0),
                 ("Prompt caching, 90% prefix hits", 0.9, 0.0),
                 ("+ semantic cache, 15% hits", 0.9, 0.15),
                 ("+ trim retrieved context to 1,800 tok", 0.9, 0.15)]
    base = None
    for name, ph, sh in scenarios:
        ww = replace(w, retrieved_tok=1_800) if "trim" in name else w
        q, c = monthly_cost(ww, p, ph, sh)
        base = base or c
        print(f"{name:<40} queries/mo={q:>10,.0f}  cost/mo=${c:>9,.0f}  ({c/base:6.1%} of baseline)  per query=${c/q:.4f}")
    # Sensitivity: which knob moves cost most? (+20% on each)
    print("\nSensitivity of baseline cost to +20% on each input:")
    _, c0 = monthly_cost(w, p)
    for f in ["llm_calls_per_query", "static_prefix_tok", "retrieved_tok", "output_tok", "queries_per_active_user"]:
        _, c1 = monthly_cost(replace(w, **{f: getattr(w, f) * 1.2}), p)
        print(f"  {f:<26} {c1/c0-1:+6.1%}")
