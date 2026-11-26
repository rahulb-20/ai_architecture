"""Error mapping is what lets the gateway retry, fall back and trip its breaker. No network: httpx.MockTransport."""
import json

import httpx
import pytest

from aiarch.adapters.llm.openai_compatible import OpenAICompatibleLLM
from aiarch.domain.errors import ProviderError, RateLimited
from aiarch.domain.models import Usage
from aiarch.ports import LLMRequest, Message

REQ = LLMRequest(messages=[Message("system", "be brief"), Message("user", "hi")])
OK = {"model": "served-model", "choices": [{"message": {"content": "hello"}}],
      "usage": {"prompt_tokens": 7, "completion_tokens": 3}}


def _llm(handler, model: str = "m1") -> OpenAICompatibleLLM:
    client = httpx.Client(base_url="http://test/v1", transport=httpx.MockTransport(handler))
    return OpenAICompatibleLLM("prov", "http://test/v1", model, client=client)


def _capture(response: dict):
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["path"], seen["body"] = req.url.path, json.loads(req.read())
        return httpx.Response(200, json=response)
    return handler, seen


# --- request shape ---------------------------------------------------------------

def test_request_carries_messages_model_and_limits():
    handler, seen = _capture(OK)
    _llm(handler).generate(LLMRequest(messages=REQ.messages, temperature=0.3, max_output_tokens=123))
    assert seen["path"] == "/v1/chat/completions"
    assert seen["body"] == {"model": "m1", "temperature": 0.3, "max_tokens": 123,
                            "messages": [{"role": "system", "content": "be brief"}, {"role": "user", "content": "hi"}]}


def test_no_schema_means_no_response_format():
    handler, seen = _capture(OK)
    _llm(handler).generate(REQ)
    assert "response_format" not in seen["body"]


def test_schema_is_sent_as_strict_json_schema():
    handler, seen = _capture(OK)
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    _llm(handler).generate(LLMRequest(messages=REQ.messages, json_schema=schema))
    assert seen["body"]["response_format"] == {"type": "json_schema",
                                               "json_schema": {"name": "answer", "schema": schema, "strict": True}}


def test_request_model_overrides_the_adapter_default():
    handler, seen = _capture(OK)
    _llm(handler).generate(LLMRequest(messages=REQ.messages, model="per-request"))
    assert seen["body"]["model"] == "per-request"


def test_own_client_sets_key_and_extra_headers():
    llm = OpenAICompatibleLLM("p", "http://host/v1/", "m", api_key="sk-abc", extra_headers={"x-team": "arch"})
    assert llm._client.headers["authorization"] == "Bearer sk-abc" and llm._client.headers["x-team"] == "arch"
    assert str(llm._client.base_url).rstrip("/") == "http://host/v1"


# --- response mapping ------------------------------------------------------------

def test_response_maps_text_model_provider_and_usage():
    r = _llm(_capture(OK)[0]).generate(REQ)
    assert (r.text, r.model, r.provider) == ("hello", "served-model", "prov")
    assert r.usage == Usage(7, 3, 0) and r.latency_ms >= 0


@pytest.mark.parametrize("usage", [None, {}, {"prompt_tokens": 4}, {"prompt_tokens_details": None}])
def test_missing_usage_fields_become_zero(usage):
    body = {"choices": [{"message": {"content": "x"}}]} | ({"usage": usage} if usage is not None else {})
    r = _llm(_capture(body)[0]).generate(REQ)
    assert r.usage.output_tokens == 0 and r.usage.cached_input_tokens == 0
    assert r.usage.input_tokens == (usage or {}).get("prompt_tokens", 0)


def test_null_content_becomes_empty_text_and_missing_model_falls_back():
    r = _llm(_capture({"choices": [{"message": {"content": None}}]})[0]).generate(REQ)
    assert r.text == "" and r.model == "m1"


# --- error mapping ---------------------------------------------------------------

@pytest.mark.parametrize(("status", "retryable"), [
    (400, False), (401, False), (403, False), (404, False), (422, False),
    (408, True), (409, True), (500, True), (502, True), (503, True), (504, True)])
def test_http_errors_map_to_provider_error_with_the_right_retry_flag(status, retryable):
    with pytest.raises(ProviderError) as e:
        _llm(lambda req: httpx.Response(status, text="boom")).generate(REQ)
    assert e.value.retryable is retryable and e.value.provider == "prov"
    assert f"HTTP {status}" in str(e.value) and not isinstance(e.value, RateLimited)


@pytest.mark.parametrize(("header", "expected"), [("2", 2.0), ("1.5", 1.5), ("0", 0.0), ("soon", None),
                                                  ("Wed, 21 Oct 2026 07:28:00 GMT", None), (None, None)])
def test_429_becomes_rate_limited_and_parses_retry_after(header, expected):
    headers = {"retry-after": header} if header is not None else {}
    with pytest.raises(RateLimited) as e:
        _llm(lambda req: httpx.Response(429, headers=headers)).generate(REQ)
    assert e.value.retry_after_s == expected and e.value.retryable is True


@pytest.mark.parametrize(("exc", "prefix"), [(httpx.ReadTimeout, "timeout"), (httpx.ConnectTimeout, "timeout"),
                                             (httpx.ConnectError, "transport"), (httpx.ReadError, "transport")])
def test_network_failures_are_retryable_provider_errors(exc, prefix):
    def handler(req: httpx.Request) -> httpx.Response:
        raise exc("network down", request=req)
    with pytest.raises(ProviderError) as e:
        _llm(handler).generate(REQ)
    assert e.value.retryable is True and str(e.value).startswith(prefix) and e.value.__cause__ is not None


def test_error_message_keeps_only_the_start_of_a_long_body():
    with pytest.raises(ProviderError) as e:
        _llm(lambda req: httpx.Response(500, text="x" * 5_000)).generate(REQ)
    assert len(str(e.value)) < 250
