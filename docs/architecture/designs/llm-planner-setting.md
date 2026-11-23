# Design brief: choose the agent planner by setting, and make `LLMPlanner` fail closed

- Slug: `llm-planner-setting`
- Suggested branch: `feat/llm-planner-setting`
- ADR: [ADR-0009](../adr/0009-agent-planner-selection.md)

## Assumptions

1. The setting is `agent_planner` (`AIARCH_AGENT_PLANNER`), values exactly `rule` and `llm`, lower case. Any other value is refused at start-up; it is not silently mapped to `rule`.
2. "Cope with malformed output" means fail closed: the run stops with a fixed message and a distinct `stopped_reason`. There is no repair, no re-ask and no stripping of Markdown code fences in this change.
3. "Degrade sensibly" means the run stops with the existing `planner_unavailable` result and keeps the steps already taken. The agent does not switch to `RulePlanner` mid-run.
4. A new `stopped_reason` value (`planner_invalid`) in the `/v1/agent` response is acceptable. The field is a free string today and no client enumerates it in this repo.
5. Unknown extra keys in the model's JSON are ignored, because they cannot change what executes. Wrong types for `tool`, `args` or `final` are refused.
6. `FakeLLM` is not changed. Tests script planner replies with a test-only `LLMClient`.

## 1. Problem

- `LLMPlanner` is dead code. `src/aiarch/container.py:22` imports only `Agent, RulePlanner`, and line 94 always builds `Agent(registry, RulePlanner(), ...)`. `src/aiarch/config.py:26-45` has no planner setting. No test imports `LLMPlanner` (`tests/unit/test_agent.py`, `tests/unit/test_agent_guard.py`).
- `LLMPlanner.next_step` (`src/aiarch/services/agents/agent.py:80-92`) trusts the model:
  - Line 90 returns `d.get("tool")` without checking it against the `tools` argument. The tool is still refused later by `ToolRegistry.call` (`tools.py:47-50`), so nothing unauthorised executes, but the planner itself can return a step for a tool the caller may not use. Before that refusal, `Agent.run` line 130-135 looks the name up in the whole registry and may call `approve` for a side-effecting tool the caller cannot use.
  - Line 91 catches only `json.JSONDecodeError` and `AttributeError`. Valid JSON of the wrong type passes: `"tool": 5` reaches `registry.tools.get(5)`, and `"args": "x"` reaches `model_validate("x")`. Both happen to end as error observations and the loop continues to `max_steps`, spending one model call per step.
  - `{"tool": null, "final": null}` becomes `Step(None, {}, None)`, which `Agent.run` line 127-129 reports as a successful `final` with an empty answer.
- Offline behaviour of `FakeLLM` for this schema: `_json_answer` (`src/aiarch/adapters/llm/fake.py:81-98`) fills a key only when `type` is a single string or the spec has `enum`. `_STEP_SCHEMA` (`agent.py:68-73`) uses `["string", "null"]` for `tool` and `final`, and `object` for `args`, so `FakeLLM` returns `{"tool": null, "args": null, "final": null}`. With `llm` selected on the default route, every agent run would today end as `final` with `""`.
- `Agent.run` line 125 catches `AllProvidersFailed` only. `ModelGateway.generate` can also raise `BudgetExceeded` (`gateway.py:89-90`), which `RagService` handles (`rag.py:72`) and the agent does not; with an LLM planner that becomes an HTTP 500 from `/v1/agent`.
- Docs and code disagree: the module docstring (`agent.py:11-12`) and `README.md:42` describe a selectable planner port; only `RulePlanner` is reachable.

## 2. Decision

Add `Settings.agent_planner: Literal["rule", "llm"] = "rule"` and one branch in `container.py` that builds `LLMPlanner(gateway)` or `RulePlanner()`. `LLMPlanner` validates the model reply with a strict pydantic model and against the names of the `tools` it was given, and raises a new typed error `PlannerOutputInvalid` for anything else. `Agent.run` turns that error into `stopped_reason="planner_invalid"` with a fixed message, and treats `BudgetExceeded` like `AllProvidersFailed` (`planner_unavailable`). The `Planner` protocol, `_STEP_SCHEMA`, `FakeLLM` and the tool/approval path are unchanged. Tests script replies with a small test-only `LLMClient` passed through the existing `build_container(llm_clients=...)` seam.

## 3. Options considered

**Where the allowlist check lives**

