Baseline written for v1: pass_rate=0.75
## Eval gate: `ticket_triage` v2 (qwen2.5:3b)
**PASSED**

| metric | baseline | candidate |
|---|---|---|
| pass_rate | 0.75 | 1.0 |
| schema_rate | 1.0 | 1.0 |
| p95_latency_ms | 1840.2 | 1712.6 |
| cost_per_1k_requests_usd | 0.0702 | 0.0815 |

| case | passed | reason |
|---|---|---|
| g01 | yes | ok |
| g05 | yes | ok |
...
exit code: 0
