"""
Day 16 - Provider-neutral tool-calling harness (runs locally on Ollama).

One canonical tool registry -> adapters for OpenAI / Anthropic / Gemini / Ollama,
plus a production-style execution loop: Pydantic validation, parallel execution,
timeouts, retries for transient errors, error-as-tool-result, loop guard,
step budget and a human-approval gate for side-effecting tools.

Usage:
  python tool_harness.py --mock            # no model needed: tests the loop logic
  python tool_harness.py                   # real run against Ollama (qwen3:4b)
  python tool_harness.py --model llama3.2:3b
  python tool_harness.py --show-schemas    # print the same tool for 4 providers
"""
from __future__ import annotations

import argparse
import json
import random
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field, ValidationError

# --------------------------------------------------------------------------
# 1. Canonical tool registry
# --------------------------------------------------------------------------

@dataclass
class ToolSpec:
    name: str
    description: str
    args: type[BaseModel]
    fn: Callable[..., Any]
    side_effect: bool = False      # writes -> approval gate + idempotency
    timeout_s: float = 5.0
    retries: int = 2               # only for TransientError


class TransientError(Exception):
    """Retryable failure (timeouts, 429/503 from a downstream API)."""


def strict_schema(model: type[BaseModel]) -> dict:
    """Pydantic JSON schema -> 'strict-mode friendly' schema:
    every property required, additionalProperties false, no titles."""
    s = model.model_json_schema()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            node.pop("title", None)
            if node.get("type") == "object":
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}).keys())
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(s)
    return s


# ---- adapters: same tool, four wire formats ------------------------------

def to_openai(t: ToolSpec) -> dict:      # Responses API function tool
    return {"type": "function", "name": t.name, "description": t.description,
            "parameters": strict_schema(t.args), "strict": True}


def to_anthropic(t: ToolSpec) -> dict:   # Messages API tool
    return {"name": t.name, "description": t.description,
            "input_schema": strict_schema(t.args), "strict": True}


def to_gemini(t: ToolSpec) -> dict:      # function declaration (OpenAPI-subset schema)
    s = strict_schema(t.args)
    s.pop("additionalProperties", None)  # not part of Gemini's classic schema subset
    return {"name": t.name, "description": t.description, "parameters": s}


def to_ollama(t: ToolSpec) -> dict:      # OpenAI chat-completions style
    return {"type": "function", "function": {
        "name": t.name, "description": t.description,
        "parameters": strict_schema(t.args)}}


# --------------------------------------------------------------------------
# 2. Demo tools (customer-support copilot)
# --------------------------------------------------------------------------

ORDERS = {
    "A100": {"status": "delivered", "total": 2499.0, "carrier": "BlueDart"},
    "A101": {"status": "in_transit", "total": 899.0, "carrier": "Delhivery"},
    "A102": {"status": "cancelled", "total": 15999.0, "carrier": None},
}
REFUNDS: dict[str, dict] = {}       # idempotency store: key -> result


class OrderArgs(BaseModel):
    order_id: str = Field(pattern=r"^A\d{3}$", description="Order id, e.g. A100")


class KBArgs(BaseModel):
    query: str = Field(min_length=3, description="Natural-language search query")


class RefundArgs(BaseModel):
    order_id: str = Field(pattern=r"^A\d{3}$")
    amount: float = Field(gt=0, description="Refund amount in INR")
    reason: Literal["damaged", "late", "cancelled", "other"]


def get_order(order_id: str) -> dict:
    if order_id not in ORDERS:
        raise KeyError(f"order {order_id} not found")
    return {"order_id": order_id, **ORDERS[order_id]}


def get_tracking(order_id: str) -> dict:
    if random.random() < 0.35:                       # flaky downstream
        raise TransientError("carrier API 503")
    o = get_order(order_id)
    return {"order_id": order_id, "carrier": o["carrier"],
            "eta_days": 2 if o["status"] == "in_transit" else 0}


def search_kb(query: str) -> dict:
    kb = {"refund": "Refunds allowed within 30 days; max = order total.",
          "shipping": "Standard shipping 3-5 days; express 1-2 days."}
    hits = [v for k, v in kb.items() if k in query.lower()]
    return {"hits": hits or ["No article found."]}


def issue_refund(order_id: str, amount: float, reason: str) -> dict:
    o = get_order(order_id)
    if amount > o["total"]:
        raise ValueError(f"amount {amount} exceeds order total {o['total']}")
    return {"refund_id": "R-" + uuid.uuid4().hex[:6], "order_id": order_id,
            "amount": amount, "reason": reason}


