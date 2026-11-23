# Day 8 — Vector DB Selection

> Part of the [GenAI Architect study path](../../README.md) · Production Retrieval · [Original interactive pack](https://claude.ai/artifact/PXEw4FH3hcEbTp2WHku5TU) · [Offline HTML](../../archive/html/day-08.html)


Week 2 · Production retrieval · Day 8 of 30

# Vector DB selection: filtering, tenancy and the real cost of "just add a where clause"

Pinecone, Weaviate, Chroma, Milvus, Azure AI Search, Elastic and pgvector all do ANN. They differ in how they filter, how they isolate tenants, and who runs them at 2 a.m. Today is about choosing on those axes and proving the choice with numbers.

chromadb 1.5.9lancedb 0.40.0pgvector 0.8.7pinecone SDK 10.0weaviate-client 4.23~75 min hands-on

## Why this matters for the JD

The JD names seven stores by brand under **"Vector DBs & retrieval … metadata filtering"**. An interviewer will not ask what HNSW is again; they will ask which store you would pick for *their* constraints and how you keep tenant A's documents out of tenant B's answers. That ties straight into the good-to-haves on **RBAC/IAM, compliance, NFRs, decision logs and cost estimates**.

## Concept brief

### Select on five axes, not on benchmark charts

1. **Filtered-search behaviour.** Real enterprise queries always carry a filter: tenant, ACL group, region, doc type, effective date. How the engine combines filter and ANN decides recall, more than raw QPS does.
2. **Tenancy model.** Silo, bridge or pool (diagram below). This sets your isolation story, your noisy-neighbour risk and your cost floor.
3. **Hybrid and rerank story.** Native BM25/sparse and fusion (Weaviate, Elastic, Azure AI Search, Milvus, Pinecone) vs bolt-on (pgvector + Postgres FTS, Chroma). Day 9 goes deep here.
4. **Operational ownership.** Managed serverless (Pinecone, Azure AI Search), managed-or-self-host (Weaviate, Milvus/Zilliz, Elastic), or "it's already our database" (pgvector on RDS/Aurora/Azure Flexible Server/AlloyDB). Who patches, who scales, who gets paged.
5. **Enterprise fit.** Private endpoints, CMK encryption, data residency, audit logs, RBAC in the control plane, and whether procurement already has a contract.

### Pre-filter, post-filter and in-filter

**Post-filter** runs ANN for top-k, then drops rows that fail the filter. With a selective filter (a small tenant, 0.5% of rows) you get back 0–2 results instead of 10. That is a silent recall failure, and it hits your smallest customers hardest.

**Pre-filter** computes the allowed set first, then searches only those rows. It is correct, but it is brute force when the set is small and it can break HNSW graph connectivity when the set is medium-sized.

**In-filter / filtered traversal** checks the filter during graph traversal and widens the search until k matches are found. Weaviate's ACORN-style filtering, Qdrant's payload-aware HNSW and pgvector 0.8's `hnsw.iterative_scan` all work this way. pgvector before 0.8 was the classic overfiltering trap: `ORDER BY embedding <=> $1 LIMIT 10` with a `WHERE` clause could return fewer than 10 rows.

Good engines also pick a strategy based on selectivity: brute force for tiny sets, filtered ANN for large ones. Today's exercise makes you watch that happen.

### Three tenancy models

![Diagram 1](diagrams/fig-1.svg)

In practice, mature platforms run a **tiered hybrid**: silo for the few regulated or very large tenants, bridge or pool for the long tail. In AWS SaaS terms this is the same silo/bridge/pool vocabulary you would use for any multi-tenant data tier, and that is the right register for a design review.

### Map to the IBM stack

watsonx.data ships Milvus as its integrated vector store, and watsonx.ai RAG patterns commonly sit on Elasticsearch or Milvus. When an IBM client asks "Milvus or Elastic?", the honest split is: Elastic if they already run it for search and logs and want BM25 + vectors + ACL filtering in one place; Milvus if vector volume is large (hundreds of millions+) and they want GPU indexes, partition-key tenancy and a dedicated vector tier.

## Trade-offs & decision table

| Store | Pick when | Tenancy primitive | Filtering notes | Failure modes | Cost / latency shape |
| --- | --- | --- | --- | --- | --- |
| Pinecone (serverless) | No platform team; spiky or unknown load; want SLAs fast | Namespaces (bridge), recommended for tenant isolation | Metadata filters on any query; namespace scoping is cheaper than filters | Vendor lock-in; data residency limited to offered regions; cold reads after idle | Pay per read/write units + storage; cheap at low QPS, revisit at sustained high QPS |
| Weaviate | Many tenants with uneven activity; want native hybrid | First-class multi-tenancy: shard per tenant, tenant states (active / inactive / offloaded) | Filtered HNSW with ACORN strategy; BM25 + vector fusion built in | Memory-hungry HNSW; tenant count planning per node | Self-host or Weaviate Cloud; offloading idle tenants cuts RAM cost a lot |
| Chroma | Prototypes, dev loops, embedded single-node apps; Chroma Cloud for small prod | Collections; tenant/database namespaces in the client | Simple `where` metadata and document filters; brute-force fallback on small filtered sets | Not a fit for strict HA or very large self-hosted corpora | Free locally; latency fine to low millions of vectors on one box |
| Milvus / Zilliz | 100M–billions of vectors; GPU indexes; IBM watsonx.data clients | Database / collection / partition / partition key | Scalar indexes + filtered search; partition key prunes before ANN | Distributed mode is many moving parts (etcd, object storage, message queue) | Best $/vector at very large scale; high ops cost if self-hosted |
| Azure AI Search | Azure-first enterprise; need private endpoints, Entra ID RBAC, semantic ranker, document-level security | Index per tenant (silo) or filter field (pool) for security trimming | Pre-filter by default on vector queries; filterable fields must be declared up front | Index schema changes need reindex; tier limits on index count and storage | Fixed cost per search unit (replicas × partitions); predictable, not elastic |
| Elasticsearch / OpenSearch | Already running Elastic; hybrid search, ACL filters and analytics in one engine | Index per tenant, or a routing / filter field | Mature filter DSL; kNN with filter applied during HNSW search; quantized vectors to save RAM | JVM heap and off-heap vector memory tuning; shard sizing mistakes | Reuse existing cluster; RAM-heavy at high dimensions |
| pgvector | Under ~10–50M vectors; data already in Postgres; need transactions, joins, RLS | Row-level security + tenant column, or partitions per tenant | 0.8+ iterative index scans fix overfiltering; HNSW or IVFFlat; halfvec/binary to save space | Index build memory (`maintenance_work_mem`); vacuum and bloat; planner choosing a sequential scan | Cheapest if Postgres already exists; scale-up limits before scale-out |

**Rule of thumb for reviews:** start from the database the organization already operates (Postgres, Elastic, Azure AI Search). Move to a dedicated vector DB only when a measured NFR fails: p95 latency, recall under filters, vector count, or tenant count. "We might need scale" is not a measured NFR.

## Hands-on: multi-tenant filtered search, measured

**Goal (75 min):** build the same 40k-vector, 40-tenant corpus three ways and measure recall@10 against exact search, plus p50/p95 latency, separately for the biggest and the smallest tenant. Tenant sizes are Zipf-skewed, like real business units. You will see the two failure modes from the concept brief appear in numbers.

No GPU, Docker or model downloads are needed. Vectors are synthetic and clustered, 384-dimensional (the size of MiniLM / bge-small embeddings), so the run is deterministic and fast. Both packages ship native Windows wheels.

### Setup (PowerShell)

**PowerShell**

```powershell
mkdir day08-vdb; cd day08-vdb
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install "chromadb==1.5.9" "lancedb==0.40.0" numpy pyarrow pandas
# save the script below as vdb_tenancy_bench.py, then:
python vdb_tenancy_bench.py
```

If activation is blocked, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once. LanceDB may print Rust deprecation warnings to stderr. They are harmless.

### Starter code

**vdb_tenancy_bench.py** · `lab/vdb_tenancy_bench.py`

```python
"""Day 8 - Multi-tenant filtered vector search: Chroma vs LanceDB.

Compares three tenancy layouts on the same synthetic corpus:
  A) Chroma, one shared collection + metadata filter (where={"tenant": ...})
  B) Chroma, one collection per tenant (physical isolation)
  C) LanceDB, one table + ANN index + scalar index on tenant + prefilter
Reports recall@10 against exact (brute-force) filtered search, p50/p95 latency,
and the on-disk footprint. Tenant sizes are Zipf-skewed, like real enterprises
(a few huge business units, a long tail of small teams).
"""
import os, shutil, time, statistics
import numpy as np
import pyarrow as pa
import chromadb
import lancedb
from lancedb.index import IvfHnswSq, Bitmap

N, DIM, TENANTS, K, QUERIES = 40_000, 384, 40, 10, 200
ROOT = os.path.abspath("./vdb_bench_data")
rng = np.random.default_rng(42)

def norm(x):
    return (x / np.linalg.norm(x, axis=1, keepdims=True)).astype("float32")

# --- synthetic corpus with clustered vectors (more realistic than pure noise) ---
centers = norm(rng.normal(size=(64, DIM)))
assign = rng.integers(0, 64, N)
vecs = norm(centers[assign] + 0.25 * rng.normal(size=(N, DIM)))
w = 1 / np.arange(1, TENANTS + 1) ** 1.1          # Zipf skew
tenant = rng.choice(TENANTS, size=N, p=w / w.sum())
tenant_ids = np.array([f"t{t:02d}" for t in tenant])
doc_type = rng.choice(["policy", "faq", "contract", "ticket"], N)
ids = [f"d{i}" for i in range(N)]
counts = {t: int((tenant_ids == t).sum()) for t in np.unique(tenant_ids)}
big, small = max(counts, key=counts.get), min(counts, key=counts.get)
print(f"corpus={N} dim={DIM} tenants={len(counts)} | biggest {big}={counts[big]} smallest {small}={counts[small]}")

# queries: 50% from the biggest tenant, 50% from the smallest (stress both ends)
q_tenants = [big] * (QUERIES // 2) + [small] * (QUERIES // 2)
qvecs = norm(centers[rng.integers(0, 64, QUERIES)] + 0.25 * rng.normal(size=(QUERIES, DIM)))

def exact(qv, t):
    idx = np.where(tenant_ids == t)[0]
    sims = vecs[idx] @ qv
    return {ids[i] for i in idx[np.argsort(-sims)[:K]]}

truth = [exact(q, t) for q, t in zip(qvecs, q_tenants)]

def du(path):
    return sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(path) for f in fs) / 1e6

def run(name, fn):
    lat, rec = [], []
    for q, t, gt in zip(qvecs, q_tenants, truth):
        s = time.perf_counter(); got = fn(q, t); lat.append((time.perf_counter() - s) * 1000)
        rec.append(len(set(got) & gt) / K)
    big_rec = statistics.mean(rec[: QUERIES // 2]); small_rec = statistics.mean(rec[QUERIES // 2 :])
    p = np.percentile(lat, [50, 95])
    print(f"{name:<34} recall@10 big={big_rec:.3f} small={small_rec:.3f}  p50={p[0]:.2f}ms p95={p[1]:.2f}ms")

shutil.rmtree(ROOT, ignore_errors=True)

# ---------- A) Chroma shared collection + metadata filter ----------
# ef_search is THE recall/latency knob for HNSW. Set it at creation time.
ch = chromadb.PersistentClient(path=os.path.join(ROOT, "chroma_shared"))
bs = ch.get_max_batch_size()
for ef in (100, 400):
    col = ch.create_collection(f"docs_ef{ef}", configuration={"hnsw": {"space": "cosine", "ef_search": ef}})
    t0 = time.perf_counter()
    for i in range(0, N, bs):
        col.add(ids=ids[i:i+bs], embeddings=vecs[i:i+bs],
                metadatas=[{"tenant": tenant_ids[j], "doc_type": doc_type[j]} for j in range(i, min(i+bs, N))])
    print(f"[A] chroma shared ef={ef} ingest {time.perf_counter()-t0:.1f}s")
    run(f"A chroma shared + where (ef={ef})",
        lambda q, t, c=col: c.query(query_embeddings=[q], n_results=K, where={"tenant": t})["ids"][0])
print(f"[A] disk (both collections) {du(os.path.join(ROOT,'chroma_shared')):.0f} MB")

# ---------- B) Chroma collection per tenant ----------
chb = chromadb.PersistentClient(path=os.path.join(ROOT, "chroma_per_tenant"))
cols = {}
t0 = time.perf_counter()
for t in counts:
    idx = np.where(tenant_ids == t)[0]
    c = chb.create_collection(f"docs_{t}", configuration={"hnsw": {"space": "cosine", "ef_search": 100}})
    for i in range(0, len(idx), bs):
        part = idx[i:i+bs]
        c.add(ids=[ids[j] for j in part], embeddings=vecs[part])
    cols[t] = c
print(f"[B] chroma per-tenant ingest {time.perf_counter()-t0:.1f}s, disk {du(os.path.join(ROOT,'chroma_per_tenant')):.0f} MB")
run("B chroma collection-per-tenant", lambda q, t: cols[t].query(query_embeddings=[q], n_results=min(K, counts[t]))["ids"][0])

# ---------- C) LanceDB single table + IVF index + scalar index + prefilter ----------
db = lancedb.connect(os.path.join(ROOT, "lance"))
tbl_data = pa.table({
    "id": ids, "tenant": tenant_ids.tolist(), "doc_type": doc_type.tolist(),
    "vector": pa.FixedSizeListArray.from_arrays(pa.array(vecs.ravel()), DIM),
})
t0 = time.perf_counter()
tbl = db.create_table("docs", data=tbl_data, mode="overwrite")
tbl.create_index("vector", config=IvfHnswSq(distance_type="cosine", num_partitions=16))
tbl.create_scalar_index("tenant", index_type="BITMAP")
print(f"[C] lancedb ingest+index {time.perf_counter()-t0:.1f}s, disk {du(os.path.join(ROOT,'lance')):.0f} MB")

def lance_q(q, t, nprobes=8):
    return [r["id"] for r in tbl.search(q).where(f"tenant = '{t}'", prefilter=True)
            .nprobes(nprobes).limit(K).to_list()]

run("C lancedb prefilter nprobes=8", lance_q)
run("C lancedb prefilter nprobes=16", lambda q, t: lance_q(q, t, 16))
```

### Expected output

This is a reference run on a Linux CPU sandbox. Your laptop's absolute latencies will differ, but the pattern should hold.

| Layout | Recall@10, big tenant (10,714 docs) | Recall@10, small tenant (187 docs) | p50 | p95 |
| --- | --- | --- | --- | --- |
| A · Chroma shared + where, ef=100 | 0.528 | 1.000 | 38.3 ms | 53.4 ms |
| A · Chroma shared + where, ef=400 | 0.906 | 1.000 | 48.3 ms | 89.1 ms |
| B · Chroma collection per tenant | 0.537 | 1.000 | 1.2 ms | 1.4 ms |
| C · LanceDB prefilter, nprobes=8 | 0.561 | 0.575 | 5.6 ms | 7.5 ms |
| C · LanceDB prefilter, nprobes=16 | 0.833 | 0.979 | 6.8 ms | 9.5 ms |

### What to conclude (write these in your notes)

- **Defaults are not a recall guarantee.** At ef\_search=100, the big tenant gets about 53% recall. Raising ef to 400 lifts it to about 91% at roughly 1.3× the p50. Synthetic isotropic data is harder than real embeddings, but the lesson stands: you must measure recall on your own data before you sign off an NFR.
- **The small tenant is perfect in Chroma** because a 187-row allowed set gets brute-forced. Selectivity-aware planning protects small tenants.
- **The small tenant breaks in LanceDB at nprobes=8.** Its 187 docs are spread across all 16 IVF partitions, and probing half of them misses about 40% of its true neighbours. This is the partition-pruning version of overfiltering.
- **Collection per tenant is about 30× faster at p50** than the shared collection with a filter, at the same recall. Isolation buys latency as well as security, until the collection count becomes an ops problem.

### Stretch goals

1. **pgvector iterative scans** (needs Docker Desktop with the WSL2 backend): `docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=pg pgvector/pgvector:pg17`, then load the same data with `psycopg` + `pgvector` (pip). Build an HNSW index, query with `WHERE tenant = %s ORDER BY embedding <=> %s LIMIT 10`, and compare result counts and recall with `SET hnsw.iterative_scan = off` vs `relaxed_order`.
2. **Tiered tenancy router:** put tenants above 5% of the corpus into dedicated collections and the long tail into one shared collection. Wrap both behind a `search(tenant, q)` function and add a test that asserts no returned ID belongs to another tenant (your leak test for CI). Report recall and p95 for the blended workload.

## GitHub repos to study

**[pgvector/pgvector](https://github.com/pgvector/pgvector)**  
Read the README sections on "Filtering" and "Iterative Index Scans", then CHANGELOG.md. 0.8.7 shipped on 2026-10-01 with an IVFFlat build fix.

**[lancedb/lancedb](https://github.com/lancedb/lancedb)**  
Look at the Python `index.py` configs (IvfPq, IvfHnswSq, Bitmap) and how prefilter vs postfilter is exposed in the query builder. Active: 0.40.0 released 2026-10-07.

**[chroma-core/chroma](https://github.com/chroma-core/chroma)**  
The Rust core (`rust/`) shows how metadata filtering and brute-force fallback are planned; the Python client shows tenant/database scoping.

**[zilliztech/VectorDBBench](https://github.com/zilliztech/VectorDBBench)**  
Its filtered-search and streaming test cases are a template for your own selection benchmark. Treat vendor-run results with care. v2.0.0 released 2026-09-10.

## Read / watch

- [Pinecone: Implement multitenancy using namespaces](https://docs.pinecone.io/guides/index-data/implement-multitenancy). A short, opinionated guide on the bridge model; note what they say about per-tenant deletes and costs.
- [Weaviate: Multi-tenancy](https://docs.weaviate.io/weaviate/manage-collections/multi-tenancy). Tenant lifecycle and states; the best reference for designing around thousands of mostly-idle tenants.
- [AWS: pgvector 0.8.0 on Aurora PostgreSQL](https://aws.amazon.com/blogs/database/supercharging-vector-search-performance-and-relevance-with-pgvector-0-8-0-on-amazon-aurora-postgresql). Before/after numbers for iterative scans on filtered queries, a good case to cite in reviews.

## Interview questions

**A client wants "Pinecone because it's the best vector DB". They run 30M chunks on Azure, have Postgres everywhere and strict data residency in India. How do you respond?**

Reframe it as an NFR-driven choice. 30M chunks is within pgvector territory on Azure Database for PostgreSQL Flexible Server (HNSW, halfvec to halve memory, iterative scans for filters). Azure AI Search is the other natural candidate: private endpoints, Entra RBAC, Indian regions and built-in hybrid + semantic ranking. Pinecone is viable only if its region and private networking options meet residency and the security team accepts a new vendor. I would shortlist two, run a two-week bake-off on their own data with filtered recall@10, p95 and monthly cost at projected QPS, and record the result in an ADR.

**Your RAG bot's smallest customers complain that answers are "empty" while large customers are fine. What's your first hypothesis?**

Overfiltering. With post-filtering, or partition pruning (IVF nprobes too low), a tenant that is 0.5% of the corpus gets fewer than k results after the filter. Confirm by logging result counts per query per tenant. Fixes: switch to pre-filter or filtered traversal (pgvector iterative scans, Weaviate ACORN), raise ef or nprobes, move small tenants to namespaces or partitions, or route very selective filters to brute force. Add a per-tenant result-count metric to the dashboard so it never regresses silently.

**How do you guarantee tenant isolation in a pooled vector index?**

Never trust the caller to add the filter. Inject the tenant filter server-side from the authenticated identity (a JWT claim mapped to a tenant ID) in a single retrieval service; no other component can query the store directly (network policy plus IAM). Use DB-level enforcement where available: Postgres RLS, Azure AI Search security filters, or namespaces as a hard scope. Add a contract test in CI that seeds two tenants and asserts zero cross-tenant IDs, and audit-log tenant + query + returned doc IDs. For regulated tenants, use silo with separate keys.

**When would you not use a dedicated vector database at all?**

When the corpus is under tens of millions of vectors, the data and ACLs already live in Postgres or Elastic, and you need transactional consistency between documents and their embeddings (no dual-write drift). One fewer system means less IAM, backup, DR and on-call surface. Revisit when a measured NFR fails: p95 under load, filtered recall, index build time, or memory cost.

**How do you estimate memory for an HNSW index of 50M × 1024-dim float32 vectors, and how do you cut it?**

Raw vectors: 50M × 1024 × 4 B ≈ 205 GB. The HNSW graph adds roughly M × 2 × 4–8 B per vector for links; at M=16 that is about 6–13 GB. Plus replicas. Ways to cut it: Matryoshka truncation to 512 or 256 dims (2–4×), float16/halfvec (2×), int8 scalar quantization (4×), binary quantization with rescoring (32× on the first pass), PQ or DiskANN-style on-disk indexes, and offloading idle tenants. Each one trades recall, so re-measure filtered recall on the golden set after every step.

## Architect's lens · ADR-014

Title

Vector store and tenancy model for the multi-BU knowledge assistant

Context

12 business units (2 regulated), about 25M chunks growing 40% a year, Azure landing zone, Postgres already operated by the platform team, India data residency, p95 retrieval ≤ 150 ms, filtered recall@10 ≥ 0.95 on the golden set, per-BU deletion within 30 days on request.

Options

(1) Pinecone serverless with namespace per BU. (2) Azure AI Search with an index per regulated BU and a security-filter field for the rest. (3) pgvector on Azure PostgreSQL Flexible Server with RLS + a tenant column, and a separate database for regulated BUs.

Decision

Option 2. It meets residency and private-endpoint needs natively, provides hybrid + semantic ranking without extra services, and its fixed search-unit pricing fits the finance team's budgeting. Regulated BUs get silo indexes with their own CMK; the rest share a pooled index with server-side injected filters.

Consequences

+ One managed service, Entra RBAC, audit via Azure Monitor. − Schema changes need reindex pipelines; index-count tier limits must be watched; cost is fixed even at low traffic. Revisit if the corpus passes 100M chunks or recall under filters drops below the NFR. Option 3 remains the fallback if cost pressure grows.

## Tomorrow

**Day 9:** Hybrid search (BM25 + dense, reciprocal rank fusion) and reranking with cross-encoders and Cohere / bge rerankers.

**Sources:**
[pgvector CHANGELOG](https://raw.githubusercontent.com/pgvector/pgvector/master/CHANGELOG.md) ·
[pgvector 0.8.0 release](https://www.postgresql.org/about/news/pgvector-080-released-2952/) ·
[Pinecone multitenancy](https://docs.pinecone.io/guides/index-data/implement-multitenancy) ·
[Weaviate multi-tenancy](https://docs.weaviate.io/weaviate/manage-collections/multi-tenancy) ·
[AWS pgvector 0.8.0 blog](https://aws.amazon.com/blogs/database/supercharging-vector-search-performance-and-relevance-with-pgvector-0-8-0-on-amazon-aurora-postgresql) ·
[chromadb on PyPI](https://pypi.org/project/chromadb/) ·
[lancedb on PyPI](https://pypi.org/project/lancedb/) ·
[vectordb-bench on PyPI](https://pypi.org/project/vectordb-bench/) ·
[pinecone on PyPI](https://pypi.org/project/pinecone/) ·
[weaviate-client on PyPI](https://pypi.org/project/weaviate-client/)

## Lab files in this folder

- [`lab/vdb_tenancy_bench.py`](lab/vdb_tenancy_bench.py)
