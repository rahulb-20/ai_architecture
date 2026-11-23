"""Part E - the architect's back-of-envelope: cost/month and p50 latency for a design option.
Fill PRICES from the provider's current pricing page before you trust any number."""
from dataclasses import dataclass

@dataclass
class Option:
    name: str
    in_tok: int            # prompt tokens per request (system + history + retrieved context + question)
    out_tok: int           # generated tokens per request
    cached_frac: float     # share of input tokens served from a prompt/prefix cache
    price_in: float        # USD per 1M input tokens   <-- fill from pricing page
    price_out: float       # USD per 1M output tokens  <-- fill from pricing page
    cache_read_mult: float = 0.1   # e.g. Anthropic cache reads bill at 0.1x base input
    prefill_tps: float = 5000      # provider/hardware prefill throughput (measure it!)
    decode_tps: float = 60         # per-stream decode speed (measure it!)

    def cost_per_req(self) -> float:
        cached = self.in_tok * self.cached_frac
        fresh = self.in_tok - cached
        return (fresh * self.price_in + cached * self.price_in * self.cache_read_mult
                + self.out_tok * self.price_out) / 1e6

    def latency_s(self, network_s: float = 0.15) -> float:
        fresh = self.in_tok * (1 - self.cached_frac)
        return network_s + fresh / self.prefill_tps + self.out_tok / self.decode_tps

REQ_PER_MONTH = 50_000 * 8 * 22   # 50k employees x 8 questions/day x 22 working days

options = [
    # Placeholder prices - replace with today's list prices for the models you are comparing.
    Option("RAG: 6 chunks x 400 tok", in_tok=3_500, out_tok=350, cached_frac=0.3, price_in=3.0, price_out=15.0),
    Option("Long-context: stuff 120k tok", in_tok=120_000, out_tok=350, cached_frac=0.0, price_in=3.0, price_out=15.0),
    Option("Long-context + prompt cache", in_tok=120_000, out_tok=350, cached_frac=0.9, price_in=3.0, price_out=15.0),
    Option("RAG on small model", in_tok=3_500, out_tok=350, cached_frac=0.3, price_in=0.25, price_out=1.25, decode_tps=150),
]

print(f"Requests/month: {REQ_PER_MONTH:,}")
print(f"{'option':<32}{'$/req':>9}{'$/month':>13}{'p50 latency':>13}")
for o in options:
    print(f"{o.name:<32}{o.cost_per_req():>9.4f}{o.cost_per_req()*REQ_PER_MONTH:>13,.0f}{o.latency_s():>11.2f} s")

# KV-cache memory: why context length is a capacity decision, not just a model spec
def kv_gib(layers, kv_heads, head_dim, tokens, bytes_per=2, concurrent=1):
    return 2 * layers * kv_heads * head_dim * bytes_per * tokens * concurrent / 2**30

print("\nKV cache (fp16):")
print(f"  Qwen3-1.7B  @ 8k ctx, 1 user   : {kv_gib(28, 8, 128, 8_192):.2f} GiB")
print(f"  Llama-3.1-8B @ 128k ctx, 1 user: {kv_gib(32, 8, 128, 131_072):.2f} GiB")
print(f"  Llama-3.1-8B @ 8k ctx, 32 users: {kv_gib(32, 8, 128, 8_192, concurrent=32):.2f} GiB")
