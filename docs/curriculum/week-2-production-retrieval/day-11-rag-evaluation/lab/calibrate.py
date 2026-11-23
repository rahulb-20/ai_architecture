"""Stretch 1 - is your judge trustworthy? Compare Ragas faithfulness with your own 1/0 labels."""
import csv

rows = [r for r in csv.DictReader(open("eval_results.csv", encoding="utf-8"))
        if r["faithfulness"] and r["human_faithful"] in ("0", "1")]
judge = [1 if float(r["faithfulness"]) >= 0.8 else 0 for r in rows]   # threshold = your release bar
human = [int(r["human_faithful"]) for r in rows]

n = len(rows)
po = sum(j == h for j, h in zip(judge, human)) / n                    # observed agreement
pj, ph = sum(judge) / n, sum(human) / n
pe = pj * ph + (1 - pj) * (1 - ph)                                    # agreement expected by chance
kappa = (po - pe) / (1 - pe) if pe < 1 else float("nan")
fp = sum(j == 1 and h == 0 for j, h in zip(judge, human))             # judge passed a hallucination

print(f"n={n}  agreement={po:.2f}  Cohen's kappa={kappa:.2f}  judge-passed-but-human-failed={fp}")
print("Rule of thumb: kappa >= 0.6 before you let this judge gate a release.")