TOOLS = {t.name: t for t in [
    ToolSpec("get_order", "Look up an order's status and total by order id.", OrderArgs, get_order),
    ToolSpec("get_tracking", "Get carrier and ETA for an order.", OrderArgs, get_tracking, timeout_s=3),
    ToolSpec("search_kb", "Search the support knowledge base for policy articles.", KBArgs, search_kb),
    ToolSpec("issue_refund", "Issue a refund. Side-effecting: requires human approval.",
             RefundArgs, issue_refund, side_effect=True),
]}

# --------------------------------------------------------------------------
# 3. Executor: validate -> approve -> run with timeout/retry -> structured result
# --------------------------------------------------------------------------

TOOL_POOL = ThreadPoolExecutor(max_workers=8)   # runs tool functions
CALL_POOL = ThreadPoolExecutor(max_workers=8)   # runs execute() per call (separate pool -> no nested-pool deadlock)


@dataclass
class Stats:
    calls: int = 0
    invalid_args: int = 0
    tool_errors: int = 0
    retries: int = 0
    blocked: int = 0
    tools_used: list[str] = field(default_factory=list)


def approve(spec: ToolSpec, args: dict, auto: bool | None) -> bool:
    if auto is not None:
        return auto
    ans = input(f"\n  APPROVAL NEEDED -> {spec.name}({args}) [y/N]: ")
    return ans.strip().lower() == "y"


def execute(name: str, raw_args: Any, stats: Stats, auto_approve: bool | None) -> dict:
    stats.calls += 1
    stats.tools_used.append(name)
    spec = TOOLS.get(name)
    if spec is None:
        stats.invalid_args += 1
        return {"ok": False, "error": f"unknown tool '{name}'. Available: {list(TOOLS)}"}
    if isinstance(raw_args, str):                    # some providers send JSON strings
        try:
            raw_args = json.loads(raw_args or "{}")
        except json.JSONDecodeError as e:
            stats.invalid_args += 1
            return {"ok": False, "error": f"arguments were not valid JSON: {e}"}
    try:
        args = spec.args.model_validate(raw_args).model_dump()
    except ValidationError as e:
        stats.invalid_args += 1
        # Give the model a compact, actionable error so it can self-repair.
        errs = [f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()]
        return {"ok": False, "error": "invalid arguments", "details": errs}

    if spec.side_effect:
        key = f"{name}:{json.dumps(args, sort_keys=True)}"   # idempotency key
        if key in REFUNDS:
            return {"ok": True, "result": REFUNDS[key], "note": "duplicate call - returned cached result"}
        if not approve(spec, args, auto_approve):
            stats.blocked += 1
            return {"ok": False, "error": "rejected by human reviewer; do not retry, tell the user"}

    for attempt in range(spec.retries + 1):
        fut = TOOL_POOL.submit(spec.fn, **args)
        try:
            result = fut.result(timeout=spec.timeout_s)
            if spec.side_effect:
                REFUNDS[key] = result
            return {"ok": True, "result": result}
        except (TransientError, FutTimeout) as e:
            if attempt < spec.retries:
                stats.retries += 1
                time.sleep(0.2 * 2 ** attempt + random.random() * 0.1)  # backoff + jitter
                continue
            stats.tool_errors += 1
            return {"ok": False, "error": f"transient failure after retries: {e!r}", "retryable": False}
        except Exception as e:                       # business error: don't retry
            stats.tool_errors += 1
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": False, "error": "unreachable"}


# --------------------------------------------------------------------------
# 4. Agent loop (Ollama chat API)
# --------------------------------------------------------------------------

SYSTEM = ("You are a support copilot. Use tools for any order fact; never guess. "
          "Call independent tools in parallel. If a tool returns ok=false, fix the "
          "arguments once or explain the problem to the user. Answer concisely.")


def run(client, model: str, user_msg: str, auto_approve: bool | None = True,
        max_steps: int = 6, verbose: bool = True) -> tuple[str, Stats]:
    stats = Stats()
    msgs: list[dict] = [{"role": "system", "content": SYSTEM},
                        {"role": "user", "content": user_msg}]
    seen: dict[str, int] = {}
    tools = [to_ollama(t) for t in TOOLS.values()]
    for step in range(max_steps):
        resp = client.chat(model=model, messages=msgs, tools=tools,
                           options={"temperature": 0})
        m = resp.message
        calls = m.tool_calls or []
        msgs.append({"role": "assistant", "content": m.content or "",
                     "tool_calls": [c.model_dump() if hasattr(c, "model_dump") else c
                                    for c in calls]})
        if not calls:
            return m.content or "", stats
        if verbose:
            print(f"  step {step}: {len(calls)} tool call(s) -> "
                  + ", ".join(f"{c.function.name}({dict(c.function.arguments)})" for c in calls))
        # Loop guard: the same call 3x means the model is stuck.
        for c in calls:
            sig = c.function.name + json.dumps(dict(c.function.arguments), sort_keys=True)
            seen[sig] = seen.get(sig, 0) + 1
            if seen[sig] >= 3:
                return "[aborted: repeated identical tool call - escalate to human]", stats
        # Parallel execution of independent calls; results appended in call order.
        futs = [CALL_POOL.submit(execute, c.function.name, dict(c.function.arguments),
                            stats, auto_approve) for c in calls]
        for c, f in zip(calls, futs):
            out = f.result()
            msgs.append({"role": "tool", "tool_name": c.function.name,
                         "content": json.dumps(out)})
    return "[aborted: step budget exhausted]", stats


