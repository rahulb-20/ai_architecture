import pytest
from pydantic import BaseModel

from aiarch.domain.errors import ToolNotAllowed
from aiarch.domain.models import Principal
from aiarch.services.agents.tools import (
    Tool,
    ToolRegistry,
    calculator_tool,
    kb_search_tool,
    safe_eval,
    ticket_tool,
)

ALICE = Principal("alice", "acme")
HR = Principal("hema", "acme", frozenset({"all", "hr"}))


class _Args(BaseModel):
    n: int


def _tool(name: str, **kw) -> Tool:
    return Tool(name, f"{name} tool", _Args, lambda a, p: {"n": a.n, "by": p.user_id}, **kw)


# --- safe_eval -------------------------------------------------------------------

@pytest.mark.parametrize(("expr", "value"), [
    ("1 + 2", 3), ("7 - 10", -3), ("6 * 7", 42), ("7 / 2", 3.5), ("2 ** 10", 1024), ("17 % 5", 2),
    ("-(3 + 4)", -7), ("12 * (3 + 4)", 84), ("((1 + 2) * (3 + 4)) / 7", 3.0), ("1.5 * 4", 6.0), ("  2+2  ".strip(), 4),
])
def test_arithmetic_is_evaluated(expr, value):
    assert safe_eval(expr) == pytest.approx(value)


@pytest.mark.parametrize("expr", [
    "__import__('os').system('id')", "open('/etc/passwd').read()", "x + 1", "(1).__class__", "[1, 2][0]",
    "'a' * 3", "1 if 2 else 3", "lambda: 1", "1 < 2", "1 and 2", "~1", "+1", "1 // 2", "1 << 8", "abs(-1)",
    "(1, 2)", "{1: 2}", "f'{1}'",
])
def test_anything_but_plain_arithmetic_is_refused(expr):
    with pytest.raises(ValueError, match="disallowed expression"):
        safe_eval(expr)


@pytest.mark.parametrize("expr", ["", "2 +", "1; 2", "import os", "x = 1"])
def test_text_that_is_not_an_expression_is_a_syntax_error(expr):
    with pytest.raises(SyntaxError):
        safe_eval(expr)


def test_division_by_zero_raises_and_is_left_to_the_caller():
    with pytest.raises(ZeroDivisionError):
        safe_eval("1 / 0")


@pytest.mark.xfail(run=False, reason="defect: safe_eval puts no bound on exponents, so a model-supplied expression "
                                     "such as 9**9**9 tries to build a number with hundreds of millions of digits "
                                     "and hangs the worker (not run, because it would hang the test suite too)")
def test_huge_exponent_is_refused_instead_of_hanging():
    with pytest.raises(ValueError):
        safe_eval("9**9**9")


# --- registry --------------------------------------------------------------------

def test_call_validates_arguments_then_runs_the_tool_as_the_caller():
    reg = ToolRegistry()
    reg.register(_tool("echo"))
    assert reg.call("echo", {"n": "5"}, ALICE) == {"n": 5, "by": "alice"}


def test_invalid_arguments_come_back_as_an_observation_not_an_exception():
    reg = ToolRegistry()
    reg.register(_tool("echo"))
    for bad in ({}, {"n": "five"}, {"n": None}):
        out = reg.call("echo", bad, ALICE)
        assert out["error"] == "invalid_arguments" and out["detail"] and "url" not in out["detail"][0]


def test_unknown_and_forbidden_tools_raise_the_same_error():
    reg = ToolRegistry()
    reg.register(_tool("payroll", allowed_groups=frozenset({"hr"})))
    for name in ("payroll", "missing", ""):
        with pytest.raises(ToolNotAllowed):
            reg.call(name, {"n": 1}, ALICE)
    assert reg.call("payroll", {"n": 1}, HR) == {"n": 1, "by": "hema"}


def test_permission_is_checked_before_arguments_are_looked_at():
    reg = ToolRegistry()
    reg.register(_tool("payroll", allowed_groups=frozenset({"hr"})))
    with pytest.raises(ToolNotAllowed):
        reg.call("payroll", {"garbage": True}, ALICE)


def test_for_principal_lists_only_what_the_caller_may_use():
    reg = ToolRegistry()
    for t in (_tool("open"), _tool("payroll", allowed_groups=frozenset({"hr"})), _tool("legal",
        allowed_groups=frozenset({"legal"}))):
        reg.register(t)
    assert [t.name for t in reg.for_principal(ALICE)] == ["open"]
    assert [t.name for t in reg.for_principal(HR)] == ["open", "payroll"]
    assert reg.for_principal(Principal("nobody", "acme", frozenset())) == []


def test_registering_a_name_twice_replaces_the_tool():
    reg = ToolRegistry()
    reg.register(_tool("echo"))
    reg.register(Tool("echo", "v2", _Args, lambda a, p: "second"))
    assert reg.call("echo", {"n": 1}, ALICE) == "second" and len(reg.tools) == 1


def test_registries_do_not_share_tools():
    a, b = ToolRegistry(), ToolRegistry()
    a.register(_tool("echo"))
    assert b.tools == {}


def test_tools_default_to_read_only_and_open_to_all():
    t = _tool("echo")
    assert t.side_effects is False and t.allowed_groups == frozenset({"all"})


def test_json_schema_is_in_function_calling_shape():
    schema = calculator_tool().as_json_schema()
    assert schema["type"] == "function" and schema["function"]["name"] == "calculator"
    params = schema["function"]["parameters"]
    assert params["required"] == ["expression"] and params["properties"]["expression"]["maxLength"] == 200


# --- the three built-in tools ----------------------------------------------------

def test_calculator_limits_expression_length():
    reg = ToolRegistry()
    reg.register(calculator_tool())
    assert reg.call("calculator", {"expression": "2 + 2"}, ALICE) == {"result": 4}
    assert reg.call("calculator", {"expression": "1+" * 100 + "1"}, ALICE)["error"] == "invalid_arguments"


def test_search_tool_passes_the_query_and_the_caller_through():
    seen = []
    reg = ToolRegistry()
    reg.register(kb_search_tool(lambda q, p: seen.append((q, p)) or [{"doc_id": "d"}]))
    assert reg.call("search_kb", {"query": "vpn"}, HR) == {"results": [{"doc_id": "d"}]}
    assert seen == [("vpn", HR)]
    assert reg.call("search_kb", {"query": "v"}, HR)["error"] == "invalid_arguments"
    assert reg.call("search_kb", {"query": "x" * 501}, HR)["error"] == "invalid_arguments"


def test_ticket_tool_is_side_effecting_numbers_tickets_and_records_the_author():
    store: list[dict] = []
    tool = ticket_tool(store)
    reg = ToolRegistry()
    reg.register(tool)
    assert tool.side_effects is True
    assert reg.call("create_ticket", {"title": "A", "body": "one"}, ALICE) == {"ticket_id": "TCK-0001"}
    assert reg.call("create_ticket", {"title": "B", "body": "two"}, HR) == {"ticket_id": "TCK-0002"}
    assert [(t["id"], t["by"], t["title"]) for t in store] == [("TCK-0001", "alice", "A"), ("TCK-0002", "hema", "B")]
    assert reg.call("create_ticket", {"title": "x" * 121, "body": "b"}, ALICE)["error"] == "invalid_arguments"
    assert len(store) == 2
