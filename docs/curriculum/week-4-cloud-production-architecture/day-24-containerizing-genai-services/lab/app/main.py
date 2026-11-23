"""Day 24 — streaming LLM gateway: FastAPI SSE + Ollama, container-ready."""
import asyncio
import json
import logging
import os
import time
import uuid
from collections.abc import AsyncIterable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from ollama import AsyncClient, ResponseError
from pydantic import BaseModel, Field

# ---- config: 12-factor, env only ------------------------------------------
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
MODEL = os.getenv("MODEL", "qwen2.5:1.5b")
MAX_CONCURRENCY = int(os.getenv("MAX_CONCURRENCY", "2"))      # GPU slots, not CPU cores
FIRST_TOKEN_TIMEOUT_S = float(os.getenv("FIRST_TOKEN_TIMEOUT_S", "30"))
INTER_TOKEN_TIMEOUT_S = float(os.getenv("INTER_TOKEN_TIMEOUT_S", "15"))
MAX_STREAM_S = float(os.getenv("MAX_STREAM_S", "120"))
API_KEY_FILE = Path(os.getenv("API_KEY_FILE", "/run/secrets/api_key"))

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("gateway")
logging.getLogger("httpx").setLevel(logging.WARNING)  # no per-call noise


def jlog(**kv) -> None:
    """One JSON object per line on stdout: the container runtime ships it."""
    log.info(json.dumps({"ts": round(time.time(), 3), **kv}))


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.llm = AsyncClient(host=OLLAMA_HOST)
    app.state.sem = asyncio.Semaphore(MAX_CONCURRENCY)
    app.state.inflight = 0
    app.state.draining = False
    app.state.api_key = API_KEY_FILE.read_text().strip() if API_KEY_FILE.exists() else None
    jlog(evt="startup", model=MODEL, ollama=OLLAMA_HOST, auth=bool(app.state.api_key))
    yield
    app.state.draining = True          # SIGTERM -> readyz goes 503, LB stops routing
    jlog(evt="shutdown", inflight=app.state.inflight)


app = FastAPI(title="genai-gateway", lifespan=lifespan)


class ChatIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    max_tokens: int = Field(256, ge=1, le=1024)


# ---- probes ----------------------------------------------------------------
@app.get("/healthz")
async def healthz():
    """Liveness: is THIS process alive? Never call dependencies here."""
    return {"status": "ok"}


@app.get("/readyz")
async def readyz(request: Request):
    """Readiness: can we serve traffic right now? Checks the model backend."""
    if request.app.state.draining:
        return JSONResponse({"status": "draining"}, status_code=503)
    try:
        await asyncio.wait_for(request.app.state.llm.show(MODEL), timeout=2)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"status": "unready", "reason": type(e).__name__}, status_code=503)
    return {"status": "ready", "model": MODEL, "inflight": request.app.state.inflight}


# ---- guards (run BEFORE the stream opens, so they can return real HTTP codes)
def require_key(request: Request, authorization: str | None = Header(default=None)):
    key = request.app.state.api_key
    if key and authorization != f"Bearer {key}":
        raise HTTPException(401, "missing or bad bearer token")


def admit(request: Request):
    """Load-shed instead of queueing forever: 429 + Retry-After when GPU slots are full."""
    if request.app.state.sem.locked():
        raise HTTPException(429, "busy", headers={"Retry-After": "2"})


# ---- the streaming endpoint -------------------------------------------------
@app.post("/v1/chat/stream", response_class=EventSourceResponse,
          dependencies=[Depends(require_key), Depends(admit)])
async def chat_stream(body: ChatIn, request: Request) -> AsyncIterable[ServerSentEvent]:
    rid = uuid.uuid4().hex[:8]
    t0 = time.perf_counter()
    ttft_ms, n_chunks, usage, outcome = None, 0, {}, "ok"
    st = request.app.state
    async with st.sem:
        st.inflight += 1
        stream = None
        try:
            yield ServerSentEvent(event="meta", data={"request_id": rid, "model": MODEL})
            stream = await st.llm.chat(
                model=MODEL,
                messages=[{"role": "user", "content": body.prompt}],
                stream=True,
                options={"num_predict": body.max_tokens},
            )
            async with asyncio.timeout(MAX_STREAM_S):              # hard wall-clock cap
                while True:
                    budget = FIRST_TOKEN_TIMEOUT_S if ttft_ms is None else INTER_TOKEN_TIMEOUT_S
                    try:
                        part = await asyncio.wait_for(anext(stream), timeout=budget)
                    except StopAsyncIteration:
                        break
                    if await request.is_disconnected():
                        outcome = "client_disconnected"
                        break
                    tok = part.message.content
                    if tok:
                        if ttft_ms is None:
                            ttft_ms = round((time.perf_counter() - t0) * 1000)
                        n_chunks += 1
                        yield ServerSentEvent(event="token", data=tok, id=str(n_chunks))
                    if part.done:
                        usage = {"prompt_tokens": part.prompt_eval_count,
                                 "completion_tokens": part.eval_count}
            if outcome == "ok":
                yield ServerSentEvent(event="done", data={
                    "request_id": rid, "ttft_ms": ttft_ms, "chunks": n_chunks,
                    "total_ms": round((time.perf_counter() - t0) * 1000), **usage})
        except TimeoutError:
            outcome = "timeout"
            yield ServerSentEvent(event="error", data={"code": "upstream_timeout", "request_id": rid})
        except ResponseError as e:
            outcome = "upstream_error"
            yield ServerSentEvent(event="error", data={"code": "upstream_error",
                                                       "status": e.status_code, "request_id": rid})
        except (asyncio.CancelledError, GeneratorExit):
            outcome = "cancelled"   # client went away mid-yield
            raise
        finally:
            if stream is not None:
                await stream.aclose()   # closes the HTTP call -> Ollama stops generating
            st.inflight -= 1
            jlog(evt="chat", rid=rid, outcome=outcome, ttft_ms=ttft_ms, chunks=n_chunks,
                 total_ms=round((time.perf_counter() - t0) * 1000), **usage)
