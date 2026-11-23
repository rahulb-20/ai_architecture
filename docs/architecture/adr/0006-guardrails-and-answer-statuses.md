# ADR-0006: Layered guardrails and explicit answer statuses

- Status: Accepted
- Date: 2026-11-23

## Context
Reviewers need to know what the system does when it *cannot* answer safely. Exceptions or plausible guesses are both unacceptable.

## Decision
- Input: length limit, prompt-injection heuristics, and PII masking before any text leaves the trust boundary.
- Output: every claim must cite a valid `[S<n>]`, there is a blocked-term policy, and PII is masked.
- Retrieved text is framed as data in the prompt.
- Every outcome maps to `ANSWERED | ABSTAINED | BLOCKED | DEGRADED`. DEGRADED returns sources when all models fail.
- Heavier checks (Llama Guard, NeMo Guardrails, Presidio, Azure AI Content Safety, watsonx.governance) plug in as extra `Check`s.

## Consequences
- Plus: predictable UX and clean metrics per status. Abstention is measurable.
- Minus: regex heuristics have false positives and negatives, so treat them as layer one only.
