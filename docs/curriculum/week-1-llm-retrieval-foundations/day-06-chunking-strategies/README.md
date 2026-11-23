# Day 6 — Chunking Strategies

> Part of the [GenAI Architect study path](../../README.md) · Llm Retrieval Foundations · [Original interactive pack](https://claude.ai/artifact/7YszJXokgooHrhdJng1A51) · [Offline HTML](../../archive/html/day-06.html)


GenAI Architect · 30-day plan · Week 1

# Day 6 — Chunking strategies, with measurement

Fixed, recursive, semantic, structure-aware, parent-child and late chunking. Today's goal is to stop arguing about chunk size and start measuring it: one corpus, one golden set, six strategies, and a table that tells you which one earns its tokens.

Thu 15 Oct 2026
Lab ≈ 75 min
RTX 3050 6 GB · Ollama · bge-small
langchain-text-splitters 1.1.3 · chonkie 1.7.0

01

## Why this matters for the JD

The JD names **RAG optimization (chunking, reranking, hybrid, feedback loops)** as a preferred skill and **evaluation (groundedness, relevance, latency/cost)** as a must-have. Chunking is where those meet: it sets the ceiling on retrieval recall, it sets the per-query token bill (k × chunk size), and it is the decision reviewers most often see made by default rather than by data. An architect who can show a chunking bake-off table in a design review wins that argument quickly.

02

## Concept brief

### Two units, not one

Every chunk does two jobs. It is the **unit of retrieval**: the text that gets turned into one vector, which should be about one thing so the vector is sharp. It is also the **unit of generation**: the text the LLM reads, which should be self-sufficient (it names its subject, keeps the table header, keeps the condition attached to the rule). These pull in opposite directions. Small chunks retrieve precisely but arrive at the LLM as orphans; large chunks read well but their vectors average several topics into mush. Most "advanced" chunking techniques are ways of decoupling the two units.

![Diagram 1](diagrams/fig-1.svg)

Parent-child ("small-to-big"): match on small children, return the parent. Context headers, contextual retrieval and late chunking solve the same tension in different places.

### The hard constraints you design inside

- **Embedder max sequence length.** `bge-small-en-v1.5` truncates at 512 tokens, silently. A 800-token chunk embeds only its first 512; the tail is unretrievable. Always chunk with the *embedder's* tokenizer, not tiktoken.
- **Context budget.** Tokens per query ≈ k × chunk tokens (+ prompt). At 10k queries/day, k=5 and 800-token chunks, that is 40M input tokens/day before the answer is written. Halving chunk size at the same recall is a direct cost cut.
- **Index size.** Overlap of 64 on 256-token windows adds ~33% more vectors. That is storage, ingest time, and duplicated context in top-k.
- **Metadata inheritance.** Every chunk must carry doc\_id, section path, version and the source document's ACL tags. Chunking that drops ACLs turns retrieval into a data-leak path.

### The strategies, by mechanism

- **Fixed-size tokens**: slide a window of N tokens (± overlap). Cheap and deterministic, blind to meaning; cuts sentences and tables in half.
- **Recursive**: try separators in order (`\n\n`, `\n`, space, char) until pieces fit. The sensible default for prose. Chroma's study found recursive at 200 tokens with no overlap consistently strong.
- **Structure-aware**: split on the document's own structure (Markdown headings, HTML sections, PDF layout items, table boundaries), then size-cap inside each section. Docling's `HybridChunker` does this from a parsed `DoclingDocument`, merges undersized siblings (`merge_peers`) and offers `contextualize()` to prepend the heading path to what you embed.
- **Semantic**: embed sentences, compute similarity across a sliding window, cut where it drops below a threshold. Chonkie's `SemanticChunker` also smooths the similarity curve (Savitzky-Golay filter) before picking cut points. Costs a sentence-level embedding pass at ingest, and the threshold is corpus-specific.
- **Parent-child**: the diagram above. Two stores, two IDs, one dedupe step.
- **Contextual retrieval** (Anthropic): an LLM writes 50–100 tokens of document context for each chunk, prepended before embedding and BM25 indexing. Reported top-20 retrieval failure: 5.7% → 3.7% with contextual embeddings, 2.9% adding contextual BM25, 1.9% adding reranking. One LLM call per chunk at ingest (~$1.02 per million document tokens with prompt caching in their setup).
- **Late chunking** (Jina): run a long-context embedder over the whole document first, then mean-pool token embeddings per chunk span. Each chunk vector "knows" its surroundings. Requires a long-context embedding model and access to token-level outputs, so most managed embedding APIs can't do it.

### What the 2024–2026 evidence says

- Chroma's token-level evaluation: recall spread across chunkers was ~8–9 points; the OpenAI Assistants default (800 tokens, 400 overlap) had the lowest precision and IoU of all configs; removing overlap generally improved IoU.
- Zhou et al. (arXiv 2602.16974, Feb 2026): simple structure-based chunking beat LLM-guided chunking for in-corpus retrieval; contextualized chunking helped in-corpus retrieval but hurt in-document retrieval; chunk size only weakly correlated with in-corpus effectiveness.
- Takeaway for reviews: the strategy rarely changes recall by more than ~10 points, but it can change *context tokens per correct answer* by 3–5×. Optimise for recall at a fixed token budget.

### Where this lives on the platforms

- **watsonx.ai**: AutoAI RAG (`rag_optimizer` in the watsonx.ai Python SDK) searches chunking parameters alongside embedding and retrieval settings and ranks patterns by eval metrics. Docling (from IBM Research) is the natural parser and chunker for the InstructLab/Granite stack you already know.
- **AWS Bedrock Knowledge Bases**: fixed-size, hierarchical (parent-child), semantic, or no chunking, plus a custom Lambda transformation.
- **Azure AI Search**: integrated vectorization with the Text Split skill (page length and overlap), and a Document Layout skill for structure-aware splitting.
- **Vertex AI RAG Engine**: chunk size and overlap set on the import's transformation config.

Review line to remember: "Managed chunking is a starting configuration, not a decision. We ran our golden set across three settings and picked the one with the best recall per 1k context tokens."

03

## Trade-offs & decision table

| Strategy | Pick when | Failure modes | Ingest cost | Query-time cost |
| --- | --- | --- | --- | --- |
| Fixed tokens | Unstructured text dumps, logs, baseline for comparison | Splits sentences, tables, code; overlap used as a band-aid | Lowest | Predictable; overlap duplicates context in top-k |
| Recursive 200–500 | Default for prose, wikis, tickets, emails | Ignores headings, so chunks lose their section subject; tables flattened | Low | Good recall per token at 200–400 |
| Structure-aware | Policies, manuals, specs, contracts, anything with headings or tables | Only as good as the parser; scanned PDFs need OCR/layout models; giant sections still need a size cap | Medium to high (layout model, GPU helps) | Best citations; breadcrumbs add ~10–20 tokens per chunk |
| Semantic | Long unstructured narrative: transcripts, call notes, chat logs | Threshold is corpus-specific; produces fragments or giant chunks; non-deterministic across embedder upgrades | Sentence-level embedding pass | Variable chunk sizes make k hard to budget |
| Parent-child | Precise questions whose answers need surrounding context; long sections | Parent too large blows the context budget; needs a doc store and dedupe; harder citations | ~4–8× more vectors | Highest context tokens; use small k |
| Contextual retrieval | High-value corpora where chunks are ambiguous on their own ("the company", "this clause") | LLM can hallucinate context; re-run on every doc change | One LLM call per chunk (cache the doc prefix) | +50–100 tokens per chunk if you return the context too |
| Late chunking | Self-hosted long-context embedder, documents with heavy cross-references | Doc length capped by embedder context (e.g. 8k); not available on most hosted APIs | One long forward pass per doc | Same as base chunking |

04

## Hands-on lab: the chunking bake-off (60–90 min)

**Task.** Build a fixed golden set of 30 questions with a local LLM, then run six chunking strategies through the same embedder and score each on evidence hit@1, hit@5, MRR and context tokens per query. You finish with a CSV you can paste into an ADR.

**How scoring works.** Each question is generated from one paragraph (the *evidence*). A retrieved chunk counts as a hit if it contains ≥80% of that evidence, measured by longest common substring after whitespace normalisation. This is a simplified version of Chroma's token-level recall. The golden set is cached in `golden.jsonl` so every strategy and every rerun sees the same questions.

### Setup (PowerShell, no WSL or Docker needed)

**PowerShell**

```powershell
mkdir day06-chunking; cd day06-chunking
py -3.13 -m venv .venv
# If activation is blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip

# GPU (optional): install the CUDA build of torch first. Copy the exact
# command for your driver from https://pytorch.org/get-started/locally/
# Skip it and everything still runs on CPU (slower embedding, same results).

python -m pip install "langchain-text-splitters>=1.1" "chonkie[semantic]>=1.7" "sentence-transformers>=5" ollama numpy pydantic

# Ollama for Windows must be running (tray icon). 3B model fits in 6 GB VRAM.
ollama pull qwen2.5:3b

# Save the script below as chunking_bakeoff.py, then:
python chunking_bakeoff.py                    # downloads 4 Kubernetes concept docs into .\corpus
# or point it at your own docs (.md / .txt):
python chunking_bakeoff.py --docs C:\path\to\your\docs --golden golden_mine.jsonl
```

### chunking\_bakeoff.py

**Python 3.13** · saved as `lab/chunking_bakeoff.py`

```python
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
```

The pipeline logic was dry-run end to end before publishing (with a stub embedder and stub LLM, since model downloads were not available in the build environment). First real run downloads bge-small (~130 MB) from Hugging Face.

### Expected output

**Console (shape; your numbers will differ)**

```text
[corpus] 4 docs, ~135,000 chars
[golden]  1/30  What happens to a Pod's ephemeral containers when ...
...
[embed] BAAI/bge-small-en-v1.5 on cuda:0, max_seq_length=512
[done] fixed_256       ~120 units
[done] fixed_256_ov64  ~160 units
[done] recursive_512   ~70 units
[done] md_structure    ~120 units
[done] semantic        varies a lot with threshold
[done] parent_child    ~300 units

     strategy |  chunks | avg_tok | max_tok | hit@1 | hit@5 |  mrr | ctx_tok@k | hits/1k_ctx | build_s | embed_s
          ... sorted by MRR ...
[saved] chunking_results.csv
```

**What to look for** (hypotheses to confirm or kill with your numbers):

- Overlap adds ~33% more chunks and context tokens; check whether hit@5 actually moves.
- `parent_child` should lead on hit@5 but cost the most context tokens. Is it best on `hits/1k_ctx`?
- `md_structure` should beat `recursive_512` on docs with good headings, because the breadcrumb tells the vector what the chunk is about.
- If `semantic` shows hundreds of tiny chunks, the threshold is too high for this embedder. Tune it before judging the method.
- Bias check: questions come from paragraphs, so paragraph-aligned chunkers get a small home advantage. Note it in your write-up; reviewers ask.

### Stretch goals

1. **Structure-aware on real PDFs with Docling.** `python -m pip install docling`, convert 3–5 policy PDFs (your compliance-bot corpus is ideal), chunk with `HybridChunker(tokenizer=HuggingFaceTokenizer(tokenizer=model.tokenizer, max_tokens=450), merge_peers=True)`, embed `chunker.contextualize(chunk)`, and add it as a seventh strategy. Note how tables come through compared with the Markdown splitter.
2. **Contextual retrieval, measured.** For `recursive_512`, ask `qwen2.5:3b` for a 50-token "situate this chunk in the document" line per chunk, prepend it to `embed_text` only, and rerun. Report the hit@5 delta and the ingest cost (LLM calls = chunks, wall-clock minutes). Is the gain worth it at 10× the corpus size?

05

## GitHub repos to study

- [chonkie-inc/chonkie](https://github.com/chonkie-inc/chonkie) (~4.8k★, MIT; currently served from the feyninc org): 11 chunkers in one API, including Semantic, Late, Code, Table and an LLM-driven one. Read `chunker/semantic.py` to see windowed similarity plus filtering decide cut points.
- [docling-project/docling](https://github.com/docling-project/docling) (~69k★, MIT): layout-aware parsing to `DoclingDocument`. Study the hybrid-chunking example and how heading context is carried into each chunk.
- [brandonstarxel/chunking\_evaluation](https://github.com/brandonstarxel/chunking_evaluation) (~510★): the code behind Chroma's chunking study. Look at the token-level recall/precision/IoU metrics and `ClusterSemanticChunker`. Commit activity is low, so borrow the method rather than the dependency.
- [jina-ai/late-chunking](https://github.com/jina-ai/late-chunking) (~540★, Apache-2.0): `chunked_pooling/` shows span-level mean pooling over a single long forward pass; `run_chunked_eval.py` reproduces the BeIR comparison.

06

## Read

- [Chroma Research — Evaluating Chunking Strategies for Retrieval](https://www.trychroma.com/research/evaluating-chunking). The metric design (token-level IoU and Precision\_Ω) is the part to steal for your own eval harness. 25 min.
- [Anthropic — Introducing Contextual Retrieval](https://www.anthropic.com/news/contextual-retrieval). Cleanest published ablation of contextual embeddings + BM25 + reranking, with ingest cost math. 15 min.
- [Zhou et al. (2026) — Beyond Chunk-Then-Embed: a taxonomy and evaluation of chunking strategies](https://arxiv.org/abs/2602.16974). Use it to defend "structure-based first, LLM chunking only with evidence". Skim results tables, 30 min. Companion: [Late Chunking paper (Jina)](https://arxiv.org/abs/2409.04701).

07

## Interview questions

**Users say the assistant misses answers that are definitely in the documents. How do you prove or rule out chunking as the cause?**

Separate retrieval from generation first. Build a golden set with evidence spans, run retrieval only, and measure evidence recall@k. Then classify misses: (1) *split evidence*, the answer straddles a boundary; (2) *diluted vector*, the chunk covers several topics; (3) *orphaned chunk*, the chunk never names its subject, so the query can't match it; (4) *truncation*, the chunk exceeds the embedder's max length and the tail was never embedded. Check (4) first because it is silent and common. Each class has a different fix: structure-aware boundaries, smaller chunks, breadcrumbs or contextual retrieval, and tokenizer-aligned sizing.

**How do you choose chunk size?**

Bound it above by the embedder's max sequence length and by k × size against the context budget. Then match query shape: factoid lookups favour 128–256 tokens; synthesis or procedure questions favour larger units or parent-child. Sweep three sizes on the golden set and choose on recall@k *at a fixed context-token budget*, not raw recall, because a bigger chunk always looks better on recall alone while costing more per query and diluting grounding.

**Is chunk overlap a good default?**

It's a patch for blind boundaries. It increases vector count (64/256 ≈ +33%) and puts duplicate text into top-k, which wastes context and can crowd out a distinct relevant chunk. Chroma found removing overlap generally improved IoU. Prefer boundaries that respect structure, or parent-child; keep small overlap only for fixed-size splitting of unstructured text, and dedupe near-identical hits before the prompt.

**A team wants LLM-based or semantic chunking everywhere. Your position?**

Show me the eval. Recent results (Chroma 2024; Zhou et al. 2026) show structure-based and recursive splitting matching or beating semantic and LLM chunking for corpus-level retrieval, while semantic and LLM chunking add ingest cost, threshold tuning per corpus and non-determinism when the model changes. I'd allow them where structure is absent (transcripts, chat logs) and the golden set shows a gain that pays for the ingest cost.

**You need to change chunking on a 20M-chunk production index. How do you roll it out safely?**

Treat chunker + embedder as a versioned artifact. Use deterministic chunk IDs (hash of doc\_id, version and span) and put `chunker_version` and `embedder_version` in metadata. Build a shadow index, run offline golden-set eval plus a shadow-traffic comparison, then switch with an index alias (blue-green), keeping the old index for rollback. Estimate the re-embedding cost and time up front, keep ACL metadata identical, and check that citations still resolve to the same source anchors.

08

## Architect's lens

### ADR-006: Default chunking for an enterprise policy & procedures assistant

Status
:   Proposed, pending golden-set evaluation

Context
:   ~40k policy PDFs and DOCX files with heading hierarchies and tables; per-document ACLs; questions split between precise lookups ("retention period for backups in EU") and procedures. Embedder max length 512 tokens. Target: evidence hit@5 ≥ 0.90 and ≤ 3k context tokens per query at p95.

Options
:   (A) Vendor default fixed 800/400. (B) Recursive 400/0. (C) Structure-aware via Docling HybridChunker, heading breadcrumb in the embedded text, parent-child with section parents ≤1024 tokens. (D) Semantic or LLM chunking. (E) Late chunking with a self-hosted long-context embedder.

Decision
:   C as the default; B as the fallback for documents without usable structure. Contextual retrieval is held back for the top two high-value collections and enabled only if it lifts hit@5 by ≥3 points on their golden sets.

Consequences
:   **+** Citations map to real sections; tables stay intact; ACLs inherited per section; recall improves without larger k. **−** A layout-parsing dependency and GPU cost at ingest; two stores (vectors + parent text); chunker version must be tracked and re-indexing planned. A rejected because the token cost per answer is about 4× with no recall gain in published evals; D and E rejected for ingest cost and hosted-API constraints respectively.

Revisit when
:   hit@5 drops below 0.85 on the monthly eval, the embedder changes, or a long-context embedder becomes available on the chosen platform.

09

## Tomorrow

**Day 7 — Vector search internals:** HNSW, IVF and PQ, recall vs latency trade-offs, with FAISS hands-on on the chunks you produced today.

## Lab files in this folder

- [`lab/chunking_bakeoff.py`](lab/chunking_bakeoff.py)
