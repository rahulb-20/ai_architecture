# ADR-0009: The agent planner is chosen by a setting, and an LLM plan is validated or refused

- Status: Accepted
- Date: 2026-11-23

## Context
The agent takes its next step from a `Planner`. `RulePlanner` is deterministic and offline; `LLMPlanner` asks a model through the gateway. Only `RulePlanner` was wired, and `LLMPlanner` passed model output to the agent loop unchecked. Model output is untrusted: it can be malformed, can name a tool the caller may not use, and can be steered by text in tool observations.

## Options
1. Keep `RulePlanner` only and delete `LLMPlanner`.
2. Select the planner by setting; accept model output leniently and let the tool registry refuse bad calls.
3. Select the planner by setting; validate the plan in the planner and stop the run when it is invalid.
4. As 3, plus one re-ask or a fallback to `RulePlanner` on invalid output.

## Decision
Option 3. `AIARCH_AGENT_PLANNER` takes `rule` (default) or `llm`; any other value fails at start-up. `LLMPlanner` accepts a reply only if it parses into the plan shape with the right types and either names a tool from the list it was given for this caller or carries a non-empty final answer. Anything else raises `PlannerOutputInvalid`, which the agent reports as `stopped_reason="planner_invalid"`. Gateway failure (`AllProvidersFailed`, `BudgetExceeded`) is reported as `planner_unavailable`. The planner never repairs output and the agent never switches planner during a run. `ToolRegistry.call` remains the enforcing permission check; the planner check is defence in depth. `FakeLLM` is not taught to plan: tests script replies through a test-only `LLMClient`.

## Consequences
- Plus: the default still runs offline with no model call. A planner failure is distinguishable from an answer in the API response and in traces. Invalid output costs one model call, not `max_steps`.
- Plus: the setting is one branch in `container.py`; services and ports are unchanged.
- Minus: models that wrap JSON in prose or code fences are refused until a bounded re-ask is added. Fewer requests complete with weaker models.
- Minus: with `llm`, a `fake` fallback route gives `planner_invalid` rather than a plan, so the fake is not a useful last route for the agent the way it is for `/v1/ask`.
- Minus: the eval gate has no agent cases, so planner quality on a real route is not gated yet.
