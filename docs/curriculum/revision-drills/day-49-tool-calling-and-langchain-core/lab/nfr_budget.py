"""Day 49 lab, part B: turn a task-level SLO into per-step numbers you can put in an HLD.

Standard library only.  Run:  python nfr_budget.py
Every input below is an assumption to replace with your own measurements and contract values.
"""
from __future__ import annotations

# ---- targets the business agreed to ----
TASK_SUCCESS_SLO = 0.95        # share of refund tasks finished correctly, 28-day window
TASK_P95_S = 20.0              # end-to-end, user-visible
TASKS_PER_DAY = 6_000
WINDOW_DAYS = 28

# ---- shape of one task ----
TOOL_CALLS = 6
LLM_TURNS = TOOL_CALLS + 1     # one turn per call plus the final answer
PARALLEL_READS = 3             # the three lookups can go out in one turn

# ---- dependencies in the serving path: (name, availability SLA) ----
SERIAL = [("api gateway", 0.9995), ("orchestrator", 0.9995), ("LLM provider", 0.999),
          ("order API", 0.999), ("payments API", 0.9995), ("email API", 0.999)]
FALLBACK_LLM = 0.999           # second provider, assumed independent
FAILOVER_DETECT = 0.98         # share of primary outages the router actually catches

# ---- token model for cost ----
CATALOGUE_TOOLS = 60           # tools registered for this assistant
TOKENS_PER_TOOL_DEF = 150
BASE_INPUT_TOKENS = 1_800      # system prompt + conversation so far
OUTPUT_TOKENS_PER_TURN = 120
PRICE_IN_PER_M = 1.00          # USD per million input tokens (illustrative)
PRICE_OUT_PER_M = 5.00         # USD per million output tokens (illustrative)
DEFERRED_TOOLS_LOADED = 6      # with tool search: definitions actually pulled in
CACHE_READ_DISCOUNT = 0.10     # cached input billed at 10% (illustrative)


def series(avails: list[float]) -> float:
    out = 1.0
    for a in avails:
        out *= a
    return out


def cost_per_task(tool_defs: int, cached_prefix: bool) -> float:
    defs = tool_defs * TOKENS_PER_TOOL_DEF
    total = 0.0
    for turn in range(LLM_TURNS):
        grow = turn * 350                              # earlier calls and results stay in context
        prefix = defs + BASE_INPUT_TOKENS
        rate = CACHE_READ_DISCOUNT if (cached_prefix and turn > 0) else 1.0
        total += (prefix * rate + grow) * PRICE_IN_PER_M / 1e6
        total += OUTPUT_TOKENS_PER_TURN * PRICE_OUT_PER_M / 1e6
    return total


if __name__ == "__main__":
    print("1. Success budget")
    per_step = TASK_SUCCESS_SLO ** (1 / TOOL_CALLS)
    budget = round((1 - TASK_SUCCESS_SLO) * TASKS_PER_DAY * WINDOW_DAYS)
    print(f"   task SLO {TASK_SUCCESS_SLO:.0%} over {TOOL_CALLS} calls needs {per_step:.4f} per call "
          f"(a 99% call gives {0.99 ** TOOL_CALLS:.1%} per task, a 97% call {0.97 ** TOOL_CALLS:.1%})")
    print(f"   error budget: {budget:,} failed tasks per {WINDOW_DAYS} days out of {TASKS_PER_DAY * WINDOW_DAYS:,}")

    print("\n2. Availability of the serving path")
    one = series([a for _, a in SERIAL])
    llm = 0.999
    llm_with_fallback = llm + (1 - llm) * FAILOVER_DETECT * FALLBACK_LLM
    two = one / llm * llm_with_fallback
    mins = WINDOW_DAYS * 24 * 60
    print(f"   single LLM provider : {one:.4%}  -> {(1 - one) * mins:,.0f} min down per {WINDOW_DAYS} days")
    print(f"   with LLM fallback   : {two:.4%}  -> {(1 - two) * mins:,.0f} min down per {WINDOW_DAYS} days")
    tools_only = series([a for n, a in SERIAL if n != "LLM provider"])
    print(f"   everything except the LLM: {tools_only:.4%}. Failover cannot lift the path above this.")
    print("\n3. Latency budget (p95, seconds)")
    llm_p95, tool_p95, overhead = 2.4, 0.9, 0.6
    serial_total = LLM_TURNS * llm_p95 + TOOL_CALLS * tool_p95 + overhead
    turns_par = LLM_TURNS - (PARALLEL_READS - 1)
    par_total = turns_par * llm_p95 + (TOOL_CALLS - PARALLEL_READS + 1) * tool_p95 + overhead
    print(f"   all sequential      : {LLM_TURNS} turns x {llm_p95} + {TOOL_CALLS} calls x {tool_p95} + {overhead} = {serial_total:.1f}"
          f"  ({'inside' if serial_total <= TASK_P95_S else 'OVER'} the {TASK_P95_S:.0f}s target)")
    print(f"   3 reads in parallel : {turns_par} turns x {llm_p95} + {TOOL_CALLS - PARALLEL_READS + 1} waits x {tool_p95} + {overhead} = {par_total:.1f}"
          f"  ({'inside' if par_total <= TASK_P95_S else 'OVER'})")
    print("   (summing p95s overstates the true p95; treat it as a safe upper bound for a first budget)")

    print("\n4. Cost per task (illustrative prices)")
    rows = [("all 60 tool definitions, no cache", cost_per_task(CATALOGUE_TOOLS, False)),
            ("all 60 definitions, cached prefix", cost_per_task(CATALOGUE_TOOLS, True)),
            ("tool search, 6 definitions loaded", cost_per_task(DEFERRED_TOOLS_LOADED, False)),
            ("tool search + cached prefix", cost_per_task(DEFERRED_TOOLS_LOADED, True))]
    base = rows[0][1]
    for label, c in rows:
        print(f"   {label:<36} ${c:.4f}/task  ${c * TASKS_PER_DAY * 30:>8,.0f}/month  ({c / base:>4.0%} of baseline)")
