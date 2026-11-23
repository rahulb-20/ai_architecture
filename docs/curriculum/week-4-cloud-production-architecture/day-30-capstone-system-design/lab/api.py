"""Thin FastAPI facade. Run: python -m uvicorn api:app --port 8000
In production the groups come from a validated OIDC/Entra ID token (group claims or a
directory lookup) at the gateway - NEVER from a client-supplied header like this demo."""
from fastapi import FastAPI, Header
from pydantic import BaseModel

from kassist import Index, answer

app = FastAPI(title="Kestrel Knowledge Assistant")
IDX = Index.build()


class Ask(BaseModel):
    question: str


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "chunks": len(IDX.chunks)}


@app.post("/ask")
def ask(body: Ask, x_user_groups: str = Header(default="all")) -> dict:
    groups = [g.strip() for g in x_user_groups.split(",") if g.strip()]
    return answer(IDX, body.question, groups)
