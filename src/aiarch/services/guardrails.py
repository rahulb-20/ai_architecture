"""Layered guardrails: cheap deterministic checks in-process, heavier models optional.

Input:  size limit -> prompt-injection heuristics -> PII masking (before anything leaves the boundary)
Output: grounding/citation check -> PII masking -> policy terms

Heuristics are a first layer, not a solution. Add Llama Guard / NeMo Guardrails /
Presidio as extra `Check`s for production; the pipeline shape stays the same (ADR-0006).
"""
from __future__ import annotations

import re
from collections.abc import Callable
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

# Order matters: more specific patterns first.
PII_PATTERNS: dict[str, re.Pattern[str]] = {
    "EMAIL": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    "IN_PAN": re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),
    "IN_AADHAAR": re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b"),
    "CARD": re.compile(r"\b(?:\d[ -]?){13,16}\b"),
    "PHONE": re.compile(r"(?<!\d)(?:\+?\d{1,3}[ -]?)?\d{10}(?!\d)"),
}


def mask_pii(text: str) -> tuple[str, dict[str, int]]:
    counts: dict[str, int] = {}
    for label, pat in PII_PATTERNS.items():
        text, n = pat.subn(f"<{label}>", text)
        if n:
            counts[label] = n
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

    def __call__(self, text: str, n_sources: int) -> GuardResult:
        cited = {int(x) for x in _CITE.findall(text)}
        if self.require_citations and text.strip() != "INSUFFICIENT_CONTEXT":
            if not cited:
                raise GuardrailViolation("uncited_answer", "answer has no [S<n>] citations")
            if any(c < 1 or c > n_sources for c in cited):
                raise GuardrailViolation("invalid_citation", f"cited {sorted(cited)} of {n_sources}")
        for term in self.blocked_terms:
            if term.lower() in text.lower():
                raise GuardrailViolation("blocked_term", term)
        masked, found = mask_pii(text)
        return GuardResult(masked, found)


def cited_indices(text: str) -> list[int]:
    return sorted({int(x) for x in _CITE.findall(text)})
