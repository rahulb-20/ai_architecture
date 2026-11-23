import sqlite3, sys
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.sqlite import SqliteSaver

class State(TypedDict):
    request: str
    refund_inr: int
    status: str

def draft(state: State) -> dict:
    return {"refund_inr": 18000, "status": "drafted"}

def approve(state: State) -> dict:
    # Everything before interrupt() re-runs on resume, so keep this node side-effect free.
    if state["refund_inr"] <= 10000:
        return {"status": "auto-approved"}
    decision = interrupt({"question": "Approve refund?", "amount": state["refund_inr"]})
    if decision.get("approved"):
        return {"refund_inr": decision.get("amount", state["refund_inr"]), "status": "approved"}
    return {"status": "rejected"}

def execute(state: State) -> dict:
    return {"status": state["status"] + "+executed" if state["status"] != "rejected" else "closed"}

g = StateGraph(State)
g.add_node("draft", draft); g.add_node("approve", approve); g.add_node("execute", execute)
g.add_edge(START, "draft"); g.add_edge("draft", "approve"); g.add_edge("approve", "execute"); g.add_edge("execute", END)

conn = sqlite3.connect("checkpoints.db", check_same_thread=False)
app = g.compile(checkpointer=SqliteSaver(conn))
cfg = {"configurable": {"thread_id": "ticket-4711"}}

if sys.argv[1:] == ["start"]:
    out = app.invoke({"request": "refund for duplicate charge"}, cfg)
    print("paused:", out["__interrupt__"][0].value)
    print("next node:", app.get_state(cfg).next)
else:  # run again later, in a new process: python hitl.py resume
    out = app.invoke(Command(resume={"approved": True, "amount": 15000}), cfg)
    print("final:", out)
    for snap in app.get_state_history(cfg):
        print(snap.config["configurable"]["checkpoint_id"][-8:], snap.next, snap.values.get("status"))
