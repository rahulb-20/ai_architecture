# ADR-0007: Versioned prompts and a golden-set eval gate in CI

- Status: Accepted
- Date: 2026-11-23

## Context
Prompt, model, chunking and retrieval changes regress quality silently. Unit tests do not catch that.

## Decision
Prompts live in `PromptRegistry` with `id@version`, and every `Answer` records the version that produced it. `evals/golden.jsonl` holds answerable, ACL-restricted, out-of-scope, injection and cross-tenant cases. `python -m aiarch.evaluation.runner --gate` fails CI below these thresholds: status accuracy 0.9, recall 0.8, citation precision 0.8, must-contain 0.7, zero ACL leaks, p95 latency 5s.

## Consequences
- Plus: quality is a release criterion, and the report is uploaded as a CI artifact.
- Minus: the default metrics are deterministic and lexical. For real models, add an LLM-as-judge faithfulness metric (RAGAS/DeepEval) calibrated against human labels, and grow the golden set from production feedback.
