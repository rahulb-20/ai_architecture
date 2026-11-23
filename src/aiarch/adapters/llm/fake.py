"""Deterministic offline LLM.

Lets the whole stack (API, RAG, agent, evals, CI) run with zero network access and
zero cost. It is *extractive*: it answers by quoting the context sentence that best
overlaps the question and cites it, which is exactly the behaviour a grounded RAG
prompt asks a real model for. Swap it out with `AIARCH_LLM_ROUTES`.
"""
from __future__ import annotations

import json
import re
import time

from aiarch.domain.errors import ProviderError, RateLimited
from aiarch.domain.models import Usage
from aiarch.ports import LLMRequest, LLMResponse

_WORD = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "is", "are", "of", "to", "for", "in", "on", "and", "or", "what",
         "how", "do", "i", "my", "we", "can", "does", "with", "be", "it", "at", "by", "much", "many"}


def _terms(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 1}


class FakeLLM:
    """`fail_first` lets tests and chaos drills simulate an outage on this route."""

    def __init__(self, name: str = "fake", model: str = "fake-extractive-1",
                 fail_first: int = 0, rate_limit: bool = False) -> None:
        self.name = name
        self.model = model
        self._fail_remaining = fail_first
        self._rate_limit = rate_limit
        self.calls = 0

    def generate(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        t0 = time.perf_counter()
        if self._fail_remaining > 0:
            self._fail_remaining -= 1
            if self._rate_limit:
                raise RateLimited("simulated 429", provider=self.name, retry_after_s=0.0)
            raise ProviderError("simulated 503", provider=self.name, retryable=True)

        prompt = "\n".join(m.content for m in request.messages)
        if request.json_schema is not None:
            text = json.dumps(self._json_answer(prompt, request.json_schema))
        else:
            text = self._extract(prompt)
        usage = Usage(input_tokens=max(1, len(prompt) // 4), output_tokens=max(1, len(text) // 4))
        return LLMResponse(text=text, model=self.model, provider=self.name, usage=usage,
                           latency_ms=(time.perf_counter() - t0) * 1000)

    @staticmethod
    def _split(prompt: str) -> tuple[str, list[tuple[str, str]]]:
        question = ""
        m = re.search(r"QUESTION:\s*(.+)", prompt)
        if m:
            question = m.group(1).strip()
        sources = re.findall(r"\[(S\d+)\]\s*(.+?)(?=\n\[S\d+\]|\nQUESTION:|\Z)", prompt, re.S)
        return question, [(sid, txt.strip()) for sid, txt in sources]

    def _extract(self, prompt: str) -> str:
        question, sources = self._split(prompt)
        if not sources:
            return question and f"I received: {question}" or "OK"
        q = _terms(question)
        best: tuple[float, str, str] = (0.0, "", "")
        for sid, txt in sources:
            for sent in re.split(r"(?<=[.!?])\s+", txt):
                s = _terms(sent)
                score = len(q & s) / (len(q) or 1)
                if score > best[0]:
                    best = (score, sid, sent.strip())
        if best[0] < 0.34:
            return "INSUFFICIENT_CONTEXT"
        return f"{best[2]} [{best[1]}]"

    def _json_answer(self, prompt: str, schema: dict) -> dict:
        props = schema.get("properties", {})
        out: dict = {}
        for key, spec in props.items():
            t = spec.get("type")
            if "enum" in spec:
                out[key] = spec["enum"][0]
            elif t == "string":
                out[key] = self._extract(prompt)
            elif t in ("number", "integer"):
                out[key] = 0
            elif t == "boolean":
                out[key] = False
            elif t == "array":
                out[key] = []
            else:
                out[key] = None
        return out
