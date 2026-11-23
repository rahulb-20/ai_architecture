"""Minimal tracer that emits JSON-lines spans using OpenTelemetry GenAI attribute names.

No collector is needed to start; the span shape is OTel-compatible, so you can
replace this adapter with an OTel SDK / Langfuse / Phoenix exporter later without
changing any service code (services only see the `Tracer` port).
"""
from __future__ import annotations

import contextvars
import json
import logging
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

_trace_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("trace_id", default=None)
_parent: contextvars.ContextVar[str | None] = contextvars.ContextVar("parent_span", default=None)

log = logging.getLogger("aiarch.trace")


class _Span:
    def __init__(self, attrs: dict[str, Any]) -> None:
        self.attrs = attrs

    def set(self, key: str, value: Any) -> None:
        self.attrs[key] = value


class JsonTracer:
    """`sink=None` logs via `logging`; pass a list to capture spans in tests."""

    def __init__(self, service_name: str = "aiarch", sink: list[dict] | None = None,
                 redact_keys: frozenset[str] = frozenset({"gen_ai.prompt", "gen_ai.completion"})) -> None:
        self.service_name = service_name
        self.sink = sink
        self.redact_keys = redact_keys

    @contextmanager
    def span(self, name: str, **attrs: Any) -> Iterator[_Span]:
        trace_id = _trace_id.get()
        root = trace_id is None
        if root:
            trace_id = uuid.uuid4().hex
        span_id = uuid.uuid4().hex[:16]
        t1 = _trace_id.set(trace_id)
        parent = _parent.get()
        t2 = _parent.set(span_id)
        sp = _Span(dict(attrs))
        start = time.perf_counter()
        status = "ok"
        try:
            yield sp
        except Exception as e:
            status = "error"
            sp.set("error.type", type(e).__name__)
            raise
        finally:
            _parent.reset(t2)
            _trace_id.reset(t1)
            rec = {
                "service": self.service_name, "trace_id": trace_id, "span_id": span_id,
                "parent_span_id": parent, "name": name, "status": status,
                "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                "attributes": {k: ("[redacted]" if k in self.redact_keys else v)
                               for k, v in sp.attrs.items()},
            }
            if self.sink is not None:
                self.sink.append(rec)
            else:
                log.info(json.dumps(rec, default=str))

    def current_trace_id(self) -> str | None:
        return _trace_id.get()


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(stream=sys.stdout, level=level, format="%(message)s")
