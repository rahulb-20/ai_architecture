"""Day 18 - single agent vs supervisor vs router, same tools, same tasks.
Measures: correctness, LLM calls, tokens, latency. Runs on Ollama locally.
"""
import ast
import operator as op
import os
import time
from typing import Literal, TypedDict

from langchain.agents import create_agent
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.tools import tool
from langchain_ollama import ChatOllama
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")


# ---------- metering: counts every LLM call, including ones inside sub-agents ----------
class Meter(BaseCallbackHandler):
    def __init__(self):
        self.reset()

    def reset(self):
        self.calls, self.tin, self.tout = 0, 0, 0

    def on_llm_end(self, response, **kwargs):
        self.calls += 1
        for gens in response.generations:
            for g in gens:
                um = getattr(getattr(g, "message", None), "usage_metadata", None) or {}
                self.tin += um.get("input_tokens", 0)
                self.tout += um.get("output_tokens", 0)


METER = Meter()


def llm():
    return ChatOllama(model=MODEL, temperature=0, num_ctx=8192, callbacks=[METER])


# ---------- a tiny, deterministic HR/finance domain ----------
POLICY = {
    "leave": "Employees accrue 1.5 days of earned leave per month; max carry-forward is 30 days.",
    "wfh": "Up to 2 work-from-home days per week, with manager approval.",
    "travel": "Domestic per-diem is INR 3500 per day. International per-diem is USD 75 per day.",
}
BALANCES = {"E101": 12.0, "E202": 27.5}
USD_INR = 88.0  # fixed demo rate, not a live quote


@tool
def lookup_policy(topic: Literal["leave", "wfh", "travel"]) -> str:
    """Return the company policy text for a topic: leave, wfh or travel."""
    return POLICY[topic]


@tool
def leave_balance(employee_id: str) -> str:
    """Return the current earned-leave balance (days) for an employee id like E101."""
    eid = employee_id.strip().upper()
    return f"{eid} has {BALANCES[eid]} days" if eid in BALANCES else f"Unknown employee {eid}"


_OPS = {ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv, ast.USub: op.neg}


