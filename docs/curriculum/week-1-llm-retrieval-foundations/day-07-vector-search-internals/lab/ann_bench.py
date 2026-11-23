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
