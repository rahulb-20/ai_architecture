"""Day 40 drill: audit an LLM judge, then gate a release. Standard library only."""
import random
import statistics as st

rng = random.Random(40)


def judge_metrics(human, judge):
    n = len(human)
    observed = sum(h == j for h, j in zip(human, judge)) / n
    p_h, p_j = sum(human) / n, sum(judge) / n
    expected = p_h * p_j + (1 - p_h) * (1 - p_j)
    kappa = (observed - expected) / (1 - expected)
    bad = [j for h, j in zip(human, judge) if h == 0]
    caught = sum(j == 0 for j in bad) / len(bad)
    return observed, kappa, caught


def bootstrap_ci(values, rounds=4000):
    means = sorted(
        st.fmean(rng.choices(values, k=len(values))) for _ in range(rounds)
    )
    return means[int(0.025 * rounds)], means[int(0.975 * rounds)]


def clamp(x):
    return max(0.0, min(1.0, x))


# Part 1: 400 claims labelled by humans (1 = supported, 0 = unsupported).
# The simulated judge is lenient: it misses most unsupported claims.
human = [1 if rng.random() < 0.85 else 0 for _ in range(400)]
judge = [
    (1 if rng.random() < 0.97 else 0) if h else (0 if rng.random() < 0.35 else 1)
    for h in human
]
acc, kappa, caught = judge_metrics(human, judge)
print("JUDGE AUDIT (n=400 claims)")
print(f"  exact agreement          {acc:.3f}")
print(f"  Cohen's kappa            {kappa:.3f}")
print(f"  unsupported claims found {caught:.3f}")

# Part 2: 150 golden questions, same set scored on both releases.
# The candidate regresses only on the 'policy' segment (20% of traffic).
rows = []
for i in range(150):
    segment = "policy" if i % 5 == 0 else "general"
    base = clamp(rng.gauss(0.84, 0.12))
    drop = 0.12 if segment == "policy" else 0.0
    cand = clamp(base - drop + rng.gauss(0, 0.03))
    rows.append((segment, base, cand))

base = [r[1] for r in rows]
cand = [r[2] for r in rows]
diffs = [c - b for b, c in zip(base, cand)]
b_lo, b_hi = bootstrap_ci(base)
c_lo, c_hi = bootstrap_ci(cand)
d_lo, d_hi = bootstrap_ci(diffs)

print("\nRELEASE GATE (n=150 paired questions, margin -0.02)")
print(f"  baseline mean {st.fmean(base):.3f}  CI [{b_lo:.3f}, {b_hi:.3f}]")
print(f"  candidate mean {st.fmean(cand):.3f}  CI [{c_lo:.3f}, {c_hi:.3f}]")
print(f"  intervals overlap: {c_hi >= b_lo and b_hi >= c_lo}")
print(f"  paired delta {st.fmean(diffs):+.3f}  CI [{d_lo:+.3f}, {d_hi:+.3f}]")
print(f"  overall gate: {'PASS' if d_lo >= -0.02 else 'FAIL'}")

for name in ("general", "policy"):
    seg = [c - b for s, b, c in rows if s == name]
    lo, hi = bootstrap_ci(seg)
    verdict = "PASS" if lo >= -0.02 else "FAIL"
    print(f"  {name:<8} n={len(seg):<3} delta {st.fmean(seg):+.3f}  "
          f"CI [{lo:+.3f}, {hi:+.3f}]  {verdict}")
