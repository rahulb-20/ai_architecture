"""Day 19 - Agent memory lab: short-term (checkpointer) + long-term (store) + summarization.

Short-term : SqliteSaver checkpoints, scoped by thread_id   -> survives restarts, per conversation
Long-term  : SqliteStore + sqlite-vec semantic index, scoped by ("memories", user_id) -> cross-thread
Hygiene    : rolling summary once history > MAX_MSGS, dedup-on-write, /forget for erasure

Run:  python memory_agent.py --user rahul
Cmds: /new (new thread, same user)  /mem (list memories)  /forget <id-prefix|all>  /quit
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, SystemMessage
from langchain_ollama import ChatOllama, OllamaEmbeddings
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.runtime import Runtime
from pydantic import BaseModel, Field

CHAT_MODEL = os.getenv("CHAT_MODEL", "qwen2.5:3b")      # fits 6 GB VRAM (Q4)
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
EMBED_DIMS = 768
MAX_MSGS = 10          # summarise when the thread grows past this
DEDUP_SCORE = 0.88     # cosine similarity above which a new fact is treated as a duplicate/update


# ---------- state & context ----------
class State(MessagesState):
    summary: str
    recalled: list[str]


@dataclass
class Ctx:
    user_id: str


class Fact(BaseModel):
    text: str = Field(description="One durable fact about the user, third person, <= 20 words")
    kind: Literal["profile", "preference", "project", "constraint"]


class Extraction(BaseModel):
    facts: list[Fact] = Field(default_factory=list,
                              description="Only durable facts worth remembering across sessions. Empty if none.")


llm = ChatOllama(model=CHAT_MODEL, temperature=0.2)
extractor = ChatOllama(model=CHAT_MODEL, temperature=0).with_structured_output(Extraction)


def ns(user_id: str) -> tuple[str, ...]:
    return ("memories", user_id)


# ---------- nodes ----------
def recall(state: State, runtime: Runtime[Ctx]) -> dict:
    query = state["messages"][-1].content
    hits = runtime.store.search(ns(runtime.context.user_id), query=query, limit=4)
    return {"recalled": [f"- {h.value['text']} (score={h.score:.2f})" for h in hits if (h.score or 0) > 0.45]}


def respond(state: State) -> dict:
    sys = ("You are a concise study assistant for a GenAI architect.\n"
           f"Known facts about the user (may be stale; never invent others):\n"
           f"{chr(10).join(state.get('recalled') or ['- none'])}\n"
           f"Earlier conversation summary: {state.get('summary') or 'none'}")
    reply = llm.invoke([SystemMessage(sys), *state["messages"]])
    return {"messages": [reply]}


def remember(state: State, runtime: Runtime[Ctx]) -> dict:
    """Write path: extract -> dedup against existing -> upsert. Runs after the reply (hot path for the lab;
    in prod move this to a background worker so it never adds user-facing latency)."""
    last_user = next(m for m in reversed(state["messages"]) if isinstance(m, HumanMessage))
    out: Extraction = extractor.invoke([
        SystemMessage("Extract durable user facts from the message. Ignore questions, chit-chat, "
                      "and anything temporary. Never extract secrets, passwords or ID numbers."),
        HumanMessage(last_user.content),
    ])
    space = ns(runtime.context.user_id)
    for f in out.facts:
        near = runtime.store.search(space, query=f.text, limit=1)
        key = near[0].key if near and (near[0].score or 0) >= DEDUP_SCORE else str(uuid.uuid4())
        action = "UPDATE" if near and key == near[0].key else "ADD"
        runtime.store.put(space, key, {"text": f.text, "kind": f.kind,
                                       "updated_at": datetime.now(timezone.utc).isoformat()},
                           index=["text"])  # explicit: embed only the fact text, not the whole JSON
        print(f"   [memory {action}] {key[:8]} {f.kind}: {f.text}")
    return {}


def summarize(state: State) -> dict:
    old = state["messages"][:-4]                       # keep the last 4 turns verbatim
    prompt = (f"Existing summary: {state.get('summary') or 'none'}\n"
              "Extend it with the conversation below. <= 120 words, keep decisions and open questions.")
    s = llm.invoke([SystemMessage(prompt), *old]).content
    print(f"   [summary] compacted {len(old)} messages")
    return {"summary": s, "messages": [RemoveMessage(id=m.id) for m in old]}


def needs_summary(state: State) -> str:
    return "summarize" if len(state["messages"]) > MAX_MSGS else END


# ---------- graph ----------
def build(checkpointer, store):
    g = StateGraph(State, context_schema=Ctx)
    g.add_node("recall", recall)
    g.add_node("respond", respond)
    g.add_node("remember", remember)
    g.add_node("summarize", summarize)
    g.add_edge(START, "recall")
    g.add_edge("recall", "respond")
    g.add_edge("respond", "remember")
    g.add_conditional_edges("remember", needs_summary, ["summarize", END])
    g.add_edge("summarize", END)
    return g.compile(checkpointer=checkpointer, store=store)


def make_store():
    index = {"dims": EMBED_DIMS, "embed": OllamaEmbeddings(model=EMBED_MODEL), "fields": ["text"]}
    try:
        from langgraph.store.sqlite import SqliteStore
        conn = sqlite3.connect("memory_store.db", check_same_thread=False, isolation_level=None)
        store = SqliteStore(conn, index=index)
        store.setup()
        return store, "SqliteStore (persistent, sqlite-vec)"
    except Exception as e:  # e.g. Python build without sqlite extension loading
        from langgraph.store.memory import InMemoryStore
        print(f"!! SqliteStore unavailable ({e!r}) - falling back to InMemoryStore (not persistent)")
        return InMemoryStore(index=index), "InMemoryStore"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default="rahul")
    args = ap.parse_args()
    store, kind = make_store()
    with SqliteSaver.from_conn_string("checkpoints.db") as saver:
        app = build(saver, store)
        thread = str(uuid.uuid4())
        print(f"store={kind}  model={CHAT_MODEL}  user={args.user}  thread={thread[:8]}")
        while True:
            text = input("\nyou> ").strip()
            if not text:
                continue
            if text == "/quit":
                break
            if text == "/new":
                thread = str(uuid.uuid4()); print(f"   new thread {thread[:8]} (short-term memory empty)"); continue
            if text == "/mem":
                for it in store.search(ns(args.user), limit=50):
                    print(f"   {it.key[:8]} [{it.value['kind']}] {it.value['text']}  ({it.value['updated_at'][:19]})")
                continue
            if text.startswith("/forget"):
                target = text.split(maxsplit=1)[1] if " " in text else ""
                items = store.search(ns(args.user), limit=500)
                gone = [i for i in items if target == "all" or (target and i.key.startswith(target))]
                for i in gone:
                    store.delete(ns(args.user), i.key)
                print(f"   deleted {len(gone)} memories"); continue
            cfg = {"configurable": {"thread_id": thread}}
            out = app.invoke({"messages": [HumanMessage(text)]}, cfg, context=Ctx(user_id=args.user))
            ai = next(m for m in reversed(out["messages"]) if isinstance(m, AIMessage))
            print(f"bot> {ai.content}")


if __name__ == "__main__":
    main()
