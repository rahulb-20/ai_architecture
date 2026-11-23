"""Model gateway: one entry point for every LLM call in the system.

Responsibilities (ADR-0002):
- ordered fallback routes (primary -> secondary region/provider -> small local model)
- bounded retries with exponential backoff + jitter, honouring Retry-After
- a circuit breaker per route so a dead provider stops costing latency
- per-request token budget and cost accounting
- one span per attempt with OTel GenAI attributes

Services never import a provider SDK; they call `ModelGateway.generate`.
"""
from __future__ import annotations

import dataclasses
import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from aiarch.domain.errors import AllProvidersFailed, BudgetExceeded, ProviderError, RateLimited
from aiarch.ports import LLMClient, LLMRequest, LLMResponse, Tracer
from aiarch.services.cost import PriceBook


@dataclass
class CircuitBreaker:
    failure_threshold: int = 3
    reset_after_s: float = 30.0
    _failures: int = 0
    _opened_at: float | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def allow(self, now: float) -> bool:
        with self._lock:
            if self._opened_at is None:
                return True
            if now - self._opened_at >= self.reset_after_s:   # half-open: let one probe through
                self._opened_at = None
                self._failures = self.failure_threshold - 1
                return True
            return False

    def record(self, ok: bool, now: float) -> None:
        with self._lock:
            if ok:
                self._failures, self._opened_at = 0, None
            else:
                self._failures += 1
                if self._failures >= self.failure_threshold:
                    self._opened_at = now

    @property
    def is_open(self) -> bool:
        return self._opened_at is not None


@dataclass
class Route:
    client: LLMClient
    model: str | None = None
    max_retries: int = 1
    breaker: CircuitBreaker = field(default_factory=CircuitBreaker)


@dataclass
class GatewayResult:
    response: LLMResponse
    route: str
    attempts: int
    cost_usd: float


class ModelGateway:
    def __init__(self, routes: list[Route], tracer: Tracer, prices: PriceBook,
                 max_input_tokens: int = 12_000, backoff_base_s: float = 0.2,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if not routes:
            raise ValueError("gateway needs at least one route")
        self.routes = routes
        self.tracer = tracer
        self.prices = prices
        self.max_input_tokens = max_input_tokens
        self.backoff_base_s = backoff_base_s
        self._sleep, self._clock = sleep, clock

    def generate(self, request: LLMRequest, *, estimated_input_tokens: int | None = None) -> GatewayResult:
        est = estimated_input_tokens or sum(len(m.content) for m in request.messages) // 4
        if est > self.max_input_tokens:
            raise BudgetExceeded(f"estimated {est} input tokens > budget {self.max_input_tokens}")
        attempts = 0
        errors: list[str] = []
        for route in self.routes:
            if not route.breaker.allow(self._clock()):
                errors.append(f"{route.client.name}: circuit open")
                continue
            for retry in range(route.max_retries + 1):
                attempts += 1
                req = dataclasses.replace(request, model=route.model or request.model)
                with self.tracer.span("gen_ai.chat", **{
                    "gen_ai.system": route.client.name,
                    "gen_ai.request.model": req.model or getattr(route.client, "model", None),
                    "gen_ai.request.temperature": req.temperature,
                    "gen_ai.request.max_tokens": req.max_output_tokens,
                    "aiarch.attempt": attempts,
                }) as sp:
                    try:
                        resp = route.client.generate(req)
                    except ProviderError as e:
                        route.breaker.record(False, self._clock())
                        sp.set("error.type", type(e).__name__)
                        errors.append(f"{route.client.name}: {e}")
                        if not e.retryable or retry == route.max_retries:
                            break
                        delay = (e.retry_after_s if isinstance(e, RateLimited) and e.retry_after_s
                                 else self.backoff_base_s * (2 ** retry) * (0.5 + random.random()))
                        self._sleep(delay)
                        continue
                    route.breaker.record(True, self._clock())
                    cost = self.prices.cost(resp.model, resp.usage)
                    sp.set("gen_ai.response.model", resp.model)
                    sp.set("gen_ai.usage.input_tokens", resp.usage.input_tokens)
                    sp.set("gen_ai.usage.output_tokens", resp.usage.output_tokens)
                    sp.set("aiarch.cost_usd", round(cost, 6))
                    return GatewayResult(resp, route.client.name, attempts, cost)
        raise AllProvidersFailed("; ".join(errors) or "no routes available")
