import os
from collections.abc import AsyncIterable

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.sse import EventSourceResponse, ServerSentEvent
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_ollama import ChatOllama
from pydantic import BaseModel, Field

OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
MODEL = os.getenv("MODEL", "qwen2.5:3b")

llm = ChatOllama(model=MODEL, base_url=OLLAMA_URL, temperature=0, num_predict=256)
prompt = ChatPromptTemplate.from_messages([
    ("system", "You are a concise enterprise assistant. Answer in at most 120 words."),
    ("human", "{question}"),
])
# LCEL: prompt -> model -> parser. Every step is a Runnable, so .astream() works end to end.
chain = (prompt | llm | StrOutputParser()).with_config(run_name="ask_chain")

app = FastAPI(title="genai-stream-svc")


class Ask(BaseModel):
    question: str = Field(min_length=3, max_length=2000)


@app.get("/healthz")  # liveness: the process is up
async def healthz() -> dict:
    return {"status": "ok"}


@app.get("/readyz")  # readiness: the model backend answers
async def readyz(response: Response) -> dict:
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
            r.raise_for_status()
        return {"status": "ready", "model": MODEL}
    except Exception as exc:
        response.status_code = 503
        return {"status": "not-ready", "error": type(exc).__name__}


@app.post("/ask/stream", response_class=EventSourceResponse)
async def ask_stream(body: Ask, request: Request) -> AsyncIterable[ServerSentEvent]:
    n = 0
    async for chunk in chain.astream({"question": body.question}):
        if await request.is_disconnected():  # stop paying for tokens nobody reads
            break
        n += 1
        yield ServerSentEvent(data={"token": chunk}, event="token", id=str(n))
    yield ServerSentEvent(raw_data="[DONE]", event="done")
