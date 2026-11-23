from aiarch.adapters.telemetry.tracer import JsonTracer
from aiarch.domain.models import Principal
from aiarch.services.agents.agent import Agent, Step
from aiarch.services.agents.tools import ToolRegistry, calculator_tool, safe_eval, ticket_tool

P = Principal("u", "acme")


def _agent(planner=None, approve=lambda t, a, p: False, tickets=None):
    reg = ToolRegistry()
    reg.register(calculator_tool())
    reg.register(ticket_tool(tickets if tickets is not None else []))
    from aiarch.services.agents.agent import RulePlanner
    return Agent(reg, planner or RulePlanner(), JsonTracer(sink=[]), approve=approve, max_steps=3)


def test_calculator_path():
    r = _agent().run("what is 12 * (3 + 4)", P)
    assert r.stopped_reason == "final" and r.steps[0].output == {"result": 84}


def test_side_effect_requires_approval():
    tickets = []
    r = _agent(tickets=tickets).run("open a ticket: laptop broken", P)
    assert r.stopped_reason == "approval_denied" and tickets == []
    r = _agent(approve=lambda t, a, p: True, tickets=tickets).run("open a ticket: laptop broken", P)
    assert r.stopped_reason == "final" and len(tickets) == 1


def test_max_steps_stops_runaway_loop():
    class Loop:
        def next_step(self, goal, tools, history):
            return Step("calculator", {"expression": "1+1"})
    assert _agent(Loop()).run("loop", P).stopped_reason == "max_steps"


def test_unknown_tool_becomes_observation_not_crash():
    class Rogue:
        def next_step(self, goal, tools, history):
            return Step(None, final="x") if history else Step("delete_everything", {})
    r = _agent(Rogue()).run("x", P)
    assert r.steps[0].output["error"] == "tool_not_allowed"


def test_safe_eval_rejects_code():
    import pytest
    with pytest.raises(ValueError):
        safe_eval("__import__('os').system('dir')")
