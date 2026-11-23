"""Fire concurrent requests at the gateway and summarise outcomes."""
import asyncio, statistics, sys, time
import httpx

URL = "http://localhost:8080/v1/chat"

async def one(c, i, key):
    body = {"messages": [{"role": "user", "content": f"In one sentence, what is a KV cache? (#{i % 4})"}],
            "temperature": 0, "max_tokens": 64}
    t = time.perf_counter()
    r = await c.post(URL, json=body, headers={"X-API-Key": key})
    return r.status_code, (time.perf_counter() - t) * 1000, r.json()

async def main(n=12, key="team-a"):
    async with httpx.AsyncClient(timeout=120) as c:
        res = await asyncio.gather(*(one(c, i, key) for i in range(n)))
    ok = [r for r in res if r[0] == 200]
    lat = sorted(r[1] for r in ok)
    print(f"key={key} sent={n} ok={len(ok)} 429={sum(r[0]==429 for r in res)} 503={sum(r[0]==503 for r in res)}")
    if lat:
        print(f"p50={statistics.median(lat):.0f}ms  p95={lat[int(0.95*(len(lat)-1))]:.0f}ms")
    print("served_by:", {r[2]['served_by'] for r in ok}, " cache hits:", sum(r[2]['cache']=='hit' for r in ok))

if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 12, sys.argv[2] if len(sys.argv) > 2 else "team-a"))
