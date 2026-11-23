"""One adapter for every OpenAI-compatible Chat Completions endpoint.

Covers OpenAI, Azure OpenAI (v1 endpoint), Ollama (`http://localhost:11434/v1`),
vLLM, TGI, LM Studio, and any gateway (LiteLLM, Portkey, APIM) that fronts
Anthropic, Gemini or watsonx.ai behind the same schema. Pointing the boilerplate at
a new provider is configuration, not code.
"""
from __future__ import annotations

import time

import httpx

from aiarch.domain.errors import ProviderError, RateLimited
from aiarch.domain.models import Usage
from aiarch.ports import LLMRequest, LLMResponse

_RETRYABLE = {408, 409, 429, 500, 502, 503, 504}


class OpenAICompatibleLLM:
    def __init__(self, name: str, base_url: str, model: str, api_key: str | None = None,
                 timeout_s: float = 30.0, extra_headers: dict[str, str] | None = None,
                 client: httpx.Client | None = None) -> None:
        self.name = name
        self.model = model
        headers = {"Content-Type": "application/json", **(extra_headers or {})}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = client or httpx.Client(base_url=base_url.rstrip("/"), headers=headers,
                                              timeout=httpx.Timeout(timeout_s, connect=5.0))

    def generate(self, request: LLMRequest) -> LLMResponse:
        body: dict = {
            "model": request.model or self.model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_output_tokens,
        }
        if request.json_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "answer", "schema": request.json_schema, "strict": True},
            }
        t0 = time.perf_counter()
        try:
            r = self._client.post("/chat/completions", json=body)
        except httpx.TimeoutException as e:
            raise ProviderError(f"timeout: {e}", provider=self.name, retryable=True) from e
        except httpx.TransportError as e:
            raise ProviderError(f"transport: {e}", provider=self.name, retryable=True) from e
        if r.status_code == 429:
            ra = r.headers.get("retry-after")
            raise RateLimited("rate limited", provider=self.name,
                              retry_after_s=float(ra) if ra and ra.replace(".", "").isdigit() else None)
        if r.status_code >= 400:
            raise ProviderError(f"HTTP {r.status_code}: {r.text[:200]}", provider=self.name,
                                retryable=r.status_code in _RETRYABLE)
        data = r.json()
        u = data.get("usage") or {}
        cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
        return LLMResponse(
            text=data["choices"][0]["message"].get("content") or "",
            model=data.get("model", body["model"]),
            provider=self.name,
            usage=Usage(u.get("prompt_tokens", 0), u.get("completion_tokens", 0), cached or 0),
            latency_ms=(time.perf_counter() - t0) * 1000,
        )
