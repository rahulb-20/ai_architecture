# Pipeline backlog

The scheduled pipeline task takes the **first unchecked item** in "Ready", runs it through the three agents (`ai-architect`, `developer`, `tester`; see [the guide](guides/agent-pipeline.md)), and opens a pull request. It does one item per run and does not start another while a pipeline pull request is still open.

How to use this file:

- **Order is priority.** Move an item up to have it done sooner.
- **One outcome per item.** Write what must be true when it is done, and what is out of scope.
- **To pause the pipeline,** leave "Ready" with no unchecked items, or disable the scheduled task.
- An item is ticked, with its pull request number, in the pull request that implements it. Merging that pull request is what marks it done.

## Ready

- [ ] **Bound the calculator's exponent.** `safe_eval` accepts any power, so a model-supplied expression such as `9**9**9` tries to build a number with hundreds of millions of digits and hangs the worker. Refuse results or exponents beyond a stated bound with the same `ValueError` as other disallowed expressions. Done when `test_huge_exponent_is_refused_instead_of_hanging` in `tests/unit/test_tools_registry.py` runs and passes with its `xfail` marker removed. Out of scope: new operators.
- [ ] **Never index an empty chunk.** A heading followed only by blank lines, or a whitespace-only document, produces a chunk with empty text that is embedded and stored. Done when `test_no_chunk_is_ever_empty` in `tests/unit/test_chunking_edges.py` passes with its `xfail` marker removed. Out of scope: any other chunking change.
- [ ] **`aiarch estimate` with a free model.** With input and output prices of 0 (a local model), `sensitivity()` divides by zero and the command crashes. Report a cost of 0 and a sensitivity of 0 instead. Done when `test_estimate_for_a_free_local_model_reports_zero_cost` in `tests/unit/test_cli.py` passes with its `xfail` marker removed.
- [ ] **Reject an unknown embedder name.** `AIARCH_EMBEDDER` set to anything other than `sentence-transformers` silently falls back to the hashing embedder, so a typo ships the low-quality default. Make the setting a `Literal` that fails at start-up, like `agent_planner`.
- [ ] **Invisible-only final answers.** `LLMPlanner` accepts a final answer made only of invisible characters (for example a zero-width space) as a real answer. Treat such a reply as an empty plan. Out of scope: any other planner change.
- [ ] **Scan tool output for indirect injection.** Text returned by agent tools (for example `search_kb`) reaches the planner unchecked. Apply the injection heuristics to tool observations and record a finding on the span; decide in the design whether a hit stops the run or redacts the observation. Out of scope: new detection models or dependencies.
- [ ] **Contract test suite for the `VectorStore` port.** One reusable set of tests (tenant isolation, ACL pre-filter, `replace_doc` atomic swap, `doc_version`, empty batches) that any adapter can be run against, applied to the in-memory store. Out of scope: a new adapter.
- [ ] **Contract test suite for the `AnswerCache` port.** Same idea for cache scope, similarity threshold, and invalidation by document. Out of scope: a new adapter.
- [ ] **Per-tenant token budget and rate limit in the gateway.** `BudgetExceeded` mentions per-tenant limits but only a per-request limit exists. Add a per-tenant budget over a time window, in memory, behind a port so it can move to Redis later. Out of scope: Redis itself.
- [ ] **Agent approval flow.** Side-effecting tools can never run through the API because approval always denies. Add pause, persist and resume: the run stops with a pending approval, an endpoint approves or rejects it, and the run continues. In-memory persistence behind a port. Out of scope: a UI.
- [ ] **JWT principal for the API.** Replace the client-supplied `x-user`, `x-tenant` and `x-groups` headers with a verified JWT (tenant and groups from claims), keeping the header mode for local development behind an explicit setting. Tests must run offline with locally generated keys.
- [ ] **Multi-turn conversations.** Add a conversation id to `/v1/ask`, a session memory port with an in-memory adapter, and rewriting of follow-up questions into standalone queries before retrieval. Memory must be scoped by tenant and user.
- [ ] **Streaming answers.** Add a streaming method to the `LLMClient` port and a server-sent-events variant of `/v1/ask`, with output guardrails still applied, so time to first token (NFR-01) can be measured. The fake LLM must stream offline.
- [ ] **Reranker port.** Make the reranker a port with a no-op default and an optional cross-encoder adapter (optional dependency, never required by the checks), and use the rerank score in the abstain decision.

## Done

- [x] Planner selectable by `AIARCH_AGENT_PLANNER`, `LLMPlanner` fails closed (#7)
