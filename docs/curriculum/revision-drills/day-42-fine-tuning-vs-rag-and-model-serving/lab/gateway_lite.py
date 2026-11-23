"""gateway_lite.py - a 150-line model gateway you can reason about.

Routes OpenAI-compatible chat calls to Ollama with:
  * per-tenant token budgets (TPM-style, sliding 60 s window)
  * ordered fallback chain with a circuit breaker per route
  * exact-match response cache
  * latency + token accounting, printed as a p50/p95 table
Run:  python gateway_lite.py
"""
from __future__ import annotations

import hashlib
import json
import statistics
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

import httpx

OLLAMA = "http://localhost:11434/v1/chat/completions"

# Route chain: first entry is deliberately broken to prove fallback works.
ROUTES = [
    {"name": "primary-missing", "model": "does-not-exist:1b"},
    {"name": "llama3.2-3b", "model": "llama3.2:3b"},
    {"name": "qwen2.5-1.5b", "model": "qwen2.5:1.5b"},
]
TENANT_TPM = {"finance": 4000, "hr": 400}   # tokens per rolling minute


@dataclass
class Breaker:
    fails: int = 0
    open_until: float = 0.0
    threshold: int = 2
    cooldown_s: float = 30.0

    def allow(self) -> bool:
        return time.monotonic() >= self.open_until

    def record(self, ok: bool) -> None:
        if ok:
            self.fails = 0
            return
        self.fails += 1
        if self.fails >= self.threshold:
            self.open_until = time.monotonic() + self.cooldown_s


@dataclass
class Gateway:
    breakers: dict = field(default_factory=lambda: defaultdict(Breaker))
    usage: dict = field(default_factory=lambda: defaultdict(deque))  # tenant -> (ts, tokens)
    cache: dict = field(default_factory=dict)
    stats: dict = field(default_factory=lambda: defaultdict(list))   # route -> latencies
    events: list = field(default_factory=list)

    def _tokens_used(self, tenant: str) -> int:
        q, now = self.usage[tenant], time.monotonic()
        while q and now - q[0][0] > 60:
            q.popleft()
        return sum(t for _, t in q)

    def chat(self, tenant: str, messages: list[dict], max_tokens: int = 120) -> dict:
        # 1. Quota check uses a *pessimistic* estimate: prompt chars/4 + max_tokens.
        est = sum(len(m["content"]) for m in messages) // 4 + max_tokens
        if self._tokens_used(tenant) + est > TENANT_TPM[tenant]:
            self.events.append((tenant, "429 quota"))
            return {"status": 429, "error": f"{tenant} over {TENANT_TPM[tenant]} TPM"}

        # 2. Cache (exact match on tenant + messages). Tenant is in the key: never share across tenants.
        key = hashlib.sha256(json.dumps([tenant, messages], sort_keys=True).encode()).hexdigest()
        if key in self.cache:
            self.events.append((tenant, "cache hit"))
            return {**self.cache[key], "cached": True}

        # 3. Walk the fallback chain.
        for r in ROUTES:
            br = self.breakers[r["name"]]
            if not br.allow():
                self.events.append((tenant, f"skip {r['name']} (breaker open)"))
                continue
            t0 = time.perf_counter()
            try:
                resp = httpx.post(OLLAMA, timeout=60, json={
                    "model": r["model"], "messages": messages,
                    "max_tokens": max_tokens, "temperature": 0,
                })
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                br.record(False)
                self.events.append((tenant, f"fail {r['name']}: {type(exc).__name__}"))
                continue
            dt = time.perf_counter() - t0
            br.record(True)
            body = resp.json()
            used = body.get("usage", {}).get("total_tokens", est)
            self.usage[tenant].append((time.monotonic(), used))   # charge actual tokens
            self.stats[r["name"]].append(dt)
            out = {"status": 200, "route": r["name"], "tokens": used, "latency_s": round(dt, 2),
                   "text": body["choices"][0]["message"]["content"].strip()}
            self.cache[key] = out
            return out
        return {"status": 503, "error": "all routes failed"}

    def report(self) -> None:
        print("\nroute            n   p50(s)  p95(s)")
        for name, lat in self.stats.items():
            lat = sorted(lat)
            p95 = lat[min(len(lat) - 1, int(0.95 * len(lat)))]
            print(f"{name:<15}{len(lat):>3}  {statistics.median(lat):>6.2f}  {p95:>6.2f}")
        for t in TENANT_TPM:
            print(f"tenant {t:<8} tokens in window: {self._tokens_used(t)} / {TENANT_TPM[t]}")
        print("\nevents:")
        for e in self.events:
            print("  ", *e)


if __name__ == "__main__":
    gw = Gateway()
    qs = [
        ("finance", "In one sentence: what is a LoRA adapter?"),
        ("finance", "In one sentence: when is RAG better than fine-tuning?"),
        ("finance", "In one sentence: what is a LoRA adapter?"),          # cache hit
        ("hr", "Summarise in 2 lines why token-based quotas beat request-based ones."),
        ("hr", "List 5 risks of running an AI gateway with every provider key in it."),
        ("hr", "Explain continuous batching to a CFO in 3 sentences."),   # likely 429
    ]
    for tenant, q in qs:
        r = gw.chat(tenant, [{"role": "user", "content": q}])
        print(tenant, r.get("status"), r.get("route", ""), r.get("latency_s", ""),
              "| cached" if r.get("cached") else "", "|", (r.get("text") or r.get("error"))[:90])
    gw.report()
