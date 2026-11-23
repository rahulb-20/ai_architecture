"""Day 41 drill: crash-safe multimodal ingestion with LangGraph checkpoints.

parse -> (text | tables | figures in parallel) -> index
Run 1: the figure-captioning node times out (simulated VLM failure).
Run 2: resume the same thread; the index node crashes halfway through its writes.
Run 3: resume again; the run completes.
Watch which nodes re-execute and whether the vector store gets duplicates.
"""
import hashlib
import operator
import sqlite3
import sys
from pathlib import Path
from typing import Annotated, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

DB = Path("day41.db")
IDEMPOTENT = "--no-ledger" not in sys.argv
FAIL_PLAN = {"figures": 1, "index": 1}  # failures to inject, per node
calls: dict[str, int] = {}

# A fake parsed PDF: what Docling would hand you (text, tables, pictures).
DOC = {
    "doc_id": "policy-2026-07",
    "text": ["Section 4.2 covers flood damage.", "Claims must be filed in 30 days."],
    "tables": [[["Plan", "Flood cap"], ["Gold", "10L"], ["Silver", "4L"]]],
    "figures": ["fig-1.png", "fig-2.png"],
}


class State(TypedDict):
    doc_id: str
    chunks: Annotated[list[dict], operator.add]
    indexed: int


def tick(node: str) -> int:
    calls[node] = calls.get(node, 0) + 1
    return calls[node]


def maybe_fail(node: str, attempt: int, why: str) -> None:
    if attempt <= FAIL_PLAN.get(node, 0):
        raise RuntimeError(f"{node}: {why}")


def parse(state: State) -> dict:
    tick("parse")
    return {"doc_id": DOC["doc_id"]}


def text(state: State) -> dict:
    tick("text")
    return {"chunks": [{"kind": "text", "body": t, "page": 1} for t in DOC["text"]]}


def tables(state: State) -> dict:
    tick("tables")
    out = []
    for t in DOC["tables"]:
        header, *rows = t
        md = "| " + " | ".join(header) + " |\n" + "\n".join(
            "| " + " | ".join(r) + " |" for r in rows
        )
        out.append({"kind": "table", "body": md, "page": 2})
    return {"chunks": out}


def figures(state: State) -> dict:
    attempt = tick("figures")
    maybe_fail("figures", attempt, "VLM caption call timed out")
    caps = [f"[caption of {f}] flood-zone map" for f in DOC["figures"]]
    return {"chunks": [{"kind": "figure", "body": c, "page": 3} for c in caps]}


def index(state: State) -> dict:
    attempt = tick("index")
    con = sqlite3.connect(DB)
    con.execute(
        "CREATE TABLE IF NOT EXISTS vectors(key TEXT PRIMARY KEY, doc TEXT, body TEXT)"
        if IDEMPOTENT
        else "CREATE TABLE IF NOT EXISTS vectors(key TEXT, doc TEXT, body TEXT)"
    )
    chunks = sorted(state["chunks"], key=lambda c: (c["page"], c["body"]))
    for i, c in enumerate(chunks):
        key = hashlib.sha256(f"{state['doc_id']}|{c['body']}".encode()).hexdigest()[:16]
        verb = "INSERT OR IGNORE" if IDEMPOTENT else "INSERT"
        con.execute(f"{verb} INTO vectors VALUES (?,?,?)", (key, state["doc_id"], c["body"]))
        con.commit()  # each upsert is its own external side effect
        if i == 2:
            maybe_fail("index", attempt, "vector store connection reset mid-batch")
    n = con.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
    con.close()
    return {"indexed": n}


def build(checkpointer):
    g = StateGraph(State)
    for name, fn in [("parse", parse), ("text", text), ("tables", tables),
                     ("figures", figures), ("index", index)]:
        g.add_node(name, fn)
    g.add_edge(START, "parse")
    for branch in ("text", "tables", "figures"):
        g.add_edge("parse", branch)
    g.add_edge(["text", "tables", "figures"], "index")
    g.add_edge("index", END)
    return g.compile(checkpointer=checkpointer)


def main() -> None:
    DB.unlink(missing_ok=True)
    cfg = {"configurable": {"thread_id": "ingest-policy-2026-07"}}
    print(f"ledger (idempotent upserts): {IDEMPOTENT}")
    with SqliteSaver.from_conn_string(str(DB)) as saver:
        for run, payload in enumerate([{"chunks": []}, None, None], start=1):
            graph = build(saver)  # a fresh graph object, as after a restart
            before = dict(calls)
            try:
                out = graph.invoke(payload, cfg, durability="sync")
                status = f"done, indexed={out['indexed']}"
            except RuntimeError as e:
                status = f"FAILED ({e})"
            ran = sorted(n for n in calls if calls[n] != before.get(n, 0))
            nxt = graph.get_state(cfg).next
            print(f"run {run}: ran {ran} -> {status}; next={list(nxt)}")
        steps = sum(1 for _ in saver.list(cfg))
    con = sqlite3.connect(DB)
    total, distinct = con.execute(
        "SELECT COUNT(*), COUNT(DISTINCT body) FROM vectors").fetchone()
    con.close()
    print(f"checkpoints on thread: {steps}")
    print(f"vector rows: {total} (distinct chunks: {distinct})")
    print(f"node call counts: {dict(sorted(calls.items()))}")


if __name__ == "__main__":
    main()
