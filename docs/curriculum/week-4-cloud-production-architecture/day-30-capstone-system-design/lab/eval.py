"""Release gate: run the golden set, print a scorecard, exit 1 if any gate fails (use in CI)."""
import statistics
import sys

from corpus import DOCS, GOLDEN
from kassist import Index, allowed, answer

ACL = {d["id"]: d["acl"] for d in DOCS}
GATES = {"acl_leaks": 0, "hit_rate_min": 0.90, "abstain_acc_min": 0.90, "p95_total_s_max": 15.0}


def main() -> int:
    idx = Index.build()
    leaks = hits = answerable = abstain_ok = must_abstain = false_abstain = 0
    lat = []
    for g in GOLDEN:
        r = answer(idx, g["q"], g["groups"])
        lat.append(r["latency_s"]["total"])
        # leak = any retrieved chunk or citation the user is not entitled to
        retrieved_docs = {h["id"].split("#")[0] for h in r["hits"]} | set(r["citations"])
        leak = [d for d in retrieved_docs if not any(x in g["groups"] for x in ACL[d])]
        leaks += len(leak)
        if g["expect"]:
            answerable += 1
            got = g["expect"] in r["citations"]
            hits += got
            false_abstain += r["abstained"]
            mark = "OK " if got else "MISS"
        else:
            must_abstain += 1
            abstain_ok += r["abstained"]
            mark = "OK " if r["abstained"] else "HALL"
        print(f"{mark} {'LEAK ' if leak else ''}{g['groups']!s:22} {g['q'][:52]:52} -> {r['citations']}")

    p95 = sorted(lat)[max(0, int(round(0.95 * len(lat))) - 1)]
    card = {"acl_leaks": leaks,
            "hit_rate": round(hits / answerable, 3),
            "false_abstain_rate": round(false_abstain / answerable, 3),
            "abstain_acc": round(abstain_ok / must_abstain, 3),
            "p50_total_s": round(statistics.median(lat), 2), "p95_total_s": round(p95, 2)}
    print("\nSCORECARD", card)
    fails = []
    if card["acl_leaks"] > GATES["acl_leaks"]: fails.append("ACL leak")
    if card["hit_rate"] < GATES["hit_rate_min"]: fails.append("hit rate")
    if card["abstain_acc"] < GATES["abstain_acc_min"]: fails.append("abstention")
    if card["p95_total_s"] > GATES["p95_total_s_max"]: fails.append("latency")
    print("GATE:", "PASS" if not fails else "FAIL -> " + ", ".join(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
