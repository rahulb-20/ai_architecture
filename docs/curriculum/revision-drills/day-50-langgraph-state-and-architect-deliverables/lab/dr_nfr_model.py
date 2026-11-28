"""Day 50 lab, part 2: turn the measurements into the numbers a DR review asks for.

Standard library only.

    python dr_nfr_model.py                 # uses measurements.json if state_drill.py wrote one
    python dr_nfr_model.py --rto 30 --rpo 1

Every input below is an assumption for a 50k-employee knowledge assistant. Change them
to your own figures; the point is the shape of the calculation, not these values.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

# ---- workload assumptions ------------------------------------------------
EMPLOYEES = 50_000
DAILY_ACTIVE = 0.30          # share of employees who use it on a working day
THREADS_PER_USER_DAY = 2
TURNS_PER_THREAD = 5
RETENTION_DAYS = 30          # how long a thread must stay resumable
WORKING_DAYS_PER_MONTH = 22
PEAK_REQUESTS_PER_SECOND = 40
SECONDS_PER_AGENT_RUN = 12   # one multi-step request, start to finish

# ---- dependency availability (monthly, from each provider's SLA or your SLO)
DEPENDENCIES = {
    "API gateway": 0.9995,
    "Agent service (your pods)": 0.9995,
    "Checkpoint database": 0.9995,
    "Vector search": 0.999,
    "LLM endpoint": 0.999,
}
LLM_FALLBACK = 0.999         # a second model route in another region or provider

# ---- business inputs -----------------------------------------------------
COST_PER_OUTAGE_HOUR = 9_000     # currency units of lost productivity per hour down
REGION_OUTAGES_PER_YEAR = 0.33   # one regional event every three years
PRIMARY_RUN_COST_PER_YEAR = 240_000  # non-token platform cost of the primary region


@dataclass
class Tier:
    name: str
    rto_min: float       # time to serve again after a regional loss
    rpo_min: float       # data written in this window before the loss is gone
    standby_cost: float  # yearly cost as a fraction of the primary's run cost
    note: str


# RTO and RPO here are planning figures in the ranges Microsoft publishes as
# "approximate" for hot/cold, hot/warm and hot/hot Foundry topologies. Replace
# them with numbers from your own failover test.
TIERS = [
    Tier("Backup and restore", 360, 240, 0.05, "restore database, redeploy from IaC"),
    Tier("Pilot light", 120, 15, 0.15, "data replicated, compute scaled to zero"),
    Tier("Warm standby", 30, 5, 0.45, "replica plus a small always-on stack"),
    Tier("Active/active", 2, 1, 1.00, "both regions serve, async replication"),
]


def load_measurements() -> dict:
    path = Path(__file__).with_name("measurements.json")
    if path.exists():
        return json.loads(path.read_text())["D"]
    # values from a reference run of state_drill.py
    return {"turns": 20, "kb_by_turn": [7.3, 0, 0, 0, 111.5], "delta_kb_by_turn": [4.2, 0, 0, 0, 23.9],
            "live_state_kb": 36.2}


def storage(m: dict) -> list[tuple[str, float]]:
    threads_retained = EMPLOYEES * DAILY_ACTIVE * THREADS_PER_USER_DAY * WORKING_DAYS_PER_MONTH * RETENTION_DAYS / 30
    i = TURNS_PER_THREAD - 1
    live_kb = m["live_state_kb"] * TURNS_PER_THREAD / m["turns"]
    rows = [
        ("Every checkpoint kept, plain reducer", m["kb_by_turn"][i]),
        ("Every checkpoint kept, DeltaChannel (beta)", m["delta_kb_by_turn"][i]),
        ("Latest checkpoint only (nightly prune)", live_kb),
    ]
    return [(name, kb * threads_retained / 1024 / 1024) for name, kb in rows]


def availability() -> tuple[float, float]:
    serial = 1.0
    for a in DEPENDENCIES.values():
        serial *= a
    llm = DEPENDENCIES["LLM endpoint"]
    with_fallback = serial / llm * (1 - (1 - llm) * (1 - LLM_FALLBACK))
    return serial, with_fallback


def minutes_down(a: float) -> float:
    return (1 - a) * 30 * 24 * 60


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rto", type=float, default=60, help="required RTO in minutes")
    ap.add_argument("--rpo", type=float, default=15, help="required RPO in minutes")
    args = ap.parse_args()

    print("1. Checkpoint storage at steady state")
    print(f"   {EMPLOYEES:,} employees, {DAILY_ACTIVE:.0%} active, {THREADS_PER_USER_DAY} threads a day, "
          f"{TURNS_PER_THREAD} turns each, kept {RETENTION_DAYS} days")
    for name, gb in storage(load_measurements()):
        print(f"   {name:<44}{gb:>8.1f} GB")
    print("   A cross-region replica doubles whichever figure you pick, and every byte is in scope for backup and deletion requests.")

    serial, with_fb = availability()
    print("\n2. Availability you can promise (dependencies in series)")
    for name, a in DEPENDENCIES.items():
        print(f"   {name:<28}{a:.2%}")
    print(f"   {'Composite':<28}{serial:.3%}  = {minutes_down(serial):.0f} min down per 30 days")
    print(f"   {'With an LLM fallback route':<28}{with_fb:.3%}  = {minutes_down(with_fb):.0f} min down per 30 days")
    print("   A 99.9% SLO allows 43 min. Check the composite before you sign it.")

    in_flight = PEAK_REQUESTS_PER_SECOND * SECONDS_PER_AGENT_RUN
    print("\n3. DR tier against the requirement "
          f"(RTO <= {args.rto:g} min, RPO <= {args.rpo:g} min)")
    print(f"   {'tier':<20}{'RTO':>6}{'RPO':>6}{'standby/yr':>12}{'outage loss/yr':>16}{'total/yr':>10}  meets?")
    best = None
    for t in TIERS:
        standby = t.standby_cost * PRIMARY_RUN_COST_PER_YEAR
        loss = REGION_OUTAGES_PER_YEAR * (t.rto_min / 60) * COST_PER_OUTAGE_HOUR
        total = standby + loss
        ok = t.rto_min <= args.rto and t.rpo_min <= args.rpo
        if ok and (best is None or total < best[1]):
            best = (t, total)
        print(f"   {t.name:<20}{t.rto_min:>6g}{t.rpo_min:>6g}{standby:>12,.0f}{loss:>16,.0f}{total:>10,.0f}  {'yes' if ok else 'no'}")
    if best:
        print(f"   Cheapest tier that meets the requirement: {best[0].name} ({best[0].note}).")
    else:
        print("   No tier meets the requirement. Relax it or change the design.")
    cheapest = min(TIERS, key=lambda t: t.standby_cost * PRIMARY_RUN_COST_PER_YEAR
                   + REGION_OUTAGES_PER_YEAR * (t.rto_min / 60) * COST_PER_OUTAGE_HOUR)
    print(f"   Cheapest tier on expected cost alone: {cheapest.name}. "
          "If that differs from the line above, the requirement is the cost driver. Say so in the ADR.")

    print("\n4. What a failover does to work in flight")
    print(f"   At peak about {in_flight:,} agent runs are in flight ({PEAK_REQUESTS_PER_SECOND} rps x {SECONDS_PER_AGENT_RUN} s).")
    for t in TIERS:
        at_risk = PEAK_REQUESTS_PER_SECOND * t.rpo_min * 60
        print(f"   {t.name:<20} requests whose checkpoints may be missing in the standby: up to {at_risk:,.0f}")
    print("   Runs in flight restart from their last replicated checkpoint. Any write tool they had")
    print("   already called runs again, which is why part C of state_drill.py matters here.")


if __name__ == "__main__":
    main()
