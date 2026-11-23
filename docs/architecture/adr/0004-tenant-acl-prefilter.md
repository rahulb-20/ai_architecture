# ADR-0004: Tenant and ACL filtering happens inside retrieval (pre-filter), never after

- Status: Accepted
- Date: 2026-11-23

## Context
Post-filtering top-k results leaks information (via timing, counts and cache entries) and silently reduces recall for restricted users.

## Decision
A `Principal` (user, tenant, groups, region) is created at the API edge and passed to every service call. `VectorStore.search` must exclude other tenants and unreadable ACLs **before** ranking. The eval suite includes leak cases and the gate requires `acl_leaks == 0`.

## Consequences
- Plus: authorisation is part of the data path and is regression-tested in CI.
- Minus: each vector DB adapter must implement filtering natively (namespaces/partitions per tenant plus metadata filters). Caches must be scoped the same way (ADR-0005).
