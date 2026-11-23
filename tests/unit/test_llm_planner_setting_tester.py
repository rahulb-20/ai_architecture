"""Tester's adversarial cases for `agent_planner` (brief: docs/architecture/designs/llm-planner-setting.md).

Independent of the developer's `test_llm_planner.py`: the helpers are redefined here on purpose, so
these cases keep their meaning if that file changes. Nothing needs a model, a key or a network.
"""
import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

from aiarch.adapters.llm.fake import FakeLLM
from aiarch.adapters.telemetry.tracer import JsonTracer
from aiarch.api.app import create_app
from aiarch.cli import main as cli_main
from aiarch.config import Settings
from aiarch.container import build_container
from aiarch.domain.errors import AiArchError, PlannerOutputInvalid
from aiarch.domain.models import Principal, Usage
from aiarch.ports import LLMRequest, LLMResponse
from aiarch.services.agents.agent import Agent, LLMPlanner, Observation, RulePlanner, Step
from aiarch.services.agents.tools import Tool, ToolRegistry, calculator_tool, ticket_tool
from aiarch.services.cost import PriceBook
from aiarch.services.gateway import ModelGateway, Route

REPO = Path(__file__).resolve().parents[2]
SECRET = "SECRET-MODEL-TEXT-9f3a"
CALC = '{"tool": "calculator", "args": {"expression": "12 * (3 + 4)"}, "final": null}'
FINAL = '{"tool": null, "args": {}, "final": "The answer is 84."}'
TICKET = '{"tool": "create_ticket", "args": {"title": "t", "body": "b"}, "final": null}'
HR = '{"tool": "hr_lookup", "args": {}, "final": null}'


class Scripted:
    """Test-only `LLMClient`: one raw reply per call, in order."""

    name = "scripted"
    model = "scripted-1"

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.requests: list[LLMRequest] = []

    def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        assert self._replies, "unexpected extra model call"
        return LLMResponse(text=self._replies.pop(0), model=self.model, provider=self.name,
                           usage=Usage(input_tokens=1, output_tokens=1), latency_ms=0.0)


class ByContent:
    """Test-only `LLMClient` that is safe to share between threads: asks for `hr_lookup` until the
    request shows an observation, then gives a final answer."""

    name = "by-content"
    model = "by-content-1"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls = 0

    def generate(self, request: LLMRequest) -> LLMResponse:
        with self._lock:
            self.calls += 1
        text = FINAL if "<observation" in request.messages[-1].content else HR
        return LLMResponse(text=text, model=self.model, provider=self.name,
                           usage=Usage(input_tokens=1, output_tokens=1), latency_ms=0.0)


class _NoArgs(BaseModel):
    pass


def _hr_tool(**kw) -> Tool:
    return Tool("hr_lookup", "HR only", _NoArgs, lambda a, p: {"ok": True},
                allowed_groups=frozenset({"hr"}), **kw)


def _planner(*replies: str) -> tuple[LLMPlanner, Scripted]:
    llm = Scripted(list(replies))
    return LLMPlanner(ModelGateway([Route(llm)], JsonTracer(sink=[]), PriceBook())), llm


def _tools() -> list[Tool]:
    return [calculator_tool(), ticket_tool([])]


def _container(clients=None, spans=None, **settings):
    return build_container(Settings(agent_planner="llm", **settings),
                           tracer=JsonTracer(sink=spans if spans is not None else []), llm_clients=clients)


def _reason(planner: LLMPlanner, tools: list[Tool]) -> str:
    with pytest.raises(PlannerOutputInvalid) as e:
        planner.next_step("x", tools, [])
    return e.value.reason


# --- the setting ----------------------------------------------------------------------------

@pytest.mark.parametrize("value", [None, 1, True, "LLM", "llm ", "rule,llm", "RulePlanner", b"llm"])
def test_settings_constructor_refuses_anything_but_the_two_values(value):
    with pytest.raises(ValidationError):
        Settings(agent_planner=value)


