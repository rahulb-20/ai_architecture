"""Mock LLM serving tier. ROLE=backend simulates a GPU model server
(fixed concurrency slots, prefill + per-token decode, warm-up on start).
ROLE=gateway is a thin AI gateway that relays streams and exposes the
global in-flight count that KEDA scales on."""
import asyncio
import os
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

ROLE = os.getenv("ROLE", "backend")
MAX_CONCURRENCY = int(os.getenv("MAX_CONCURRENCY", "2"))  # ~ KV-cache slots
PREFILL_MS = float(os.getenv("PREFILL_MS", "150"))
TPOT_MS = float(os.getenv("TPOT_MS", "40"))               # time per output token
WARMUP_S = float(os.getenv("WARMUP_SECONDS", "20"))       # ~ weight load
BACKEND_URL = os.getenv("BACKEND_URL", "http://mockllm-backend:8000")
KEEPALIVE = int(os.getenv("KEEPALIVE", "0"))              # 0 = new conn per request
POD = os.getenv("HOSTNAME", "local")

state = {"running": 0, "waiting": 0, "inflight": 0, "served": 0}
started = time.monotonic()
slots = asyncio.Semaphore(MAX_CONCURRENCY)
client: httpx.AsyncClient | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global client
    if ROLE == "gateway":
        client = httpx.AsyncClient(
            base_url=BACKEND_URL,
            timeout=httpx.Timeout(180.0, connect=5.0),
            limits=httpx.Limits(max_keepalive_connections=KEEPALIVE),
        )
    yield
    if client:
        await client.aclose()


app = FastAPI(lifespan=lifespan)


class GenRequest(BaseModel):
    prompt: str
    max_tokens: int = 64


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/readyz")
async def readyz():
    if ROLE == "backend" and time.monotonic() - started < WARMUP_S:
        raise HTTPException(status_code=503, detail="loading model weights")
    return {"ready": True}


@app.get("/stats")
async def stats():
    return {"role": ROLE, "pod": POD, "max_concurrency": MAX_CONCURRENCY, **state}


async def _generate(req: GenRequest):
    state["waiting"] += 1
    acquired = False
    try:
        await slots.acquire()
        acquired = True
        state["waiting"] -= 1
        state["running"] += 1
        # prefill cost grows with prompt length
        await asyncio.sleep(PREFILL_MS / 1000 * (1 + len(req.prompt) / 2000))
        yield f"[{POD}] "
        for i in range(req.max_tokens):
            await asyncio.sleep(TPOT_MS / 1000)
            yield f"t{i} "
    finally:
        if acquired:
            slots.release()
            state["running"] -= 1
            state["served"] += 1
        else:
            state["waiting"] -= 1


async def _relay(req: GenRequest):
    state["inflight"] += 1
    try:
        async with client.stream("POST", "/v1/generate", json=req.model_dump()) as r:
            r.raise_for_status()
            async for chunk in r.aiter_text():
                yield chunk
    finally:
        state["inflight"] -= 1
        state["served"] += 1


@app.post("/v1/generate")
async def generate(req: GenRequest):
    gen = _generate(req) if ROLE == "backend" else _relay(req)
    return StreamingResponse(gen, media_type="text/plain")
