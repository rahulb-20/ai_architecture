"""Contract test for the OpenAI-compatible adapter using httpx.MockTransport (no network)."""
import httpx
import pytest

from aiarch.adapters.llm.openai_compatible import OpenAICompatibleLLM
from aiarch.domain.errors import ProviderError, RateLimited
from aiarch.ports import LLMRequest, Message

REQ = LLMRequest(messages=[Message("user", "hi")], json_schema={"type": "object", "properties": {}})


def _llm(handler):
    client = httpx.Client(base_url="http://test/v1", transport=httpx.MockTransport(handler))
    return OpenAICompatibleLLM("test", "http://test/v1", "m1", client=client)


def test_success_maps_usage_and_structured_output():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["body"] = req.read().decode()
        return httpx.Response(200, json={"model": "m1", "choices": [{"message": {"content": "{}"}}],
                                         "usage": {"prompt_tokens": 10, "completion_tokens": 2,
                                                   "prompt_tokens_details": {"cached_tokens": 8}}})
    r = _llm(handler).generate(REQ)
    assert r.usage.input_tokens == 10 and r.usage.cached_input_tokens == 8
    assert '"json_schema"' in seen["body"]


def test_429_becomes_rate_limited_with_retry_after():
    with pytest.raises(RateLimited) as e:
        _llm(lambda req: httpx.Response(429, headers={"retry-after": "2"})).generate(REQ)
    assert e.value.retry_after_s == 2.0


@pytest.mark.parametrize("code,retryable", [(503, True), (400, False), (401, False)])
def test_http_errors_classified(code, retryable):
    with pytest.raises(ProviderError) as e:
        _llm(lambda req: httpx.Response(code, text="err")).generate(REQ)
    assert e.value.retryable is retryable
