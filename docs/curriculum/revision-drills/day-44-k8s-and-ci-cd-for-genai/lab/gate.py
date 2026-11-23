import json, sys, time, statistics, pathlib
from ollama import chat

MODEL = "qwen2.5:1.5b"
PROMPT_VERSION = "hr-answer@1.0"
SYSTEM = ("Answer only from CONTEXT. If the answer is not in CONTEXT, reply exactly: "
          "I don't know. Keep answers under 40 words.")
CONTEXT = ("Employees get 24 days of paid leave per year. Up to 8 unused days carry over. "
           "Remote work is allowed 2 days per week with manager approval.")

# golden set: (question, must_contain_any, critical)
GOLDEN = [
    ("How many paid leave days do I get?", ["24"], False),
    ("How many unused days carry over?", ["8", "eight"], False),
    ("How many remote days per week?", ["2", "two"], False),
    ("What is the CEO's salary?", ["i don't know"], True),     # must abstain
    ("Can I work remotely without approval?", ["approval", "no"], False),
]
BASE = pathlib.Path("baseline.json")
TOLERANCE = 0.0   # allowed drop in pass rate vs baseline
LAT_SLACK = 1.5   # allowed p95 latency multiplier

def run():
    results, lats = [], []
    for q, expect, critical in GOLDEN:
        t0 = time.perf_counter()
        r = chat(model=MODEL, options={"temperature": 0, "seed": 7},
                 messages=[{"role": "system", "content": SYSTEM},
                           {"role": "user", "content": f"CONTEXT:\n{CONTEXT}\n\nQ: {q}"}])
        lats.append(time.perf_counter() - t0)
        text = r.message.content.strip().lower()
        ok = any(e in text for e in expect)
        results.append({"q": q, "ok": ok, "critical": critical, "answer": text[:120],
                        "out_tokens": r.eval_count})
    p95 = sorted(lats)[max(0, int(len(lats) * 0.95) - 1)]
    return {"model": MODEL, "prompt": PROMPT_VERSION,
            "pass_rate": sum(r["ok"] for r in results) / len(results),
            "p95_s": round(p95, 2), "mean_out_tokens": statistics.mean(r["out_tokens"] for r in results),
            "results": results}

cur = run()
for r in cur["results"]:
    print(("PASS " if r["ok"] else "FAIL ") + ("[critical] " if r["critical"] else "") + r["q"])
print(f"pass_rate={cur['pass_rate']:.2f} p95={cur['p95_s']}s out_tokens={cur['mean_out_tokens']:.0f}")

if "--write-baseline" in sys.argv:
    BASE.write_text(json.dumps(cur, indent=2)); print("baseline written"); sys.exit(0)

base = json.loads(BASE.read_text())
reasons = []
if any(r["critical"] and not r["ok"] for r in cur["results"]):
    reasons.append("critical case failed")
if cur["pass_rate"] < base["pass_rate"] - TOLERANCE:
    reasons.append(f"pass_rate {cur['pass_rate']:.2f} < baseline {base['pass_rate']:.2f}")
if cur["p95_s"] > base["p95_s"] * LAT_SLACK:
    reasons.append(f"p95 {cur['p95_s']}s > {LAT_SLACK}x baseline {base['p95_s']}s")
if cur["mean_out_tokens"] > base["mean_out_tokens"] * 1.3:
    reasons.append("output tokens +30% (cost regression)")
print("GATE: " + ("FAIL - " + "; ".join(reasons) if reasons else "PASS"))
sys.exit(1 if reasons else 0)
