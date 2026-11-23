# ADR-0002: Every LLM call goes through one model gateway

- Status: Accepted
- Date: 2026-11-23

## Context
Provider outages, 429s, regional quota exhaustion and model deprecations are normal operating conditions. Cost must be attributable per request and per tenant.

## Options
1. Retry logic in each service.
2. An external AI gateway only (LiteLLM proxy, Portkey, Azure APIM, Kong AI).
3. An in-process `ModelGateway` with the same semantics, which can also sit *behind* an external gateway.

## Decision
Option 3. `ModelGateway` owns ordered fallback routes, bounded retries with jittered exponential backoff (honouring Retry-After), a circuit breaker per route, a pre-call token budget, cost accounting from `config/prices.json`, and one OTel GenAI span per attempt. An external gateway is just another `openai_compatible` route.

## Consequences
- Plus: resilience behaviour is testable (see `tests/unit/test_gateway.py`). `/readyz` reports breaker state, and cost is attached to every answer.
- Minus: a fallback model produces different output, so **every fallback route must pass the eval gate on its own** before it is configured. Per-process breakers don't share state across replicas; use the external gateway for fleet-wide quotas.
