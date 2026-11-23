"""Day 27 lab: trace a mini RAG pipeline with OpenTelemetry -> Phoenix, and build a
cost/latency/quality ledger from the same spans (vendor-neutral SpanProcessor)."""
import contextvars, os, statistics, time
from collections import defaultdict

from openai import OpenAI
from opentelemetry import trace
from opentelemetry.sdk.trace import SpanProcessor
from phoenix.otel import register
from openinference.instrumentation.openai import OpenAIInstrumentor

PHOENIX = os.getenv("PHOENIX_ENDPOINT", "http://localhost:6006/v1/traces")
MODEL = os.getenv("LAB_MODEL", "qwen2.5:1.5b")
BASE_URL = os.getenv("LAB_BASE_URL", "http://localhost:11434/v1")  # Ollama's OpenAI-compatible API

# Shadow price list (USD per 1M tokens). Local models are "free", but pricing them as if they
# were a hosted model is how you build the business case before choosing a provider.
PRICES = {MODEL: {"in": 0.15, "out": 0.60}}

PROMPT_VERSION = contextvars.ContextVar("prompt_version", default="?")


class CostLedger(SpanProcessor):
    """Reads token usage from LLM spans. Understands both OpenInference attributes
    (llm.token_count.*) and OTel GenAI semconv (gen_ai.usage.*), so it survives a swap
    of instrumentation library."""

    def __init__(self):
        self.rows = []

    def on_start(self, span, parent_context=None):
        # Stamp request-scoped metadata on EVERY span (incl. auto-instrumented LLM spans),
        # the same idea as Langfuse's propagate_attributes(). Enables group-by-version.
        span.set_attribute("app.prompt.version", PROMPT_VERSION.get())

    def on_end(self, span):
        a = span.attributes or {}
        tin = a.get("gen_ai.usage.input_tokens", a.get("llm.token_count.prompt"))
        tout = a.get("gen_ai.usage.output_tokens", a.get("llm.token_count.completion"))
        if tin is None:
            return
        model = a.get("gen_ai.request.model", a.get("llm.model_name", MODEL))
        p = PRICES.get(model, next(iter(PRICES.values())))
        self.rows.append({
            "version": a.get("app.prompt.version", "?"),
            "ms": (span.end_time - span.start_time) / 1e6,
            "tin": tin, "tout": tout,
            "usd": tin / 1e6 * p["in"] + tout / 1e6 * p["out"],
        })

    def report(self):
        by = defaultdict(list)
        for r in self.rows:
            by[r["version"]].append(r)
        print(f"\n{'prompt':<8}{'calls':>6}{'p50 ms':>9}{'p95 ms':>9}{'tok in':>8}{'tok out':>8}{'$/1k req':>10}")
        for v, rs in sorted(by.items()):
            ms = sorted(r["ms"] for r in rs)
            p95 = ms[min(len(ms) - 1, int(0.95 * len(ms)))]
            usd = sum(r["usd"] for r in rs) / len(rs) * 1000
            print(f"{v:<8}{len(rs):>6}{statistics.median(ms):>9.0f}{p95:>9.0f}"
                  f"{sum(r['tin'] for r in rs):>8}{sum(r['tout'] for r in rs):>8}{usd:>10.4f}")


# ---- tracing setup -------------------------------------------------------------------------
tp = register(project_name="day27-observability", endpoint=PHOENIX, batch=True, verbose=False)
ledger = CostLedger()
# GOTCHA: phoenix.otel replaces its exporter on add_span_processor() unless told not to.
tp.add_span_processor(ledger, replace_default_processor=False)
OpenAIInstrumentor().instrument(tracer_provider=tp)
tracer = trace.get_tracer("day27.lab")

client = OpenAI(base_url=BASE_URL, api_key="ollama")

DOCS = {
    "kb-01": "Employees may carry forward at most 10 days of unused annual leave into the next year.",
    "kb-02": "Expense claims above INR 50,000 need approval from a cost-centre head within 7 days.",
    "kb-03": "Production access requires a ticket, manager approval and expires after 8 hours.",
    "kb-04": "Laptops must be encrypted with BitLocker; USB mass storage is blocked by policy.",
}
PROMPTS = {
    "v1": "Answer the question using the context.\nContext:\n{ctx}\nQuestion: {q}",
    "v2": ("You are a policy assistant. Answer ONLY from the context in one sentence and cite the "
           "doc id in brackets. If the context does not contain the answer, reply exactly: "
           "I don't know.\nContext:\n{ctx}\nQuestion: {q}"),
}
QUESTIONS = [
    "How many leave days can I carry forward?",
    "Who approves an expense claim of INR 80,000?",
    "How long does production access last?",
    "What is the office Wi-Fi password?",  # unanswerable: tests abstention
]


def retrieve(q, k=2):
    with tracer.start_as_current_span("retrieve") as s:
        s.set_attribute("openinference.span.kind", "RETRIEVER")
        s.set_attribute("input.value", q)
        qw = set(q.lower().split())
        ranked = sorted(DOCS.items(), key=lambda kv: -len(qw & set(kv[1].lower().split())))[:k]
        for i, (doc_id, text) in enumerate(ranked):  # OpenInference retrieval attribute layout
            s.set_attribute(f"retrieval.documents.{i}.document.id", doc_id)
            s.set_attribute(f"retrieval.documents.{i}.document.content", text)
        return ranked


def groundedness(answer, ctx):
    """Cheap lexical proxy (0..1). In production use an LLM judge or NLI model - see stretch goal."""
    aw = {w.strip(".,[]").lower() for w in answer.split() if len(w) > 3}
    cw = {w.strip(".,").lower() for w in ctx.split()}
    return round(len(aw & cw) / max(1, len(aw)), 2)


def answer(q, version):
    token = PROMPT_VERSION.set(version)
    try:
        return _answer(q, version)
    finally:
        PROMPT_VERSION.reset(token)


def _answer(q, version):
    with tracer.start_as_current_span("rag.answer") as root:
        root.set_attribute("openinference.span.kind", "CHAIN")
        root.set_attribute("input.value", q)
        root.set_attribute("session.id", "lab-session-1")
        docs = retrieve(q)
        ctx = "\n".join(f"[{d}] {t}" for d, t in docs)
        resp = client.chat.completions.create(
            model=MODEL, temperature=0, max_tokens=120,
            messages=[{"role": "user", "content": PROMPTS[version].format(ctx=ctx, q=q)}],
        )
        text = resp.choices[0].message.content.strip()
        g = groundedness(text, ctx)
        root.set_attribute("output.value", text)
        root.set_attribute("eval.groundedness", g)
        print(f"[{version}] {q[:42]:<44} g={g:<5} -> {text[:70]}")
        return text


if __name__ == "__main__":
    t0 = time.time()
    for v in PROMPTS:
        for q in QUESTIONS:
            answer(q, v)
    tp.force_flush()
    ledger.report()
    print(f"\nDone in {time.time() - t0:.1f}s. Open http://localhost:6006 -> project 'day27-observability'.")