def test_real_process_environment_selects_the_planner(monkeypatch):
    monkeypatch.setenv("AIARCH_AGENT_PLANNER", "llm")
    assert isinstance(build_container().agent.planner, LLMPlanner)
    monkeypatch.setenv("AIARCH_AGENT_PLANNER", "rule")
    assert isinstance(build_container().agent.planner, RulePlanner)
    monkeypatch.delenv("AIARCH_AGENT_PLANNER")
    assert isinstance(build_container().agent.planner, RulePlanner)


def test_bad_value_in_the_environment_stops_start_up_instead_of_falling_back_to_rule(monkeypatch):
    monkeypatch.setenv("AIARCH_AGENT_PLANNER", "gpt")
    with pytest.raises(ValidationError):
        build_container()


def test_explicit_rule_setting_never_calls_the_model_even_for_several_runs(alice):
    llm = Scripted([])   # any call would assert
    c = build_container(Settings(agent_planner="rule"), tracer=JsonTracer(sink=[]), llm_clients=[llm])
    for goal in ("what is 2 + 2", "how many leave days", "open a ticket: broken laptop"):
        c.agent.run(goal, alice)
    assert llm.requests == []


def test_two_containers_do_not_share_planner_or_gateway():
    a, b = _container(), _container()
    pa, pb = a.agent.planner, b.agent.planner
    assert isinstance(pa, LLMPlanner) and isinstance(pb, LLMPlanner) and pa is not pb
    assert pa.gateway is a.gateway and pb.gateway is b.gateway and a.gateway is not b.gateway


def test_env_example_documents_the_setting_with_the_default_value():
    lines = (REPO / ".env.example").read_text(encoding="utf-8").splitlines()
    assert "AIARCH_AGENT_PLANNER=rule" in lines


def test_readme_names_the_setting_and_both_values():
    text = (REPO / "README.md").read_text(encoding="utf-8")
    line = next(ln for ln in text.splitlines() if "AIARCH_AGENT_PLANNER" in ln)
    assert "`rule`" in line and "`llm`" in line and "planner_invalid" in line


# --- the error type -------------------------------------------------------------------------

def test_planner_output_invalid_is_a_domain_error_with_only_a_reason():
    e = PlannerOutputInvalid("malformed")
    assert isinstance(e, AiArchError) and e.reason == "malformed" and str(e) == "malformed"
    assert e.args == ("malformed",)


# --- LLMPlanner.next_step: hostile or odd model output ----------------------------------------

@pytest.mark.parametrize("reply", [
    "﻿" + FINAL,                                   # byte-order mark
    FINAL + " trailing prose",
    FINAL + CALC,                                       # two objects
    "Sure! " + CALC,
    "null",
    "true",
    "42",
    "{",
    '{"tool": NaN, "args": {}, "final": null}',
    '{"tool": true, "args": {}, "final": null}',
    '{"tool": ["calculator"], "args": {}, "final": null}',
    '{"tool": {"name": "calculator"}, "args": {}, "final": null}',
    '{"tool": "calculator", "args": [], "final": null}',
    '{"tool": "calculator", "args": 0, "final": null}',
    '{"tool": "calculator", "args": false, "final": null}',
    '{"tool": null, "args": [], "final": "done"}',       # bad args refused even with a good final
    '{"tool": null, "args": {}, "final": ["done"]}',
    '{"tool": null, "args": {}, "final": true}',
    '{"tool": null, "args": {}, "final": 1.5}',
    '{"tool": null, "args": {}, "final": {"text": "done"}}',
    '{"tool": null, "args": {}, "final": "\\ud800"}',   # lone surrogate
    "[" * 100_000,                                      # nesting bomb
    '{"tool": "calculator", "args": ' + "[" * 100_000,
    "{'tool': 'calculator', 'args': {}, 'final': None}",   # Python repr, not JSON
])
def test_more_malformed_replies_are_refused(reply):
    planner, llm = _planner(reply)
    assert _reason(planner, _tools()) == "malformed"
    assert len(llm.requests) == 1   # no re-ask


