# Day 41 — Revision Drill: Multimodal RAG & LangGraph Recovery

> Part of the [GenAI Architect study path](../../README.md) · Revision drill · [Original interactive pack](https://claude.ai/artifact/NiGTREEq8R1qhMTb4NdApY) · [Offline HTML](../../archive/html/day-41.html)


GenAI Architect prep · Day 41 · Revision drill · Thu 19 Nov 2026

# Multimodal RAG on messy documents, and ingestion that survives a crash

Multimodal RAG (Day 12) gets its first drill. LangGraph comes back from Day 17, this time for checkpointing, durability modes and recovery rather than human-in-the-loop. The scenario joins them: a GPU-heavy document pipeline that must never half-index a file.

langgraph 1.2.22langgraph-checkpoint-postgres 3.2.1docling 2.150.1sentence-transformers 6.3.0

## Why this matters for the JD

The JD lists document Q&A and knowledge assistants, preferred RAG optimisation, LangGraph and agentic frameworks, plus resilience, DR and rollback. Enterprise corpora are scans, tables and diagrams, and ingestion runs for hours on expensive GPUs. Panels test whether you can say what happens to a half-processed document when a node dies.

## What to have current

### LangGraph persistence

- Pass `durability="exit" | "async" | "sync"` on `invoke` or `stream`. Only `sync` commits a checkpoint before the next step starts. `exit` cannot recover from a process crash mid-run.
- Resume after an error with `graph.invoke(None, config)` on the same `thread_id`. Resumption is at a super-step boundary, and an interrupted node restarts from its first line.
- Writes from siblings that succeeded are kept in `checkpoint_writes`, so they are not re-run.
- Until the first checkpoint is durable, `invoke(None, config)` raises `EmptyInputError`. Record admission in your own outbox row.
- Encrypt with `EncryptedSerializer.from_pycryptodome_aes()`, which reads `LANGGRAPH_AES_KEY`. The docs describe no built-in TTL, so prune old checkpoints yourself.

### Multimodal parsing and retrieval

- Docling's VLM pipeline runs with `docling --pipeline vlm --vlm-model granite_docling`. Granite-Docling is 258M parameters, Apache 2.0, and emits DocTags.
- Docling also ships an MCP server, and its formats now include audio and video.
- The `colpali-engine` README now says the package is deprecated. It points new work to Sentence Transformers v6 and its `MultiVectorEncoder`.
- `unstructured` 0.27.37 requires Python 3.11 to 3.13, so it works on your 3.13 but not on 3.14.

## Eight interview questions

Answer each aloud in under 90 seconds before opening the model answer.

Q1 · Multimodal RAG

Half your corpus is scanned PDFs with charts and forms. Do you parse to text, or retrieve page images with a ColPali-style model? Defend the choice, including storage.

**Model answer**

It depends on what carries the meaning. If the answer lives in the words and tables, parse to text: you get hybrid search, cheap single-vector indexes, chunk-level citations and PII masking on text. If meaning lives in layout, charts or stamps, text extraction loses it, and page-image retrieval wins. The ColPali paper reports that embedding page images directly largely outperforms text-extraction pipelines on its ViDoRe benchmark.

The cost is storage and serving. Late-interaction models keep about a thousand patch vectors per page. At 1,030 × 128 dims in fp16, that is about 264 KB per page, compared with 4 KB for one 1,024-dim fp32 vector. A million pages becomes roughly 264 GB against 4 GB, and MaxSim scoring needs an engine that supports multi-vector search.

My default is a hybrid. Parse everything to text as the primary index. Route visually dense document types, such as valuation reports and engineering drawings, into a page-image index as well. Fuse the two with RRF, or use the image model only to rerank a candidate set. Token pooling or binary quantisation cuts the multi-vector footprint.

Follow-up: page-image retrieval returns a page, not a passage. The generator then needs a vision LLM, which adds image tokens to every answer.

Q2 · Multimodal RAG

Users ask numeric questions over large tables, such as "flood cap for the Silver plan in 2025". Recursive chunking returns half a table. What is your table strategy?

**Model answer**

Treat a table as a structured object, not prose. A layout-aware parser such as Docling detects the table and its cell structure. I store three things for it:

- The structured table, as JSON or a dataframe, keyed by document, version and page.
- A Markdown rendering for the generator. Large tables are split into row groups, and each group repeats the header.
- A short natural-language summary of what the table covers, which is the text I embed. Raw grids embed poorly.

For lookups and aggregates, retrieval finds the table and a tool does the arithmetic, using SQL or pandas over the structured copy. The LLM should not add up numbers it read from Markdown.

I evaluate parsing separately from retrieval, with a cell-level score such as TEDS on a labelled sample. Most "wrong number" incidents turn out to be merged header cells or a table split across pages.

Q3 · Multimodal RAG

You switch OCR from a classic engine to a small VLM parser. Text quality looks better, but a reviewer finds an invented sanction amount. Why does this happen, and how do you control it?

**Model answer**

Classic OCR fails visibly. It produces garbled characters that a reader or a dictionary check can spot. A VLM generates text, so on a blurred field it can produce a fluent, plausible value. That failure is harder to catch, and it is worst on exactly the fields that matter: amounts, dates and IDs.

- **Provenance:** keep page number and bounding box for every element, and show the source crop next to any cited number.
- **Cross-check critical fields:** run a second extractor or classic OCR on detected amounts and dates. Disagreement sends the field to review.
- **Routing:** use the standard layout pipeline for clean digital PDFs, and reserve the VLM for scans or pages the first pass scores poorly.
- **Evaluation:** measure character error rate and field-level exact match on a labelled scan set, by document type and scan quality.

Follow-up: a 258M model such as Granite-Docling runs on a 6 GB laptop GPU. That makes it practical to keep parsing inside the bank's network.

Q4 · Multimodal RAG

Retrieval is solid, but answers about diagrams are weak. Product wants to send every retrieved page image to a vision LLM. What do you push back on?

**Model answer**

Cost, latency and data exposure. Every page image adds image tokens to the prompt, so five pages per answer multiplies input cost and time to first token. Scans also carry personal data that text-side masking never touched.

I would route. A classifier or the retrieval metadata decides whether the question needs visual evidence. Most questions are answered from text, captions and table summaries. For the rest, send cropped figure regions rather than full pages, at a resolution chosen by testing. Caption figures once at ingestion so the text index can find them, and keep the crop for answer time.

Before turning it on, I would measure answer quality, p95 latency and cost per query on a golden set that includes diagram questions. I would also confirm the vision endpoint runs in-region, and redact or exclude page types such as identity documents.

Q5 · LangGraph

Which durability mode do you choose for a five-minute ingestion graph on spot GPUs, and for a 300 ms chat agent? What does each choice cost?

**Model answer**

**Ingestion on spot GPUs:** `sync`. Each step is expensive and the node can disappear, so I want the checkpoint committed before the next step starts. The extra write per super-step is small next to a GPU parse. With `exit`, losing the pod loses the whole run.

**Chat agent:** `async` is usually right. The checkpoint is written while the next step runs, so it adds little latency. The risk is losing the last step on a crash, and the user can resend. `exit` fits short graphs where the run is the unit of retry and nothing is worth keeping mid-run.

One subtlety applies in every mode. Before the first checkpoint is durable, the run cannot be resumed, and `invoke(None, config)` raises `EmptyInputError`. If you must tell "never accepted" from "accepted but lost", write an outbox row at admission and have a sweeper restart threads that have no checkpoint.

Q6 · LangGraph

After a crash and resume, your vector store holds duplicate chunks. Explain exactly why, and the design rules that prevent it.

**Model answer**

Checkpoints exist at super-step boundaries. A node that fails, or is interrupted, re-runs from its first line on resume. If the node wrote three of five chunks before dying, the retry writes all five again, so you get eight rows. Today's exercise shows exactly that: 5 rows with deterministic keys, 8 without.

Siblings that completed in the same super-step are not re-run, because their writes were saved. That is why only the failed branch re-executes.

- Every external write is idempotent. Derive the key from stable inputs, such as document, version and content hash, and upsert.
- Put side effects in their own small nodes, after the expensive pure work, so a retry repeats as little as possible.
- Code before an `interrupt()` runs again on resume. Never put a side effect above an interrupt.
- Publish atomically. Index into a version, then flip an alias once the document is complete, so readers never see half a document.

Q7 · LangGraph

The security review asks what is inside your checkpoint tables. How do you run a Postgres checkpointer for a regulated, multi-tenant workload?

**Model answer**

Checkpoints are full snapshots of state at every step, so they hold whatever you put in state, including prompts, retrieved text and tool results. I treat that store as production data.

- **Keep state small:** store URIs to page images and parsed output in object storage, not blobs. Mask personal data before it enters state.
- **Encrypt:** application-level encryption with `EncryptedSerializer`, key from the KMS, in addition to disk encryption.
- **Tenancy:** namespace `thread_id` by tenant (under 255 characters on Postgres), and enforce it with row-level security or separate schemas. A guessed thread ID must not load another tenant's state.
- **Retention:** the store grows without bound, so a scheduled job prunes by age, aligned with your DPDP retention policy. Erasure requests must reach checkpoints too.
- **Schema change:** in-flight threads hold the old state shape. Add fields as optional, version the state, and drain or migrate long-running threads before removing a field.

Q8 · LangGraph

An agent produced a bad answer last week. Can you replay the run from its checkpoints to debug it? What are the hazards?

**Model answer**

Yes, if retention kept the thread. `get_state_history(config)` lists snapshots newest first, with each step's writes and the next nodes. I can inspect the state where the run went wrong, and `update_state` creates a new checkpoint from a corrected state, which forks the run without changing the original.

The hazard is that replay is re-execution. The docs say nodes after the chosen checkpoint run again, including LLM calls and API requests. So:

- Replay in a sandbox, with write tools stubbed or pointed at test systems.
- Expect different output. Models and retrieval results have changed since last week, so replay explains the path, while the stored trace explains the actual outcome.
- Pin the model version and corpus snapshot if you need to reproduce a result rather than explore it.

Follow-up: checkpoints and traces answer different questions. Keep both, with the trace ID stored in checkpoint metadata.

## Design scenario · 25 minutes on paper

**A private bank wants its 6,000 credit officers to query loan files.** About two million pages arrive each month: scanned sanction letters, agreements with schedules of tables, and property valuation reports with photos and site maps.

- Parsing runs on preemptible GPU nodes to control cost. A node can vanish at any moment.
- A reprocessed file must never appear twice, or half-indexed, in search.
- Data stays in India. Access follows branch and role.
- Numbers in answers must link to the exact page region they came from.
- A new parser or embedding model must be rolled out without re-ingesting during business hours.

**Produce:** the ingestion graph and its recovery behaviour, the index design, GPU sizing, the evaluation plan and your top three risks.

**Reference design**

![Documents enter a queue and an admission outbox, then a LangGraph ingestion graph with a checkpoint after every super-step. The graph classifies, parses, fans out to text, tables and figures, masks PII, embeds, upserts with deterministic keys and finally flips the version alias. A Postgres checkpointer and object storage sit underneath.](diagrams/fig-1.svg)

**Recovery.** One thread per document version, with `thread_id = tenant:doc:version`. A sweeper resumes threads whose pods died with `invoke(None, config)`, and restarts outbox rows that have no checkpoint. A retry policy handles transient VLM timeouts inside a node. A document that fails three resumes goes to a dead-letter queue with its last state.

**Index.** The text index is hybrid, with metadata for document, version, page, bounding box, branch and role, applied as server-side filters. Valuation reports also go to a page-image index, used as a second retriever and fused with RRF. A new parser or embedding model builds a new index version in the background. Traffic moves by alias after the eval gate passes, and the old version stays for rollback.

**GPU sizing, as a habit.** Two million pages a month averages about 0.77 pages per second. Plan for a peak three times that, about 2.3 pages per second. With an assumed 1.5 seconds of GPU time per page and 60% useful utilisation, you need about 2.3 × 1.5 ÷ 0.6 ≈ 6 GPUs, plus headroom for preemption. If 20% of pages enter the page-image index at about 264 KB each, that is about 106 GB a month before pooling or quantisation.

| Layer | Check | Gate |
| --- | --- | --- |
| Parsing | Field exact match on amounts and dates; table TEDS; character error rate by scan quality | No regression on critical fields |
| Recovery | Chaos test: kill pods mid-parse and mid-upsert across 500 documents | Zero duplicates, zero partial versions, every document terminal |
| Retrieval | Recall@k by modality (text, table, figure) | Paired non-inferiority per document type |
| Answers | Faithfulness; every number carries a page and box citation | Uncited numbers block release |
| Access | Per-role negative retrieval cases | Zero leaks |
| Cost | GPU seconds and storage per page | Within budget at peak load |

**Top risks.** A VLM inventing a sanction amount, handled by field cross-checks and source crops. A half-indexed document after a crash, handled by idempotent upserts and the alias flip. Checkpoints becoming a second copy of customer data, handled by URIs in state, masking, encryption and pruning.

## Today's exercise · 25 minutes

Run a three-branch ingestion graph with a SQLite checkpointer. Two failures are injected: a VLM timeout, then a vector-store reset halfway through the upsert batch. You resume the same thread twice and see which nodes re-run and whether the index gets duplicates. It needs no GPU, API key, WSL2 or Docker.

```text
New-Item -ItemType Directory -Force day41-drill | Out-Null
Set-Location day41-drill
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1   # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
python -m pip install "langgraph==1.2.22" "langgraph-checkpoint-sqlite==3.1.3"
notepad day41_resume.py        # paste the script below and save
python day41_resume.py
python day41_resume.py --no-ledger
```
`lab/day41_lab.py`

```python
"""Day 41 drill: crash-safe multimodal ingestion with LangGraph checkpoints.

parse -> (text | tables | figures in parallel) -> index
Run 1: the figure-captioning node times out (simulated VLM failure).
Run 2: resume the same thread; the index node crashes halfway through its writes.
Run 3: resume again; the run completes.
Watch which nodes re-execute and whether the vector store gets duplicates.
"""
import hashlib
import operator
import sqlite3
import sys
from pathlib import Path
from typing import Annotated, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

DB = Path("day41.db")
IDEMPOTENT = "--no-ledger" not in sys.argv
FAIL_PLAN = {"figures": 1, "index": 1}  # failures to inject, per node
calls: dict[str, int] = {}

# A fake parsed PDF: what Docling would hand you (text, tables, pictures).
DOC = {
    "doc_id": "policy-2026-07",
    "text": ["Section 4.2 covers flood damage.", "Claims must be filed in 30 days."],
    "tables": [[["Plan", "Flood cap"], ["Gold", "10L"], ["Silver", "4L"]]],
    "figures": ["fig-1.png", "fig-2.png"],
}

class State(TypedDict):
    doc_id: str
    chunks: Annotated[list[dict], operator.add]
    indexed: int

def tick(node: str) -> int:
    calls[node] = calls.get(node, 0) + 1
    return calls[node]

def maybe_fail(node: str, attempt: int, why: str) -> None:
    if attempt <= FAIL_PLAN.get(node, 0):
        raise RuntimeError(f"{node}: {why}")

def parse(state: State) -> dict:
    tick("parse")
    return {"doc_id": DOC["doc_id"]}

def text(state: State) -> dict:
    tick("text")
    return {"chunks": [{"kind": "text", "body": t, "page": 1} for t in DOC["text"]]}

def tables(state: State) -> dict:
    tick("tables")
    out = []
    for t in DOC["tables"]:
        header, *rows = t
        md = "| " + " | ".join(header) + " |\n" + "\n".join(
            "| " + " | ".join(r) + " |" for r in rows
        )
        out.append({"kind": "table", "body": md, "page": 2})
    return {"chunks": out}

def figures(state: State) -> dict:
    attempt = tick("figures")
    maybe_fail("figures", attempt, "VLM caption call timed out")
    caps = [f"[caption of {f}] flood-zone map" for f in DOC["figures"]]
    return {"chunks": [{"kind": "figure", "body": c, "page": 3} for c in caps]}

def index(state: State) -> dict:
    attempt = tick("index")
    con = sqlite3.connect(DB)
    con.execute(
        "CREATE TABLE IF NOT EXISTS vectors(key TEXT PRIMARY KEY, doc TEXT, body TEXT)"
        if IDEMPOTENT
        else "CREATE TABLE IF NOT EXISTS vectors(key TEXT, doc TEXT, body TEXT)"
    )
    chunks = sorted(state["chunks"], key=lambda c: (c["page"], c["body"]))
    for i, c in enumerate(chunks):
        key = hashlib.sha256(f"{state['doc_id']}|{c['body']}".encode()).hexdigest()[:16]
        verb = "INSERT OR IGNORE" if IDEMPOTENT else "INSERT"
        con.execute(f"{verb} INTO vectors VALUES (?,?,?)", (key, state["doc_id"], c["body"]))
        con.commit()  # each upsert is its own external side effect
        if i == 2:
            maybe_fail("index", attempt, "vector store connection reset mid-batch")
    n = con.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
    con.close()
    return {"indexed": n}

def build(checkpointer):
    g = StateGraph(State)
    for name, fn in [("parse", parse), ("text", text), ("tables", tables),
                     ("figures", figures), ("index", index)]:
        g.add_node(name, fn)
    g.add_edge(START, "parse")
    for branch in ("text", "tables", "figures"):
        g.add_edge("parse", branch)
    g.add_edge(["text", "tables", "figures"], "index")
    g.add_edge("index", END)
    return g.compile(checkpointer=checkpointer)

def main() -> None:
    DB.unlink(missing_ok=True)
    cfg = {"configurable": {"thread_id": "ingest-policy-2026-07"}}
    print(f"ledger (idempotent upserts): {IDEMPOTENT}")
    with SqliteSaver.from_conn_string(str(DB)) as saver:
        for run, payload in enumerate([{"chunks": []}, None, None], start=1):
            graph = build(saver)  # a fresh graph object, as after a restart
            before = dict(calls)
            try:
                out = graph.invoke(payload, cfg, durability="sync")
                status = f"done, indexed={out['indexed']}"
            except RuntimeError as e:
                status = f"FAILED ({e})"
            ran = sorted(n for n in calls if calls[n] != before.get(n, 0))
            nxt = graph.get_state(cfg).next
            print(f"run {run}: ran {ran} -> {status}; next={list(nxt)}")
        steps = sum(1 for _ in saver.list(cfg))
    con = sqlite3.connect(DB)
    total, distinct = con.execute(
        "SELECT COUNT(*), COUNT(DISTINCT body) FROM vectors").fetchone()
    con.close()
    print(f"checkpoints on thread: {steps}")
    print(f"vector rows: {total} (distinct chunks: {distinct})")
    print(f"node call counts: {dict(sorted(calls.items()))}")

if __name__ == "__main__":
    main()
```

### Expected output

```text
ledger (idempotent upserts): True
run 1: ran ['figures', 'parse', 'tables', 'text'] -> FAILED (figures: VLM caption call timed out); next=['figures']
run 2: ran ['figures', 'index'] -> FAILED (index: vector store connection reset mid-batch); next=['index']
run 3: ran ['index'] -> done, indexed=5; next=[]
checkpoints on thread: 5
vector rows: 5 (distinct chunks: 5)
node call counts: {'figures': 2, 'index': 2, 'parse': 1, 'tables': 1, 'text': 1}

# with --no-ledger, the last lines change to:
vector rows: 8 (distinct chunks: 5)
node call counts: {'figures': 2, 'index': 2, 'parse': 1, 'tables': 1, 'text': 1}
```

### Then do this

1. Explain aloud why `text` and `tables` ran once while `index` ran twice, and where the three extra rows come from in the `--no-ledger` run.
2. Add `for s in graph.get_state_history(cfg): print(s.metadata["step"], s.next)` after the loop and match each checkpoint to a super-step.
3. Stretch: replace the `raise` in `figures` with `os._exit(1)`, remove `DB.unlink`, and run each attempt as a separate process. Compare `durability="exit"` with `"sync"`.
4. Stretch: replace the fake `DOC` with real Docling output from one of your compliance-bot PDFs (`python -m pip install docling`; the first run downloads layout models). Map Docling's text, tables and pictures to the three branches, and keep page and bounding box in each chunk.

## Repos to look at

- [langchain-ai/langgraph](https://github.com/langchain-ai/langgraph). Read the checkpoint libraries under `libs/`, especially how the Postgres saver stores `checkpoint_writes`.
- [docling-project/docling](https://github.com/docling-project/docling). Compare the standard and VLM pipelines, and how provenance (page, bounding box) is kept on each element.
- [illuin-tech/colpali](https://github.com/illuin-tech/colpali). Read the model table and the deprecation note, which explains where multi-vector page retrieval is moving.

## Read if you have time

- [LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers). Read the durability modes, the super-step section and the "choose how to continue a thread" table.
- [Docling: vision models](https://docling-project.github.io/docling/usage/vision_models/). The preset table shows which VLMs run locally and on which devices.
- [ColPali: Efficient Document Retrieval with Vision Language Models](https://arxiv.org/abs/2407.01449) (Faysse et al.), which introduces the ViDoRe benchmark.

## Tomorrow

Day 42 revision drill: fine-tuning vs RAG with LoRA/QLoRA (Day 3) and model serving with vLLM (Day 23).

## Sources

- [LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)
- [LangGraph persistence and durable execution](https://docs.langchain.com/oss/python/langgraph/durable-execution)
- [Docling on GitHub](https://github.com/docling-project/docling) and [Docling vision models](https://docling-project.github.io/docling/usage/vision_models/)
- [IBM: Granite-Docling announcement](https://www.ibm.com/new/announcements/granite-docling-end-to-end-document-conversion)
- [ColPali Engine on GitHub](https://github.com/illuin-tech/colpali) and [ColPali paper (arXiv 2407.01449)](https://arxiv.org/abs/2407.01449)
- [Unstructured on GitHub](https://github.com/Unstructured-IO/unstructured)
- PyPI: [langgraph](https://pypi.org/project/langgraph/), [langgraph-checkpoint-sqlite](https://pypi.org/project/langgraph-checkpoint-sqlite/), [langgraph-checkpoint-postgres](https://pypi.org/project/langgraph-checkpoint-postgres/), [docling](https://pypi.org/project/docling/), [unstructured](https://pypi.org/project/unstructured/), [colpali-engine](https://pypi.org/project/colpali-engine/), [sentence-transformers](https://pypi.org/project/sentence-transformers/)

## Lab files in this folder

- [`lab/day41_lab.py`](lab/day41_lab.py)
