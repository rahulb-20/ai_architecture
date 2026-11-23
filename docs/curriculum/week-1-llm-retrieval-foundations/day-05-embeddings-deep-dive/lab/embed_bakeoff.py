"""
Day 5 - Embedding bake-off: model choice x Matryoshka dimension x quantization.

Evaluates embedding models on BEIR SciFact (5,183 docs, 300 test queries) and reports
nDCG@10 / Recall@10 / MRR@10, index size and query-encode latency for each
(model, dimension, precision) combination.

Run:  python embed_bakeoff.py                      # default models
      python embed_bakeoff.py --models granite-small qwen3-0.6b --max-docs 2000
"""
from __future__ import annotations

import argparse
import csv
import time
from pathlib import Path

import numpy as np
import torch
from datasets import load_dataset
from sentence_transformers import SentenceTransformer
from sentence_transformers.quantization import quantize_embeddings

# alias -> (HF model id, is_matryoshka_trained, extra kwargs)
MODELS = {
    # IBM Granite R2 English small: 47M params, 384-d, NOT Matryoshka-trained (control group)
    "granite-small": ("ibm-granite/granite-embedding-small-english-r2", False, {}),
    # IBM Granite R2 multilingual: 311M params, 768-d, Matryoshka 768/512/384/256/128
    "granite-311m": ("ibm-granite/granite-embedding-311m-multilingual-r2", True, {}),
    # Qwen3 0.6B: 1024-d, MRL, instruction-aware queries (needs ~1.5 GB VRAM in fp16)
    "qwen3-0.6b": ("Qwen/Qwen3-Embedding-0.6B", True, {"processor_kwargs": {"padding_side": "left"}}),
}
DIMS = [1024, 768, 512, 384, 256, 128, 64]
K = 10
CACHE = Path(".emb_cache")


# ---------------------------------------------------------------- data
def _first_split(ds):
    return ds[next(iter(ds.keys()))]


def load_scifact(max_docs: int | None):
    corpus = _first_split(load_dataset("mteb/scifact", "corpus"))
    queries = _first_split(load_dataset("mteb/scifact", "queries"))
    qrels = load_dataset("mteb/scifact", "default")["test"]

    relevant: dict[str, set[str]] = {}
    for r in qrels:
        if r["score"] > 0:
            relevant.setdefault(str(r["query-id"]), set()).add(str(r["corpus-id"]))

    doc_ids = [str(x) for x in corpus["_id"]]
    doc_texts = [f"{t}\n{x}".strip() for t, x in zip(corpus["title"], corpus["text"])]
    if max_docs:  # keep every relevant doc, then pad with others -> eval stays honest
        must = set().union(*relevant.values())
        keep = [i for i, d in enumerate(doc_ids) if d in must]
        keep += [i for i, d in enumerate(doc_ids) if d not in must][: max(0, max_docs - len(keep))]
        doc_ids = [doc_ids[i] for i in keep]
        doc_texts = [doc_texts[i] for i in keep]

    qmap = {str(i): t for i, t in zip(queries["_id"], queries["text"])}
    q_ids = [q for q in relevant if q in qmap]
    q_texts = [qmap[q] for q in q_ids]
    return doc_ids, doc_texts, q_ids, q_texts, relevant


# ---------------------------------------------------------------- metrics
def evaluate(scores: np.ndarray, doc_ids, q_ids, relevant, k: int = K) -> dict[str, float]:
    top = np.argsort(-scores, axis=1)[:, :k]
    ndcg = rec = mrr = 0.0
    for qi, qid in enumerate(q_ids):
        rel = relevant[qid]
        hits = [1.0 if doc_ids[j] in rel else 0.0 for j in top[qi]]
        dcg = sum(h / np.log2(r + 2) for r, h in enumerate(hits))
        idcg = sum(1.0 / np.log2(r + 2) for r in range(min(len(rel), k)))
        ndcg += dcg / idcg
        rec += sum(hits) / len(rel)
        mrr += next((1.0 / (r + 1) for r, h in enumerate(hits) if h), 0.0)
    n = len(q_ids)
    return {"ndcg@10": ndcg / n, "recall@10": rec / n, "mrr@10": mrr / n}


def l2norm(x: np.ndarray) -> np.ndarray:
    return x / np.clip(np.linalg.norm(x, axis=1, keepdims=True), 1e-12, None)


