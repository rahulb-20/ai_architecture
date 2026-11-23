# Production checklist

Use this before taking the boilerplate past a pilot.

## Identity and access
- [ ] Principal built from a validated OIDC token at the API gateway, not from client headers
- [ ] `AIARCH_API_KEYS` replaced by gateway auth; service-to-service via workload identity
- [ ] Vector store adapter enforces tenant and ACL pre-filtering; leak tests pass against it

## Data and residency
- [ ] One stack per residency region (index, cache, logs, conversation store)
- [ ] Prompts and completions in traces redacted or sampled under your data policy
- [ ] Re-ingestion pipeline triggers `invalidate_doc` on every document change

## Resilience
- [ ] At least two LLM routes in different regions or providers, each passing the eval gate on its own
- [ ] Breaker thresholds and timeouts tuned from load tests; `/readyz` wired to the load balancer
- [ ] DEGRADED mode reviewed with the business; DR game-day scheduled

## Quality
- [ ] Golden set grown to 200+ cases from real queries, refreshed monthly
- [ ] LLM-as-judge faithfulness metric added and calibrated against human labels
- [ ] Separate gate thresholds for real-model runs (pre-release) and offline runs (every PR)

## Security
- [ ] OWASP Top 10 for LLM applications reviewed; extra guard checks added (Llama Guard / Presidio / content safety)
- [ ] Agent tools with side effects require approval; tool allowlists per group
- [ ] Secrets in a vault, image scanned, containers run read-only as non-root (as in `compose.yaml`)

## Cost
- [ ] `config/prices.json` updated from the current price sheet
- [ ] Per-tenant budgets and alerts on cost per query; prefix-cache hit ratio monitored