# --------------------------------------------------------------------------
# 5. Mock client (lets you test the harness with no model)
# --------------------------------------------------------------------------

class _Fn:
    def __init__(self, name, arguments): self.name, self.arguments = name, arguments
class _Call:
    def __init__(self, name, args): self.function = _Fn(name, args)
    def model_dump(self): return {"function": {"name": self.function.name, "arguments": self.function.arguments}}
class _Msg:
    def __init__(self, content="", tool_calls=None): self.content, self.tool_calls = content, tool_calls
class _Resp:
    def __init__(self, msg): self.message = msg


class MockClient:
    """Scripted 'model': parallel calls, a bad-arg call, self-repair, final answer."""
    def chat(self, model, messages, tools, options=None):
        n_tool = sum(1 for m in messages if m["role"] == "tool")
        if n_tool == 0:
            return _Resp(_Msg(tool_calls=[_Call("get_order", {"order_id": "A101"}),
                                          _Call("get_tracking", {"order_id": "A101"}),
                                          _Call("search_kb", {"query": "ok"})]))   # too short -> invalid
        if n_tool == 3:
            return _Resp(_Msg(tool_calls=[_Call("search_kb", {"query": "shipping policy"})]))
        return _Resp(_Msg(content="Order A101 is in transit with Delhivery, ETA ~2 days."))


# --------------------------------------------------------------------------
# 6. Mini eval: tool selection + arg validity + latency
# --------------------------------------------------------------------------

CASES = [
    ("What's the status of order A100?", {"get_order"}),
    ("Where is my order A101 and when will it arrive?", {"get_tracking"}),
    ("What is your refund policy?", {"search_kb"}),
    ("Compare the totals of orders A100 and A102.", {"get_order"}),
    ("Order A100 arrived damaged, refund the full 2499.", {"issue_refund"}),
    ("Refund 99999 on order A101 because it was late.", {"issue_refund", "get_order"}),
    ("What's the capital of France?", set()),
    ("Status of order B999?", {"get_order"}),
]


def evaluate(client, model: str) -> None:
    print(f"\n=== Eval on {model} ({len(CASES)} cases) ===")
    hit = 0; tot = Stats(); t0 = time.perf_counter()
    for q, expected in CASES:
        t = time.perf_counter()
        ans, s = run(client, model, q, auto_approve=True, verbose=False)
        used = set(s.tools_used)
        ok = (expected <= used) if expected else not used
        hit += ok
        for f in ("calls", "invalid_args", "tool_errors", "retries", "blocked"):
            setattr(tot, f, getattr(tot, f) + getattr(s, f))
        print(f"{'PASS' if ok else 'FAIL'} {time.perf_counter()-t:5.1f}s  used={sorted(used)!s:40} q={q[:45]}")
    print(f"\nTool-selection accuracy: {hit}/{len(CASES)}  | calls={tot.calls} "
          f"invalid_args={tot.invalid_args} tool_errors={tot.tool_errors} "
          f"retries={tot.retries}  | total {time.perf_counter()-t0:.1f}s")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3:4b")
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--show-schemas", action="store_true")
    ap.add_argument("--no-eval", action="store_true")
    a = ap.parse_args()
    random.seed(7)

    if a.show_schemas:
        t = TOOLS["issue_refund"]
        for fn in (to_openai, to_anthropic, to_gemini, to_ollama):
            print(f"--- {fn.__name__} ---\n{json.dumps(fn(t), indent=2)}")
        raise SystemExit

    if a.mock:
        client = MockClient()
    else:
        from ollama import Client
        client = Client()                       # http://localhost:11434

    print("=== Demo conversation ===")
    ans, st = run(client, a.model, "Where is order A101 and what's your shipping policy?")
    print("\nANSWER:", ans)
    print("STATS:", st)
    if not a.no_eval and not a.mock:
        evaluate(client, a.model)
