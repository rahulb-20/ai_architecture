"""
Day 12 - Multimodal RAG on a laptop (Docling + Ollama, no cloud keys).

  py mmrag.py ingest docling_report.pdf      # parse -> elements -> embeddings
  py mmrag.py ask "your question"            # retrieve + grounded answer
  py mmrag.py compare                        # structured vs naive-text retrieval on QUESTIONS

Models (Ollama): nomic-embed-text (embeddings), granite3.2-vision (figures),
granite3.3:2b (table summaries + answers). Swap via env vars below.
"""
import json, os, sys, time
from pathlib import Path

import numpy as np
import ollama

EMBED = os.getenv("EMBED_MODEL", "nomic-embed-text")
VISION = os.getenv("VISION_MODEL", "granite3.2-vision")
LLM = os.getenv("LLM_MODEL", "granite3.3:2b")
OUT = Path("mm_index"); OUT.mkdir(exist_ok=True)
FIG_DIR = OUT / "figures"; FIG_DIR.mkdir(exist_ok=True)

# Edit these after looking at your PDF: mix text, table and figure questions.
QUESTIONS = [
    "Which hardware/thread configuration gave the fastest pages-per-second, and what was it?",
    "What does the architecture figure show about the processing pipeline stages?",
    "Which models does Docling use for layout analysis and table structure?",
]


# ---------- embeddings ----------
def embed(texts, kind):
    # nomic-embed-text is trained with task prefixes; skipping them costs recall.
    prefix = "search_document: " if kind == "doc" else "search_query: "
    vecs = []
    for i in range(0, len(texts), 32):
        r = ollama.embed(model=EMBED, input=[prefix + t for t in texts[i:i + 32]])
        vecs.extend(r.embeddings)
    m = np.asarray(vecs, dtype=np.float32)
    return m / np.linalg.norm(m, axis=1, keepdims=True)


def llm(prompt, model=LLM, images=None):
    msg = {"role": "user", "content": prompt}
    if images:
        msg["images"] = images
    r = ollama.chat(model=model, messages=[msg], options={"temperature": 0})
    return r.message.content.strip()


