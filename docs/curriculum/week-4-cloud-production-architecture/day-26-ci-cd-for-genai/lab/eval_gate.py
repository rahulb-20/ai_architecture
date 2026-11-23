"""Eval gate: score a prompt version on the golden set and compare to the production baseline.

Exit code 0 = gate passed, 1 = regression (blocks the PR / deployment).
Usage:  python eval_gate.py --prompt ticket_triage --alias candidate
        python eval_gate.py --prompt ticket_triage --alias production --write-baseline
"""
import argparse, json, statistics, sys
from pathlib import Path
from llm import chat

ROOT = Path(__file__).parent
ABSTAIN = "i don't have enough information"
# Price card per 1M tokens, so the gate also reports cost. Local Ollama is free; these
# placeholder numbers stand in for whatever hosted model you would run in production.
PRICE_IN, PRICE_OUT = 0.15, 0.60

GATE = {
    "min_pass_rate": 0.80,        # absolute floor
    "max_pass_drop": 0.05,        # vs production baseline
    "min_schema_rate": 1.00,      # structured output must never break
    "max_p95_ratio": 1.30,        # latency budget vs baseline
    "max_cost_ratio": 1.25,       # cost budget vs baseline
}


def load_prompt(name: str, alias: str) -> tuple[str, dict]:
    reg = json.loads((ROOT / "prompts" / "registry.json").read_text(encoding="utf-8"))[name]
    version = reg["aliases"].get(alias, alias)  # accept an alias or a raw version id
    return version, reg["versions"][version]


def score_case(case: dict, raw: str) -> dict:
    exp = case["expect"]
    try:
        out = json.loads(raw)
        schema_ok = all(k in out for k in ("category", "priority", "answer", "cited_ids"))
    except json.JSONDecodeError:
        return {"schema_ok": False, "passed": False, "reason": "invalid JSON"}
    if not schema_ok:
        return {"schema_ok": False, "passed": False, "reason": "missing keys"}

    cited = set(out["cited_ids"])
    checks = {
        "category": out["category"] == exp["category"],
        "no_invented_ids": cited <= set(case["context"]),          # groundedness, cheap form
    }
    if exp.get("abstain"):
        checks["abstained"] = ABSTAIN in out["answer"].lower() and not cited
    else:
        checks["cites_expected"] = set(exp.get("cited", [])) <= cited
    if "priority" in exp:
        checks["priority"] = out["priority"] == exp["priority"]
    failed = [k for k, v in checks.items() if not v]
    return {"schema_ok": True, "passed": not failed, "reason": ", ".join(failed) or "ok"}


def run(name: str, alias: str) -> dict:
    version, p = load_prompt(name, alias)
    cases = [json.loads(l) for l in (ROOT / "data" / "golden.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    rows = []
    for c in cases:
        ctx = "\n".join(f"{k}: {v}" for k, v in c["context"].items())
        messages = [{"role": "system", "content": p["system"]},
                    {"role": "user", "content": p["template"].format(context=ctx, ticket=c["ticket"])}]
        r = chat(p["model"], messages, case=c)
        rows.append({"id": c["id"], **score_case(c, r["content"]), **{k: r[k] for k in ("latency_ms", "in_tokens", "out_tokens")}})

    lat = sorted(r["latency_ms"] for r in rows)
    p95 = lat[min(len(lat) - 1, round(0.95 * (len(lat) - 1)))]
    cost = sum(r["in_tokens"] * PRICE_IN + r["out_tokens"] * PRICE_OUT for r in rows) / 1e6
    return {
        "prompt": name, "version": version, "model": p["model"], "n": len(rows),
        "pass_rate": round(sum(r["passed"] for r in rows) / len(rows), 3),
        "schema_rate": round(sum(r["schema_ok"] for r in rows) / len(rows), 3),
        "p95_latency_ms": p95, "median_latency_ms": statistics.median(lat),
        "cost_per_1k_requests_usd": round(cost / len(rows) * 1000, 4),
        "rows": rows,
    }


def gate(cand: dict, base: dict | None) -> list[str]:
    fails = []
    if cand["pass_rate"] < GATE["min_pass_rate"]:
        fails.append(f"pass_rate {cand['pass_rate']} < floor {GATE['min_pass_rate']}")
    if cand["schema_rate"] < GATE["min_schema_rate"]:
        fails.append(f"schema_rate {cand['schema_rate']} < {GATE['min_schema_rate']}")
    if base:
        if base["pass_rate"] - cand["pass_rate"] > GATE["max_pass_drop"]:
            fails.append(f"pass_rate dropped {base['pass_rate']} -> {cand['pass_rate']}")
        if cand["p95_latency_ms"] > base["p95_latency_ms"] * GATE["max_p95_ratio"]:
            fails.append(f"p95 latency {cand['p95_latency_ms']}ms > {GATE['max_p95_ratio']}x baseline")
        if base["cost_per_1k_requests_usd"] and cand["cost_per_1k_requests_usd"] > base["cost_per_1k_requests_usd"] * GATE["max_cost_ratio"]:
            fails.append(f"cost/1k ${cand['cost_per_1k_requests_usd']} > {GATE['max_cost_ratio']}x baseline")
    return fails


def report_md(cand: dict, base: dict | None, fails: list[str]) -> str:
    def b(k): return base[k] if base else "-"
    lines = [f"## Eval gate: `{cand['prompt']}` {cand['version']} ({cand['model']})",
             f"**{'FAILED' if fails else 'PASSED'}**", "",
             "| metric | baseline | candidate |", "|---|---|---|"]
    for k in ("pass_rate", "schema_rate", "p95_latency_ms", "cost_per_1k_requests_usd"):
        lines.append(f"| {k} | {b(k)} | {cand[k]} |")
    lines += ["", "| case | passed | reason |", "|---|---|---|"]
    lines += [f"| {r['id']} | {'yes' if r['passed'] else 'no'} | {r['reason']} |" for r in cand["rows"]]
    if fails:
        lines += ["", "### Blocking", *[f"- {f}" for f in fails]]
    return "\n".join(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", default="ticket_triage")
    ap.add_argument("--alias", default="candidate")
    ap.add_argument("--write-baseline", action="store_true")
    a = ap.parse_args()

    res = run(a.prompt, a.alias)
    base_path = ROOT / "baselines" / f"{a.prompt}.json"
    if a.write_baseline:
        base_path.write_text(json.dumps(res, indent=2), encoding="utf-8")
        print(f"Baseline written for {res['version']}: pass_rate={res['pass_rate']}")
        sys.exit(0)

    base = json.loads(base_path.read_text(encoding="utf-8")) if base_path.exists() else None
    fails = gate(res, base)
    md = report_md(res, base, fails)
    (ROOT / "eval_report.md").write_text(md, encoding="utf-8")
    print(md)
    sys.exit(1 if fails else 0)
