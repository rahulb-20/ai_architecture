"""Command line: `aiarch ask|agent|eval|estimate|serve`. Works offline with defaults."""
from __future__ import annotations

import argparse
import json
import sys

from aiarch.adapters.telemetry.tracer import JsonTracer


def _principal(args: argparse.Namespace):
    from aiarch.domain.models import Principal
    groups = frozenset(g.strip() for g in args.groups.split(",") if g.strip()) | {"all"}
    return Principal(args.user, args.tenant, groups)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="aiarch")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("ask", "agent"):
        p = sub.add_parser(name)
        p.add_argument("text")
        p.add_argument("--tenant", default="acme")
        p.add_argument("--groups", default="all")
        p.add_argument("--user", default="cli")
        p.add_argument("--trace", action="store_true", help="print spans")
    sub.add_parser("eval").add_argument("--gate", action="store_true")
    est = sub.add_parser("estimate", help="monthly token-cost estimate with sensitivity")
    est.add_argument("--users", type=int, default=50_000)
    est.add_argument("--input-per-m", type=float, default=3.0)
    est.add_argument("--output-per-m", type=float, default=15.0)
    est.add_argument("--prefix-cache-hit", type=float, default=0.0)
    est.add_argument("--semantic-cache-hit", type=float, default=0.0)
    srv = sub.add_parser("serve")
    srv.add_argument("--host", default="127.0.0.1")
    srv.add_argument("--port", type=int, default=8000)
    a = ap.parse_args(argv)

    if a.cmd in ("ask", "agent"):
        from aiarch.container import build_container
        spans: list[dict] = []
        c = build_container(tracer=JsonTracer(sink=spans))
        if a.cmd == "ask":
            ans = c.rag.ask(a.text, _principal(a))
            print(json.dumps({"status": ans.status.value, "answer": ans.text,
                              "citations": [x.doc_id for x in ans.citations], "model": ans.model,
                              "prompt": ans.prompt_version, "cost_usd": ans.cost_usd,
                              "reasons": ans.reasons}, indent=2))
        else:
            r = c.agent.run(a.text, _principal(a))
            print(json.dumps({"final": r.final, "stopped": r.stopped_reason,
                              "steps": [s.tool for s in r.steps]}, indent=2, default=str))
        if a.trace:
            for s in spans:
                print(f"  span {s['name']:<18} {s['duration_ms']:>8} ms  {s['status']}")
        return 0
    if a.cmd == "eval":
        from aiarch.evaluation.runner import main as eval_main
        return eval_main(["--gate"] if a.gate else [])
    if a.cmd == "estimate":
        from aiarch.services.cost import ModelPrice, Workload, monthly_estimate, sensitivity
        w = Workload(users=a.users, prefix_cache_hit_rate=a.prefix_cache_hit,
                     semantic_cache_hit_rate=a.semantic_cache_hit)
        price = ModelPrice(a.input_per_m, a.output_per_m)
        print(json.dumps({"estimate": monthly_estimate(w, price),
                          "sensitivity_plus_20pct": sensitivity(w, price)}, indent=2))
        return 0
    if a.cmd == "serve":
        import uvicorn
        uvicorn.run("aiarch.api.app:app", host=a.host, port=a.port)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