# ---------- ingest: one record per *element*, typed ----------
def ingest(pdf):
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.chunking import HybridChunker

    opts = PdfPipelineOptions()
    opts.do_table_structure = True          # TableFormer -> real rows/cols
    opts.generate_picture_images = True     # keep figure crops for the VLM
    opts.images_scale = 2.0                 # ~144 dpi crops; VLMs need pixels
    conv = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})

    t0 = time.perf_counter()
    doc = conv.convert(pdf).document
    print(f"[parse] {pdf}: {len(doc.texts)} text items, {len(doc.tables)} tables, "
          f"{len(doc.pictures)} pictures in {time.perf_counter()-t0:.1f}s")
    (OUT / "doc.md").write_text(doc.export_to_markdown(), encoding="utf-8")

    records = []  # {id, type, page, embed_text, payload}

    # 1) text: structure-aware chunks (headings carried into each chunk)
    chunker = HybridChunker()
    for i, ch in enumerate(chunker.chunk(dl_doc=doc)):
        items = ch.meta.doc_items or []
        if items and all(it.label.value in ("table", "picture") for it in items):
            continue  # tables/figures get their own records below
        page = items[0].prov[0].page_no if items and items[0].prov else None
        ctx = chunker.contextualize(chunk=ch)  # "Heading > Subheading\ntext"
        records.append(dict(id=f"txt{i}", type="text", page=page, embed_text=ctx, payload=ctx))

    # 2) tables: embed a summary, generate from the raw markdown (multi-vector pattern)
    for i, tbl in enumerate(doc.tables):
        md = tbl.export_to_markdown(doc=doc)
        cap = tbl.caption_text(doc) or ""
        page = tbl.prov[0].page_no if tbl.prov else None
        summary = llm(
            "Summarise this table for search indexing in 3 sentences. Name the columns, "
            "what each row represents, and the most notable values.\n"
            f"Caption: {cap}\n\n{md[:6000]}")
        records.append(dict(id=f"tbl{i}", type="table", page=page,
                            embed_text=f"Table. {cap}\n{summary}", payload=f"{cap}\n{md}"))
        print(f"[table] tbl{i} p{page}: {summary[:90]}...")

    # 3) figures: VLM description becomes the searchable text
    for i, pic in enumerate(doc.pictures):
        img = pic.get_image(doc)
        if img is None or min(img.size) < 64:
            continue  # skip logos / icons
        path = FIG_DIR / f"fig{i}.png"; img.save(path)
        cap = pic.caption_text(doc) or ""
        desc = llm(
            "Describe this figure from a technical document for a search index. "
            "State the figure type, the components or axes, and any text or numbers you can read. "
            f"Caption (may be empty): {cap}", model=VISION, images=[str(path)])
        page = pic.prov[0].page_no if pic.prov else None
        records.append(dict(id=f"fig{i}", type="figure", page=page,
                            embed_text=f"Figure. {cap}\n{desc}", payload=f"{cap}\n{desc}", image=str(path)))
        print(f"[figure] fig{i} p{page}: {desc[:90]}...")

    E = embed([r["embed_text"] for r in records], "doc")
    np.save(OUT / "vectors.npy", E)
    (OUT / "records.json").write_text(json.dumps(records, indent=1), encoding="utf-8")

    # naive baseline: plain text per page, fixed 1200-char windows (no tables, no figures)
    from pypdf import PdfReader
    naive = []
    for pno, page in enumerate(PdfReader(pdf).pages, start=1):
        t = page.extract_text() or ""
        naive += [dict(id=f"naive{pno}_{j}", type="text", page=pno, embed_text=t[j:j+1200], payload=t[j:j+1200])
                  for j in range(0, len(t), 1000) if t[j:j+1200].strip()]
    np.save(OUT / "naive_vectors.npy", embed([r["embed_text"] for r in naive], "doc"))
    (OUT / "naive_records.json").write_text(json.dumps(naive), encoding="utf-8")
    counts = {t: sum(r["type"] == t for r in records) for t in ("text", "table", "figure")}
    print(f"[index] structured={counts}  naive={len(naive)} chunks  -> {OUT}/")


# ---------- retrieve + answer ----------
def load(prefix=""):
    recs = json.loads((OUT / f"{prefix}records.json").read_text(encoding="utf-8"))
    return recs, np.load(OUT / f"{prefix}vectors.npy")


def retrieve(q, recs, E, k=4):
    s = E @ embed([q], "query")[0]
    idx = np.argsort(-s)[:k]
    return [(recs[i], float(s[i])) for i in idx]


def ask(q, k=4):
    recs, E = load()
    hits = retrieve(q, recs, E, k)
    ctx = "\n\n".join(f"[{r['id']} | {r['type']} | p.{r['page']}]\n{r['payload'][:2500]}" for r, _ in hits)
    ans = llm(
        "Answer using ONLY the sources below. Cite source ids in square brackets after each claim. "
        "If the sources do not contain the answer, reply exactly: NOT IN SOURCES.\n\n"
        f"{ctx}\n\nQuestion: {q}")
    for r, s in hits:
        print(f"  {s:.3f}  {r['id']:<7} {r['type']:<6} p.{r['page']}")
    print("\n" + ans)


def compare():
    S = load(); N = load("naive_")
    for q in QUESTIONS:
        print(f"\nQ: {q}")
        for name, (recs, E) in (("structured", S), ("naive", N)):
            hits = retrieve(q, recs, E, 4)
            print(f"  {name:<10} " + "  ".join(f"{r['id']}(p{r['page']},{s:.2f})" for r, s in hits))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "ingest": ingest(sys.argv[2])
    elif cmd == "ask": ask(" ".join(sys.argv[2:]))
    elif cmd == "compare": compare()
    else: print(__doc__)
