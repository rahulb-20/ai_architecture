"""Part D - a tiny 'lost in the middle' test: does position/length change recall?"""
import random
import ollama

MODEL = "qwen3:1.7b"
NEEDLE = "Note for auditors: the escrow release code for the Pune data centre is 7319-KESTREL."
QUESTION = "What is the escrow release code for the Pune data centre? Reply with the code only."

random.seed(7)
SUBJ = ["The vendor register", "Access recertification", "The DR runbook", "Change board minutes",
        "The SOC2 evidence pack", "Patch compliance", "The encryption key inventory"]
VERB = ["was reviewed on", "was updated on", "was escalated on", "was signed off on", "was archived on"]
TAIL = ["with two minor findings.", "after a delayed CAB.", "pending owner confirmation.",
        "and no exceptions were raised.", "with the Bengaluru team in copy."]

def filler(n_sentences: int) -> list[str]:
    return [f"{random.choice(SUBJ)} {random.choice(VERB)} 2026-0{random.randint(1,9)}-{random.randint(10,28)} "
            f"{random.choice(TAIL)}" for _ in range(n_sentences)]

def trial(n_sentences: int, depth: float) -> tuple[int, bool]:
    hay = filler(n_sentences)
    hay.insert(int(depth * len(hay)), NEEDLE)
    prompt = "Document:\n" + " ".join(hay) + "\n\n" + QUESTION
    r = ollama.chat(model=MODEL, messages=[{"role": "user", "content": prompt}],
                    options={"temperature": 0, "num_ctx": 16384, "num_predict": 20},
                    think=False)
    return r.prompt_eval_count, "7319-KESTREL" in r.message.content.upper()

if __name__ == "__main__":
    print(f"{'tokens':>7} | " + " | ".join(f"depth {d:.0%}" for d in (0.1, 0.5, 0.9)))
    for n in (60, 250, 500, 900):      # roughly 1k -> 14k tokens
        cells, toks = [], 0
        for d in (0.1, 0.5, 0.9):
            toks, ok = trial(n, d)
            cells.append("  HIT  " if ok else "  miss ")
        print(f"{toks:>7} | " + " | ".join(f"{c:>9}" for c in cells))
