"""Day 49 lab, part B: the tool loop in langchain-core, and the three things it will not do for you.

Offline: a scripted fake chat model stands in for the LLM. Needs only langchain-core.
Run:  python lcel_tool_loop.py
"""
from __future__ import annotations

import hashlib
import json

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableLambda
from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field, ValidationError

TASK_ID = "task-7781"


# ---------- a back end that loses the reply to the first refund request ----------
class Payments:
    def __init__(self) -> None:
        self.ledger: list[tuple[str, float]] = []
        self.seen: dict[str, str] = {}
        self.calls = 0

    def refund(self, order_id: str, amount: float, key: str | None) -> str:
        self.calls += 1
        if key and key in self.seen:
            return self.seen[key]                      # same key: return the first result, write nothing
        self.ledger.append((order_id, amount))
        receipt = f"refund-{len(self.ledger):03d}"
        if key:
            self.seen[key] = receipt
        if self.calls == 1:
            raise TimeoutError("payments API: no reply within 3s")   # the write happened, the ack did not arrive
        return receipt


PAYMENTS = Payments()
USE_KEYS = False


def idempotency_key(tool_name: str, args: dict) -> str:
    """Derived from the task and the logical call, so every retry of the same call gets the same key."""
    blob = json.dumps({"task": TASK_ID, "tool": tool_name, "args": args}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


# ---------- the tool contract: a strict Pydantic schema is the server-side check ----------
class RefundArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: str = Field(pattern=r"^ORD-\d{5}$", description="Order id, e.g. ORD-10422")
    amount: float = Field(gt=0, le=500, description="Refund amount in EUR, at most 500")


@tool(args_schema=RefundArgs)
def create_refund(order_id: str, amount: float) -> str:
    """Refund part or all of an order to the original payment method."""
    args = {"order_id": order_id, "amount": amount}
    key = idempotency_key("create_refund", args) if USE_KEYS else None
    return PAYMENTS.refund(order_id, amount, key)


TOOLS = {create_refund.name: create_refund}


def execute(call: dict) -> ToolMessage:
    """Run one tool call. Invalid arguments become an error message the model can read."""
    try:
        return TOOLS[call["name"]].invoke(call)        # a tool-call dict in, a ToolMessage out
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        return ToolMessage(content=f"Invalid arguments: {problems}", tool_call_id=call["id"], status="error")


# Retry only what is worth retrying. The default retries on every Exception.
safe_execute = RunnableLambda(execute).with_retry(
    retry_if_exception_type=(TimeoutError,), stop_after_attempt=3, wait_exponential_jitter=False
)


def scripted_model() -> GenericFakeChatModel:
    """What a real model might emit: one bad call, one corrected call, then the answer."""
    return GenericFakeChatModel(messages=iter([
        AIMessage(content="", tool_calls=[{"name": "create_refund", "id": "call_1",
                                           "args": {"order_id": "10422", "amount": -40}}]),
        AIMessage(content="", tool_calls=[{"name": "create_refund", "id": "call_2",
                                           "args": {"order_id": "ORD-10422", "amount": 40}}]),
        AIMessage(content="Your refund of EUR 40 for ORD-10422 has been issued."),
    ]))


def run_task(use_keys: bool) -> None:
    global PAYMENTS, USE_KEYS
    PAYMENTS, USE_KEYS = Payments(), use_keys
    model = scripted_model()
    messages: list = [HumanMessage("Refund 40 euros on order 10422, the kettle arrived broken.")]
    for turn in range(1, 6):                           # hard cap on turns: a loop needs a budget
        ai = model.invoke(messages)
        messages.append(ai)
        if not ai.tool_calls:
            print(f"  turn {turn}: answer  -> {ai.content}")
            break
        for call in ai.tool_calls:
            result = safe_execute.invoke(call)
            messages.append(result)
            print(f"  turn {turn}: {call['name']}({call['args']}) -> [{result.status}] {result.content}")
    print(f"  back-end requests: {PAYMENTS.calls}   refunds in the ledger: {len(PAYMENTS.ledger)}"
          f"   {'OK' if len(PAYMENTS.ledger) == 1 else 'DUPLICATE REFUND'}")


def fallback_demo() -> None:
    def primary(_: str) -> str:
        raise ConnectionError("provider A: 529 overloaded")

    def buggy(_: str) -> str:
        return {}["missing"]                           # a programming error, not an outage

    backup = RunnableLambda(lambda q: f"(provider B) answer to: {q}")
    narrow = (ConnectionError, TimeoutError)
    print("  outage, narrow fallback :", RunnableLambda(primary).with_fallbacks([backup], exceptions_to_handle=narrow).invoke("hi"))
    print("  bug, default fallback   :", RunnableLambda(buggy).with_fallbacks([backup]).invoke("hi"), " <- the bug is hidden")
    try:
        RunnableLambda(buggy).with_fallbacks([backup], exceptions_to_handle=narrow).invoke("hi")
    except KeyError as exc:
        print(f"  bug, narrow fallback    : KeyError({exc}) raised  <- the bug is visible")


if __name__ == "__main__":
    print("1. Tool loop with retries, no idempotency key")
    run_task(use_keys=False)
    print("\n2. Same loop, key derived from task + tool + arguments")
    run_task(use_keys=True)
    print("\n3. with_fallbacks: what the default catches")
    fallback_demo()
