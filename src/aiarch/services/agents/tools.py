"""Tool registry with typed arguments, permissions and side-effect classification.

Each tool declares: a pydantic args model (validated before execution), whether it
has side effects (side-effecting tools need human approval), and which groups may
call it. This is the same contract an MCP server exposes; `as_json_schema()` gives
the function-calling schema for OpenAI/Anthropic/Gemini-style tool use.
"""
from __future__ import annotations

import ast
import operator
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from aiarch.domain.errors import ToolNotAllowed
from aiarch.domain.models import Principal


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    fn: Callable[[Any, Principal], Any]  # receives the validated args_model instance
    side_effects: bool = False
    allowed_groups: frozenset[str] = frozenset({"all"})

    def as_json_schema(self) -> dict[str, Any]:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": self.args_model.model_json_schema()}}


@dataclass
class ToolRegistry:
    tools: dict[str, Tool] = field(default_factory=dict)

    def register(self, tool: Tool) -> None:
        self.tools[tool.name] = tool

    def for_principal(self, p: Principal) -> list[Tool]:
        return [t for t in self.tools.values() if p.can_read(t.allowed_groups)]

    def call(self, name: str, raw_args: dict[str, Any], p: Principal) -> Any:
        tool = self.tools.get(name)
        if tool is None or not p.can_read(tool.allowed_groups):
            raise ToolNotAllowed(name)
        try:
            args = tool.args_model.model_validate(raw_args)
        except ValidationError as e:
            return {"error": "invalid_arguments", "detail": e.errors(include_url=False)}
        return tool.fn(args, p)


# --- example tools -------------------------------------------------------------

class CalcArgs(BaseModel):
    expression: str = Field(..., max_length=200, description="Arithmetic only, e.g. '12 * (3 + 4)'")


_OPS: dict[type, Callable[..., Any]] = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.Pow: operator.pow, ast.USub: operator.neg, ast.Mod: operator.mod}


def safe_eval(expr: str) -> float:
    """Never `eval()` model output. Walk the AST and allow arithmetic nodes only."""
    def _ev(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return _ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](_ev(node.left), _ev(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](_ev(node.operand))
        raise ValueError(f"disallowed expression: {type(node).__name__}")
    return _ev(ast.parse(expr, mode="eval"))


def calculator_tool() -> Tool:
    return Tool("calculator", "Evaluate an arithmetic expression.", CalcArgs,
                lambda a, p: {"result": safe_eval(a.expression)})


class SearchArgs(BaseModel):
    query: str = Field(..., min_length=2, max_length=500)


def kb_search_tool(search: Callable[[str, Principal], list[dict[str, Any]]]) -> Tool:
    return Tool("search_kb", "Search the knowledge base the caller has access to.", SearchArgs,
                lambda a, p: {"results": search(a.query, p)})


class TicketArgs(BaseModel):
    title: str = Field(..., max_length=120)
    body: str = Field(..., max_length=2_000)


def ticket_tool(store: list[dict[str, Any]]) -> Tool:
    def _create(a: TicketArgs, p: Principal) -> dict[str, Any]:
        tid = f"TCK-{len(store) + 1:04d}"
        store.append({"id": tid, "by": p.user_id, "title": a.title, "body": a.body})
        return {"ticket_id": tid}
    return Tool("create_ticket", "Open an IT/HR ticket on behalf of the user.", TicketArgs,
                _create, side_effects=True)
