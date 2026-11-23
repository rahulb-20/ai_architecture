# ADR-0006: Layered guardrails and explicit answer statuses

- Status: Accepted
- Date: 2026-11-23

## Context
Reviewers need to know what the system does when it *cannot* answer safely. Exceptions or plausible guesses are both unacceptable.

## Decision
- Input: length limit, prompt-injection heuristics, and PII masking before any text leaves the trust boundary.
- Output: every claim must cite a valid `[S<n>]`, there is a blocked-term policy, and PII is masked.
- Card and Aadhaar numbers are masked only when they pass their checksum (Luhn, Verhoeff), so invoice and ticket numbers of the same shape are left alone. A bare phone number must look like an Indian mobile; other countries need the `+` prefix.
- Output masking covers every PII-shaped value by default. `AIARCH_OUTPUT_KEEP_SOURCE_PII=true` leaves values that appear verbatim in the cited sources, for corpora where contact numbers are part of the answer.
- Retrieved text is framed as data in the prompt.
- Every outcome maps to `ANSWERED | ABSTAINED | BLOCKED | DEGRADED`. DEGRADED returns sources when all models fail.
- Heavier checks (Llama Guard, NeMo Guardrails, Presidio, Azure AI Content Safety, watsonx.governance) plug in as extra `Check`s.

## Consequences
- Plus: predictable UX and clean metrics per status. Abstention is measurable.
- Minus: regex heuristics have false positives and negatives, so treat them as layer one only. Checksums cut false positives; they do not find PII the patterns miss (names, addresses), which is what Presidio is for.
