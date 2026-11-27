"""Day 49 lab, part A: what retries, idempotency keys and strict schemas do to a tool-calling agent.

Offline simulation, standard library only. No model, no network, no GPU.
Run:  python tool_reliability.py
"""
from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass, field

# ---- the task: a refund workflow, six tool calls, three of them write ----
STEPS = [
    ("lookup_order", False),
    ("get_customer", False),
    ("check_policy", False),
    ("create_refund", True),
    ("send_email", True),
    ("update_ticket", True),
]

# ---- failure rates (edit these; they are assumptions, not measurements) ----
P_BAD_ARGS = 0.04        # model emits arguments that fail the schema (non-strict decoding)
P_REPAIR_OK = 0.90       # model fixes the arguments after seeing the validation error
P_WRONG_CALL = 0.015     # valid schema, wrong meaning (wrong order id). Nothing below catches it.
P_TRANSIENT = 0.05       # tool call fails with a timeout or 5xx
P_ACK_LOST = 0.50        # share of write failures where the write happened and the reply was lost
TOOL_TIMEOUT_S = 3.0
MAX_RETRIES = 2
TRIALS = 8               # trials per task, for pass^k
TASKS = 400


@dataclass(frozen=True)
class Config:
    name: str
    repair: bool        # feed validation errors back to the model
    retry: bool         # retry transient tool failures with backoff
    idempotent: bool    # send an idempotency key; the back end deduplicates
    strict: bool        # constrained decoding: arguments always match the schema


@dataclass
class Trial:
    ok: bool = True
    duplicates: int = 0     # a write that happened twice
    orphans: int = 0        # a write that happened, but the agent believes it failed
    latency: float = 0.0
    llm_turns: int = 0
    committed: dict[str, int] = field(default_factory=dict)


def llm_turn(rng: random.Random, t: Trial) -> None:
    t.llm_turns += 1
    t.latency += rng.lognormvariate(math.log(1.2), 0.45)


def call_tool(rng: random.Random, t: Trial, name: str, write: bool, cfg: Config) -> bool:
    """One logical tool call, with retries. Returns True if the agent saw a success."""
    attempts = 1 + (MAX_RETRIES if cfg.retry else 0)
    for attempt in range(attempts):
        if attempt:
            t.latency += 0.5 * 2 ** (attempt - 1) + rng.uniform(0, 0.25)   # backoff + jitter
        failed = rng.random() < P_TRANSIENT
        happened = (not failed) or (write and rng.random() < P_ACK_LOST)
        if write and happened:
            already = t.committed.get(name, 0)
            if not (cfg.idempotent and already):          # same key: back end returns the first result
                t.committed[name] = already + 1
        if not failed:
            t.latency += rng.lognormvariate(math.log(0.3), 0.5)
            return True
        t.latency += TOOL_TIMEOUT_S
    return False


def run_trial(rng: random.Random, cfg: Config, difficulty: float) -> Trial:
    t = Trial()
    for name, write in STEPS:
        llm_turn(rng, t)                                   # model decides on the call
        if rng.random() < P_WRONG_CALL * difficulty:
            t.ok = False                                   # silent: schema-valid, semantically wrong
        if not cfg.strict and rng.random() < P_BAD_ARGS:
            if not cfg.repair:
                t.ok = False
                break
            llm_turn(rng, t)                               # error goes back, model tries again
            if rng.random() > P_REPAIR_OK:
                t.ok = False
                break
        if not call_tool(rng, t, name, write, cfg):
            t.ok = False
            if write and t.committed.get(name):
                t.orphans += 1
            break
    llm_turn(rng, t)                                       # final answer to the user
    t.duplicates = sum(n - 1 for n in t.committed.values() if n > 1)
    return t


def pass_hat_k(successes: list[int], n: int, k: int) -> float:
    """tau-bench's pass^k: chance that k trials of the same task all succeed, averaged over tasks."""
    return statistics.fmean(math.comb(c, k) / math.comb(n, k) for c in successes)


def evaluate(cfg: Config, seed: int = 49) -> dict[str, float]:
    rng = random.Random(seed)
    per_task, lat, turns = [], [], []
    dup = orphan = 0
    for _ in range(TASKS):
        difficulty = rng.choice([0.5, 1.0, 1.0, 4.0])       # a quarter of tasks are hard
        good = 0
        for _ in range(TRIALS):
            t = run_trial(rng, cfg, difficulty)
            clean = t.ok and t.duplicates == 0
            good += clean
            dup += t.duplicates
            orphan += t.orphans
            lat.append(t.latency)
            turns.append(t.llm_turns)
        per_task.append(good)
    runs = TASKS * TRIALS
    lat.sort()
    return {
        "pass1": pass_hat_k(per_task, TRIALS, 1),
        "pass4": pass_hat_k(per_task, TRIALS, 4),
        "pass8": pass_hat_k(per_task, TRIALS, 8),
        "dup_per_1k": 1000 * dup / runs,
        "orphan_per_1k": 1000 * orphan / runs,
        "p50": lat[len(lat) // 2],
        "p95": lat[int(len(lat) * 0.95)],
        "turns": statistics.fmean(turns),
    }


CONFIGS = [
    Config("A naive", repair=False, retry=False, idempotent=False, strict=False),
    Config("B repair+retry", repair=True, retry=True, idempotent=False, strict=False),
    Config("C B+idempotency", repair=True, retry=True, idempotent=True, strict=False),
    Config("D C+strict", repair=True, retry=True, idempotent=True, strict=True),
]

if __name__ == "__main__":
    print(f"{TASKS} tasks x {TRIALS} trials, {len(STEPS)} tool calls per task. "
          "A run passes only if it finished AND wrote nothing twice.\n")
    head = f"{'config':<18}{'pass^1':>8}{'pass^4':>8}{'pass^8':>8}{'dup/1k':>8}{'orphan/1k':>11}{'p50 s':>7}{'p95 s':>7}{'turns':>7}"
    print(head)
    print("-" * len(head))
    for cfg in CONFIGS:
        r = evaluate(cfg)
        print(f"{cfg.name:<18}{r['pass1']:>8.3f}{r['pass4']:>8.3f}{r['pass8']:>8.3f}"
              f"{r['dup_per_1k']:>8.1f}{r['orphan_per_1k']:>11.1f}{r['p50']:>7.1f}{r['p95']:>7.1f}{r['turns']:>7.2f}")
    print("\ndup/1k    = writes that happened twice, per 1,000 runs (duplicate refunds, duplicate emails)")
    print("orphan/1k = writes that happened while the agent reported failure, per 1,000 runs")
