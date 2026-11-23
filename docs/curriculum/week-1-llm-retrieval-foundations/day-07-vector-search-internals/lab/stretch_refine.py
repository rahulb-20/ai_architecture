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
