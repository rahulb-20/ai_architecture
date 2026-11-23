"""Layered guardrails: cheap deterministic checks in-process, heavier models optional.

Input:  size limit -> prompt-injection heuristics -> PII masking (before anything leaves the boundary)
Output: grounding/citation check -> PII masking -> policy terms

Heuristics are a first layer, not a solution. Add Llama Guard / NeMo Guardrails /
Presidio as extra `Check`s for production; the pipeline shape stays the same (ADR-0006).
"""
from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from aiarch.domain.errors import GuardrailViolation

_INJECTION = [
    r"ignore (all |any |the )?(previous|prior|above) (instructions|rules)",
    r"disregard (the )?(system|previous) (prompt|instructions)",
    r"you are now (?!an enterprise)",
    r"reveal (your|the) (system prompt|instructions|hidden)",
    r"\bjailbreak\b",
    r"act as (dan|an? unfiltered)",
]
_INJECTION_RE = re.compile("|".join(_INJECTION), re.I)

# Order matters: more specific patterns first (a card is masked before its first 12 digits can
# be read as an Aadhaar number). A shape match alone is not enough for the numeric IDs: invoice,
# order and ticket numbers have the same shape, so cards and Aadhaar numbers must also pass
# their checksum.
PII_PATTERNS: dict[str, re.Pattern[str]] = {
    "EMAIL": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    "IN_PAN": re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),
    "CARD": re.compile(r"(?<!\d)(?<!\d[ -])(?:\d[ -]?){12,18}\d(?![ -]?\d)"),
    "IN_AADHAAR": re.compile(r"(?<!\d)(?<!\d[ -])[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}(?![ -]?\d)"),
    # International form needs the + prefix; a bare number must look like an Indian mobile (starts 6-9).
    "PHONE": re.compile(r"(?<![\d+])(?:\+\d{1,3}[ -]?\d{10}|[6-9]\d{9})(?!\d)"),
}

_VERHOEFF_D = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5], [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
               [3, 4, 0, 1, 2, 8, 9, 5, 6, 7], [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
               [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3], [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
               [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]]
_VERHOEFF_P = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4], [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
               [8, 9, 1, 6, 0, 4, 3, 5, 2, 7], [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
               [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8]]


def _digits(text: str) -> list[int]:
    return [int(ch) for ch in text if ch.isdigit()]


def luhn_ok(number: str) -> bool:
    """Checksum used by payment cards."""
    digits = _digits(number)[::-1]
    if not digits:
        return False
    total = sum(digits[0::2]) + sum(sum(divmod(2 * d, 10)) for d in digits[1::2])
    return total % 10 == 0


def verhoeff_ok(number: str) -> bool:
    """Checksum used by Aadhaar numbers."""
    digits = _digits(number)
    if not digits:
        return False
    c = 0
    for i, d in enumerate(reversed(digits)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][d]]
    return c == 0


_VALIDATORS: dict[str, Callable[[str], bool]] = {"CARD": luhn_ok, "IN_AADHAAR": verhoeff_ok}


def _normalise(value: str) -> str:
    return re.sub(r"[\s-]", "", value).lower()


def mask_pii(text: str, keep_values_from: Sequence[str] = ()) -> tuple[str, dict[str, int]]:
    """Replace PII with `<LABEL>` and count what was masked.

    `keep_values_from`: texts whose own values are left unmasked (spacing and hyphens ignored).
    The output guard can pass the retrieved sources here, so a value the caller is already
    allowed to read, such as a helpdesk number in a policy, is not masked on the way out.
    """
    keep = _normalise(" ".join(keep_values_from)) if keep_values_from else ""
    counts: dict[str, int] = {}
    for label, pat in PII_PATTERNS.items():
        valid = _VALIDATORS.get(label)

        def _sub(m: re.Match[str], label: str = label, valid: Callable[[str], bool] | None = valid) -> str:
            value = m.group(0)
            if (valid and not valid(value)) or (keep and _normalise(value) in keep):
                return value
            counts[label] = counts.get(label, 0) + 1
            return f"<{label}>"

        text = pat.sub(_sub, text)
    return text, counts


@dataclass
class GuardResult:
    text: str
    findings: dict[str, int] = field(default_factory=dict)


Check = Callable[[str], GuardResult]


@dataclass
class InputGuard:
    max_chars: int = 4_000
    extra_checks: list[Check] = field(default_factory=list)

    def __call__(self, text: str) -> GuardResult:
        if len(text) > self.max_chars:
            raise GuardrailViolation("input_too_long", f"{len(text)} > {self.max_chars}")
        if _INJECTION_RE.search(text):
            raise GuardrailViolation("prompt_injection", "matched injection heuristic")
        masked, found = mask_pii(text)
        res = GuardResult(masked, found)
        for chk in self.extra_checks:
            r = chk(res.text)
            res = GuardResult(r.text, {**res.findings, **r.findings})
        return res


_CITE = re.compile(r"\[S(\d+)\]")


@dataclass
class OutputGuard:
    require_citations: bool = True
    blocked_terms: tuple[str, ...] = ()
    # Off by default: every PII-shaped value in an answer is masked (NFR-08). Turn on to leave
    # values that appear verbatim in the cited sources, which the caller may already read.
    keep_source_values: bool = False

    def __call__(self, text: str, n_sources: int, sources: Sequence[str] = ()) -> GuardResult:
        cited = {int(x) for x in _CITE.findall(text)}
        if self.require_citations and text.strip() != "INSUFFICIENT_CONTEXT":
            if not cited:
                raise GuardrailViolation("uncited_answer", "answer has no [S<n>] citations")
            if any(c < 1 or c > n_sources for c in cited):
                raise GuardrailViolation("invalid_citation", f"cited {sorted(cited)} of {n_sources}")
        for term in self.blocked_terms:
            if term.lower() in text.lower():
                raise GuardrailViolation("blocked_term", term)
        masked, found = mask_pii(text, sources if self.keep_source_values else ())
        return GuardResult(masked, found)


def cited_indices(text: str) -> list[int]:
    return sorted({int(x) for x in _CITE.findall(text)})