def _eval(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    raise ValueError("unsupported expression")


@tool
def calculator(expression: str) -> str:
    """Evaluate an arithmetic expression with + - * / and parentheses, e.g. '12 + 1.5*6'."""
    try:
        return str(_eval(ast.parse(expression, mode="eval").body))
    except Exception as e:  # return errors to the model instead of crashing the run
        return f"error: {e}"


@tool
def usd_to_inr(amount_usd: float) -> str:
    """Convert a USD amount to INR at the company's fixed rate."""
    return f"{amount_usd * USD_INR:.0f} INR"


ALL_TOOLS = [lookup_policy, leave_balance, calculator, usd_to_inr]


def ask(agent, text: str) -> str:
    return agent.invoke({"messages": [{"role": "user", "content": text}]})["messages"][-1].content


# ---------- Architecture A: one agent, all tools ----------
single = create_agent(
    llm(), ALL_TOOLS,
    system_prompt="You are an HR assistant. Always use tools for facts and arithmetic; never guess numbers. "
                  "Answer in one short sentence.",
)

# ---------- specialists (reused by B and C) ----------
policy_agent = create_agent(
    llm(), [lookup_policy, leave_balance],
    system_prompt="You are the HR policy specialist. Use tools and quote the exact numbers you find.",
)
finance_agent = create_agent(
    llm(), [calculator, usd_to_inr],
    system_prompt="You are the finance specialist. Use the calculator or converter for every computation. "
                  "Reply with the final number.",
)


# ---------- Architecture B: supervisor, sub-agents exposed as tools ----------
@tool
def ask_policy_specialist(request: str) -> str:
    """Ask the HR policy specialist about leave, WFH or travel policy, or an employee's leave balance."""
    return ask(policy_agent, request)


@tool
def ask_finance_specialist(request: str) -> str:
    """Ask the finance specialist to compute something. Put every number it needs in the request."""
    return ask(finance_agent, request)


supervisor = create_agent(
    llm(), [ask_policy_specialist, ask_finance_specialist],
    system_prompt="You coordinate two specialists. Get facts from the policy specialist first, then pass those "
                  "facts explicitly to the finance specialist if a calculation is needed. "
                  "Answer the user in one short sentence.",
)


# ---------- Architecture C: router workflow (1 classify call, fixed paths) ----------
class Route(BaseModel):
    route: Literal["policy", "finance", "both"]


class S(TypedDict, total=False):
    question: str
    route: str
    facts: str
    answer: str


def classify(s: S) -> S:
    r = llm().with_structured_output(Route).invoke([
        ("system", "Classify the HR question. policy = needs policy text or leave balance only. "
                   "finance = pure arithmetic/conversion with all numbers given. "
                   "both = needs policy facts AND a calculation."),
        ("user", s["question"]),
    ])
    return {"route": r.route}


def policy_node(s: S) -> S:
    facts = ask(policy_agent, s["question"])
    return {"facts": facts, "answer": facts}


def finance_node(s: S) -> S:
    q = s["question"] + (f"\nKnown facts: {s['facts']}" if s.get("facts") else "")
    return {"answer": ask(finance_agent, q)}


g = StateGraph(S)
g.add_node("classify", classify)
g.add_node("policy", policy_node)
g.add_node("finance", finance_node)
g.add_edge(START, "classify")
g.add_conditional_edges("classify", lambda s: "finance" if s["route"] == "finance" else "policy",
                        ["policy", "finance"])
g.add_conditional_edges("policy", lambda s: "finance" if s["route"] == "both" else END, ["finance", END])
g.add_edge("finance", END)
router = g.compile()

# ---------- the benchmark ----------
TASKS = [
    ("What is the domestic per-diem per day?", ["3500"]),
    ("How many leave days does E202 have?", ["27.5"]),
    ("E101 accrues leave at the policy rate. What will E101's balance be after 6 more months?", ["21"]),
    ("What is the total international per-diem for a 4-day trip, in INR?", ["26400"]),
    ("Am I allowed to work from home 3 days a week?", ["2"]),
]

ARCHS = {
    "A single": lambda q: ask(single, q),
    "B supervisor": lambda q: ask(supervisor, q),
    "C router": lambda q: router.invoke({"question": q})["answer"],
}


def correct(ans: str, expected: list[str]) -> bool:
    norm = ans.replace(",", "").replace("₹", "")
    return any(e in norm for e in expected)


if __name__ == "__main__":
    print(f"model={MODEL}\n")
    print(f"{'arch':<13}{'task':>4}{'ok':>4}{'calls':>6}{'tok_in':>8}{'tok_out':>8}{'sec':>7}")
    totals = {}
    for name, run in ARCHS.items():
        t = totals.setdefault(name, [0, 0, 0, 0, 0.0])
        for i, (q, exp) in enumerate(TASKS, 1):
            METER.reset()
            t0 = time.perf_counter()
            try:
                ans = run(q)
            except Exception as e:  # recursion limit, bad tool args, etc. count as failures
                ans = f"ERROR {type(e).__name__}: {e}"
            dt = time.perf_counter() - t0
            ok = correct(ans, exp)
            print(f"{name:<13}{i:>4}{'Y' if ok else 'n':>4}{METER.calls:>6}{METER.tin:>8}{METER.tout:>8}{dt:>7.1f}"
                  f"   {ans[:70]!r}")
            for k, v in enumerate([ok, METER.calls, METER.tin, METER.tout, dt]):
                t[k] += v
    print("\nTOTALS")
    for name, (ok, c, ti, to, dt) in totals.items():
        print(f"{name:<13} correct={ok}/{len(TASKS)}  llm_calls={c}  tokens_in={ti}  tokens_out={to}  time={dt:.0f}s")
