import random

import pytest

from aiarch.adapters.llm.fake import FakeLLM
from aiarch.adapters.telemetry.tracer import JsonTracer
from aiarch.domain.errors import AllProvidersFailed, BudgetExceeded, ProviderError, RateLimited
from aiarch.domain.models import Usage
from aiarch.ports import LLMRequest, LLMResponse, Message
from aiarch.services.cost import PriceBook
from aiarch.services.gateway import CircuitBreaker, ModelGateway, Route

REQ = LLMRequest([Message("user", "hello")])


class Flaky:
    """Raises the queued errors in order, then answers. Records every request."""

    def __init__(self, name: str, errors=()) -> None:
        self.name, self.model, self.errors, self.requests = name, f"{name}-model", list(errors), []

    def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if self.errors:
            raise self.errors.pop(0)
        return LLMResponse("ok", self.model, self.name, Usage(10, 5), 1.0)


def _err(name: str, retryable: bool = True) -> ProviderError:
    return ProviderError("boom", provider=name, retryable=retryable)


def _gateway(routes, spans=None, clock=None, **kw):
    sleeps: list[float] = []
    now = clock if clock is not None else {"t": 0.0}
    gw = ModelGateway(routes, JsonTracer(sink=spans if spans is not None else []), PriceBook(),
                      sleep=sleeps.append, clock=lambda: now["t"], **kw)
    return gw, sleeps


def test_a_gateway_needs_at_least_one_route():
    with pytest.raises(ValueError, match="at least one route"):
        ModelGateway([], JsonTracer(sink=[]), PriceBook())


def test_first_healthy_route_answers_in_one_attempt():
    a, b = Flaky("a"), Flaky("b")
    gw, sleeps = _gateway([Route(a), Route(b)])
    r = gw.generate(REQ)
    assert (r.route, r.attempts, r.response.text) == ("a", 1, "ok") and b.requests == [] and sleeps == []


def test_route_model_overrides_the_request_without_mutating_it():
    client = Flaky("a")
    gw, _ = _gateway([Route(client, model="pinned-model")])
    req = LLMRequest([Message("user", "hi")], model="asked-for")
    gw.generate(req)
    assert client.requests[0].model == "pinned-model" and req.model == "asked-for"
    plain = Flaky("b")
    _gateway([Route(plain)])[0].generate(req)
    assert plain.requests[0].model == "asked-for"


def test_attempts_are_counted_across_retries_and_routes():
    a, b = Flaky("a", [_err("a"), _err("a")]), Flaky("b")
    gw, sleeps = _gateway([Route(a, max_retries=1), Route(b)])
    r = gw.generate(REQ)
    assert (r.route, r.attempts) == ("b", 3) and len(a.requests) == 2 and len(sleeps) == 1


def test_non_retryable_error_moves_on_at_once_without_sleeping():
    a, b = Flaky("a", [_err("a", retryable=False)]), Flaky("b")
    gw, sleeps = _gateway([Route(a, max_retries=5), Route(b)])
    assert gw.generate(REQ).route == "b" and len(a.requests) == 1 and sleeps == []


def test_retry_after_from_the_provider_sets_the_wait():
    a = Flaky("a", [RateLimited("slow down", provider="a", retry_after_s=7.5)])
    gw, sleeps = _gateway([Route(a, max_retries=1)])
    assert gw.generate(REQ).attempts == 2 and sleeps == [7.5]


def test_backoff_doubles_each_retry(monkeypatch):
    monkeypatch.setattr(random, "random", lambda: 0.5)
    a = Flaky("a", [_err("a")] * 3)
    gw, sleeps = _gateway([Route(a, max_retries=3, breaker=CircuitBreaker(failure_threshold=99))], backoff_base_s=1.0)
    assert gw.generate(REQ).attempts == 4 and sleeps == [1.0, 2.0, 4.0]


def test_jitter_keeps_each_wait_between_half_and_one_and_a_half_times_the_base(monkeypatch):
    for roll, expected in ((0.0, 0.5), (0.999, 1.499)):
        monkeypatch.setattr(random, "random", lambda roll=roll: roll)
        gw, sleeps = _gateway([Route(Flaky("a", [_err("a")]), max_retries=1)], backoff_base_s=1.0)
        gw.generate(REQ)
        assert sleeps == [pytest.approx(expected)]


def test_all_routes_failing_lists_every_error():
    gw, _ = _gateway([Route(Flaky("a", [_err("a")] * 9), max_retries=0), Route(Flaky("b", [_err("b", False)]))])
    with pytest.raises(AllProvidersFailed) as e:
        gw.generate(REQ)
    assert str(e.value) == "a: boom; b: boom"


def test_open_breaker_skips_the_route_without_calling_it():
    a, b = Flaky("a", [_err("a")] * 3), Flaky("b")
    gw, _ = _gateway([Route(a, max_retries=2), Route(b)])
    gw.generate(REQ)
    assert gw.routes[0].breaker.is_open and len(a.requests) == 3
    assert gw.generate(REQ).route == "b" and len(a.requests) == 3


