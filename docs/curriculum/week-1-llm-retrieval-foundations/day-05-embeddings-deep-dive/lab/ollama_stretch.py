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
