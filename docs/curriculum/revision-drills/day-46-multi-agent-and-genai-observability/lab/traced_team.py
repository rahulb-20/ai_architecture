"""Day 46 lab, part 1: a supervisor team that emits OpenTelemetry GenAI spans.

Runs offline with a scripted fake model (no GPU, no API key). Spans follow the
OpenTelemetry GenAI semantic conventions (status: Development, semconv 1.45):
  invoke_workflow <team>  ->  invoke_agent <agent>  ->  chat <model> / execute_tool <tool>
Anything under the "app." prefix is our own attribute, not part of the spec.

Usage:
  python traced_team.py --scenario ok
  python traced_team.py --scenario pingpong --no-guard
  python traced_team.py --scenario pingpong
  python traced_team.py --scenario ok --context isolated
"""
from __future__ import annotations

import argparse
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace import SpanKind, Status, StatusCode

SUPERVISOR_MODEL = "fake-large"   # the router gets the expensive model
WORKER_MODEL = "fake-small"
OUT_TOKENS = {"supervisor": 20, "researcher": 120, "writer": 180}
HARD_STOP = 12                    # keeps --no-guard runs finite


class JsonlExporter(SpanExporter):
    """Writes one JSON object per finished span. Stands in for an OTLP exporter."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        with self.path.open("a", encoding="utf-8") as fh:
            for s in spans:
                fh.write(json.dumps({
                    "trace_id": format(s.context.trace_id, "032x"),
                    "span_id": format(s.context.span_id, "016x"),
                    "parent_id": format(s.parent.span_id, "016x") if s.parent else None,
                    "name": s.name,
                    "kind": s.kind.name,
                    "start_ns": s.start_time,
                    "end_ns": s.end_time,
                    "status": s.status.status_code.name,
                    "attributes": dict(s.attributes or {}),
                }) + "\n")
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:  # nothing to close
        return None


@dataclass
class Team:
    scenario: str
    context: str                      # "shared" or "isolated"
    guard: bool
    max_handoffs: int
    token_budget: int
    tracer: trace.Tracer
    transcript: list[str] = field(default_factory=list)
    tokens_used: int = 0
    handoffs: int = 0
    evidence: str = ""
    draft: str = ""

    # ---- model and tool calls (each one is a span) -------------------------
    def chat(self, agent: str, model: str, prompt: str, reply: str) -> str:
        in_tok, out_tok = max(1, len(prompt) // 4), OUT_TOKENS[agent]
        with self.tracer.start_as_current_span(f"chat {model}", kind=SpanKind.CLIENT) as span:
            span.set_attribute("gen_ai.operation.name", "chat")
            span.set_attribute("gen_ai.provider.name", "fake")
            span.set_attribute("gen_ai.request.model", model)
            span.set_attribute("gen_ai.usage.input_tokens", in_tok)
            span.set_attribute("gen_ai.usage.output_tokens", out_tok)
        self.tokens_used += in_tok + out_tok
        return reply + " " + "x" * (out_tok * 4 - len(reply) - 1)   # pad to the token count

    def kb_search(self, query: str) -> str:
        with self.tracer.start_as_current_span("execute_tool kb_search", kind=SpanKind.INTERNAL) as span:
            span.set_attribute("gen_ai.operation.name", "execute_tool")
            span.set_attribute("gen_ai.tool.name", "kb_search")
            if self.scenario == "tool_error":
                span.set_attribute("error.type", "TimeoutError")
                span.set_status(Status(StatusCode.ERROR, "kb_search timed out after 2 s"))
                return ""
            return "POL-7 s3.2: VPN passwords expire every 90 days."

    # ---- agents ---------------------------------------------------------------
    def view(self, brief: str) -> str:
        """What a worker sees: the whole transcript, or only its brief."""
        return "\n".join(self.transcript) if self.context == "shared" else brief

    def run_agent(self, name: str, came_from: str, brief: str) -> None:
        with self.tracer.start_as_current_span(f"invoke_agent {name}", kind=SpanKind.INTERNAL) as span:
            span.set_attribute("gen_ai.operation.name", "invoke_agent")
            span.set_attribute("gen_ai.agent.name", name)
            span.set_attribute("app.handoff.from", came_from)
            if name == "researcher":
                self.evidence = self.kb_search(brief)
                out = self.chat(name, WORKER_MODEL, self.view(brief) + self.evidence,
                                f"EVIDENCE: {self.evidence or 'none found'}")
            else:
                verdict = "NEED_MORE_EVIDENCE" if self.scenario == "pingpong" else "DRAFT_READY"
                if not self.evidence:
                    verdict = "ABSTAIN"
                self.draft = verdict
                out = self.chat(name, WORKER_MODEL, self.view(brief) + self.evidence, f"{verdict}:")
            # shared: the full output joins the transcript. isolated: a one-line summary does.
            self.transcript.append(f"[{name}] " + (out if self.context == "shared" else out[:80]))

    def route(self) -> str:
        """The supervisor's decision. One chat span per decision, on the large model."""
        with self.tracer.start_as_current_span("invoke_agent supervisor", kind=SpanKind.INTERNAL) as span:
            span.set_attribute("gen_ai.operation.name", "invoke_agent")
            span.set_attribute("gen_ai.agent.name", "supervisor")
            if not self.evidence and self.draft != "ABSTAIN" and self.handoffs == 0:
                nxt = "researcher"
            elif self.draft in ("", "NEED_MORE_EVIDENCE") and self.transcript[-1].startswith("[researcher]"):
                nxt = "writer"
            elif self.draft == "NEED_MORE_EVIDENCE":
                nxt = "researcher"
            else:
                nxt = "FINISH"
            self.chat("supervisor", SUPERVISOR_MODEL, "\n".join(self.transcript), f"next={nxt}")
            span.set_attribute("app.route.next", nxt)
            return nxt

    # ---- the workflow ---------------------------------------------------------
    def run(self, question: str) -> str:
        conversation_id = f"conv-{uuid.uuid4().hex[:8]}"   # in production: the real session id
        with self.tracer.start_as_current_span("invoke_workflow helpdesk_team", kind=SpanKind.INTERNAL) as root:
            root.set_attribute("gen_ai.operation.name", "invoke_workflow")
            root.set_attribute("gen_ai.conversation.id", conversation_id)
            root.set_attribute("app.scenario", self.scenario)
            root.set_attribute("app.context_mode", self.context)
            root.set_attribute("app.tenant", "acme")
            self.transcript.append(f"[user] {question}")
            outcome = "answered"
            for _ in range(HARD_STOP):
                nxt = self.route()
                if nxt == "FINISH":
                    break
                if self.guard and (self.handoffs >= self.max_handoffs or self.tokens_used >= self.token_budget):
                    outcome = "budget_exceeded"
                    root.set_attribute("error.type", "budget_exceeded")
                    root.set_status(Status(StatusCode.ERROR, "handoff or token budget exceeded"))
                    break
                self.handoffs += 1
                self.run_agent(nxt, "supervisor", question)
            else:
                outcome = "hard_stop"
                root.set_attribute("error.type", "hard_stop")
                root.set_status(Status(StatusCode.ERROR, "loop hit the hard stop"))
            if outcome == "answered" and self.draft == "ABSTAIN":
                outcome = "abstained"
            root.set_attribute("app.outcome", outcome)
            root.set_attribute("app.handoffs", self.handoffs)
        return f"{conversation_id} outcome={outcome} handoffs={self.handoffs} tokens={self.tokens_used}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", choices=["ok", "pingpong", "tool_error"], default="ok")
    ap.add_argument("--context", choices=["shared", "isolated"], default="shared")
    ap.add_argument("--no-guard", action="store_true", help="disable the handoff and token budgets")
    ap.add_argument("--max-handoffs", type=int, default=4)
    ap.add_argument("--token-budget", type=int, default=4000)
    ap.add_argument("--out", type=Path, default=Path("spans.jsonl"))
    args = ap.parse_args()

    provider = TracerProvider(resource=Resource.create({"service.name": "helpdesk-team"}))
    provider.add_span_processor(SimpleSpanProcessor(JsonlExporter(args.out)))
    team = Team(args.scenario, args.context, not args.no_guard, args.max_handoffs,
                args.token_budget, provider.get_tracer("day46.lab"))
    print(team.run("How often do VPN passwords expire, and what is the policy reference?"))
    provider.shutdown()


if __name__ == "__main__":
    main()