@pytest.mark.parametrize("name", [
    "calculator\n", "calculator\u0000", "CALCULATOR", "calculator ", "​calculator",
    "create_ticket;calculator", "calculator,create_ticket", "search_kb", "*", "None", "null",
    "cаlculator",   # Cyrillic a
])
def test_near_miss_tool_names_are_refused(name):
    planner, _ = _planner(json.dumps({"tool": name, "args": {}, "final": None}))
    assert _reason(planner, _tools()) == "tool_not_allowed"


def test_disallowed_tool_is_refused_even_when_a_final_answer_is_also_given():
    planner, _ = _planner('{"tool": "delete_everything", "args": {}, "final": "all good"}')
    assert _reason(planner, _tools()) == "tool_not_allowed"


def test_duplicate_tool_key_cannot_smuggle_a_disallowed_tool():
    planner, _ = _planner('{"tool": "calculator", "tool": "delete_everything", "args": {}, "final": null}')
    assert _reason(planner, _tools()) == "tool_not_allowed"


def test_empty_tool_list_refuses_every_tool_but_accepts_a_final():
    planner, _ = _planner(CALC, FINAL)
    assert _reason(planner, []) == "tool_not_allowed"
    assert planner.next_step("x", [], []) == Step(None, {}, "The answer is 84.")


@pytest.mark.parametrize("final", ["", " ", "\n\t\r", " ", "   "])
def test_whitespace_only_finals_are_empty_plans(final):
    planner, _ = _planner(json.dumps({"tool": None, "args": {}, "final": final}))
    assert _reason(planner, _tools()) == "empty_plan"


def test_final_reply_ignores_args_and_keeps_the_text_as_given():
    planner, _ = _planner('{"tool": null, "args": {"expression": "1+1"}, "final": "  done \\n"}')
    assert planner.next_step("x", _tools(), []) == Step(None, {}, "  done \n")


def test_null_args_with_a_tool_become_an_empty_dict():
    planner, _ = _planner('{"tool": "calculator", "args": null, "final": null}')
    assert planner.next_step("x", _tools(), []) == Step("calculator", {})


def test_leading_and_trailing_whitespace_around_the_json_is_accepted():
    planner, _ = _planner("\n  " + CALC + "\n")
    assert planner.next_step("x", _tools(), []).tool == "calculator"


def test_args_are_passed_through_unvalidated_for_the_registry_to_judge():
    planner, _ = _planner('{"tool": "calculator", "args": {"expression": 5, "x": {"y": [1]}}, "final": null}')
    assert planner.next_step("x", _tools(), []) == Step("calculator", {"expression": 5, "x": {"y": [1]}})


def test_allowlist_is_taken_from_each_call_not_remembered(alice, hr_user):
    """Same planner, different callers: what the hr user may plan must not carry over to alice."""
    reg = ToolRegistry()
    reg.register(calculator_tool())
    reg.register(_hr_tool())
    planner, _ = _planner(HR, HR, HR)
    assert planner.next_step("x", reg.for_principal(hr_user), []) == Step("hr_lookup", {})
    assert _reason(planner, reg.for_principal(alice)) == "tool_not_allowed"
    assert planner.next_step("x", reg.for_principal(hr_user), []).tool == "hr_lookup"


def test_planner_recovers_after_an_invalid_reply():
    planner, _ = _planner("not json", CALC)
    assert _reason(planner, _tools()) == "malformed"
    assert planner.next_step("x", _tools(), []).tool == "calculator"


def test_system_message_lists_only_the_callers_tools_end_to_end(alice, hr_user):
    for principal, expected in ((alice, False), (hr_user, True)):
        llm = Scripted([FINAL])
        c = _container([llm])
        c.agent.registry.register(_hr_tool())
        c.agent.run("anything at all", principal)
        listed = json.loads(llm.requests[0].messages[0].content.split("TOOLS: ", 1)[1])
        assert ("hr_lookup" in [t["name"] for t in listed]) is expected


