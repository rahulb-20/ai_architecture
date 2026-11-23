"""Day 17 - LangGraph refund-approval agent.
State + nodes + conditional edges + SQLite checkpointing + interrupt() HITL + streaming.
Run:  python refund_agent.py            (new ticket, pauses for approval if needed)
      python refund_agent.py --resume T1 approve|reject
      python refund_agent.py --history T1
"""
import os, sys, json, sqlite3
from typing import Literal, Optional, TypedDict, Annotated
import operator

from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import interrupt, Command
from langgraph.config import get_stream_writer

MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
APPROVAL_LIMIT = 5000.0  # INR: refunds above this need a human

# ---------- 1. State: the contract between nodes ----------
class Ticket(TypedDict):
    ticket_id: str
    text: str
    category: Optional[str]
    amount: Optional[float]
    order_found: Optional[bool]
    decision: Optional[str]
    reply: Optional[str]
    audit: Annotated[list[str], operator.add]  # reducer: nodes append, never overwrite

class Triage(BaseModel):
    category: Literal["refund", "question", "other"]
    amount: float = Field(0, description="refund amount in INR, 0 if none")

ORDERS = {"ORD-1001": 1299.0, "ORD-2002": 18999.0}  # fake order system

def get_llm():
    if os.getenv("FAKE_LLM"):  # offline mode: no Ollama needed
        return None
    from langchain_ollama import ChatOllama
    return ChatOllama(model=MODEL, temperature=0)

# ---------- 2. Nodes: plain functions returning partial state ----------
def triage(state: Ticket):
    llm = get_llm()
    if llm is None:
        t = state["text"].lower()
        out = Triage(category="refund" if "refund" in t else "question",
                     amount=next((v for k, v in ORDERS.items() if k.lower() in t), 0))
    else:
        out = llm.with_structured_output(Triage).invoke(
            "Classify this support ticket and extract the refund amount in INR.\n"
            f"Known orders: {json.dumps(ORDERS)}\nTicket: {state['text']}")
    get_stream_writer()({"progress": f"triaged as {out.category}"})  # custom stream event
    return {"category": out.category, "amount": out.amount,
            "audit": [f"triage -> {out.category}, amount={out.amount}"]}

def lookup_order(state: Ticket):
    found = any(k in state["text"] for k in ORDERS)
    return {"order_found": found, "audit": [f"lookup -> found={found}"]}

def human_approval(state: Ticket):
    # Execution pauses here; the payload is surfaced to the caller. On resume,
    # interrupt() returns the value passed in Command(resume=...).
    # NOTE: the node re-runs from the top on resume - keep code before interrupt() idempotent.
    answer = interrupt({"ticket": state["ticket_id"], "amount": state["amount"],
                        "question": "Approve refund above limit?"})
    return {"decision": "approved" if answer == "approve" else "rejected",
            "audit": [f"human -> {answer}"]}

def auto_approve(state: Ticket):
    return {"decision": "approved", "audit": ["auto-approved under limit"]}

def answer_question(state: Ticket):
    llm = get_llm()
    reply = ("Thanks, an agent will reply shortly." if llm is None else
             llm.invoke(f"Reply in 2 sentences to: {state['text']}").content)
    return {"reply": reply, "decision": "n/a", "audit": ["answered question"]}

def write_reply(state: Ticket):
    msg = (f"Refund of INR {state['amount']:.0f} {state['decision']}."
           if state["category"] == "refund" and state.get("order_found")
           else "We could not find that order; please share the order ID.")
    return {"reply": msg, "audit": ["reply written"]}

# ---------- 3. Conditional edges: routing is code, not LLM whim ----------
def route_category(state: Ticket) -> Literal["lookup_order", "answer_question"]:
    return "lookup_order" if state["category"] == "refund" else "answer_question"

def route_amount(state: Ticket) -> Literal["human_approval", "auto_approve", "write_reply"]:
    if not state["order_found"]:
        return "write_reply"
    return "human_approval" if state["amount"] > APPROVAL_LIMIT else "auto_approve"

def build(checkpointer):
    g = StateGraph(Ticket)
    for name, fn in [("triage", triage), ("lookup_order", lookup_order),
                     ("human_approval", human_approval), ("auto_approve", auto_approve),
                     ("answer_question", answer_question), ("write_reply", write_reply)]:
        g.add_node(name, fn)
    g.add_edge(START, "triage")
    g.add_conditional_edges("triage", route_category)
    g.add_conditional_edges("lookup_order", route_amount)
    g.add_edge("human_approval", "write_reply")
    g.add_edge("auto_approve", "write_reply")
    g.add_edge("write_reply", END)
    g.add_edge("answer_question", END)
    return g.compile(checkpointer=checkpointer)

def run(graph, inp, tid):
    cfg = {"configurable": {"thread_id": tid}}
    for mode, chunk in graph.stream(inp, cfg, stream_mode=["updates", "custom"]):
        if mode == "custom":
            print("  [custom]", chunk)
        elif "__interrupt__" in chunk:
            print("  PAUSED ->", chunk["__interrupt__"][0].value)
        else:
            for node, upd in chunk.items():
                print(f"  [{node}]", {k: v for k, v in (upd or {}).items() if k != "audit"})
    snap = graph.get_state(cfg)
    print("next:", snap.next or "(done)", "| reply:", snap.values.get("reply"))

if __name__ == "__main__":
    conn = sqlite3.connect("checkpoints.db", check_same_thread=False)
    graph = build(SqliteSaver(conn))
    args = sys.argv[1:]
    if args[:1] == ["--resume"]:
        run(graph, Command(resume=args[2]), args[1])
    elif args[:1] == ["--history"]:
        for s in graph.get_state_history({"configurable": {"thread_id": args[1]}}):
            print(s.config["configurable"]["checkpoint_id"][:13], s.next, s.values.get("audit", [])[-1:])
    else:
        tickets = {"T1": "I want a refund for ORD-2002, the laptop arrived broken.",
                   "T2": "Please refund ORD-1001, wrong size.",
                   "T3": "What are your support hours?"}
        for tid, text in tickets.items():
            print(f"\n=== {tid}: {text}")
            run(graph, {"ticket_id": tid, "text": text, "audit": []}, tid)