| Option | Verdict |
|---|---|
| A. In `LLMPlanner.next_step`, against the `tools` argument (already filtered by `ToolRegistry.for_principal`) | **Chosen.** Meets "the planner only ever returns an allowed tool" directly, needs no new dependency, and `ToolRegistry.call` stays as the second, authoritative check. |
| B. In `Agent.run`, for every planner | Not chosen. Changes what `test_unknown_tool_becomes_observation_not_crash` asserts for custom planners and touches the approval branch, which is out of scope. |
| C. Put an `enum` of allowed names into the JSON schema per request | Not chosen. It only constrains providers that enforce schemas, so a check is still needed. It also breaks the offline default: `FakeLLM._json_answer` returns `enum[0]`, so the fake would "choose" the first tool on every step. |

**What happens on invalid model output**

| Option | Verdict |
|---|---|
| A. Typed error `PlannerOutputInvalid`, mapped by `Agent` to `planner_invalid` | **Chosen.** `domain/errors.py` says callers branch on type. Operators can tell a planner failure from a real answer in the response and in the `agent.run` span. |
| B. Return `Step(None, final="I could not plan this request.")` (today's behaviour for bad JSON) | Not chosen. Reported as `final`, so it is indistinguishable from an answer and invisible to metrics. |
| C. Re-ask the model once with the validation error | Deferred. Doubles worst-case cost and latency per step and needs its own cap; add later if real models need it. |
| D. Fall back to `RulePlanner` for that step | Not chosen. Silently changes behaviour and can propose `create_ticket` from a keyword match the operator opted out of. |

**How tests script planner replies**

| Option | Verdict |
|---|---|
| A. Test-only `ScriptedLLM` implementing the `LLMClient` port, defined under `tests/` | **Chosen.** No production change. Same pattern as `Bad(FakeLLM)` in `tests/unit/test_gateway.py:40`. |
| B. Add a `scripted_replies` parameter to `FakeLLM` | Not chosen. Adds a test hook to a production adapter that the default route uses. |
| C. Teach `FakeLLM._json_answer` union types or a planning heuristic | Not chosen. Changes offline production behaviour and duplicates `RulePlanner`. |

## 4. Changes

No port in `src/aiarch/ports/__init__.py` changes. The `Planner` protocol signature in `agent.py` is unchanged.

| File | Change |
|---|---|
| `src/aiarch/config.py` | Add `agent_planner: Literal["rule", "llm"] = "rule"` to `Settings` (import `Literal` from `typing`). No change to `from_env`: the generic loop already maps `AIARCH_AGENT_PLANNER`, and pydantic rejects other values. |
| `src/aiarch/domain/errors.py` | Add `class PlannerOutputInvalid(AiArchError)` with `__init__(self, reason: str)`; `self.reason = reason`; message is the reason only. Reason codes: `"malformed"`, `"tool_not_allowed"`, `"empty_plan"`. The model's text must not be put in the exception. |
| `src/aiarch/services/agents/agent.py` | (a) Add private pydantic model `_Plan` with `model_config = ConfigDict(strict=True, extra="ignore")` and fields `tool: str \| None = None`, `args: dict[str, Any] \| None = None`, `final: str \| None = None`. (b) Rewrite the parsing half of `LLMPlanner.next_step` (signature unchanged) as specified below. (c) In `Agent.run`, widen the existing `except AllProvidersFailed` to `except (AllProvidersFailed, BudgetExceeded) as e`, set `sp.set("agent.planner_error", type(e).__name__)`, same return as today. (d) Add `except PlannerOutputInvalid as e`: `sp.set("agent.planner_error", e.reason)` and `return AgentResult("I could not plan this request.", history, "planner_invalid")`. (e) Add `"planner_invalid"` to the `stopped_reason` comment on `AgentResult` and correct the module docstring to name the setting. |
| `src/aiarch/container.py` | Import `LLMPlanner` and `Planner`. Replace line 94 with `planner: Planner = LLMPlanner(gateway) if s.agent_planner == "llm" else RulePlanner()` and `Agent(registry, planner, tracer, input_guard=rag.input_guard)`. |
| `.env.example` | Add `AIARCH_AGENT_PLANNER=rule` with a comment: `rule` (offline, default) or `llm` (needs a real model route; the fake route cannot plan). |
| `README.md` | One sentence in the configuration section naming the setting and its two values. |
| `tests/unit/test_llm_planner.py` (new, written by the tester) | Holds the test-only `ScriptedLLM` and the cases in section 5. |
| `tests/unit/test_config.py` | Cases for the new setting. |

**`LLMPlanner.next_step` rules, in order.** The request to the gateway (messages, `_STEP_SCHEMA`, `max_output_tokens=300`) is unchanged. Gateway exceptions are not caught here.

1. `plan = _Plan.model_validate_json(r.response.text)`; on `pydantic.ValidationError` raise `PlannerOutputInvalid("malformed")`. This covers non-JSON text, a JSON value that is not an object, and wrong types for the three keys.
2. If `plan.tool is not None`: if `plan.tool` is not in `{t.name for t in tools}` raise `PlannerOutputInvalid("tool_not_allowed")`; otherwise return `Step(plan.tool, plan.args or {})`. `final` is ignored when a tool is named.
3. If `plan.tool is None`: if `plan.final` is `None` or blank after `strip()` raise `PlannerOutputInvalid("empty_plan")`; otherwise return `Step(None, final=plan.final)`.

Argument values are not validated in the planner. `ToolRegistry.call` already validates them against the tool's args model and returns an `invalid_arguments` observation.

**Test-only helper** (in `tests/`, never under `src/`):

```python
class ScriptedLLM:
    name = "scripted"
    model = "scripted-1"
    def __init__(self, replies: list[str]) -> None   # one raw reply text per generate() call, in order
    requests: list[LLMRequest]                        # every request received, for assertions
    def generate(self, request: LLMRequest) -> LLMResponse   # AssertionError when replies run out
```

Use it either as `LLMPlanner(ModelGateway([Route(ScriptedLLM([...]))], JsonTracer(sink=[]), PriceBook()))` or end to end as `build_container(Settings(agent_planner="llm"), llm_clients=[ScriptedLLM([...])])`. For the all-routes-fail case use the existing `FakeLLM(fail_first=99)`.

## 5. Acceptance criteria

Configuration and wiring

1. `Settings.from_env({}).agent_planner == "rule"`, and `build_container(Settings()).agent.planner` is a `RulePlanner`.
2. `Settings.from_env({"AIARCH_AGENT_PLANNER": "llm"}).agent_planner == "llm"`, and `build_container(Settings(agent_planner="llm")).agent.planner` is an `LLMPlanner` whose `gateway` is the container's `gateway`.
3. `Settings.from_env({"AIARCH_AGENT_PLANNER": "gpt"})` and `Settings.from_env({"AIARCH_AGENT_PLANNER": "LLM"})` raise `pydantic.ValidationError`.
4. With default settings, a `FakeLLM` passed as `llm_clients` has `calls == 0` after `container.agent.run("what is 12 * (3 + 4)", alice)`: the rule planner makes no model call.

`LLMPlanner` (scripted replies, tools = calculator + create_ticket unless stated)

5. Reply `{"tool": "calculator", "args": {"expression": "2+3"}, "final": null}` returns `Step("calculator", {"expression": "2+3"})`.
6. Reply `{"tool": null, "args": {}, "final": "done"}` returns a step with `tool is None` and `final == "done"`.
7. Reply naming a tool that exists in the registry but is not in the `tools` list given to `next_step` (for example a tool with `allowed_groups={"hr"}` and a caller without `hr`) raises `PlannerOutputInvalid` with `reason == "tool_not_allowed"`. Same for a name that exists nowhere (`"delete_everything"`) and for `"tool": ""`.
8. Each of these replies raises `PlannerOutputInvalid` with `reason == "malformed"`: `not json`, `[1, 2]`, `"text"`, `{"tool": 5, "args": {}, "final": null}`, `{"tool": "calculator", "args": "2+3", "final": null}`, `{"tool": null, "args": {}, "final": 7}`, and a reply wrapped in a Markdown code fence.
9. Each of these replies raises `PlannerOutputInvalid` with `reason == "empty_plan"`: `{}`, `{"tool": null, "args": null, "final": null}`, `{"tool": null, "args": {}, "final": "   "}`.
10. Reply `{"tool": "calculator", "final": "ignored"}` (no `args`) returns `Step("calculator", {})`. Reply with an extra key (`"reasoning": "..."`) and otherwise valid content is accepted.
11. The request the planner sends carries `json_schema == _STEP_SCHEMA`, and its system message lists exactly the names of the tools it was given (checked through `ScriptedLLM.requests`).

Agent behaviour with `agent_planner="llm"`

12. Scripted replies [calculator call for `12 * (3 + 4)`, then a final] give `stopped_reason == "final"`, one step with output `{"result": 84}`, and the scripted final text.
13. A first reply that is malformed or names a disallowed tool gives `stopped_reason == "planner_invalid"`, `final == "I could not plan this request."`, `steps == []`, no `agent.tool` span, exactly one model call, and the `agent.run` span has `agent.planner_error` equal to the reason code.
14. A valid calculator step followed by a malformed reply gives `planner_invalid` and `steps` still contains the calculator observation.
15. A scripted `create_ticket` step with the default `approve` gives `stopped_reason == "approval_denied"` and `container.tickets == []` (the approval rail is unchanged).
16. `llm_clients=[FakeLLM(fail_first=99)]` gives `stopped_reason == "planner_unavailable"`, `steps == []`, no exception. Through the API, `POST /v1/agent` returns HTTP 200 with that `stopped_reason`.
17. `Settings(agent_planner="llm", max_input_tokens=1)` with a scripted client gives `stopped_reason == "planner_unavailable"`, zero calls to the client, no exception.
18. `Settings(agent_planner="llm")` with the default fake route (no `llm_clients`) gives `stopped_reason == "planner_invalid"` and no `agent.tool` span for any goal that passes the input guard. It must not return `final` with an empty string.
19. A goal that trips the input guard gives `stopped_reason == "blocked"` and zero model calls with the LLM planner.

What must not change

20. Every existing test passes unmodified, including `test_unknown_tool_becomes_observation_not_crash` (a custom planner returning an unknown tool still yields a `tool_not_allowed` observation).
21. `git diff --stat main -- src/aiarch/adapters src/aiarch/ports src/aiarch/services/agents/tools.py src/aiarch/services/gateway.py evals` is empty.
22. `grep -rn "LLMPlanner\|RulePlanner" src/aiarch --include=*.py` shows the classes named only in `services/agents/agent.py` and `container.py`.
23. `ruff check src tests`, `mypy`, `pytest` and `python -m aiarch.evaluation.runner --gate` all pass with no network and no keys.

## 6. Out of scope

- Any change to `FakeLLM`, `OpenAICompatibleLLM`, `ModelGateway`, `ToolRegistry`, `_STEP_SCHEMA` or the planner prompt text.
- The approval flow, including the order of the approval and permission checks in `Agent.run` lines 130-135 (noted in section 7).
- Streaming and native provider tool-calling APIs.
- Retry, re-ask or repair of model output; stripping code fences; falling back to `RulePlanner`.
- Refusing the combination of `llm` with fake-only routes at start-up.
- A planner span, planner prompt versioning in `PromptRegistry`, or agent cases in `evals/golden.jsonl`.
- Changes to `cli.py` or the `/v1/agent` response shape beyond the new `stopped_reason` value.

## 7. Risks and NFR impact

| NFR | Impact |
|---|---|
| NFR-03 availability | Positive. Budget and all-routes-failed errors become a defined degraded result instead of a 500. With `llm`, a fake fallback route cannot plan: when real routes are down and the fake answers, the run ends `planner_invalid`, not `planner_unavailable`. Document this next to the setting. |
| NFR-06 access control | Positive. The planner can no longer return a tool outside the caller's list; `ToolRegistry.call` remains the enforcing check. |
| NFR-07 injection resistance | Neutral to positive. Output forged through an observation still has to name an allowed tool, and side-effecting tools still need approval. Observation framing is unchanged. |
| NFR-08 PII | Neutral. The goal is masked before planning (existing guard). The new error and span attribute carry reason codes only, never model text. |
| NFR-09 cost, NFR-02 latency | `llm` adds one model call per step, at most `max_steps` (5), each capped at 300 output tokens. Invalid output now stops the run after one call instead of looping to the step limit. The default `rule` adds nothing. |
| NFR-10 traceability | `agent.planner_error` on the `agent.run` span; planner calls already emit `gen_ai.chat` spans through the gateway. |
| NFR-13 change safety | The eval gate has no agent cases, so `llm` planner quality is not gated. Operators must evaluate a real route before enabling it (see ADR-0002 on fallback routes). |

Other risks

- Fail-closed parsing will reject replies from models that wrap JSON in code fences or prose. This is intended for now; a single bounded re-ask is the deferred remedy.
- Not verifiable offline: `OpenAICompatibleLLM` sends the schema with `"strict": True` (`openai_compatible.py:40-44`). `_STEP_SCHEMA` has a free-form `args` object, which some strict structured-output implementations reject with HTTP 400. That would surface as `planner_unavailable`. Check against the chosen provider before enabling `llm`; fixing the schema or adapter is a separate change.
- Pre-existing, not changed here: `Agent.run` consults `approve` for a side-effecting tool before the permission check, using the whole registry. After this change `LLMPlanner` can no longer trigger it; custom planners still can.