def test_history_reaches_the_model_as_observations():
    planner, llm = _planner(FINAL)
    planner.next_step("goal text", _tools(), [Observation("calculator", {"expression": "1+1"}, {"result": 2})])
    user = llm.requests[0].messages[-1].content
    assert user.startswith("GOAL: goal text") and "<observation tool='calculator'>" in user
    assert '{"result": 2}' in user


# --- Agent with agent_planner="llm" ---------------------------------------------------------

def test_planner_invalid_through_the_api_is_http_200_and_carries_no_model_text():
    spans: list[dict] = []
    c = _container([Scripted([f'{{"tool": "{SECRET}", "args": {{}}, "final": null}}'])], spans)
    with TestClient(create_app(c)) as client:
        resp = client.post("/v1/agent", json={"goal": "do something"})
    assert resp.status_code == 200
    assert resp.json() == {"final": "I could not plan this request.", "stopped_reason": "planner_invalid",
                           "steps": []}
    assert SECRET not in json.dumps(spans, default=str)


@pytest.mark.parametrize("reply", [f"not json {SECRET}", f'{{"tool": null, "args": "{SECRET}", "final": null}}'])
def test_malformed_model_text_never_reaches_result_or_spans(alice, reply):
    spans: list[dict] = []
    r = _container([Scripted([reply])], spans).agent.run("do something", alice)
    assert r.stopped_reason == "planner_invalid" and SECRET not in r.final
    assert SECRET not in json.dumps(spans, default=str)
    run = next(s for s in spans if s["name"] == "agent.run")
    assert run["attributes"]["agent.planner_error"] == "malformed" and run["status"] == "ok"


def test_default_fake_route_through_the_api_fails_closed_not_empty_final():
    with TestClient(create_app(_container())) as client:
        body = client.post("/v1/agent", json={"goal": "what is 12 * (3 + 4)"}).json()
    assert body["stopped_reason"] == "planner_invalid" and body["final"] and body["steps"] == []


def test_default_fake_route_makes_exactly_one_model_call(alice):
    fake = FakeLLM()
    r = _container([fake]).agent.run("what is 12 * (3 + 4)", alice)
    assert r.stopped_reason == "planner_invalid" and fake.calls == 1


