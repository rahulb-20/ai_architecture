"""Part C - look at the next-token distribution the sampler actually chooses from."""
import math
import ollama

resp = ollama.chat(
    model="qwen3:1.7b",
    messages=[{"role": "user", "content": "Answer with one word. Is the Earth flat? Answer:"}],
    options={"temperature": 0, "num_predict": 3, "num_ctx": 2048},
    think=False,
    logprobs=True,
    top_logprobs=5,
)
print("answer:", resp.message.content)
for pos, lp in enumerate(resp.logprobs or []):
    alts = ", ".join(f"{t.token!r}:{math.exp(t.logprob):.2%}" for t in (lp.top_logprobs or []))
    print(f"pos {pos}: chose {lp.token!r} p={math.exp(lp.logprob):.2%} | top-5: {alts}")
