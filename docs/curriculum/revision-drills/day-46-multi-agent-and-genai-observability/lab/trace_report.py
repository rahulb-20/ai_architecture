"""Day 46 lab, part 2: turn raw spans into the numbers an architect is asked for.

Reads spans.jsonl (written by traced_team.py) and prints, per conversation:
tokens and cost by agent, the handoff path, loops, errors and unattributed
model calls. With --gate it exits 1 when a conversation breaks a rule, so the
same script works as a CI check or as an alert rule prototype.

Usage:
  python trace_report.py spans.jsonl
  python trace_report.py spans.jsonl --gate --max-cost 0.02
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Placeholder prices in USD per 1M tokens (input, output). Replace with your contract rates.
PRICES = {"fake-large": (3.00, 15.00), "fake-small": (0.25, 1.25)}
LOOP_THRESHOLD = 3   # the same agent->agent edge this many times is a loop


def load(path: Path) -> dict[str, list[dict]]:
    traces: dict[str, list[dict]] = defaultdict(list)
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            span = json.loads(line)
            traces[span["trace_id"]].append(span)
    return traces


def owning_agent(span: dict, by_id: dict[str, dict]) -> str | None:
    """Walk up the tree to the nearest invoke_agent span."""
    cur = by_id.get(span["parent_id"] or "")
    while cur is not None:
        if cur["attributes"].get("gen_ai.operation.name") == "invoke_agent":
            return cur["attributes"].get("gen_ai.agent.name")
        cur = by_id.get(cur["parent_id"] or "")
    return None


def analyse(spans: list[dict]) -> dict:
    by_id = {s["span_id"]: s for s in spans}
    root = next(s for s in spans if s["parent_id"] is None)
    tokens: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    cost: dict[str, float] = defaultdict(float)
    unattributed = 0
    for s in spans:
        a = s["attributes"]
        if a.get("gen_ai.operation.name") != "chat":
            continue
        agent = owning_agent(s, by_id)
        if agent is None:
            unattributed += 1
            agent = "(unattributed)"
        i, o = a.get("gen_ai.usage.input_tokens", 0), a.get("gen_ai.usage.output_tokens", 0)
        p_in, p_out = PRICES[a["gen_ai.request.model"]]
        tokens[agent][0] += i
        tokens[agent][1] += o
        cost[agent] += (i * p_in + o * p_out) / 1_000_000
    agents = sorted((s for s in spans if s["attributes"].get("gen_ai.operation.name") == "invoke_agent"),
                    key=lambda s: s["start_ns"])
    path = [s["attributes"]["gen_ai.agent.name"] for s in agents]
    edges = Counter(zip(path, path[1:]))
    loops = {f"{a}->{b}": n for (a, b), n in edges.items() if n >= LOOP_THRESHOLD}
    errors = [f'{s["name"]}: {s["attributes"].get("error.type", "error")}'
              for s in spans if s["status"] == "ERROR"]
    return {
        "conversation": root["attributes"].get("gen_ai.conversation.id", "?"),
        "scenario": root["attributes"].get("app.scenario", "?"),
        "context": root["attributes"].get("app.context_mode", "?"),
        "outcome": root["attributes"].get("app.outcome", "?"),
        "duration_ms": (root["end_ns"] - root["start_ns"]) / 1e6,
        "tokens": dict(tokens), "cost": dict(cost), "total_cost": sum(cost.values()),
        "path": path, "loops": loops, "errors": errors, "unattributed": unattributed,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("spans", type=Path)
    ap.add_argument("--gate", action="store_true")
    ap.add_argument("--max-cost", type=float, default=0.02, help="USD per conversation")
    args = ap.parse_args()

    failed = False
    for spans in load(args.spans).values():
        r = analyse(spans)
        print(f'\n{r["conversation"]}  scenario={r["scenario"]}  context={r["context"]}  outcome={r["outcome"]}')
        print(f'  {"agent":<16}{"in_tok":>8}{"out_tok":>9}{"cost_usd":>11}{"share":>8}')
        for agent, (i, o) in sorted(r["tokens"].items(), key=lambda kv: -r["cost"][kv[0]]):
            share = r["cost"][agent] / r["total_cost"] if r["total_cost"] else 0
            print(f'  {agent:<16}{i:>8}{o:>9}{r["cost"][agent]:>11.5f}{share:>8.0%}')
        print(f'  {"TOTAL":<16}{sum(v[0] for v in r["tokens"].values()):>8}'
              f'{sum(v[1] for v in r["tokens"].values()):>9}{r["total_cost"]:>11.5f}')
        print("  path:   " + " > ".join(r["path"]))
        problems = []
        if r["loops"]:
            problems.append(f'loop {r["loops"]}')
        if r["errors"]:
            problems.append("errors " + "; ".join(r["errors"]))
        if r["unattributed"]:
            problems.append(f'{r["unattributed"]} model call(s) with no owning agent')
        if r["total_cost"] > args.max_cost:
            problems.append(f'cost {r["total_cost"]:.5f} > budget {args.max_cost}')
        print("  checks: " + ("PASS" if not problems else "FAIL - " + " | ".join(problems)))
        failed = failed or bool(problems)
    return 1 if (args.gate and failed) else 0


if __name__ == "__main__":
    sys.exit(main())
