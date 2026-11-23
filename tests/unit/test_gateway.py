import pytest

from aiarch.adapters.llm.fake import FakeLLM
from aiarch.adapters.telemetry.tracer import JsonTracer
from aiarch.domain.errors import AllProvidersFailed, BudgetExceeded, ProviderError
from aiarch.ports import LLMRequest, Message
from aiarch.services.cost import ModelPrice, PriceBook
from aiarch.services.gateway import CircuitBreaker, ModelGateway, Route

REQ = LLMRequest(messages=[Message("user", "hello")])


def gw(routes, **kw):
    return ModelGateway(routes, JsonTracer(sink=[]), PriceBook(), sleep=lambda s: None, **kw)


def test_retry_then_success_on_same_route():
    primary = FakeLLM("primary", fail_first=1)
    r = gw([Route(primary, max_retries=1)]).generate(REQ)
    assert r.route == "primary" and r.attempts == 2


def test_fallback_to_secondary_when_primary_down():
    primary, secondary = FakeLLM("primary", fail_first=99), FakeLLM("secondary")
    r = gw([Route(primary, max_retries=1), Route(secondary)]).generate(REQ)
    assert r.route == "secondary" and primary.calls == 2


def test_rate_limit_is_retryable():
    primary = FakeLLM("primary", fail_first=1, rate_limit=True)
    assert gw([Route(primary, max_retries=1)]).generate(REQ).route == "primary"


def test_all_routes_fail():
    with pytest.raises(AllProvidersFailed):
        gw([Route(FakeLLM("a", fail_first=9), max_retries=0)]).generate(REQ)


def test_non_retryable_error_skips_retries():
    class Bad(FakeLLM):
        def generate(self, request):
            self.calls += 1
            raise ProviderError("400", provider=self.name, retryable=False)
    bad, good = Bad("bad"), FakeLLM("good")
    r = gw([Route(bad, max_retries=3), Route(good)]).generate(REQ)
    assert bad.calls == 1 and r.route == "good"


def test_circuit_breaker_opens_and_half_opens():
    now = [0.0]
    br = CircuitBreaker(failure_threshold=2, reset_after_s=10)
    primary = FakeLLM("primary", fail_first=2)
    g = ModelGateway([Route(primary, max_retries=0, breaker=br), Route(FakeLLM("backup"))],
                     JsonTracer(sink=[]), PriceBook(), sleep=lambda s: None, clock=lambda: now[0])
    g.generate(REQ)
    g.generate(REQ)
    assert br.is_open
    calls = primary.calls
    g.generate(REQ)
    assert primary.calls == calls          # skipped while open
    now[0] = 11
    assert g.generate(REQ).route == "primary"   # half-open probe succeeds
    assert not br.is_open


def test_budget_enforced_before_any_call():
    llm = FakeLLM()
    with pytest.raises(BudgetExceeded):
        gw([Route(llm)], max_input_tokens=5).generate(LLMRequest(messages=[Message("user", "x" * 400)]))
    assert llm.calls == 0


def test_cost_accounting_uses_price_book():
    book = PriceBook({"fake-extractive-1": ModelPrice(1_000_000, 0)})
    g = ModelGateway([Route(FakeLLM())], JsonTracer(sink=[]), book)
    r = g.generate(REQ)
    assert r.cost_usd == pytest.approx(r.response.usage.input_tokens)
