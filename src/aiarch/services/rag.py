"""The RAG use case. Read this file first: it is the architecture in ~100 lines.

    guard(input) -> cache lookup (scoped) -> hybrid retrieve (ACL pre-filtered)
      -> abstain if nothing relevant -> versioned prompt -> gateway (fallbacks, budget)
      -> guard(output: citations, PII) -> cache store -> Answer (with cost, trace id)

Every failure mode maps to an explicit AnswerStatus instead of an exception or a
guess: BLOCKED (guardrail), ABSTAINED (no grounding), DEGRADED (all models down,
return sources only). That is what makes the system defensible in a review.
"""
from __future__ import annotations

from dataclasses import dataclass

from aiarch.domain.errors import AllProvidersFailed, BudgetExceeded, GuardrailViolation
from aiarch.domain.models import Answer, AnswerStatus, Citation, Principal, Usage
from aiarch.ports import AnswerCache, LLMRequest, Tracer
from aiarch.services.gateway import ModelGateway
from aiarch.services.guardrails import InputGuard, OutputGuard, cited_indices
from aiarch.services.prompts import PromptRegistry, render_grounded
from aiarch.services.retrieval import HybridRetriever

ABSTAIN_TEXT = "I could not find this in the documents you have access to."


def cache_scope(p: Principal) -> str:
    return f"{p.region}|{p.tenant_id}|{','.join(sorted(p.groups))}"


@dataclass
class RagService:
    retriever: HybridRetriever
    gateway: ModelGateway
    prompts: PromptRegistry
    cache: AnswerCache
    tracer: Tracer
    input_guard: InputGuard
    output_guard: OutputGuard
    prompt_id: str = "grounded_qa"
    max_output_tokens: int = 400

    def ask(self, question: str, principal: Principal) -> Answer:
        with self.tracer.span("rag.ask", **{"enduser.tenant": principal.tenant_id}) as sp:
            trace_id = self.tracer.current_trace_id()
            try:
                q = self.input_guard(question)
            except GuardrailViolation as e:
                sp.set("guard.blocked", e.rule)
                return Answer(AnswerStatus.BLOCKED, "This request was blocked by policy.",
                              trace_id=trace_id, reasons=[e.rule])

            scope = cache_scope(principal)
            hit = self.cache.get(q.text, scope)
            if hit is not None:
                cached, sim = hit
                sp.set("cache.hit", True)
                sp.set("cache.similarity", round(sim, 4))
                return Answer(cached.status, cached.text, list(cached.citations), cached.model,
                              cached.prompt_version, Usage(), 0.0, True, trace_id, ["semantic_cache"])

            hits = self.retriever.retrieve(q.text, principal)
            if not hits:
                return Answer(AnswerStatus.ABSTAINED, ABSTAIN_TEXT, trace_id=trace_id,
                              reasons=["no_relevant_context"])

            tpl = self.prompts.get(self.prompt_id)
            req = LLMRequest(messages=render_grounded(tpl, q.text, hits),
                             max_output_tokens=self.max_output_tokens,
                             metadata={"prompt": tpl.ref, "tenant": principal.tenant_id})
            try:
                result = self.gateway.generate(req)
            except (AllProvidersFailed, BudgetExceeded) as e:
                sp.set("rag.degraded", type(e).__name__)
                return Answer(AnswerStatus.DEGRADED,
                              "The assistant is temporarily unavailable. Relevant sources are listed.",
                              [Citation(h.chunk.doc_id, h.chunk.chunk_id, h.chunk.doc_version) for h in hits],
                              prompt_version=tpl.ref, trace_id=trace_id, reasons=[type(e).__name__])

            text = result.response.text.strip()

            def answer(status: AnswerStatus, body: str, cites: list[Citation], reasons: list[str]) -> Answer:
                return Answer(status, body, cites, model=result.response.model, prompt_version=tpl.ref,
                              usage=result.response.usage, cost_usd=result.cost_usd,
                              trace_id=trace_id, reasons=reasons)

            if text == "INSUFFICIENT_CONTEXT":
                return answer(AnswerStatus.ABSTAINED, ABSTAIN_TEXT, [], ["model_abstained"])
            try:
                out = self.output_guard(text, n_sources=len(hits), sources=[h.chunk.text for h in hits])
            except GuardrailViolation as e:
                sp.set("guard.output_blocked", e.rule)
                return answer(AnswerStatus.ABSTAINED, ABSTAIN_TEXT, [], [e.rule])

            cites = [hits[i - 1].chunk for i in cited_indices(out.text)]
            final = answer(AnswerStatus.ANSWERED, out.text,
                           [Citation(c.doc_id, c.chunk_id, c.doc_version) for c in cites], [])
            self.cache.put(q.text, scope, final, [c.doc_id for c in cites])
            sp.set("rag.citations", len(cites))
            return final
