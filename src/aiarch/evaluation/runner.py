"""Golden-set evaluation and release gate.

Runs every case through the real RagService (whatever providers are configured)
and reports retrieval, grounding, safety, latency and cost metrics. `--gate` exits
non-zero when a threshold fails, which is how CI blocks a prompt/model/chunking
change that regresses quality (ADR-0007).

Metrics (deterministic; add an LLM-as-judge for semantic correctness later):
- status_accuracy   answered/abstained/blocked as expected
- recall_at_k       expected doc appears among cited docs (answered cases)
- citation_precision cited docs that are in the expected set
- acl_leaks         citations of docs the principal may not read (must be 0)
- must_contain      expected key phrase present in the answer
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aiarch.config import ROOT
from aiarch.container import Container, build_container
from aiarch.domain.models import AnswerStatus, Principal


@dataclass
class Case:
    id: str
    question: str
    tenant: str = "acme"
    groups: list[str] = field(default_factory=lambda: ["all"])
    expect_status: str = "answered"
    expected_doc_ids: list[str] = field(default_factory=list)
    must_contain: list[str] = field(default_factory=list)


DEFAULT_GATES = {"status_accuracy": 0.9, "recall_at_k": 0.8, "citation_precision": 0.8,
                 "must_contain": 0.7, "acl_leaks_max": 0, "p95_latency_ms_max": 5_000}


def load_cases(path: Path) -> list[Case]:
    return [Case(**json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def evaluate(c: Container, cases: list[Case]) -> dict:
    rows: list[dict[str, Any]] = []
    lat: list[float] = []
    readable = c.store.acl_index()
    for case in cases:
        p = Principal(f"eval-{case.id}", case.tenant, frozenset(case.groups) | {"all"})
        t0 = time.perf_counter()
        a = c.rag.ask(case.question, p)
        lat.append((time.perf_counter() - t0) * 1000)
        cited = {x.doc_id for x in a.citations}
        leaks = [d for d in cited if not p.can_read(readable.get(d, frozenset()))]
        ok_status = a.status.value == case.expect_status
        row: dict[str, Any] = {"id": case.id, "status": a.status.value, "status_ok": ok_status, "cited": sorted(cited),
               "leaks": leaks, "cost_usd": a.cost_usd, "cache_hit": a.cache_hit}
        if case.expect_status == AnswerStatus.ANSWERED.value:
            exp = set(case.expected_doc_ids)
            row["recall"] = 1.0 if exp & cited else 0.0
            row["precision"] = (len(exp & cited) / len(cited)) if cited else 0.0
            row["contains"] = (1.0 if all(m.lower() in a.text.lower() for m in case.must_contain) else 0.0)
        rows.append(row)
    answered = [r for r in rows if "recall" in r]

    def mean(key: str) -> float:
        return statistics.fmean(r[key] for r in answered) if answered else 1.0
    lat_sorted = sorted(lat)
    return {
        "cases": len(rows),
        "status_accuracy": statistics.fmean(r["status_ok"] for r in rows) if rows else 0.0,
        "recall_at_k": mean("recall"),
        "citation_precision": mean("precision"),
        "must_contain": mean("contains"),
        "acl_leaks": sum(len(r["leaks"]) for r in rows),
        "p95_latency_ms": lat_sorted[max(0, int(0.95 * len(lat_sorted)) - 1)] if lat_sorted else 0.0,
        "total_cost_usd": round(sum(r["cost_usd"] for r in rows), 6),
        "rows": rows,
    }


def check_gates(report: dict, gates: dict) -> list[str]:
    fails = []
    for k in ("status_accuracy", "recall_at_k", "citation_precision", "must_contain"):
        if report[k] < gates[k]:
            fails.append(f"{k} {report[k]:.2f} < {gates[k]:.2f}")
    if report["acl_leaks"] > gates["acl_leaks_max"]:
        fails.append(f"acl_leaks {report['acl_leaks']} > {gates['acl_leaks_max']}")
    if report["p95_latency_ms"] > gates["p95_latency_ms_max"]:
        fails.append(f"p95_latency_ms {report['p95_latency_ms']:.0f} > {gates['p95_latency_ms_max']}")
    return fails


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run the golden-set eval")
    ap.add_argument("--cases", type=Path, default=ROOT / "evals" / "golden.jsonl")
    ap.add_argument("--gate", action="store_true", help="exit 1 if any gate fails")
    ap.add_argument("--out", type=Path, default=None, help="write the JSON report here")
    a = ap.parse_args(argv)
    from aiarch.adapters.telemetry.tracer import JsonTracer
    c = build_container(tracer=JsonTracer(sink=[]))
    report = evaluate(c, load_cases(a.cases))
    summary = {k: v for k, v in report.items() if k != "rows"}
    print(json.dumps(summary, indent=2))
    for r in report["rows"]:
        flag = "OK " if r["status_ok"] and not r["leaks"] and r.get("recall", 1) == 1 else "XX "
        print(flag, r["id"], r["status"], r["cited"])
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    fails = check_gates(report, DEFAULT_GATES)
    if fails:
        print("GATE FAILED:\n  " + "\n  ".join(fails))
        return 1 if a.gate else 0
    print("GATE PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
