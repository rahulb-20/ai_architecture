"""HTTP API (FastAPI). Thin by design: parse, authenticate, call a service, map to a schema.

Identity: in production the principal comes from a validated JWT at your API
gateway (Entra ID / Cognito / IBM Verify) and is passed as trusted headers or
claims. Locally, headers are accepted as-is; `AIARCH_API_KEYS` turns on a key check.
"""
from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

from aiarch.adapters.telemetry.tracer import configure_logging
from aiarch.container import Container, build_container
from aiarch.domain.errors import StaleDocumentVersion
from aiarch.domain.models import Document, Principal


class AskIn(BaseModel):
    question: str = Field(..., min_length=2, max_length=4_000)


class CitationOut(BaseModel):
    doc_id: str
    chunk_id: str
    doc_version: int


class AskOut(BaseModel):
    status: str
    answer: str
    citations: list[CitationOut]
    model: str | None
    prompt_version: str | None
    cache_hit: bool
    cost_usd: float
    trace_id: str | None
    reasons: list[str]


class AgentIn(BaseModel):
    goal: str = Field(..., min_length=2, max_length=2_000)


class DocIn(BaseModel):
    doc_id: str = Field(..., pattern=r"^[\w\-.]{1,128}$")
    text: str = Field(..., min_length=1, max_length=500_000)
    acl: list[str] = ["all"]
    version: int = 1
    title: str | None = None


def create_app(container: Container | None = None) -> FastAPI:
    state: dict[str, Container] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        c = container or build_container()
        configure_logging(c.settings.log_level)
        state["c"] = c
        yield

    app = FastAPI(title="aiarch reference API", version="0.1.0", lifespan=lifespan)

    def get_c() -> Container:
        return state["c"]

    def principal(x_user: str = Header("anonymous"), x_tenant: str = Header("acme"),
                  x_groups: str = Header("all"), x_region: str = Header("in"),
                  x_api_key: str | None = Header(None), c: Container = Depends(get_c)) -> Principal:
        if c.settings.api_keys and x_api_key not in c.settings.api_keys:
            raise HTTPException(401, "missing or invalid API key")
        groups = frozenset(g.strip() for g in x_groups.split(",") if g.strip()) | {"all"}
        return Principal(x_user, x_tenant, groups, x_region)

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        t0 = time.perf_counter()
        resp: Response = await call_next(request)
        resp.headers["x-request-id"] = rid
        resp.headers["server-timing"] = f"app;dur={(time.perf_counter() - t0) * 1000:.1f}"
        return resp

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz(c: Container = Depends(get_c)) -> dict:
        breakers = {r.client.name: ("open" if r.breaker.is_open else "closed") for r in c.gateway.routes}
        ready = any(v == "closed" for v in breakers.values())
        if not ready:
            raise HTTPException(503, {"routes": breakers})
        return {"status": "ready", "routes": breakers}

    @app.post("/v1/ask", response_model=AskOut)
    def ask(body: AskIn, p: Principal = Depends(principal), c: Container = Depends(get_c)) -> AskOut:
        a = c.rag.ask(body.question, p)
        return AskOut(status=a.status.value, answer=a.text,
                      citations=[CitationOut(doc_id=x.doc_id, chunk_id=x.chunk_id, doc_version=x.doc_version)
                                 for x in a.citations],
                      model=a.model, prompt_version=a.prompt_version, cache_hit=a.cache_hit,
                      cost_usd=round(a.cost_usd, 6), trace_id=a.trace_id, reasons=a.reasons)

    @app.post("/v1/agent")
    def agent(body: AgentIn, p: Principal = Depends(principal), c: Container = Depends(get_c)) -> dict:
        r = c.agent.run(body.goal, p)
        return {"final": r.final, "stopped_reason": r.stopped_reason,
                "steps": [{"tool": s.tool, "args": s.args, "output": s.output} for s in r.steps]}

    @app.post("/v1/documents", status_code=201)
    def ingest(body: DocIn, p: Principal = Depends(principal), c: Container = Depends(get_c)) -> dict:
        if "kb-admin" not in p.groups:
            raise HTTPException(403, "kb-admin group required")
        doc = Document(body.doc_id, body.text, p.tenant_id, frozenset(body.acl), body.version,
                       {"title": body.title or body.doc_id})
        try:
            return {"doc_id": body.doc_id, "chunks": c.ingestion.ingest(doc)}
        except StaleDocumentVersion as e:
            raise HTTPException(409, {"error": "stale_version", "indexed_version": e.current,
                                      "attempted_version": e.attempted}) from e

    return app


app = create_app()
