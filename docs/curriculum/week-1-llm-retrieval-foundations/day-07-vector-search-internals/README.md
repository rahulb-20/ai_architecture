# Day 7 — Vector Search Internals

> Part of the [GenAI Architect study path](../../README.md) · Llm Retrieval Foundations · [Original interactive pack](https://claude.ai/artifact/HvVb3VNyNgvQiMkvUHoDPb) · [Offline HTML](../../archive/html/day-07.html)


Week 1 · LLM & retrieval foundations · Day 7 of 30

# Vector search internals: HNSW, IVF, PQ and the recall–latency–memory triangle

Every vector DB you will be asked to pick on Day 8 is a wrapper around a handful of index families. Today you learn what they actually do, how to tune them, and how to measure them on your own laptop with FAISS.

Fri 16 Oct 2026faiss-cpu 1.15.1~90 min labRuns on CPU · 16 GB RAM

## Why this matters for the JD

The JD lists *"embeddings, indexing, ANN, hybrid search, metadata filtering"* and names FAISS, Milvus, Pinecone, Azure AI Search and Elastic. In an architecture review you will be asked why retrieval p95 is 400 ms, why recall dropped after a filter was added, or why the vector tier needs 180 GB of RAM. Those answers live in the index parameters, not in the vendor brochure. It also feeds the *cost estimates* and *scalability* NFR lines directly.

## Concept brief

Exact k-NN (a "flat" index) compares the query to every vector: O(N·d). At 384 dims and 10M chunks that is ~15 GB scanned per query. Approximate nearest neighbour (ANN) indexes give up a little recall to avoid that scan. They do it in three orthogonal ways, which you can combine:

- **Navigate** a proximity graph (**HNSW**, DiskANN/Vamana): greedy walk toward the query.
- **Partition** the space (**IVF**): k-means into `nlist` cells, search only the `nprobe` nearest cells.
- **Compress** the vectors (**PQ**, SQ8, binary, RaBitQ): store codes instead of float32, compare approximately.

![Three panels: HNSW layered graph search, IVF partitions with nprobe, and product quantization compressing a vector into subvector codes](diagrams/fig-1.svg)

The three levers. Production indexes combine them: `IVF4096,PQ64`, `HNSW32,SQ8`, IVF with an HNSW coarse quantizer, DiskANN with PQ in RAM and full vectors on SSD.

### HNSW in one paragraph

A multi-layer proximity graph. Each vector gets up to `M` neighbours per layer (2·M at layer 0); higher layers are exponentially sparser, like a skip list. Search starts at the top entry point, greedily moves toward the query, drops a layer, repeats, and at layer 0 keeps a candidate heap of size `efSearch`. Recall rises with `efSearch` at query time with no rebuild. Costs: memory ≈ N·(4d + 8M) bytes plus overhead, slow builds, and deletes that leave tombstones until compaction. It is the default in pgvector, Weaviate, Qdrant, OpenSearch/Elastic, Milvus and Azure AI Search.

### IVF in one paragraph

Train k-means on a sample, assign every vector to its nearest of `nlist` centroids, store inverted lists. A query scans only `nprobe` lists. Cheap to build and memory-lean, but it needs a representative training set; if your corpus drifts (new product line, new language) the centroids go stale and recall quietly decays. Re-train is a re-index.

### PQ in one paragraph

Split each d-dim vector into `m` sub-vectors, learn 256 centroids per sub-space, store one byte per sub-vector. 384-d float32 (1,536 B) becomes 48 B. Distances are computed from per-query lookup tables, which is fast and cache-friendly. The catch is a **recall ceiling**: raising `nprobe` stops helping because the codes themselves are lossy. The fix is to over-fetch k×factor candidates and re-rank with full-precision vectors (held in RAM, on SSD, or fetched from the doc store).

**The numbers that matter in a review:** recall@k against exact search (not "accuracy"), p50/p95/p99 latency at target QPS, RAM per million vectors, build/re-index time, and recall *under your real filters*. Benchmarks on uniform random data are meaningless: real embeddings sit on a low-dimensional manifold, and the lab shows what happens when they don't.

### Filtering: where most production incidents come from

**Post-filtering** (ANN top-k, then drop non-matching) returns fewer than k results when the filter is selective. **Pre-filtering** with a bitmap inside the graph walk keeps k but can disconnect the HNSW graph at low selectivity (<1–5%), so engines fall back to brute force on the filtered subset. Modern engines handle this adaptively: Qdrant's filterable HNSW, Weaviate ACORN, pgvector 0.8 *iterative index scans*, Milvus partition keys. For multi-tenancy with hard isolation, prefer a partition, namespace or collection per tenant over a metadata filter.

### watsonx and hyperscaler mapping

watsonx.data ships Milvus (HNSW, IVF\_FLAT, IVF\_PQ, DiskANN, GPU CAGRA), and watsonx.ai RAG patterns also use Elasticsearch dense\_vector (HNSW with int8/BBQ quantization). Azure AI Search exposes HNSW and exhaustive KNN with scalar/binary quantization plus oversampling and rescoring. That is the same "compress then re-rank" idea as PQ+Refine. AWS OpenSearch Serverless/Service offers HNSW and IVF via FAISS or Lucene engines; Vertex AI Vector Search uses ScaNN (partition + anisotropic quantization).

## Trade-offs & decision table

| Index | Pick when | Knobs | RAM / 1M × 768d | Failure modes |
| --- | --- | --- | --- | --- |
| **Flat** (exact) | < ~100k vectors, ground truth for evals, heavily filtered subsets | none | ~3.1 GB | Linear latency growth; looks fine in POC, breaks at scale |
| **HNSW,Flat** | Default for ≤ tens of millions in RAM; low p99 at high recall; frequent inserts | M 16–64, efC 100–400, efSearch 32–256 | ~3.4–3.6 GB | RAM-bound cost; slow builds; delete tombstones; recall collapse with selective pre-filters |
| **HNSW,SQ8** | Same as above at ~4× less vector RAM, small recall hit | + rescoring oversample | ~1 GB | Outlier dimensions clip under 8-bit; needs train sample |
| **IVF,Flat** | Batch-loaded corpora, cheap fast builds, GPU search | nlist, nprobe | ~3.1 GB | Centroid drift after data shift; tail latency when a hot cell is huge |
| **IVF,PQ (+Refine)** | 100M–1B vectors, memory-bound budgets | nlist, nprobe, m, nbits, k\_factor | ~64–100 MB codes (+ re-rank store) | Hard recall ceiling without re-rank; poor on small corpora; training cost |
| **DiskANN / Vamana** | Billion-scale on SSD, cost over raw latency | R, L, PQ bytes in RAM | PQ in RAM, vectors on NVMe | NVMe IOPS become the bottleneck; complex ops |
| **Binary / RaBitQ** | Huge scale first-stage with mandatory rescoring | oversample factor | ~96 MB | Unusable without rescoring; model-dependent quality |

**Cost math to keep in your head:** 10M chunks × 1024-d float32 = 41 GB raw. HNSW adds ~10–15%, so plan ~48 GB RAM per replica, ×2–3 replicas for HA. Halving dimensions (Matryoshka, Day 5) or SQ8 often removes a whole node tier before you touch PQ.

## Hands-on lab: benchmark four index families

**Goal (60–90 min):** build Flat, HNSW, IVF-Flat and IVF-PQ over 200k × 384-d vectors, sweep the query-time knobs, and produce your own recall / latency / memory table. CPU only; peak RAM about 1.5 GB. Your RTX 3050 is not needed (and `faiss-gpu` wheels are Linux-only, so GPU FAISS would need WSL2).

### Setup (PowerShell)

**PowerShell**

```powershell
mkdir day7-ann; cd day7-ann
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1     # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
python -m pip install --upgrade pip
python -m pip install "faiss-cpu==1.15.1" numpy
python -c "import faiss; print(faiss.__version__, faiss.omp_get_max_threads())"
```

### Starter code — ann\_bench.py

**python · python ann_bench.py** · saved as `lab/ann_bench.py`

```python
"""Day 7 - ANN index benchmark: Flat vs HNSW vs IVF-Flat vs IVF-PQ (FAISS, CPU)."""
import time, numpy as np, faiss

rng = np.random.default_rng(42)
N, D, NQ, K = 200_000, 384, 1_000, 10           # 384-d = MiniLM/bge-small size

CENTERS = rng.standard_normal((64, 32)).astype("float32")
PROJ = rng.standard_normal((32, D)).astype("float32") / np.sqrt(32)

def make_data(n):
    # Real embeddings have low intrinsic dimension: clustered points on a ~32-d
    # manifold projected into 384-d, plus a little noise. (Uniform noise would make
    # every ANN index look broken - that's the curse of dimensionality.)
    z = CENTERS[rng.integers(0, 64, n)] + 0.6 * rng.standard_normal((n, 32)).astype("float32")
    x = z @ PROJ + 0.05 * rng.standard_normal((n, D)).astype("float32")
    x = np.ascontiguousarray(x, dtype="float32")
    faiss.normalize_L2(x)                         # cosine == inner product on unit vectors
    return x

xb, xq = make_data(N), make_data(NQ)

t = time.perf_counter()
gt_index = faiss.IndexFlatIP(D); gt_index.add(xb)
_, gt = gt_index.search(xq, K)
flat_ms = (time.perf_counter() - t) * 1000 / NQ

def recall(I):  # recall@K vs exact search
    return np.mean([len(set(I[i]) & set(gt[i])) / K for i in range(NQ)])

def bench(name, index, setter=None, values=(None,)):
    rows = []
    for v in values:
        if setter: setter(index, v)
        t = time.perf_counter(); _, I = index.search(xq, K)
        ms = (time.perf_counter() - t) * 1000 / NQ
        rows.append((name, v, recall(I), ms))
    return rows

def mem_mb(index):
    return len(faiss.serialize_index(index)) / 1e6

results, build, mem = [], {}, {}
results.append(("Flat (exact)", None, 1.0, flat_ms)); mem["Flat (exact)"] = mem_mb(gt_index)

# HNSW: graph index, no training, high memory, great recall/latency
t = time.perf_counter()
hnsw = faiss.IndexHNSWFlat(D, 32, faiss.METRIC_INNER_PRODUCT)
hnsw.hnsw.efConstruction = 80
hnsw.add(xb); build["HNSW M=32"] = time.perf_counter() - t; mem["HNSW M=32"] = mem_mb(hnsw)
results += bench("HNSW M=32", hnsw, lambda i, v: setattr(i.hnsw, "efSearch", v), (16, 32, 64, 128))

# IVF-Flat: k-means partitions, needs training, nprobe trades recall for speed
nlist = 1024  # ~ sqrt(N)*2
t = time.perf_counter()
q = faiss.IndexFlatIP(D)
ivf = faiss.IndexIVFFlat(q, D, nlist, faiss.METRIC_INNER_PRODUCT)
ivf.train(xb[:50_000]); ivf.add(xb)
build["IVF1024,Flat"] = time.perf_counter() - t; mem["IVF1024,Flat"] = mem_mb(ivf)
results += bench("IVF1024,Flat", ivf, lambda i, v: setattr(i, "nprobe", v), (1, 8, 32, 64))

# IVF-PQ: compress 384 floats (1536 B) -> 48 bytes; 32x smaller, lossy
t = time.perf_counter()
q2 = faiss.IndexFlatIP(D)
ivfpq = faiss.IndexIVFPQ(q2, D, nlist, 48, 8, faiss.METRIC_INNER_PRODUCT)
ivfpq.train(xb[:50_000]); ivfpq.add(xb)
build["IVF1024,PQ48"] = time.perf_counter() - t; mem["IVF1024,PQ48"] = mem_mb(ivfpq)
results += bench("IVF1024,PQ48", ivfpq, lambda i, v: setattr(i, "nprobe", v), (8, 32, 64))

print(f"\nN={N:,}  D={D}  queries={NQ}  k={K}  threads={faiss.omp_get_max_threads()}")
print(f"{'index':<14}{'param':>7}{'recall@10':>11}{'ms/query':>10}{'mem MB':>9}{'build s':>9}")
for name, v, r, ms in results:
    print(f"{name:<14}{str(v or '-'):>7}{r:>11.3f}{ms:>10.3f}{mem[name]:>9.0f}{build.get(name, 0):>9.1f}")
```

### Expected output

Measured on a 2-thread cloud VM; your 8+ thread laptop will be faster in absolute ms but the shape should match.

**console**

```text
N=200,000  D=384  queries=1000  k=10  threads=2
index           param  recall@10  ms/query   mem MB  build s
Flat (exact)        -      1.000     2.637      307      0.0
HNSW M=32          16      0.889     0.052      362     24.5
HNSW M=32          32      0.968     0.073      362     24.5
HNSW M=32          64      0.993     0.124      362     24.5
HNSW M=32         128      0.996     0.188      362     24.5
IVF1024,Flat        1      0.287     0.030      310      9.0
IVF1024,Flat        8      0.897     0.110      310      9.0
IVF1024,Flat       32      1.000     0.380      310      9.0
IVF1024,Flat       64      1.000     0.706      310      9.0
IVF1024,PQ48        8      0.542     0.037       13     17.2
IVF1024,PQ48       32      0.560     0.079       13     17.2
IVF1024,PQ48       64      0.560     0.135       13     17.2
```

### What to notice and write down

- HNSW reaches 0.99 recall at efSearch 64 while being ~20× faster than flat. Memory is *higher* than flat because of the graph.
- IVF at nprobe 1 is fast and useless (0.29). Recall is a smooth function of nprobe, so it is easy to tune to an SLO.
- IVF-PQ plateaus at ~0.56 no matter how high nprobe goes. That is the quantization ceiling. 24× less memory is the prize.
- Change `0.6` noise to `3.0` in `make_data` and re-run: every ANN index degrades sharply. That's why you never trust benchmarks on random vectors.

### Stretch goals

1. **Break the PQ ceiling and add filters.** Run the script below: IVF-PQ + exact re-rank gets back to ~0.98 recall; HNSW over SQ8 keeps ~0.97 at ~⅓ the memory; an `IDSelectorBatch` pre-filters to one tenant.
   **python · stretch_refine.py (same folder; re-runs the benchmark once on import)** · saved as `lab/stretch_refine.py`

```python
# Stretch: fix PQ's recall ceiling with re-ranking, try SQ8, and filtered search
import numpy as np, faiss, time
from ann_bench import xb, xq, gt, recall, mem_mb, D, K   # reuses data (re-runs the benchmark once)

# 1) IVF-PQ + exact re-rank of top k*factor candidates (RAM for full vectors returns!)
idx = faiss.index_factory(D, "IVF1024,PQ48,Refine(Flat)", faiss.METRIC_INNER_PRODUCT)
idx.train(xb[:50_000]); idx.add(xb)
params = faiss.IndexRefineSearchParameters(k_factor=8, base_index_params=faiss.SearchParametersIVF(nprobe=32))
t = time.perf_counter(); _, I = idx.search(xq, K, params=params)
print(f"IVF-PQ+Refine  recall={recall(I):.3f}  {(time.perf_counter()-t)*1000/len(xq):.3f} ms/q")

# 2) HNSW over 8-bit scalar-quantized vectors: ~4x less RAM than HNSW,Flat
sq = faiss.index_factory(D, "HNSW32,SQ8", faiss.METRIC_INNER_PRODUCT)
sq.train(xb[:50_000]); sq.add(xb); sq.hnsw.efSearch = 64
_, I = sq.search(xq, K)
print(f"HNSW32,SQ8     recall={recall(I):.3f}  mem={mem_mb(sq):.0f} MB")

# 3) Metadata pre-filter: only search docs whose tenant_id == 7
tenant = np.random.default_rng(0).integers(0, 20, len(xb))
allowed = np.where(tenant == 7)[0].astype("int64")
sel = faiss.IDSelectorBatch(allowed)
_, I = sq.search(xq[:5], K, params=faiss.SearchParametersHNSW(sel=sel, efSearch=64))
print("filtered hits all tenant 7:", bool(np.all(tenant[I[I >= 0]] == 7)))
```
   Expected: `IVF-PQ+Refine recall≈0.98`, `HNSW32,SQ8 recall≈0.97 mem≈131 MB`, `filtered hits all tenant 7: True`. Then set the filter to 0.5% selectivity and watch latency and recall.
2. **Real embeddings.** Replace `make_data` with embeddings of ~20k real chunks (e.g. your compliance-bot corpus) using `ollama pull nomic-embed-text` or `sentence-transformers` with `BAAI/bge-small-en-v1.5`. Plot recall vs ms/query for HNSW and IVF and pick the operating point that meets "recall@10 ≥ 0.95, p95 < 20 ms".

## GitHub repos to study

[facebookresearch/faiss](https://github.com/facebookresearch/faiss)

Read `faiss/impl/HNSW.cpp` (search\_from\_candidates) and the wiki page "Guidelines to choose an index" for the index\_factory string grammar. v1.15 is current.

[unum-cloud/usearch](https://github.com/unum-cloud/usearch)

A compact single-header HNSW with i8/f16/binary quantization and Windows wheels; compare its API and memory against FAISS HNSW.

[erikbern/ann-benchmarks](https://github.com/erikbern/ann-benchmarks)

The reference methodology: look at how recall vs QPS Pareto fronts are produced and which datasets (glove, sift, gist) are used.

[pgvector/pgvector](https://github.com/pgvector/pgvector)

Read the README sections on HNSW vs IVFFlat, `hnsw.ef_search`, and iterative index scans for filtered queries. This is the "boring Postgres" option you'll weigh tomorrow.

## Read / watch

- [The Faiss library](https://arxiv.org/abs/2401.08281) (Douze et al., Meta) — the best single survey of IVF, PQ, HNSW and their compositions, written by the maintainers. Read sections 3–5.
- [Malkov & Yashunin, HNSW paper](https://arxiv.org/abs/1603.09320) — skim the algorithm and the heuristic neighbour selection; it explains why M and efConstruction behave as they do.
- [Pinecone · Faiss: The Missing Manual](https://www.pinecone.io/learn/series/faiss/) — visual chapters on IVF, PQ, HNSW and composite indexes; good for building stakeholder explanations.

## Interview questions

**Retrieval p99 jumped from 30 ms to 400 ms after a product team added a department filter. What happened and what do you do?**

A selective filter on an HNSW index either forces post-filtering (over-fetching huge k) or breaks graph connectivity under pre-filtering, so the engine expands the search or falls back to brute force. Check selectivity per filter value. Fixes: partition or namespace by department if it is a hard boundary; use the engine's adaptive filtered search (ACORN, filterable HNSW, iterative scans); for very selective values route to exact search on the subset; add payload indexes on the filtered field.

**How do you size the vector tier for 20M chunks with 1024-d embeddings and HA?**

Raw: 20M × 1024 × 4 B ≈ 82 GB. HNSW M=32 adds ~20M × 64 links × 4 B ≈ 5 GB plus overhead, so ~95 GB per replica, ×2 for HA, plus headroom for rebuilds (another full copy during re-index). Then show the levers: Matryoshka to 512-d halves it; SQ8 cuts vector bytes 4×; PQ/DiskANN for 10× more. State the recall impact of each, measured on a golden set.

**HNSW vs IVF-PQ: defend a choice for a 300M-vector catalogue search.**

300M × 768-d float32 is ~920 GB, too much for in-RAM HNSW at sane cost. IVF-PQ (e.g. IVF65536 with HNSW coarse quantizer, PQ96) fits ~30 GB of codes; re-rank the top 200 with full vectors from SSD or a KV store to recover recall. Alternative: DiskANN. Accept higher build time and periodic re-training as the cost; budget for a recall regression test on every re-train.

**Why can recall be fine in staging and poor in production with the same index config?**

Data distribution differs: IVF centroids trained on an old or narrow sample; production queries are out-of-distribution (short keyword-like queries vs long chunks); filters in prod but not staging; inserts after build making HNSW less well-connected or tombstone-heavy; different embedding model version for queries vs documents. Monitor online recall by shadow-running exact search on a sampled 1% of queries.

**What does "recall@10 = 0.95" actually guarantee for RAG answer quality?**

Only that 95% of the exact top-10 neighbours in embedding space were returned. It says nothing about whether those neighbours are relevant: that's the embedding model's job, measured by context recall/precision against labelled data (Day 11). ANN recall is an infrastructure SLO; retrieval relevance is a quality SLO. Track both separately, and note that a reranker over top-50 is often tolerant of ANN recall 0.9.

## Architect's lens

ADR-007 · Title
:   ANN index type for the enterprise policy assistant (8M chunks, 768-d, 40 tenants, 50 QPS peak, p95 ≤ 50 ms retrieval)

Context
:   Corpus grows ~5% a month with daily incremental loads and occasional deletes for retention. Tenant isolation is a compliance requirement. Budget favours a single 64 GB node per replica.

Options
:   (a) HNSW,Flat: ~27 GB per replica, best recall/latency, slow rebuild. (b) HNSW + SQ8 with 3× oversample and full-precision rescoring: ~8 GB vectors in RAM, full vectors on disk. (c) IVF-PQ + refine: smallest footprint, needs periodic re-training, recall ceiling risk. (d) Managed (Azure AI Search / watsonx.data Milvus) with the same choice inside.

Decision
:   (b) HNSW over SQ8 with rescoring, M=32, efConstruction=200, efSearch tuned to recall@10 ≥ 0.97 on the golden set; tenant as partition key, never a free-form filter.

Consequences
:   Fits the 64 GB node with room for a parallel rebuild. Recall loss vs (a) measured at <1 pt. Requires a weekly compaction job for deletes and a recall canary (1% shadow exact search). Re-evaluate at 50M chunks, when DiskANN or IVF-PQ becomes cheaper.

**Tomorrow · Day 8:** Vector DB selection — Pinecone vs Weaviate vs Chroma vs Milvus vs Azure AI Search vs Elastic vs pgvector; metadata filtering and multi-tenancy.

## Lab files in this folder

- [`lab/ann_bench.py`](lab/ann_bench.py)
- [`lab/stretch_refine.py`](lab/stretch_refine.py)
