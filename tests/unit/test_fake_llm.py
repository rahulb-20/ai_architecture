"""The offline model stands in for a real one in every check, so its behaviour is part of the contract."""
import json

import pytest

from aiarch.adapters.llm.fake import FakeLLM
from aiarch.domain.errors import ProviderError, RateLimited
from aiarch.ports import LLMClient, LLMRequest, Message

GROUNDED = ("SOURCES:\n[S1] Cats sleep most of the day. Dogs bark loudly.\n"
            "[S2] VPN passwords expire every 90 days.\nQUESTION: How often do VPN passwords expire?")


def _ask(text: str, **kw) -> str:
    return FakeLLM().generate(LLMRequest([Message("user", text)], **kw)).text


def test_it_is_an_llm_client_with_stable_names():
    llm = FakeLLM(name="local", model="tiny")
    r = llm.generate(LLMRequest([Message("user", GROUNDED)]))
    assert isinstance(llm, LLMClient) and (r.provider, r.model) == ("local", "tiny")


def test_it_quotes_the_best_sentence_and_cites_its_source():
    assert _ask(GROUNDED) == "VPN passwords expire every 90 days. [S2]"


def test_it_is_deterministic():
    assert _ask(GROUNDED) == _ask(GROUNDED)


def test_it_abstains_when_no_sentence_overlaps_enough():
    prompt = "SOURCES:\n[S1] Cats sleep most of the day.\nQUESTION: What is the travel per diem in metro cities?"
    assert _ask(prompt) == "INSUFFICIENT_CONTEXT"


def test_without_sources_it_echoes_the_question_or_says_ok():
    assert _ask("QUESTION: ping") == "I received: ping" and _ask("hello") == "OK"


def test_usage_is_estimated_and_never_zero():
    r = FakeLLM().generate(LLMRequest([Message("user", "x")]))
    assert r.usage.input_tokens >= 1 and r.usage.output_tokens >= 1 and r.latency_ms >= 0
    long = FakeLLM().generate(LLMRequest([Message("user", "x" * 4_000)]))
    assert long.usage.input_tokens == 1_000


def test_structured_output_fills_each_type_with_a_fixed_value():
    schema = {"type": "object", "properties": {
        "choice": {"type": "string", "enum": ["first", "second"]}, "text": {"type": "string"},
        "num": {"type": "number"}, "count": {"type": "integer"}, "flag": {"type": "boolean"},
        "items": {"type": "array"}, "obj": {"type": "object"}, "maybe": {"type": ["string", "null"]}, "bare": {}}}
    out = json.loads(_ask("hello", json_schema=schema))
    assert out == {"choice": "first", "text": "OK", "num": 0, "count": 0, "flag": False, "items": [],
                   "obj": None, "maybe": None, "bare": None}


def test_structured_output_with_no_properties_is_an_empty_object():
    assert json.loads(_ask("hello", json_schema={"type": "object"})) == {}


def test_string_fields_are_extracted_from_the_sources():
    out = json.loads(_ask(GROUNDED, json_schema={"properties": {"answer": {"type": "string"}}}))
    assert out == {"answer": "VPN passwords expire every 90 days. [S2]"}


def test_fail_first_simulates_an_outage_then_recovers():
    llm = FakeLLM(fail_first=2)
    for _ in range(2):
        with pytest.raises(ProviderError) as e:
            llm.generate(LLMRequest([Message("user", "x")]))
        assert e.value.retryable is True and not isinstance(e.value, RateLimited)
    assert llm.generate(LLMRequest([Message("user", "x")])).text == "OK" and llm.calls == 3


def test_rate_limit_mode_raises_rate_limited_with_no_wait():
    llm = FakeLLM(name="r", fail_first=1, rate_limit=True)
    with pytest.raises(RateLimited) as e:
        llm.generate(LLMRequest([Message("user", "x")]))
    assert e.value.retry_after_s == 0.0 and e.value.provider == "r"


def test_system_and_user_messages_are_both_read():
    r = FakeLLM().generate(LLMRequest([Message("system", "[S1] VPN passwords expire every 90 days."),
                                       Message("user", "QUESTION: How often do VPN passwords expire?")]))
    assert r.text.endswith("[S1]")
