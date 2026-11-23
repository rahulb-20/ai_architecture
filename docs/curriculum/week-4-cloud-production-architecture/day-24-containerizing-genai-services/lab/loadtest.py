"""Fire N concurrent streams; report TTFT, 429s, and test a mid-stream disconnect."""
import asyncio
import json
import sys
import time

import httpx

URL = "http://localhost:8000/v1/chat/stream"
HEADERS = {}  # e.g. {"Authorization": "Bearer <key>"} when the secret is mounted


async def one(client: httpx.AsyncClient, i: int, abort_after: int | None = None):
    t0 = time.perf_counter()
    ttft, text, event = None, [], None
    async with client.stream("POST", URL, headers=HEADERS,
                             json={"prompt": "In 3 sentences, why do containers help LLM apps?",
                                   "max_tokens": 120}) as r:
        if r.status_code != 200:
            return {"i": i, "status": r.status_code, "retry_after": r.headers.get("retry-after")}
        async for line in r.aiter_lines():
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:].strip())
                if event == "token":
                    ttft = ttft or round((time.perf_counter() - t0) * 1000)
                    text.append(data)
                    if abort_after and len(text) >= abort_after:
                        return {"i": i, "status": "aborted_by_client", "chunks": len(text)}
                elif event in ("done", "error"):
                    return {"i": i, "status": 200, "event": event, "client_ttft_ms": ttft,
                            "server": data, "preview": "".join(text)[:60]}
    return {"i": i, "status": "stream_ended_without_done"}


async def main(n: int):
    async with httpx.AsyncClient(timeout=httpx.Timeout(10, read=60)) as c:
        results = await asyncio.gather(*(one(c, i) for i in range(n)))
        for r in results:
            print(r)
        print("--- disconnect test ---")
        print(await one(c, 99, abort_after=3))


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 4))