# ---------------------------------------------------------------- encode (cached)
def encode_all(alias: str, doc_texts, q_texts, batch_size: int, max_seq_len: int):
    model_id, _, kwargs = MODELS[alias]
    CACHE.mkdir(exist_ok=True)
    dpath, qpath = CACHE / f"{alias}_{len(doc_texts)}_docs.npy", CACHE / f"{alias}_q.npy"
    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cuda":  # fp16 halves VRAM; keeps Qwen3-0.6B well inside 6 GB
        kwargs = {**kwargs, "model_kwargs": {"dtype": torch.float16}}
    model = SentenceTransformer(model_id, device=device, **kwargs)
    model.max_seq_length = min(model.max_seq_length or max_seq_len, max_seq_len)

    if dpath.exists():
        D = np.load(dpath)
        dps = float("nan")
    else:
        t0 = time.perf_counter()
        # encode_document / encode_query apply the model's own prompts (e.g. Qwen3 "Instruct: ...")
        D = model.encode_document(doc_texts, batch_size=batch_size, convert_to_numpy=True,
                                  normalize_embeddings=True, show_progress_bar=True)
        dps = len(doc_texts) / (time.perf_counter() - t0)
        np.save(dpath, D)

    model.encode_query(q_texts[:8])  # warm-up
    t0 = time.perf_counter()
    for q in q_texts[:50]:  # single-query latency, the number your SLO cares about
        model.encode_query([q], convert_to_numpy=True)
    q_ms = (time.perf_counter() - t0) / min(50, len(q_texts)) * 1000
    Q = model.encode_query(q_texts, batch_size=batch_size, convert_to_numpy=True, normalize_embeddings=True)
    np.save(qpath, Q)
    del model
    if device == "cuda":
        torch.cuda.empty_cache()
    return D.astype(np.float32), Q.astype(np.float32), dps, q_ms


# ---------------------------------------------------------------- precision variants
def score_variants(D: np.ndarray, Q: np.ndarray, rescore_k: int = 50):
    """Yield (precision, scores, bytes_per_vector)."""
    n, d = D.shape
    yield "fp32", Q @ D.T, d * 4

    # int8 scalar quantization: per-dimension ranges calibrated on the corpus
    Di = quantize_embeddings(D, precision="int8", calibration_embeddings=D).astype(np.int32)
    Qi = quantize_embeddings(Q, precision="int8", calibration_embeddings=D).astype(np.int32)
    yield "int8", (Qi @ Di.T).astype(np.float32), d

    # binary: 1 bit/dim, Hamming search, then rescore top-k with fp32 query x fp32 docs
    Db = quantize_embeddings(D, precision="ubinary")  # packed uint8, shape (n, d/8)
    Qb = quantize_embeddings(Q, precision="ubinary")
    popcount = np.unpackbits(np.arange(256, dtype=np.uint8)[:, None], axis=1).sum(1).astype(np.uint16)
    ham = np.concatenate([popcount[Qb[i:i + 16, None, :] ^ Db[None, :, :]].sum(axis=2)
                          for i in range(0, len(Qb), 16)])  # chunked to keep RAM < 1 GB
    yield "binary", (-ham).astype(np.float32), d // 8

    cand = np.argsort(ham, axis=1)[:, :rescore_k]
    resc = np.full((Q.shape[0], n), -1e9, dtype=np.float32)
    for qi in range(Q.shape[0]):
        resc[qi, cand[qi]] = D[cand[qi]] @ Q[qi]
    yield f"binary+rescore@{rescore_k}", resc, d // 8  # fp32 vectors live on disk, not in RAM


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", default=["granite-small", "granite-311m"], choices=list(MODELS))
    ap.add_argument("--max-docs", type=int, default=None, help="subsample corpus (relevant docs always kept)")
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--max-seq-len", type=int, default=512)
    args = ap.parse_args()

    doc_ids, doc_texts, q_ids, q_texts, relevant = load_scifact(args.max_docs)
    print(f"corpus={len(doc_ids)} docs, queries={len(q_ids)}, device={'cuda' if torch.cuda.is_available() else 'cpu'}")

    rows = []
    for alias in args.models:
        D, Q, dps, q_ms = encode_all(alias, doc_texts, q_texts, args.batch_size, args.max_seq_len)
        full = D.shape[1]
        for dim in [full] + [x for x in DIMS if x < full]:
            Dd, Qd = l2norm(D[:, :dim]), l2norm(Q[:, :dim])  # Matryoshka truncation + renormalise
            for prec, scores, bpv in score_variants(Dd, Qd):
                if prec != "fp32" and dim not in (full, 256):
                    continue  # keep the table readable
                m = evaluate(scores, doc_ids, q_ids, relevant)
                rows.append({"model": alias, "mrl": MODELS[alias][1], "dim": dim, "precision": prec,
                             **{k: round(v, 4) for k, v in m.items()},
                             "index_MB_per_1M_docs": round(bpv * 1e6 / 2**20, 1),
                             "docs_per_s": round(dps, 1), "query_ms": round(q_ms, 1)})
                print(f"{alias:14s} dim={dim:5d} {prec:20s} nDCG@10={m['ndcg@10']:.4f} "
                      f"R@10={m['recall@10']:.4f}  {rows[-1]['index_MB_per_1M_docs']:>8} MB/1M")

    with open("bakeoff_results.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    print("\nWrote bakeoff_results.csv")


if __name__ == "__main__":
    main()
