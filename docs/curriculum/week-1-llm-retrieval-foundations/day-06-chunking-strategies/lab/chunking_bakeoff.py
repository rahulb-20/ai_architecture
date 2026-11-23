"""
Day 6 - Chunking bake-off: measure, don't guess.

Compares 6 chunking strategies on the SAME corpus + SAME golden set + SAME embedder:
  fixed_256, fixed_256_ov64, recursive_512, md_structure, semantic, parent_child
Metrics per strategy: #chunks, avg tokens, hit@1, hit@k, MRR, context tokens sent to the LLM, hits per 1k ctx tokens.

A "hit" = a returned chunk contains >= 80% of the evidence passage the question was generated from
(the token-level idea behind Chroma's chunking evaluation, simplified to char overlap).

Usage (PowerShell):
  python chunking_bakeoff.py                         # downloads 4 Kubernetes docs into .\\corpus
  python chunking_bakeoff.py --docs C:\\work\\my_docs   # your own .md / .txt files
  python chunking_bakeoff.py --n-questions 40 --k 5 --llm qwen2.5:3b
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
import time
import urllib.request
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
import ollama
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
from chonkie import SemanticChunker
from chonkie.embeddings import SentenceTransformerEmbeddings

K8S_DOCS = {
    "pods.md": "https://raw.githubusercontent.com/kubernetes/website/main/content/en/docs/concepts/workloads/pods/_index.md",
    "service.md": "https://raw.githubusercontent.com/kubernetes/website/main/content/en/docs/concepts/services-networking/service.md",
    "secret.md": "https://raw.githubusercontent.com/kubernetes/website/main/content/en/docs/concepts/configuration/secret.md",
    "hpa.md": "https://raw.githubusercontent.com/kubernetes/website/main/content/en/docs/concepts/workloads/autoscaling/horizontal-pod-autoscale.md",
}
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


# ----------------------------------------------------------------------------- corpus
def clean_md(text: str) -> str:
    text = re.sub(r"^---\n.*?\n---\n", "", text, flags=re.S)          # front matter
    text = re.sub(r"\{\{[<%].*?[>%]\}\}", "", text, flags=re.S)        # hugo shortcodes
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)                 # html comments
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def load_corpus(folder: Path) -> dict[str, str]:
    folder.mkdir(parents=True, exist_ok=True)
    if not any(folder.glob("*.md")) and not any(folder.glob("*.txt")):
        print(f"[corpus] {folder} is empty -> downloading 4 Kubernetes concept docs")
        for name, url in K8S_DOCS.items():
            with urllib.request.urlopen(url, timeout=30) as r:
                (folder / name).write_text(r.read().decode("utf-8"), encoding="utf-8")
    docs = {p.name: clean_md(p.read_text(encoding="utf-8", errors="ignore"))
            for p in sorted(folder.iterdir()) if p.suffix.lower() in {".md", ".txt"}}
    print(f"[corpus] {len(docs)} docs, {sum(len(t) for t in docs.values()):,} chars")
    return docs


# ----------------------------------------------------------------------------- golden set
class GoldenQ(BaseModel):
    question: str


def candidate_passages(docs: dict[str, str]) -> list[tuple[str, str]]:
    out = []
    for doc_id, text in docs.items():
        in_code = False
        for para in text.split("\n\n"):
            if para.strip().startswith("```"):
                in_code = not in_code if para.count("```") % 2 else in_code
                continue
            p = para.strip()
            if in_code or p.startswith(("#", "|", "-", "*", "<", ">")):
                continue
            if 200 <= len(p) <= 700:
                out.append((doc_id, p))
    return out


def build_golden(docs, path: Path, n: int, llm: str, seed: int = 7) -> list[dict]:
    if path.exists():
        rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        if len(rows) >= n:
            print(f"[golden] reusing {path} ({len(rows)} questions) - keep it fixed across runs!")
            return rows[:n]
    random.seed(seed)
    cands = candidate_passages(docs)
    picks = random.sample(cands, min(n, len(cands)))
    rows = []
    for i, (doc_id, passage) in enumerate(picks, 1):
        resp = ollama.chat(
            model=llm,
            messages=[
                {"role": "system", "content": "You write evaluation questions for a retrieval system."},
                {"role": "user", "content": (
                    "Write ONE specific question that can be answered only from this passage. "
                    "Paraphrase - do not copy phrases longer than 3 words. Return JSON.\n\n"
                    f"PASSAGE:\n{passage}")},
            ],
            format=GoldenQ.model_json_schema(),
            options={"temperature": 0},
        )
        q = GoldenQ.model_validate_json(resp.message.content).question
        rows.append({"doc_id": doc_id, "question": q, "evidence": passage})
        print(f"[golden] {i:>2}/{len(picks)}  {q[:90]}")
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return rows


# ----------------------------------------------------------------------------- strategies
# Every unit: {"doc_id", "embed_text" (what we vectorise), "return_text" (what the LLM sees)}
def fixed_tokens(text, tok, size, overlap):
    enc = tok(text, add_special_tokens=False, return_offsets_mapping=True)
    offs = enc["offset_mapping"]
    step = size - overlap
    out = []
    for s in range(0, len(offs), step):
        window = offs[s:s + size]
        if not window:
            break
        out.append(text[window[0][0]:window[-1][1]])
        if s + size >= len(offs):
            break
    return out


def strat_fixed(docs, tok, size=256, overlap=0):
    return [{"doc_id": d, "embed_text": c, "return_text": c}
            for d, t in docs.items() for c in fixed_tokens(t, tok, size, overlap)]


def strat_recursive(docs, tok, size=512, overlap=64):
    sp = RecursiveCharacterTextSplitter.from_huggingface_tokenizer(tok, chunk_size=size, chunk_overlap=overlap)
    return [{"doc_id": d, "embed_text": c, "return_text": c} for d, t in docs.items() for c in sp.split_text(t)]


MD_HEADERS = [("#", "h1"), ("##", "h2"), ("###", "h3")]


def md_sections(text):
    return MarkdownHeaderTextSplitter(headers_to_split_on=MD_HEADERS, strip_headers=False).split_text(text)


def strat_md_structure(docs, tok, size=512, overlap=64):
    """Structure-aware: split on headings, then recursive inside; prepend a breadcrumb to the EMBEDDED text."""
    sp = RecursiveCharacterTextSplitter.from_huggingface_tokenizer(tok, chunk_size=size, chunk_overlap=overlap)
    out = []
    for d, t in docs.items():
        for sec in md_sections(t):
            crumb = " > ".join([d] + [sec.metadata[h] for _, h in MD_HEADERS if h in sec.metadata])
            for c in sp.split_text(sec.page_content):
                out.append({"doc_id": d, "embed_text": f"[{crumb}]\n{c}", "return_text": c})
    return out


def strat_semantic(docs, st_model, size=512, threshold=0.75):
    ch = SemanticChunker(embedding_model=SentenceTransformerEmbeddings(st_model),
                         threshold=threshold, chunk_size=size, similarity_window=3,
                         min_sentences_per_chunk=2)
    return [{"doc_id": d, "embed_text": c.text, "return_text": c.text} for d, t in docs.items() for c in ch.chunk(t)]


def strat_parent_child(docs, tok, child=128, parent=1024):
    """Retrieve on small children (precise vectors), return the parent section (enough context)."""
    psp = RecursiveCharacterTextSplitter.from_huggingface_tokenizer(tok, chunk_size=parent, chunk_overlap=0)
    csp = RecursiveCharacterTextSplitter.from_huggingface_tokenizer(tok, chunk_size=child, chunk_overlap=16)
    out = []
    for d, t in docs.items():
        for sec in md_sections(t):
            for p in psp.split_text(sec.page_content):
                for c in csp.split_text(p):
                    out.append({"doc_id": d, "embed_text": c, "return_text": p})
    return out


# ----------------------------------------------------------------------------- retrieval + metrics
def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def containment(evidence: str, chunk: str) -> float:
    a, b = norm(evidence), norm(chunk)
    m = SequenceMatcher(None, a, b, autojunk=False).find_longest_match(0, len(a), 0, len(b))
    return m.size / max(1, len(a))


def evaluate(name, units, golden, model, tok, k, build_s):
    t0 = time.perf_counter()
    vecs = model.encode([u["embed_text"] for u in units], batch_size=32,
                        normalize_embeddings=True, show_progress_bar=False)
    qv = model.encode([BGE_QUERY_PREFIX + g["question"] for g in golden],
                      normalize_embeddings=True, show_progress_bar=False)
    embed_s = time.perf_counter() - t0
    sims = qv @ vecs.T
    hit1 = hitk = rr = ctx = 0.0
    for qi, g in enumerate(golden):
        order = np.argsort(-sims[qi])
        returned, seen = [], set()
        for idx in order:                       # dedupe (parent-child returns the same parent many times)
            rt = units[idx]["return_text"]
            if rt in seen:
                continue
            seen.add(rt)
            returned.append(rt)
            if len(returned) == k:
                break
        ctx += sum(len(tok(r, add_special_tokens=False)["input_ids"]) for r in returned)
        ranks = [i for i, r in enumerate(returned, 1) if containment(g["evidence"], r) >= 0.8]
        if ranks:
            hitk += 1
            rr += 1 / ranks[0]
            hit1 += ranks[0] == 1
    n = len(golden)
    toks = [len(tok(u["embed_text"], add_special_tokens=False)["input_ids"]) for u in units]
    return {
        "strategy": name, "chunks": len(units), "avg_tok": round(float(np.mean(toks))),
        "max_tok": int(np.max(toks)), "hit@1": round(hit1 / n, 3), f"hit@{k}": round(hitk / n, 3),
        "mrr": round(rr / n, 3), "ctx_tok@k": round(ctx / n),
        "hits/1k_ctx": round((hitk / n) / max(1, ctx / n) * 1000, 3),
        "build_s": round(build_s, 2), "embed_s": round(embed_s, 2),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", default="corpus")
    ap.add_argument("--golden", default="golden.jsonl")
    ap.add_argument("--n-questions", type=int, default=30)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--llm", default="qwen2.5:3b")
    ap.add_argument("--embed", default="BAAI/bge-small-en-v1.5")
    args = ap.parse_args()

    docs = load_corpus(Path(args.docs))
    golden = build_golden(docs, Path(args.golden), args.n_questions, args.llm)
    model = SentenceTransformer(args.embed)     # auto-uses CUDA if torch sees your RTX 3050
    tok = model.tokenizer                       # chunk with the EMBEDDER's tokenizer, not tiktoken
    print(f"[embed] {args.embed} on {model.device}, max_seq_length={model.max_seq_length}")

    strategies = {
        "fixed_256": lambda: strat_fixed(docs, tok, 256, 0),
        "fixed_256_ov64": lambda: strat_fixed(docs, tok, 256, 64),
        "recursive_512": lambda: strat_recursive(docs, tok, 500, 64),
        "md_structure": lambda: strat_md_structure(docs, tok, 450, 64),
        "semantic": lambda: strat_semantic(docs, model, 500, 0.75),
        "parent_child": lambda: strat_parent_child(docs, tok, 128, 1024),
    }
    results = []
    for name, fn in strategies.items():
        t0 = time.perf_counter()
        units = fn()
        results.append(evaluate(name, units, golden, model, tok, args.k, time.perf_counter() - t0))
        print(f"[done] {name:<15} {len(units)} units")

    cols = list(results[0].keys())
    print("\n" + " | ".join(f"{c:>13}" for c in cols))
    for r in sorted(results, key=lambda r: -r["mrr"]):
        print(" | ".join(f"{str(r[c]):>13}" for c in cols))
    with open("chunking_results.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(results)
    print("\n[saved] chunking_results.csv")


if __name__ == "__main__":
    main()
