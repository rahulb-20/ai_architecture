# Day 18 — Multi-agent patterns

> Part of the [GenAI Architect study path](../../README.md) · Agentic Systems · [Original interactive pack](https://claude.ai/artifact/CThNqShw5Uz3ZYLt9P3K2g) · [Offline HTML](../../archive/html/day-18.html)


Week 3 · Agentic systems · Day 18 of 30

# Multi-agent patterns, and when not to use them

Supervisor, hierarchical, swarm/handoffs, ReAct vs plan-and-execute. Today's goal: be able to defend a single-agent design in a review as confidently as a multi-agent one, with numbers.

langgraph 1.2.xlangchain 1.4.xOllama · qwen2.5:3b≈ 75 min

## Why this matters for the JD

The JD names *"agentic frameworks (LangChain, LangGraph, MCP, tool calling, memory, planning, multi-agent, guardrails)"* and asks for *"HLD/LLD, NFRs, decision logs, cost estimates"*. Multi-agent is where those meet: the topology you pick sets token cost, latency, debuggability and the blast radius of a prompt injection. Interviewers increasingly probe the opposite question too: why you *didn't* go multi-agent.

## Concept brief

Strip away framework names and there are only a few decisions. **Who owns control flow** (code or an LLM)? **Who owns the conversation** with the user (one agent, or whoever was handed the baton)? **What context each worker sees** (the full history, or a brief written by the caller)? Everything below is a point in that space.

![Four topologies: single agent with tools, router workflow, supervisor with sub-agents as tools, and peer handoffs](diagrams/fig-1.svg)

Blue arrows carry control. In C the supervisor gets results back and stays in charge; in D control moves and stays moved.

### Supervisor vs hierarchical vs swarm

**Supervisor**: one coordinator calls specialists, usually exposed as tools. It sees summaries, never the specialists' raw tool traces, so its context stays small. LangChain's own docs now recommend building this directly with tool-calling sub-agents; the `langgraph-supervisor` package says so in its README and has had no release since Nov 2025. **Hierarchical** nests supervisors (team leads). It only pays off when each team has a large, distinct toolset; every layer adds a round-trip and a place where the brief gets lossy. **Swarm / handoffs**: the active agent transfers control with a `Command(goto=…)` (LangGraph) or a `handoff` (OpenAI Agents SDK), and the receiving agent talks to the user directly. Good for customer-service triage where the user should stay with a billing specialist for many turns. Bad when you need one owner for policy, audit and the final answer.

### ReAct vs plan-and-execute

**ReAct** interleaves think → act → observe per step. It adapts well, but each step re-reads the growing history, so cost grows roughly with the square of the number of steps, and it can loop. **Plan-and-execute** spends one strong-model call producing a plan (a DAG of steps), then runs the steps with cheaper models or plain code, often in parallel, and replans only on failure. You get bounded cost, an auditable plan (put it in front of a human for approval) and parallel fan-out. The price is brittleness when step 2's result should have changed step 3. A useful middle ground is *orchestrator-workers*: the lead plans, spawns parallel workers with self-contained briefs, then synthesises. This is the shape of Anthropic's research system.

**The numbers to remember.** Anthropic reports agents use about 4× the tokens of chat, and multi-agent systems about 15×. Their multi-agent research setup beat single-agent Opus by 90.2% on an internal eval, and token usage alone explained about 80% of performance variance on BrowseComp. Multi-agent buys quality on *breadth-first, parallelisable* work by spending tokens. Cognition (Devin) argues the opposite for tightly coupled work like coding: sub-agents act on partial context and make conflicting implicit decisions. The MAST study (UC Berkeley) catalogued 14 failure modes in 3 groups: specification/system design, inter-agent misalignment, and task verification.

### Platform mapping

| Pattern | LangGraph / LangChain | AWS | Azure | GCP | IBM |
| --- | --- | --- | --- | --- | --- |
| Supervisor | sub-agents as `@tool` around `create_agent` | Bedrock multi-agent collaboration (supervisor mode) | Agent Framework: Magentic / group-chat manager | ADK `AgentTool`, `sub_agents` | watsonx Orchestrate collaborator agents |
| Router | `add_conditional_edges` + structured output | Bedrock supervisor with routing | Agent Framework workflows (sequential / concurrent) | ADK `SequentialAgent` / `ParallelAgent` | Orchestrate agent routing |
| Handoffs | `Command(goto=…, graph=Command.PARENT)` | Strands / AgentCore (custom) | Agent Framework handoff orchestration | ADK transfer to agent | Orchestrate agent-to-agent |

Managed-service feature names change often; check the current console docs before quoting them in a client deck.

## Trade-offs & decision table

| Option | Pick when | Failure modes | Cost / latency |
| --- | --- | --- | --- |
| Single agent + tools | ≤ ~15 well-described tools, one domain, sequential reasoning. The default. | Tool confusion as the toolset grows; context bloat on long tasks | Baseline: 1 context, N calls |
| Router / workflow | Intents are known and enumerable (HR vs IT vs Finance); compliance wants fixed paths | Misclassification is silent; "both/other" intents fall through | +1 cheap classify call; best p95 control |
| Supervisor (tools) | Distinct specialist toolsets or separate permissions/data; want context isolation | Lossy briefs (supervisor forgets to pass facts); double summarisation; supervisor answers itself | ~2–3× single; serial unless the model issues parallel tool calls |
| Hierarchical | Many teams with 10+ tools each; org boundaries mirror agent boundaries | Telephone-game drift; deep stacks hard to trace | Multiplies per layer; latency stacks |
| Handoffs / swarm | Multi-turn conversations that belong to one specialist (support triage) | Ping-pong loops; no single owner for guardrails; shared history leaks context across trust boundaries | Cheap per turn; unbounded per session without a hop limit |
| Plan-and-execute | Long tasks with parallelisable steps; plan needs human approval | Stale plan; over-planning simple tasks | 1 strong call + cheap workers; parallel cuts wall-clock |
| Orchestrator-workers | Breadth-first research, many independent sub-questions | Duplicate work; weak synthesis; 15× token bills | Highest tokens, lowest wall-clock for wide tasks |

**Review heuristics.** Start with A. Move to B when the intents are enumerable. Move to C only when you can name the isolation benefit: different tools, different data permissions, or a context window that genuinely overflows. Every topology needs a hop/recursion budget, a token budget per request, and traces that show the brief each sub-agent received.

## Hands-on: benchmark three topologies (60–90 min)

Build the same tiny HR/finance assistant three ways (single agent, supervisor with sub-agents as tools, router workflow) on identical tools and five tasks. Measure correctness, LLM calls, tokens and seconds. The deliverable is a 5-line table you could paste into an ADR.

Runs natively on Windows 11 with Ollama on the RTX 3050. No WSL2 or Docker needed. A 3B model keeps the comparison fast. Small models make the hand-off failures *more* visible, which is the point of the exercise.

PowerShell · setup

```text
# 1) Ollama for Windows (native, no WSL2/Docker needed)
winget install --id Ollama.Ollama -e
ollama pull qwen2.5:3b          # ~1.9 GB, fits the 6 GB RTX 3050 with room for 8k context
# optional comparison model
ollama pull llama3.2:3b

# 2) Project + venv (PowerShell)
mkdir day18; cd day18
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1      # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
python -m pip install -U "langgraph>=1.2,<2" "langchain>=1.4,<2" "langchain-ollama>=1.1,<2"

# 3) Run (save the script below as multiagent_bench.py)
python multiagent_bench.py
$env:OLLAMA_MODEL="llama3.2:3b"; python multiagent_bench.py
```

multiagent\_bench.py

`lab/day18_lab.py`

```python
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
```

### Expected output (shape, not exact numbers)

Your numbers will differ with model and driver. What should show up: B makes roughly 2× the LLM calls and tokens of A, and fails task 3 or 4 whenever the supervisor forgets to forward the accrual rate or per-diem to the finance specialist. C is close to A on cost and gets more predictable. The single-tool tasks (1, 2, 5) are cheapest under C.

illustrative console output

```text
model=qwen2.5:3b

arch         task  ok calls  tok_in tok_out    sec
A single        1   Y     2     610      38    2.9   'The domestic per-diem is INR 3500 per day.'
A single        3   Y     4    1490      96    6.8   "E101's balance after 6 months will be 21 days."
B supervisor    3   n     6    2950     170   11.4   'E101 will have 13.5 days.'   <- facts lost in hand-off
C router        4   Y     5    2310     120    8.7   '26400'
...
TOTALS
A single      correct=4/5  llm_calls=14  tokens_in=5200   tokens_out=330  time=26s
B supervisor  correct=3/5  llm_calls=27  tokens_in=11800  tokens_out=700  time=55s
C router      correct=4/5  llm_calls=19  tokens_in=7400   tokens_out=410  time=36s
```

### What to write down

- Calls and tokens per correct answer for each topology. This is your cost-per-resolution metric.
- For every B failure, the exact brief the supervisor sent. Add `print(request)` in `ask_finance_specialist`. That brief is the "inter-agent misalignment" category from MAST, observed first-hand.

### Stretch goals

1. **Add topology D (handoffs).** Rebuild B as a `StateGraph` with two agent nodes that hand off via a tool returning `Command(goto="finance", update={...})`. Add a hop counter in state that ends the graph after 4 transfers. Compare token growth: shared history vs briefs.
2. **Inject a failure and bound it.** Make `usd_to_inr` raise on 30% of calls. Pass `config={"recursion_limit": 8}` on `invoke`. Report which topology degrades gracefully, and where you would put retry vs fail-fast in an LLD.

Verified this run: package versions on PyPI, script imports and graph compilation against langgraph 1.2 / langchain 1.4, tool functions. Not run against a live Ollama model here, so treat the console sample as illustrative.

## GitHub repos to study

- [langchain-ai/langgraph](https://github.com/langchain-ai/langgraph)v1.2.18 · released 26 Oct 2026  
  Read `libs/langgraph/langgraph/types.py` for `Command` and `Send`. These two primitives are how handoffs and map-reduce fan-out are actually built.
- [openai/openai-agents-python](https://github.com/openai/openai-agents-python)v0.24.0 · Oct 2026  
  Compare `handoffs` with `agent.as_tool()` in the examples folder. It is the cleanest side-by-side of "transfer control" vs "call as tool" in any SDK.
- [microsoft/agent-framework](https://github.com/microsoft/agent-framework)orchestrations 1.0 · Jul 2026  
  The successor to AutoGen and Semantic Kernel. Study the sequential, concurrent, handoff, group-chat and Magentic orchestrations. This is what Azure-shop clients will ask about.
- [langchain-ai/langgraph-supervisor-py](https://github.com/langchain-ai/langgraph-supervisor-py)maintenance · last release Nov 2025  
  Read `handoff.py` to see how handoff tools are generated, then note why LangChain now recommends hand-rolled tool-calling supervisors instead.

## Read / watch

- [Anthropic: How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system). Source of the 4×/15× token figures, plus prompting rules for delegation briefs and effort scaling.
- [Cognition: Don't Build Multi-Agents](https://cognition.ai/blog/dont-build-multi-agents), read together with [LangChain's multi-agent guide](https://docs.langchain.com/oss/python/langchain/multi-agent) (subagents, handoffs, router, custom). Read them as one debate.
- [Why Do Multi-Agent LLM Systems Fail? (MAST, arXiv 2503.13657)](https://arxiv.org/abs/2503.13657). Use its taxonomy as a checklist for design reviews and eval tagging.

## Interview questions

**A stakeholder wants "a team of agents" for an HR knowledge assistant. How do you respond?**

Start from the workload, not the org chart. If intents are enumerable (leave, payroll, policy), propose a router workflow over one RAG agent: fixed paths, one owner of guardrails and citations, testable per route. Go multi-agent only for a named isolation need: different data permissions (payroll PII), toolsets too large for one prompt, or parallel breadth-first work. Quantify it: multi-agent runs at roughly 15× chat tokens versus about 4× for a single agent, and you'll show the eval delta that justifies it.

**Supervisor-with-tools vs handoffs: what changes architecturally?**

Ownership and context. With tools, the supervisor keeps the user, sends each specialist a brief and gets back a summary. Context is isolated, guardrails and audit sit in one place, and the risk is a lossy brief. With handoffs, control transfers and the specialist talks to the user, usually over shared history. That gives a natural multi-turn UX, but every agent sees everything (a trust-boundary issue), loops are possible, and there is no single place to enforce output policy. I use handoffs for long specialist conversations, tools for everything else, and a hop limit in both.

**When do you choose plan-and-execute over ReAct?**

When steps are parallelisable, the plan needs approval or audit, or cost must be bounded. One strong-model call writes a DAG, cheap models or code execute it in parallel, and you replan only on failure. ReAct wins when each observation should reshape the next step (debugging, exploratory search). In ReAct, cost grows super-linearly with steps because history is re-read each turn. Hybrid: plan at the top level, ReAct inside each worker with a step budget.

**How do you evaluate and observe a multi-agent system in production?**

Trace every hop with OpenTelemetry GenAI spans (Langfuse/LangSmith/Phoenix) so each sub-agent's input brief is visible. Evaluate at three levels: the final answer (task success, faithfulness), the trajectory (right agent, right tool, hop count), and the component (each specialist on its own golden set). Tag failures with the MAST categories: spec/design, inter-agent misalignment, verification. Track cost per resolved task, p95 latency and loop rate as SLOs, and run topology changes through the same eval gate as prompt changes.

**Your multi-agent prototype costs 6× the single-agent one. How do you bring it down without losing quality?**

Measure first: find which hops add quality via ablation on the golden set. Then cut cost: route easy intents around the supervisor; run specialists on smaller models and keep the strong model for planning and synthesis; return structured summaries instead of raw traces; parallelise independent calls to recover wall-clock; use prompt caching on stable system prompts and tool schemas; enforce per-request token and hop budgets with graceful degradation. Often the answer is collapsing two agents back into one with a better tool description.

## Architect's lens: ADR-018

Title
:   Agent topology for the enterprise employee assistant (HR + IT + Finance)

Status
:   Accepted

Context
:   About 40 tools across three domains; payroll data needs separate RBAC; 80% of traffic is single-intent FAQ; p95 target 6 s; budget ≤ ₹2 per conversation; audit needs one accountable answer path.

Options
:   (1) One agent with all tools. (2) Router workflow to per-domain agents. (3) Supervisor with domain sub-agents as tools. (4) Peer handoffs/swarm.

Decision
:   (2) as the front door: a structured-output classifier routes single-intent requests to a domain agent. Cross-domain requests ("both") go to (3), a supervisor that calls domain agents as tools. Reject (4) for now: no single guardrail owner, and shared history crosses the payroll RBAC boundary. Payroll runs as its own sub-agent with its own credentials, so isolation is enforced by identity rather than prompts.

Consequences
:   + Predictable cost and latency on 80% of traffic; each route testable in CI; clean RBAC boundary. − Misroutes are silent, so log the classifier confidence and run a weekly misroute review. − Supervisor briefs can be lossy, so require a typed brief schema (facts, constraints, expected output). Revisit if cross-domain traffic exceeds 30% or the eval shows the router capping quality.

## Tomorrow

Day 19: Agent memory. Short-term, long-term, episodic, summarisation, vector memory, and the LangGraph store.

Sources: [PyPI langgraph](https://pypi.org/project/langgraph/) · [PyPI langgraph-supervisor](https://pypi.org/project/langgraph-supervisor/) · [PyPI openai-agents](https://pypi.org/project/openai-agents/) · [LangChain multi-agent docs](https://docs.langchain.com/oss/python/langchain/multi-agent) · [MS Agent Framework orchestrations 1.0](https://devblogs.microsoft.com/agent-framework/agent-frameworks-orchestration-patterns-reach-1-0) · [Anthropic engineering](https://www.anthropic.com/engineering/multi-agent-research-system) · [Cognition](https://cognition.ai/blog/dont-build-multi-agents) · [MAST paper](https://arxiv.org/abs/2503.13657)

## Lab files in this folder

- [`lab/day18_lab.py`](lab/day18_lab.py)
