# ADR-0008: Offline-first defaults

- Status: Accepted
- Date: 2026-11-23

## Context
A boilerplate that needs API keys, GPUs or Docker before the first run does not get adopted, and CI that calls paid APIs is slow, flaky and costly.

## Decision
Defaults are a deterministic extractive `FakeLLM`, a `HashingEmbedder` (no model download), an in-memory vector store, and seeded sample documents. Real providers are enabled only through `AIARCH_LLM_ROUTES` and `AIARCH_EMBEDDER`.

## Consequences
- Plus: clone → `tasks.ps1 setup` → `tasks.ps1 check` works on any laptop in minutes, and CI costs nothing.
- Minus: offline eval scores say nothing about real-model quality. Run the same eval with real routes before a release, and keep a separate gate config for that run.