def test_all_breakers_open_fails_fast_and_says_so():
    a = Flaky("a", [_err("a")] * 3)
    gw, _ = _gateway([Route(a, max_retries=2)])
    with pytest.raises(AllProvidersFailed):
        gw.generate(REQ)
    with pytest.raises(AllProvidersFailed, match="a: circuit open"):
        gw.generate(REQ)
    assert len(a.requests) == 3


def test_breaker_lets_one_probe_through_after_the_reset_time_and_closes_on_success():
    clock = {"t": 0.0}
    a, b = Flaky("a", [_err("a")] * 3), Flaky("b")
    gw, _ = _gateway([Route(a, max_retries=2), Route(b)], clock=clock)
    gw.generate(REQ)
    clock["t"] = 29.9
    assert gw.generate(REQ).route == "b"
    clock["t"] = 30.0
    assert gw.generate(REQ).route == "a" and not gw.routes[0].breaker.is_open


def test_failed_probe_reopens_the_breaker_immediately():
    clock = {"t": 0.0}
    a, b = Flaky("a", [_err("a")] * 4), Flaky("b")
    gw, _ = _gateway([Route(a, max_retries=0), Route(b)], clock=clock)
    for _ in range(3):
        assert gw.generate(REQ).route == "b"
    assert gw.routes[0].breaker.is_open and len(a.requests) == 3
    clock["t"] = 31.0
    assert gw.generate(REQ).route == "b" and len(a.requests) == 4 and gw.routes[0].breaker.is_open
    assert gw.generate(REQ).route == "b" and len(a.requests) == 4, "no second probe until the reset time passes again"


def test_a_success_resets_the_failure_count():
    a = Flaky("a", [_err("a"), _err("a")])
    gw, _ = _gateway([Route(a, max_retries=2)])
    gw.generate(REQ)
    a.errors = [_err("a"), _err("a")]
    gw.generate(REQ)
    assert not gw.routes[0].breaker.is_open


def test_breaker_state_machine_on_its_own():
    b = CircuitBreaker(failure_threshold=2, reset_after_s=10)
    assert b.allow(0) and not b.is_open
    b.record(False, 0)
    assert b.allow(1) and not b.is_open
    b.record(False, 1)
    assert b.is_open and not b.allow(5) and not b.allow(10.9)
    assert b.allow(11) and not b.is_open
    b.record(False, 11)
    assert b.is_open, "one failure after a probe must reopen"
    assert b.allow(21)
    b.record(True, 21)
    b.record(False, 22)
    assert not b.is_open


def test_budget_is_inclusive_and_can_be_supplied_by_the_caller():
    client = Flaky("a")
    gw, _ = _gateway([Route(client)], max_input_tokens=10)
    gw.generate(LLMRequest([Message("user", "x" * 40)]))
    with pytest.raises(BudgetExceeded, match="estimated 11 input tokens > budget 10"):
        gw.generate(LLMRequest([Message("user", "x" * 44)]))
    with pytest.raises(BudgetExceeded):
        gw.generate(REQ, estimated_input_tokens=11)
    gw.generate(LLMRequest([Message("user", "x" * 400)]), estimated_input_tokens=10)
    assert len(client.requests) == 2


def test_budget_counts_every_message():
    gw, _ = _gateway([Route(Flaky("a"))], max_input_tokens=10)
    with pytest.raises(BudgetExceeded):
        gw.generate(LLMRequest([Message("system", "x" * 30), Message("user", "x" * 30)]))


def test_one_span_per_attempt_with_errors_and_usage():
    spans: list[dict] = []
    gw, _ = _gateway([Route(Flaky("a", [_err("a")]), max_retries=1)], spans)
    gw.generate(LLMRequest([Message("user", "hi")], temperature=0.2, max_output_tokens=64))
    first, second = spans
    assert [s["name"] for s in spans] == ["gen_ai.chat", "gen_ai.chat"]
    assert [s["attributes"]["aiarch.attempt"] for s in spans] == [1, 2]
    assert first["attributes"]["error.type"] == "ProviderError"
    assert "gen_ai.usage.input_tokens" not in first["attributes"]
    attrs = second["attributes"]
    assert (attrs["gen_ai.system"], attrs["gen_ai.request.model"], attrs["gen_ai.response.model"]) == ("a",
        "a-model", "a-model")
    assert (attrs["gen_ai.request.temperature"], attrs["gen_ai.request.max_tokens"]) == (0.2, 64)
    assert (attrs["gen_ai.usage.input_tokens"], attrs["gen_ai.usage.output_tokens"]) == (10, 5)


def test_a_bug_in_an_adapter_is_not_swallowed_as_a_provider_failure():
    class Broken:
        name = "broken"

        def generate(self, request):
            raise RuntimeError("adapter bug")
    b = FakeLLM(name="b")
    gw, _ = _gateway([Route(Broken()), Route(b)])
    with pytest.raises(RuntimeError, match="adapter bug"):
        gw.generate(REQ)
    assert b.calls == 0