def test_cli_agent_command_with_llm_setting_offline(monkeypatch, capsys):
    monkeypatch.setenv("AIARCH_AGENT_PLANNER", "llm")
    assert cli_main(["agent", "what is 12 * (3 + 4)"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"final": "I could not plan this request.", "stopped": "planner_invalid", "steps": []}


def test_cli_agent_command_default_is_still_the_rule_planner(monkeypatch, capsys):
    monkeypatch.delenv("AIARCH_AGENT_PLANNER", raising=False)
    assert cli_main(["agent", "what is 12 * (3 + 4)"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["stopped"] == "final" and out["steps"] == ["calculator"] and "84" in out["final"]


def test_step_limit_costs_exactly_max_steps_model_calls(alice):
    llm = Scripted([CALC] * 6)
    r = _container([llm]).agent.run("loop forever", alice)
    assert r.stopped_reason == "max_steps" and len(r.steps) == 5 and len(llm.requests) == 5


def test_invalid_arguments_are_an_observation_and_the_run_continues(alice):
    llm = Scripted(['{"tool": "calculator", "args": {"expr": "1+1"}, "final": null}', FINAL])
    r = _container([llm]).agent.run("what is 1 + 1", alice)
    assert r.stopped_reason == "final" and len(llm.requests) == 2
    assert r.steps[0].output["error"] == "invalid_arguments"
    assert "invalid_arguments" in llm.requests[1].messages[-1].content


def test_unsafe_calculator_expression_from_the_model_is_not_evaluated(alice):
    llm = Scripted(['{"tool": "calculator", "args": {"expression": "__import__(\'os\').getcwd()"}, "final": null}',
                    FINAL])
    r = _container([llm]).agent.run("run this", alice)
    assert r.stopped_reason == "final" and r.steps[0].output["error"] == "ValueError"


def test_invalid_reply_on_the_last_allowed_step_keeps_all_earlier_steps(alice):
    spans: list[dict] = []
    llm = Scripted([CALC] * 4 + ["{}"])
    r = _container([llm], spans).agent.run("keep going", alice)
    assert r.stopped_reason == "planner_invalid" and len(r.steps) == 4
    run = next(s for s in spans if s["name"] == "agent.run")
    assert run["attributes"]["agent.planner_error"] == "empty_plan"
    assert sum(s["name"] == "agent.tool" for s in spans) == 4


def test_budget_exceeded_part_way_keeps_the_steps_and_does_not_raise(alice):
    """Budget set to exactly what the first request needs: the observation pushes the second over."""
    goal = "what is 12 * (3 + 4)"
    probe = Scripted([FINAL])
    _container([probe]).agent.run(goal, alice)
    need = sum(len(m.content) for m in probe.requests[0].messages) // 4
    spans: list[dict] = []
    llm = Scripted([CALC, FINAL])
    r = _container([llm], spans, max_input_tokens=need).agent.run(goal, alice)
    assert r.stopped_reason == "planner_unavailable" and len(llm.requests) == 1
    assert [(s.tool, s.output) for s in r.steps] == [("calculator", {"result": 84})]
    run = next(s for s in spans if s["name"] == "agent.run")
    assert run["attributes"]["agent.planner_error"] == "BudgetExceeded"


def test_budget_exceeded_through_the_api_is_http_200():
    with TestClient(create_app(_container([Scripted([])], max_input_tokens=1))) as client:
        resp = client.post("/v1/agent", json={"goal": "what is 2 + 2"})
    assert resp.status_code == 200 and resp.json()["stopped_reason"] == "planner_unavailable"


def test_outage_part_way_keeps_the_steps(alice):
    class DiesAfterOne(FakeLLM):
        def generate(self, request: LLMRequest) -> LLMResponse:
            if self.calls == 0:
                self.calls += 1
                return LLMResponse(text=CALC, model=self.model, provider=self.name,
                                   usage=Usage(input_tokens=1, output_tokens=1), latency_ms=0.0)
            self._fail_remaining = 99
            return super().generate(request)

    r = _container([DiesAfterOne()]).agent.run("what is 12 * (3 + 4)", alice)
    assert r.stopped_reason == "planner_unavailable"
    assert [(s.tool, s.output) for s in r.steps] == [("calculator", {"result": 84})]


def test_second_route_plans_when_the_first_is_down(alice):
    llm = Scripted([CALC, FINAL])
    r = _container([FakeLLM(name="down", fail_first=99), llm]).agent.run("what is 12 * (3 + 4)", alice)
    assert r.stopped_reason == "final" and r.steps[0].output == {"result": 84}


def test_fake_fallback_route_ends_planner_invalid_not_a_fake_answer(alice):
    """Documented consequence (ADR-0009): real route down, fake answers, run fails closed."""
    spans: list[dict] = []
    c = _container([FakeLLM(name="down", fail_first=99), FakeLLM()], spans)
    r = c.agent.run("open a ticket: laptop broken", alice)
    assert r.stopped_reason == "planner_invalid" and r.steps == [] and c.tickets == []
    assert next(s for s in spans if s["name"] == "agent.run")["attributes"]["agent.planner_error"] == "empty_plan"


def test_approved_side_effect_runs_once_as_the_caller(alice):
    llm = Scripted([TICKET, FINAL])
    c = _container([llm])
    c.agent.approve = lambda tool, args, principal: True
    r = c.agent.run("open a ticket", alice)
    assert r.stopped_reason == "final" and len(c.tickets) == 1 and c.tickets[0]["by"] == "alice"


def test_ticket_requested_after_a_harmless_step_is_still_denied(alice):
    c = _container([Scripted([CALC, TICKET])])
    r = c.agent.run("what is 12 * (3 + 4)", alice)
    assert r.stopped_reason == "approval_denied" and c.tickets == [] and len(r.steps) == 1


def test_group_restricted_tool_works_for_a_member_and_is_refused_for_others(alice, hr_user):
    for principal, stopped, steps in ((hr_user, "final", 1), (alice, "planner_invalid", 0)):
        c = _container([Scripted([HR, FINAL])])
        c.agent.registry.register(_hr_tool())
        r = c.agent.run("look me up", principal)
        assert (r.stopped_reason, len(r.steps)) == (stopped, steps)


def test_caller_with_no_groups_gets_no_tools_and_any_tool_reply_is_refused():
    nobody = Principal("nobody", "acme", frozenset())
    llm = Scripted([CALC])
    r = _container([llm]).agent.run("what is 12 * (3 + 4)", nobody)
    assert r.stopped_reason == "planner_invalid" and r.steps == []
    assert json.loads(llm.requests[0].messages[0].content.split("TOOLS: ", 1)[1]) == []


def test_goal_is_masked_before_it_reaches_the_planner_model(alice):
    llm = Scripted([FINAL])
    _container([llm]).agent.run("mail bob@example.com the result of 2 + 2", alice)
    sent = "\n".join(m.content for m in llm.requests[0].messages)
    assert "bob@example.com" not in sent and "<EMAIL>" in sent


def test_second_run_on_the_same_container_is_independent(alice):
    llm = Scripted(["not json", CALC, FINAL])
    c = _container([llm])
    assert c.agent.run("first", alice).stopped_reason == "planner_invalid"
    r = c.agent.run("what is 12 * (3 + 4)", alice)
    assert r.stopped_reason == "final" and len(r.steps) == 1
    assert "<observation" not in llm.requests[1].messages[-1].content   # no history carried over


def test_concurrent_runs_for_different_callers_do_not_leak_the_allowlist(alice, hr_user):
    c = _container([ByContent()])
    c.agent.registry.register(_hr_tool())
    results: list[tuple[str, str, int]] = []
    lock = threading.Lock()

    def run(p: Principal) -> None:
        for _ in range(25):
            r = c.agent.run("look me up", p)
            with lock:
                results.append((p.user_id, r.stopped_reason, len(r.steps)))

    threads = [threading.Thread(target=run, args=(p,)) for p in (alice, hr_user, alice, hr_user)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 100
    assert {x for x in results if x[0] == "alice"} == {("alice", "planner_invalid", 0)}
    assert {x for x in results if x[0] == "hema"} == {("hema", "final", 1)}


# --- what must not change ---------------------------------------------------------------------

def test_custom_planner_errors_other_than_the_three_handled_ones_still_propagate(alice):
    """`Agent.run` must not have grown a catch-all around the planner."""
    class Boom:
        def next_step(self, goal, tools, history):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        Agent(ToolRegistry(), Boom(), JsonTracer(sink=[])).run("x", alice)


def test_custom_planner_may_raise_planner_output_invalid_with_its_own_reason(alice):
    class Refuses:
        def next_step(self, goal, tools, history):
            raise PlannerOutputInvalid("malformed")

    spans: list[dict] = []
    r = Agent(ToolRegistry(), Refuses(), JsonTracer(sink=spans)).run("x", alice)
    assert r.stopped_reason == "planner_invalid" and r.steps == []


def test_planner_classes_are_named_only_in_agent_module_and_container():
    hits = sorted(str(p.relative_to(REPO / "src" / "aiarch")).replace("\\", "/")
                  for p in (REPO / "src" / "aiarch").rglob("*.py")
                  if "LLMPlanner" in p.read_text(encoding="utf-8") or "RulePlanner" in p.read_text(encoding="utf-8"))
    assert hits == ["container.py", "services/agents/agent.py"]


def test_services_and_domain_do_not_import_adapters_or_the_container():
    for sub in ("services", "domain"):
        for p in (REPO / "src" / "aiarch" / sub).rglob("*.py"):
            text = p.read_text(encoding="utf-8")
            assert "aiarch.adapters" not in text and "aiarch.container" not in text, p
