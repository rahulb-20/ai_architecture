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
