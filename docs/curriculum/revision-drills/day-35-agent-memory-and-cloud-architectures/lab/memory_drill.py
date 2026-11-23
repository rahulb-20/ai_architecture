"""Day 35 drill: short-term vs long-term agent memory in LangGraph, and the ways it leaks.

Short-term = per-thread state persisted by a checkpointer (here InMemorySaver).
Long-term  = cross-thread memories in a Store, namespaced (tenant, user, "memories"),
             with semantic search through a toy embedding so it runs with no GPU/API key.

  python memory_drill.py           # correct scoping
  python memory_drill.py --leaky   # namespace built without the tenant/user: watch data cross users
"""
import hashlib
import math
import sys
from dataclasses import dataclass
from typing import Annotated, TypedDict
import operator

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.store.memory import InMemoryStore

LEAKY = "--leaky" in sys.argv
WINDOW = 4  # short-term: keep the last N messages verbatim, fold older ones into a summary

# ---- toy embedding: hashed bag of words, 64 dims (swap for nomic-embed-text via Ollama) ----
DIMS = 64
def embed(texts: list[str]) -> list[list[float]]:
    out = []
    for t in texts:
        v = [0.0] * DIMS
        for w in t.lower().replace(",", " ").replace(".", " ").replace("?", " ").split():
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % DIMS] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / n for x in v])
    return out

def toks(texts: list[str]) -> int:
    return sum(len(t.split()) * 4 // 3 for t in texts)

@dataclass
class Ctx:               # trusted identity comes from the auth layer, never from the prompt
    tenant: str
    user_id: str

class S(TypedDict):
    messages: Annotated[list[str], operator.add]
    summary: str
    prompt_tokens: int
    recalled: list[str]

def ns(ctx: Ctx) -> tuple:
    return ("memories",) if LEAKY else (ctx.tenant, ctx.user_id, "memories")

def agent(state: S, runtime: Runtime[Ctx]) -> dict:
    ctx, store = runtime.context, runtime.store
    msg = state["messages"][-1]
    if msg.lower().startswith("remember"):                      # hot-path write (explicit ask)
        fact = msg.split(" ", 1)[1]
        store.put(ns(ctx), hashlib.sha1(fact.encode()).hexdigest()[:8], {"text": fact, "source": "user"})
        reply = f"OK, saved: {fact}"
        recalled = []
    else:
        hits = store.search(ns(ctx), query=msg, limit=2)        # semantic recall
        recalled = [f"{h.value['text']} (score {h.score:.2f})" for h in hits]
        reply = "Using memory: " + "; ".join(h.value["text"] for h in hits) if hits else "No memory found."
    # short-term: summary + last WINDOW messages is what the model would actually see
    window = state["messages"][-WINDOW:]
    prompt = [state.get("summary", "")] + window + [r for r in recalled]
    return {"messages": [f"AI: {reply}"], "prompt_tokens": toks(prompt) + 200, "recalled": recalled}

def summarize(state: S) -> dict:
    """Fold messages that fell out of the window into a running summary (a cheap model in real life)."""
    old = state["messages"][:-WINDOW]
    if not old:
        return {}
    return {"summary": f"[summary of {len(old)} earlier messages]"}

def build():
    g = StateGraph(S, context_schema=Ctx)
    g.add_node("agent", agent)
    g.add_node("summarize", summarize)
    g.add_edge(START, "agent")
    g.add_edge("agent", "summarize")
    g.add_edge("summarize", END)
    store = InMemoryStore(index={"embed": embed, "dims": DIMS, "fields": ["text"]})
    return g.compile(checkpointer=InMemorySaver(), store=store), store

def say(app, text, thread, ctx):
    out = app.invoke({"messages": [f"User: {text}" if not text.lower().startswith("remember") else text]},
                     {"configurable": {"thread_id": thread}}, context=ctx)
    print(f"  [{ctx.tenant}/{ctx.user_id} · {thread}] {text}")
    print(f"      -> {out['messages'][-1]}   (history={len(out['messages'])} msgs, prompt~{out['prompt_tokens']} tok)")
    return out

if __name__ == "__main__":
    app, store = build()
    rahul = Ctx("acme", "rahul")
    priya = Ctx("globex", "priya")
    print(f"leaky={LEAKY}  window={WINDOW}\n")

    print("1) Long-term writes in thread t1")
    say(app, "remember I prefer answers with Azure and watsonx mappings", "t1", rahul)
    say(app, "remember my project is the HR policy assistant for 50k employees", "t1", rahul)

    print("\n2) New thread t2: short-term is empty, long-term recall still works")
    say(app, "Which cloud mappings should you include for me?", "t2", rahul)

    print("\n3) Another tenant's user asks a similar question")
    out = say(app, "Which cloud mappings and project should you use?", "p1", priya)
    leaked = any("watsonx" in r or "HR policy" in r for r in out["recalled"])
    print(f"      cross-tenant leak: {'YES  <-- incident' if leaked else 'no'}")

    print("\n4) Long thread: prompt stays bounded because of window + summary")
    for i in range(6):
        out = say(app, f"Follow-up question number {i} about leave policy", "t2", rahul)
    print(f"      summary: {out['summary']!r}; full history would be ~{toks(out['messages']) + 200} tok and growing")

    print("\n5) Right to erasure (DPDP / GDPR): delete Rahul's long-term memories")
    for item in store.search(ns(rahul), limit=100):
        store.delete(ns(rahul), item.key)
    say(app, "Which cloud mappings should you include for me?", "t3", rahul)
    cp = app.get_state({"configurable": {"thread_id": "t2"}})
    print(f"      ...but thread t2's checkpoint still holds {len(cp.values['messages'])} messages: erasure must cover checkpoints too")
