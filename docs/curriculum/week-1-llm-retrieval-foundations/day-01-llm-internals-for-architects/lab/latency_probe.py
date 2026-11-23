"""Part B - measure prefill vs decode, TTFT, and sampling behaviour on your own GPU."""
import time
import ollama

MODEL = "qwen3:1.7b"
OPTS = {"num_ctx": 8192, "seed": 42}

def run(prompt: str, max_out: int = 128, **sampling) -> dict:
    t0 = time.perf_counter()
    ttft, text, final = None, [], None
    for chunk in ollama.chat(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        options={**OPTS, "num_predict": max_out, **sampling},
        think=False,          # Qwen3 is a hybrid reasoning model; keep hidden thinking tokens out of the timing
        stream=True,
    ):
        if ttft is None and chunk.message.content:
            ttft = time.perf_counter() - t0
        text.append(chunk.message.content or "")
        if chunk.done:
            final = chunk
    ns = 1e9
    return {
        "in_tok": final.prompt_eval_count,
        "out_tok": final.eval_count,
        "ttft_s": round(ttft or 0, 3),
        "prefill_tps": round(final.prompt_eval_count / (final.prompt_eval_duration / ns), 1)
                       if final.prompt_eval_duration else None,
        "decode_tps": round(final.eval_count / (final.eval_duration / ns), 1),
        "load_s": round(final.load_duration / ns, 2),
        "text": "".join(text).strip(),
    }

if __name__ == "__main__":
    filler = "The quarterly compliance review covers vendor onboarding, access recertification and audit trails. "
    print("== Prefill scaling: TTFT should grow with input; decode tok/s should stay ~flat ==")
    for reps in (5, 50, 200, 500):
        r = run(filler * reps + "\nSummarise the above in one sentence.", max_out=60)
        print(f"in={r['in_tok']:>5}  out={r['out_tok']:>3}  TTFT={r['ttft_s']:>6}s  "
              f"prefill={r['prefill_tps']} tok/s  decode={r['decode_tps']} tok/s  load={r['load_s']}s")

    print("\n== Sampling: same prompt, 3 runs each, different seeds ==")
    q = "Give a one-line name for an internal HR policy assistant."
    for label, s in {"greedy-ish (T=0)": {"temperature": 0},
                     "balanced (T=0.7, top_p=0.9)": {"temperature": 0.7, "top_p": 0.9},
                     "hot (T=1.3, top_k=100)": {"temperature": 1.3, "top_k": 100}}.items():
        outs = {run(q, max_out=20, seed=seed, **s)["text"] for seed in (1, 2, 3)}
        print(f"{label:<30} distinct outputs={len(outs)}  e.g. {next(iter(outs))[:70]!r}")
