# Day 5 — Embeddings Deep Dive

> Part of the [GenAI Architect study path](../../README.md) · Llm Retrieval Foundations · [Original interactive pack](https://claude.ai/artifact/BwLW5M7qyzJoscCofuuaWW) · [Offline HTML](../../archive/html/day-05.html)


Week 1 · LLM & retrieval foundations · Day 5 of 30

# Embeddings deep dive

How to choose an embedding model with MTEB and your own data, how dimension and precision drive index cost, how Matryoshka truncation and quantization buy that cost back, and when to adapt an embedder to your domain.

Wed 14 Oct 2026
~25 min read · 75–90 min lab
Runs on RTX 3050 6 GB
sentence-transformers 6.1

## Why this matters for the JD

The JD names **embeddings** twice: under LLM concepts and under vector DBs & retrieval (indexing, ANN, metadata filtering). It also asks for **cost controls** and **RAG optimization**. The embedding model is the one RAG component you can't swap cheaply. Changing it means re-embedding the whole corpus, and its dimension and precision set your vector-store bill. Yesterday's retrieval floor only works if the embedder separates relevant from irrelevant chunks well enough to put a threshold between them.

## Concept brief

### What you're actually choosing

A retrieval embedder is a **bi-encoder**: query and document are encoded independently, so documents can be embedded once and indexed. It's trained contrastively (InfoNCE with in-batch and hard negatives) to pull a query towards its relevant passage. Picking a model means picking six things at once:

- **Pooling.** CLS (Granite R2, BGE), mean (MiniLM, E5), or last-token (Qwen3-Embedding and other decoder-based embedders). sentence-transformers reads this from the model config. If you hand-roll inference with `transformers`, getting pooling wrong silently degrades quality.
- **Asymmetry.** Many models expect different inputs for queries and documents. Qwen3 uses `Instruct: {task}\nQuery:{q}` for queries only; E5 uses `query:` / `passage:`; Nomic uses `search_query:` / `search_document:`. Granite R2 needs no prefix. Forgetting the prefix typically costs a few points of nDCG and nothing errors. sentence-transformers 5+ handles this with `encode_query()` / `encode_document()`.
- **Max sequence length.** 512 tokens (watsonx-hosted slate and granite-278m), 8,192 (Granite English R2), 32,768 (Granite multilingual R2, Qwen3). Anything beyond the limit is truncated without warning, so this caps your chunk size (Day 6).
- **Dimension** (384 to 4,096) and whether the model is **Matryoshka-trained**.
- **Normalisation.** Granite R2 returns unnormalised vectors. Normalise them yourself if your index uses inner product.
- **License, languages and hosting.** Apache-2.0 open weights, API-only, or non-commercial (for example, NVIDIA's llama-embed-nemotron-8b sits near the top of MTEB but is non-commercial).

### Reading MTEB like an architect

MTEB (now MMTEB: 500+ tasks, 250+ languages) is a shortlist tool, not a decision. Five rules:

1. **Look at the Retrieval column, not the overall mean.** The mean blends in classification, clustering and STS. A model tuned for retrieval can rank lower overall and still be the best choice for RAG.
2. **Filter on your constraints first**: languages, max tokens, parameter count (it drives latency and GPU cost), license.
3. **Check zero-shot status.** Some models trained on the train splits of MTEB datasets. The leaderboard flags this; treat those scores as optimistic.
4. **Expect 1–2 point gaps between neighbours to vanish on your data.** Rankings reshuffle monthly.
5. **Run a bake-off on a golden set of 100–300 real queries** with nDCG@10 and Recall@k. That's today's lab.

### Similarity metrics: mostly a non-decision, with two traps

For L2-normalised vectors, cosine, dot product and Euclidean give **identical rankings**, because ‖a−b‖² = 2 − 2·a·b. So normalise and use inner product, which is the fastest kernel in FAISS, HNSW libraries and most vector DBs. The traps:

- **A model trained for unnormalised dot product** (rare in modern retrievers) encodes signal in the vector norm. Follow the model card.
- **Index/model metric mismatch**: building `IndexFlatIP` on unnormalised Granite vectors gives you dot-product ranking biased toward long or high-norm passages. It runs fine and is quietly wrong.

### Index cost is dimension × precision × count

Raw vector bytes = N × d × bytes-per-dimension. ANN graph overhead (HNSW links, Day 7) comes on top. For a 10M-chunk corpus:

| Representation | Bytes / vector | 10M vectors | Typical retrieval retained |
| --- | --- | --- | --- |
| 1024-d float32 | 4,096 | 38.1 GiB | 100% (baseline) |
| 1024-d int8 (scalar) | 1,024 | 9.5 GiB | ~99% with rescoring |
| 256-d float32 (MRL) | 1,024 | 9.5 GiB | model-dependent, often 95–99% |
| 256-d int8 (MRL + scalar) | 256 | 2.4 GiB | measure it |
| 1024-d binary | 128 | 1.2 GiB | ~92.5% raw, ~96% with rescoring |

The int8 and binary retention figures come from the Hugging Face / sentence-transformers quantization study (mxbai-embed-large-v1 on MTEB Retrieval). Treat them as priors and confirm them on your own model.

### Matryoshka (MRL): one model, many sizes

Matryoshka Representation Learning adds the contrastive loss at several prefix lengths (for example 768, 512, 256, 128) during training. The first 256 dimensions then form a usable embedding by themselves. At inference you **truncate and re-normalise**. It only works if the model was trained this way: truncating a non-MRL model like granite-embedding-small-english-r2 throws away information unevenly, and quality falls off sharply. Your lab measures that directly.

MRL-trained models you can use today: Qwen3-Embedding (32–1024/2560/4096), granite-embedding-311m-multilingual-r2 (768/512/384/256/128), EmbeddingGemma, Nomic v1.5. On the API side, OpenAI's text-embedding-3 models expose a `dimensions` parameter that does the same thing server-side.

### The funnel pattern: cheap first stage, exact rescoring

![Diagram 1](diagrams/fig-1.svg)

Bars are to scale. The shortlist lives in RAM at 1/16 to 1/32 of the fp32 footprint; full-precision vectors are touched only for a few hundred candidates per query. Most managed vector DBs now expose some version of this (binary or int8 quantization with oversampling and rescoring).

### Domain adaptation ladder (cheapest first)

1. **Use the model's instruction or prefix properly**, and write a task-specific instruction for instruction-aware models (Qwen3 reports 1–5% gains).
2. **Add BM25 alongside dense retrieval** (Day 9). Product codes, ticket IDs and table names are lexical problems, and no embedder fixes them reliably.
3. **Add a reranker** (Day 9). It's usually a larger gain than switching embedders, and you don't need to re-index.
4. **Train a query-side adapter**: a small linear map applied only to query vectors, learned from (query, positive) pairs. The corpus index stays untouched.
5. **Fine-tune the embedder** with `MultipleNegativesRankingLoss` (wrapped in `MatryoshkaLoss` to keep MRL) on synthetic LLM-generated queries plus mined hard negatives. A few thousand pairs is often enough for jargon-heavy corpora. The cost: a full re-index, plus a model you now own and must re-evaluate on every upgrade.

### Provider and platform map

- **IBM watsonx.ai** hosts slate-30m/125m-english-rtrvr-v2, granite-embedding-278m-multilingual, multilingual-e5-large and all-minilm-l6-v2, all with 512-token limits, plus an ms-marco MiniLM reranker. The newer Granite R2 models (8K/32K context, Apache-2.0) are open weights on Hugging Face, so you can self-host them on OpenShift AI / vLLM or check whether your watsonx region has added them.
- **Azure**: text-embedding-3-small/large via Azure OpenAI or AI Foundry. **AWS**: Bedrock embedding models (Titan, Cohere). **GCP**: Vertex AI Gemini embeddings. All charge per input token. That's cheap for queries; the full corpus re-embed is where the bill shows up.
- **Local**: Ollama ships embeddinggemma, qwen3-embedding, granite-embedding, nomic-embed-text, bge-m3 and others. GGUF quantization produces slightly different vectors from the Hugging Face build, which is stretch goal 1.

**The review-ready sentence.** "We shortlisted embedders from the MTEB retrieval tab under our language, context-length and license constraints, then picked one on a 300-query golden set by nDCG@10 and p95 latency. We store a Matryoshka-truncated int8 vector for first-stage search and keep fp32 for rescoring. Every vector is tagged with model ID, version, dimension and prompt, so a model change is a planned re-index behind an eval gate, never a mixed index."

## Trade-offs & decision table

| Option | Pick when | Cost / latency | Failure modes |
| --- | --- | --- | --- |
| Managed API embedder (OpenAI / Azure, Bedrock, Vertex, watsonx) | Fast start, no GPU ops, data allowed to leave the boundary (or a private endpoint exists) | per-token fee + network hop of 30–150 ms per query | Model deprecation forces a re-index on the vendor's timeline; regional availability; rate limits during bulk re-embeds |
| Small open encoder (granite-small-r2 47M, bge-small) | CPU serving, high QPS, English, tight latency | ~1–5 ms/query on GPU, cheap re-index | Lower ceiling on complex queries; usually not MRL, so no truncation |
| Mid open encoder with MRL (granite-311m-r2, EmbeddingGemma 300M, Qwen3-0.6B) | Default for self-hosted enterprise RAG; multilingual; long context | Single small GPU; flexible storage through MRL | Long-context models tempt you into oversized chunks; Qwen3 needs the query instruction |
| LLM-sized embedder (Qwen3-8B, Nemotron-8B) | Top quality matters more than cost; complex or multilingual queries; offline batch | GPU per replica, 4096-d vectors (16 KB each in fp32) | Re-indexing 100M chunks takes days; license traps (Nemotron is non-commercial) |
| MRL truncation (e.g. 1024 → 256) | Storage or RAM-bound; first stage of a funnel | 4× smaller, faster ANN | Big quality drop on non-MRL models; truncating without re-normalising breaks cosine |
| int8 scalar quantization | Almost always worth it at scale | 4× smaller, ~3.7× faster search | Needs calibration data for ranges; distribution drift after corpus changes |
| Binary + rescoring | Very large corpora (tens of millions of chunks and up), RAM-bound | 32× smaller, Hamming search is very fast | Weak on low-dimensional vectors; needs fp32 or int8 copies for rescoring |
| Fine-tuned domain embedder | Jargon-heavy corpus where hybrid search + reranker still miss | Training + full re-index | Overfits to synthetic queries; you own regression testing on every base-model upgrade |

**Cost math to quote:** re-embedding 10M chunks of about 400 tokens is 4B tokens. At OpenAI's list prices of USD 0.02 (text-embedding-3-small) to 0.13 (text-embedding-3-large) per million tokens, that's about USD 80–520, which is small. The real costs of a model change are the re-index pipeline time, the dual-running period, and the re-tuning of every threshold that depended on the old score distribution (yesterday's retrieval floor, for instance).

## Hands-on: embedding bake-off on BEIR SciFact

**Goal (75–90 min):** compare two IBM Granite R2 embedders, one Matryoshka-trained and one not, optionally with Qwen3-Embedding-0.6B. Measure nDCG@10, Recall@10 and MRR@10 across dimensions (full → 64) and precisions (fp32, int8, binary, binary + rescore), along with query latency and index MB per million documents. The dataset is SciFact (5,183 abstracts, 300 test queries with labelled relevant documents), small enough to embed in a few minutes on your 3050.

### Setup (PowerShell, Windows 11)

1. Create the venv. On Windows, `pip install torch` from PyPI gives a **CPU-only** build. For the GPU, install torch first using the command from the [PyTorch selector](https://pytorch.org/get-started/locally/) (Stable · Windows · Pip · newest CUDA). The script still runs on CPU, just more slowly.
2. Install sentence-transformers 6.x. It requires transformers 5 and torch ≥ 2.2. No WSL2 or Docker needed.
3. First run downloads about 1.5 GB of models plus the dataset to `%USERPROFILE%\.cache\huggingface`. A symlink warning on Windows is harmless; enabling Developer Mode silences it.

**PowerShell**

```powershell
mkdir day05; cd day05
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
# 1) GPU torch: paste the command from pytorch.org/get-started/locally (Windows, Pip, CUDA) here
# 2) the rest
python -m pip install "sentence-transformers>=6.1" datasets numpy
python -c "import torch, sentence_transformers as st; print(st.__version__, torch.cuda.is_available())"
```

If `Activate.ps1` is blocked, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

### Starter code: embed\_bakeoff.py

**embed_bakeoff.py** · `lab/embed_bakeoff.py`

```python
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
```

### Run it

**PowerShell**

```powershell
python embed_bakeoff.py                                    # granite-small vs granite-311m, ~10 min on a 3050
python embed_bakeoff.py --models qwen3-0.6b --batch-size 16  # optional third contender
python embed_bakeoff.py --max-docs 1500                    # quick pass on CPU or if VRAM is tight
```

Document embeddings are cached in `.emb_cache\`, so re-runs only re-encode queries. Delete that folder if you change `--max-seq-len`.

### What you should see

The script's logic was smoke-tested on sentence-transformers 6.1.0 with a stand-in model. The numbers below are placeholders: the real SciFact scores come from your run. Look for these patterns:

**expected output (shape, not real numbers)**

```text
corpus=5183 docs, queries=300, device=cuda
granite-small  dim=  384 fp32                 nDCG@10=0.6x  R@10=0.8x     1464.8 MB/1M
granite-small  dim=  384 int8                 nDCG@10=0.6x  (within ~1 pt of fp32)  366.2 MB/1M
granite-small  dim=  384 binary               nDCG@10=noticeably lower       45.8 MB/1M
granite-small  dim=  384 binary+rescore@50    nDCG@10=most of the gap back    45.8 MB/1M
granite-small  dim=  256 fp32                 nDCG@10=drops                   976.6 MB/1M
granite-small  dim=   64 fp32                 nDCG@10=drops sharply  <- not MRL-trained
granite-311m   dim=  768 fp32                 nDCG@10=0.6x-0.7x              2929.7 MB/1M
granite-311m   dim=  256 fp32                 nDCG@10=within a few pts of 768  <- MRL at work
granite-311m   dim=  256 int8                 nDCG@10=close to 256 fp32        244.1 MB/1M
granite-311m   dim=   64 fp32                 nDCG@10=degrades, but gracefully

Wrote bakeoff_results.csv
```

**Things to write down:**

- The **retention curve** (nDCG at d / nDCG at full d) for the MRL model and the non-MRL model. That chart is the evidence behind "we store 256-d".
- The **knee**: the smallest dimension and precision within 2 points of full fp32. Multiply its MB/1M by your real corpus size.
- **Binary works much worse at low dimensions.** 384 bits carries far less information than 1024 bits, which is why binary quantization pairs best with larger models.
- Query latency per model on your GPU. The 311M model's extra quality costs milliseconds per query, which matters at high QPS.

### Stretch goals

1. **Same model, different runtime.** `ollama pull qwen3-embedding:0.6b`, then run the script below. It encodes SciFact through Ollama's GGUF build, with and without Qwen3's query instruction. Compare with the HF numbers. Note the lesson: *vectors from different runtimes or quantizations of the "same" model are not interchangeable*, so the query encoder must match the index encoder exactly.
   **ollama_stretch.py** · `lab/ollama_stretch.py`

```python
"""Stretch 1 - same model, different runtime: Ollama's GGUF Qwen3-Embedding vs the HF build.

Prereq:  ollama pull qwen3-embedding:0.6b      (639 MB)
Run:     python ollama_stretch.py
"""
import numpy as np
import ollama

from embed_bakeoff import evaluate, l2norm, load_scifact

MODEL = "qwen3-embedding:0.6b"
TASK = "Given a scientific claim, retrieve documents that support or refute the claim"

def embed(texts: list[str], batch: int = 32) -> np.ndarray:
    out: list[list[float]] = []
    for i in range(0, len(texts), batch):
        out.extend(ollama.embed(model=MODEL, input=texts[i:i + batch]).embeddings)
    return l2norm(np.asarray(out, dtype=np.float32))

doc_ids, docs, q_ids, qs, rel = load_scifact(max_docs=2000)
D = embed(docs)
print(f"docs embedded: {D.shape}")

# Ollama does NOT add Qwen3's query instruction for you - the HF/sentence-transformers path does.
variants = {
    "no instruction": qs,
    "with instruction": [f"Instruct: {TASK}\nQuery:{q}" for q in qs],
}
for label, qtexts in variants.items():
    Q = embed(qtexts)
    for dim in (1024, 256):
        m = evaluate(l2norm(Q[:, :dim]) @ l2norm(D[:, :dim]).T, doc_ids, q_ids, rel)
        print(f"{label:17s} dim={dim:4d}  nDCG@10={m['ndcg@10']:.4f}  R@10={m['recall@10']:.4f}")
```
2. **Domain-adapt granite-small.** Fine-tune it on SciFact's *train* qrels (809 queries) with `MatryoshkaLoss(model, MultipleNegativesRankingLoss(model), [384, 256, 128, 64])` using `SentenceTransformerTrainer`. Re-run the bake-off on the test split. Did the 64-d score recover? Did full-dim nDCG move? This is the "fine-tune the embedder" rung of the ladder, measured.

## GitHub repos to study

[huggingface/sentence-transformers](https://github.com/huggingface/sentence-transformers)

Read the `quantization` module (how int8 ranges are calibrated) and `MatryoshkaLoss`. Then read the v6.0 release notes: int8 indexes built with v5 multi-process encoding aren't bit-compatible. That's a real-world re-index trigger.

~19.2k ★ · v6.1.0 on PyPI · moved from UKPLab to Hugging Face

[embeddings-benchmark/mteb](https://github.com/embeddings-benchmark/mteb)

See how a retrieval task is defined (corpus, queries, qrels), then copy that pattern to run MTEB-style evaluation on your own golden set rather than trusting the leaderboard.

~3.4k ★ · mteb 2.24.x on PyPI · 4.6k commits

[ibm-granite/granite-embedding-models](https://github.com/ibm-granite/granite-embedding-models)

Read `whats_new_in_r2.md` and the Matryoshka table for the 311M multilingual model. It's the IBM-native option to put in a watsonx design, and the numbers are self-reported, so verify them in today's lab.

Multilingual R2 released 29 Apr 2026 · Apache-2.0

[QwenLM/Qwen3-Embedding](https://github.com/QwenLM/Qwen3-Embedding)

Look at the `evaluation/` folder and the instruction format. It's a clear example of an instruction-aware, last-token-pooled LLM embedder with MRL from 32 dimensions up.

~2.0k ★ · 0.6B / 4B / 8B · Apache-2.0

## Read / watch

- [Hugging Face: Binary and Scalar Embedding Quantization](https://huggingface.co/blog/embedding-quantization). The source for the 4× / 32× and 96% / 99% figures, the rescoring method, and the speed benchmarks. Read the "rescore multiplier" section closely.
- [Kusupati et al., "Matryoshka Representation Learning"](https://arxiv.org/abs/2205.13147). Read §3 and the adaptive retrieval results. Those results are the academic version of the funnel diagram above.
- [MMTEB: Massive Multilingual Text Embedding Benchmark](https://arxiv.org/abs/2502.13595) (ICLR 2025). Explains how the current leaderboard is built, the zero-shot flag, and why the task mix matters when you read averages.

## Interview questions

**How do you choose an embedding model for a multilingual knowledge assistant serving 50k employees?**

Start with constraints: languages, data residency (can text leave the boundary?), max chunk tokens, license, and a latency/QPS budget. Shortlist 3–4 models from the MTEB retrieval tab (multilingual subset) that pass those filters, including one managed API and one self-hostable MRL model. Build a 200–300-query golden set from real search logs or SME questions across languages, and measure nDCG@10, Recall@20 (which matters for the reranker), p95 encode latency and cost per million tokens. Pick the smallest model within about 2 points of the best. Prefer MRL so storage can be tuned later without a model change.

**We need to switch embedding models on a 40M-chunk production index. What's your migration plan?**

Treat it as a data migration. (1) Every vector already carries model ID, version, dimension and prompt metadata. (2) Build a new index in parallel by backfilling from the chunk store, not from source documents, so chunking stays fixed. Dual-write new chunks to both indexes. (3) Shadow-query the new index, compare offline metrics on the golden set, and check online agreement. (4) Recalibrate thresholds such as the retrieval floor and hybrid weights, because score distributions change. (5) Cut over behind a flag, keep the old index for rollback until a soak period passes, then delete it. Never query one model's index with another model's query vectors.

**Cosine, dot product or Euclidean: does it matter?**

For normalised vectors, no. They produce identical rankings, since ‖a−b‖² = 2 − 2a·b. So normalise at write and query time and use inner product, the fastest kernel. It matters when the model is trained for unnormalised dot product, or when vectors aren't normalised but the index assumes cosine. The second case is a silent bug that biases results toward high-norm vectors. Also, raw scores aren't comparable across models, so any threshold has to be recalibrated per model.

**Vector storage is the biggest line item in our RAG bill. What are your levers, in order?**

(1) Remove duplicate and boilerplate chunks; fewer vectors is the cleanest saving. (2) int8 scalar quantization: 4× smaller and about 99% retained with rescoring, so it's almost always worth doing. (3) MRL truncation if the model supports it, for example 1024 → 256 for another 4×. (4) Binary first stage with fp32 or int8 rescoring from disk: 32× smaller in RAM. (5) PQ or IVF-PQ in the ANN layer (Day 7). Then put tiering in place, with hot tenants in RAM and cold ones on disk-based indexes. Validate each step on the golden set and stop at the knee.

**Retrieval misses on internal jargon and product codes. Do you fine-tune the embedder?**

Not first. Codes and IDs are a lexical problem, so add BM25 hybrid search and metadata filters. Then add a reranker. Check that query prefixes or instructions are correct. Only if misses persist on semantic jargon would I fine-tune, using LLM-generated queries per chunk plus mined hard negatives with MNRL wrapped in MatryoshkaLoss, evaluated on a held-out golden set. Account for the ongoing cost: a full re-index now, and re-validation every time the base model is upgraded. A query-side adapter is a cheaper middle step because it doesn't touch the index.

## Architect's lens: ADR-015 · Embedding model & vector representation

Status
:   Accepted · Day 5 study ADR (continues ADR-014)

Context
:   The same policy assistant: about 20k users, roughly 6M chunks in 9 languages, on watsonx.ai with one air-gapped region. Chunks up to about 1,000 tokens. Target p95 retrieval ≤ 150 ms. Vector-store RAM budget per region: 16 GiB.

Options
:   **A.** watsonx-hosted granite-embedding-278m-multilingual (768-d, 512-token limit). **B.** Self-hosted granite-embedding-311m-multilingual-r2 (768-d MRL, 32K context, Apache-2.0). **C.** Managed API embedder (text-embedding-3-large, 3072-d). **D.** Qwen3-Embedding-8B self-hosted.

Decision
:   **B**, storing a 256-d int8 vector for stage 1 (6M × 256 B ≈ 1.4 GiB plus HNSW overhead) and 768-d fp32 on disk for rescoring. A truncates our 1,000-token chunks. C fails the air-gapped requirement and its 3072-d vectors break the RAM budget at fp32. D needs a GPU per replica and makes re-indexing slow. B won the 300-query multilingual golden set within 1.5 nDCG points of D.

Consequences
:   - We operate an embedding service (GPU or CPU pool) with its own SLO.
    - Every vector carries `emb_model`, `emb_version`, `dim` and `precision`.
    - An embedder upgrade is a planned re-index with shadow evaluation and threshold recalibration (retrieval floor from ADR-014).
    - Re-check against watsonx's hosted catalogue each quarter. If R2 becomes hosted in-region, revisit A vs B for ops cost.

## Tomorrow

**Day 6: Chunking strategies.** Fixed, recursive, semantic, structure-aware, parent-child and late chunking, measured on the same SciFact harness you built today.

## — Sources

- <https://pypi.org/project/sentence-transformers/>
- <https://newreleases.io/project/pypi/sentence-transformers/release/6.1.0>
- <https://ecorpit.com/sentence-transformers-6-0-multivector-index-cost-dependency-floor-conflict-2026/>
- <https://sbert.net/examples/training/matryoshka/README.html>
- <https://sbert.net/examples/applications/embedding-quantization/README.html>
- <https://huggingface.co/blog/embedding-quantization>
- <https://huggingface.co/spaces/mteb/leaderboard>
- <https://modal.com/blog/mteb-leaderboard-article>
- <https://arxiv.org/abs/2205.13147>
- <https://arxiv.org/abs/2502.13595>
- <https://arxiv.org/abs/2508.21085>
- <https://huggingface.co/ibm-granite/granite-embedding-english-r2>
- <https://huggingface.co/ibm-granite/granite-embedding-small-english-r2>
- <https://huggingface.co/ibm-granite/granite-embedding-311m-multilingual-r2>
- <https://huggingface.co/Qwen/Qwen3-Embedding-0.6B>
- <https://huggingface.co/datasets/mteb/scifact>
- <https://www.ibm.com/docs/en/SSYOK8/wsj/analyze-data/fm-models-embed.html>
- <https://ollama.com/search?c=embedding>
- <https://ollama.com/library/qwen3-embedding>
- <https://pytorch.org/get-started/locally/>
- <https://developers.openai.com/api/docs/models/text-embedding-3-small>
- <https://github.com/huggingface/sentence-transformers>
- <https://github.com/embeddings-benchmark/mteb>
- <https://github.com/ibm-granite/granite-embedding-models>
- <https://github.com/QwenLM/Qwen3-Embedding>

## Lab files in this folder

- [`lab/embed_bakeoff.py`](lab/embed_bakeoff.py)
- [`lab/ollama_stretch.py`](lab/ollama_stretch.py)
