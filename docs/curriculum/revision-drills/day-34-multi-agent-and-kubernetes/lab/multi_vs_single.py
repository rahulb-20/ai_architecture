"""Day 34 drill: what a supervisor multi-agent design really costs.
Runs the same 3 tasks through (A) one agent with two tools and (B) a supervisor
that hands off to two specialist agents, using LangGraph Command handoffs.
A deterministic stub "LLM" meters calls, tokens and simulated latency, so it
runs on any laptop with no GPU or API key.

  python multi_vs_single.py            # compare both designs
  python multi_vs_single.py --flaky    # a worker gives vague answers: watch the loop guard
"""
import sys
from typing import Annotated, Literal, TypedDict
import operator
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command
from langgraph.errors import GraphRecursionError

FLAKY = "--flaky" in sys.argv
MAX_HOPS = 6  # explicit business-level budget, tighter than LangGraph's recursion_limit

# ---- metering stub LLM ---------------------------------------------------------
class Meter:
    def __init__(self): self.calls = self.tin = self.tout = 0; self.ms = 0.0
    def charge(self, prompt_tokens: int, out_tokens: int):
        self.calls += 1; self.tin += prompt_tokens; self.tout += out_tokens
        self.ms += 250 + prompt_tokens * 0.05 + out_tokens * 20   # TTFT + prefill + decode (rough 3B-class numbers)

def toks(history: list[str]) -> int:
    return sum(len(h.split()) * 4 // 3 for h in history) + 300   # +300 for system prompt and tool schemas

POLICY = {"carry forward": "Max carry-forward is 45 days (hr-leave-v4#2)."}
def policy_tool(q): return next((v for k, v in POLICY.items() if k in q), "No policy found.")
def calc_tool(q): return "Encashment = 30 days x Rs 2,000 = Rs 60,000."

TASKS = [
    "What is the leave carry forward limit?",
    "Compute leave encashment for 30 days at Rs 2,000 per day.",
    "What is the carry forward limit, and compute encashment for 30 days at Rs 2,000?",
]

def needs(task):  # what the "model" would decide to call
    n = []
    if "carry forward" in task: n.append("policy")
    if "compute" in task.lower(): n.append("calc")
    return n

# ---- (A) single agent: one ReAct loop, two tools --------------------------------
def single_agent(task, m: Meter):
    hist = [task]
    for tool in needs(task):                      # one model call per tool decision
        m.charge(toks(hist), 30)
        hist.append((policy_tool if tool == "policy" else calc_tool)(task))
    m.charge(toks(hist), 60)                      # final answer
    return " ".join(hist[1:])

# ---- (B) supervisor + two specialist agents, Command handoffs --------------------
class S(TypedDict):
    task: str
    history: Annotated[list[str], operator.add]
    done: Annotated[list[str], operator.add]
    hops: int
    answer: str

def make_supervisor(m: Meter):
    def supervisor(state: S) -> Command[Literal["policy_agent", "calc_agent", "__end__"]]:
        m.charge(toks(state["history"]), 20)      # routing decision is an LLM call
        if state["hops"] >= MAX_HOPS:
            return Command(goto=END, update={"answer": "ESCALATE: hop budget exhausted"})
        todo = [n for n in needs(state["task"]) if n not in state["done"]]
        if not todo:
            m.charge(toks(state["history"]), 60)  # synthesis call
            return Command(goto=END, update={"answer": " ".join(state["history"][1:])})
        return Command(goto=f"{todo[0]}_agent", update={"hops": state["hops"] + 1})
    return supervisor

def make_worker(name, tool, m: Meter):
    def worker(state: S) -> Command[Literal["supervisor"]]:
        m.charge(toks(state["history"]), 30)      # worker decides to call its tool
        out = tool(state["task"])
        m.charge(toks(state["history"] + [out]), 40)  # worker writes its report
        if FLAKY and name == "calc":
            return Command(goto="supervisor", update={"history": ["calc_agent: it depends on the salary basis."]})
        return Command(goto="supervisor", update={"history": [f"{name}_agent: {out}"], "done": [name]})
    return worker

def build(m: Meter):
    g = StateGraph(S)
    g.add_node("supervisor", make_supervisor(m))
    g.add_node("policy_agent", make_worker("policy", policy_tool, m))
    g.add_node("calc_agent", make_worker("calc", calc_tool, m))
    g.add_edge(START, "supervisor")
    return g.compile()

def row(label, m):
    cost = m.tin * 0.15 + m.tout * 0.60   # USD per 1M requests at $0.15 in / $0.60 out per 1M tokens
    print(f"  {label:<11} calls={m.calls:<3} in_tok={m.tin:<6} out_tok={m.tout:<5} ~latency={m.ms/1000:5.1f}s  ~${cost:,.0f} per 1M requests")

if __name__ == "__main__":
    print(f"flaky={FLAKY}  max_hops={MAX_HOPS}\n")
    for t in TASKS:
        print(f"TASK: {t}")
        a, b = Meter(), Meter()
        single_agent(t, a)
        try:
            out = build(b).invoke({"task": t, "history": [t], "done": [], "hops": 0, "answer": ""},
                                  {"recursion_limit": 25})
            ans = out["answer"]
        except GraphRecursionError:
            ans = "GraphRecursionError (no business-level guard!)"
        row("single", a); row("supervisor", b)
        print(f"  supervisor/single: calls x{b.calls/a.calls:.1f}, input tokens x{b.tin/a.tin:.1f}, latency x{b.ms/a.ms:.1f}")
        print(f"  answer: {ans}\n")
