import asyncio
import statistics
import time
from collections import Counter

import httpx

URL = "http://localhost:8000"
PHASES = [("warm", 2, 30), ("burst", 12, 150), ("cool", 2, 45)]  # name, concurrency, seconds


async def worker(client, stop_at, results, pods):
    while time.monotonic() < stop_at:
        t0 = time.monotonic()
        ttft = None
        try:
            async with client.stream("POST", "/v1/generate",
                                     json={"prompt": "Summarise the leave policy " * 20,
                                           "max_tokens": 64}) as r:
                async for chunk in r.aiter_text():
                    if ttft is None:
                        ttft = time.monotonic() - t0
                        if chunk.startswith("["):
                            pods[chunk[1:chunk.index("]")]] += 1
            results.append((ttft or 0.0, time.monotonic() - t0))
        except httpx.HTTPError as e:
            results.append((float("nan"), float("nan")))
            print("error:", type(e).__name__)


async def sampler(client, stop_at):
    while time.monotonic() < stop_at:
        s = (await client.get("/stats")).json()
        print(f"  {time.strftime('%H:%M:%S')}  gateway inflight={s['inflight']}")
        await asyncio.sleep(10)


def p(values, q):
    vals = sorted(v for v in values if v == v)
    return vals[min(len(vals) - 1, int(q * len(vals)))] if vals else float("nan")


async def main():
    timeout = httpx.Timeout(180.0, connect=5.0)
    async with httpx.AsyncClient(base_url=URL, timeout=timeout) as client:
        for name, conc, secs in PHASES:
            print(f"\n== phase {name}: concurrency={conc} for {secs}s")
            stop_at = time.monotonic() + secs
            results, pods = [], Counter()
            await asyncio.gather(sampler(client, stop_at),
                                 *(worker(client, stop_at, results, pods) for _ in range(conc)))
            ttfts = [r[0] for r in results]
            totals = [r[1] for r in results]
            print(f"   requests={len(results)}  TTFT p50={p(ttfts, .5):.2f}s p95={p(ttfts, .95):.2f}s"
                  f"  e2e p50={statistics.median(totals):.2f}s")
            print(f"   served by: {dict(pods)}")


if __name__ == "__main__":
    asyncio.run(main())
