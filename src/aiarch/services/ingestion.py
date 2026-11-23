"""Ingestion use case: document -> chunks -> embeddings -> store, with cache invalidation.

Re-ingesting a document with a higher version replaces its chunks atomically per
document and invalidates every cached answer grounded on it.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from aiarch.domain.models import Document
from aiarch.ports import AnswerCache, Embedder, Tracer, VectorStore, iter_batches
from aiarch.services.chunking import chunk_document


@dataclass
class IngestionService:
    store: VectorStore
    embedder: Embedder
    cache: AnswerCache
    tracer: Tracer
    chunk_tokens: int = 220
    chunk_overlap: int = 30
    strategy: str = "markdown"
    batch_size: int = 64

    def ingest(self, doc: Document) -> int:
        with self.tracer.span("ingest.document", **{"doc.id": doc.doc_id, "doc.version": doc.version}) as sp:
            chunks = chunk_document(doc, max_tokens=self.chunk_tokens,
                                    overlap_tokens=self.chunk_overlap, strategy=self.strategy)
            self.store.delete_doc(doc.tenant_id, doc.doc_id)
            for batch in iter_batches(chunks, self.batch_size):
                self.store.upsert(batch, self.embedder.embed([c.text for c in batch]))
            invalidated = self.cache.invalidate_doc(doc.doc_id)
            sp.set("ingest.chunks", len(chunks))
            sp.set("cache.invalidated", invalidated)
            return len(chunks)


def load_markdown_folder(folder: str | Path, tenant_id: str) -> list[Document]:
    """Front-matter-lite loader: a first line like `<!-- acl: hr,all -->` sets the ACL."""
    docs = []
    for p in sorted(Path(folder).glob("*.md")):
        text = p.read_text(encoding="utf-8")
        acl = frozenset({"all"})
        first = text.splitlines()[0] if text else ""
        if first.startswith("<!-- acl:"):
            acl = frozenset(a.strip() for a in first[9:].rstrip("-> ").split(",") if a.strip())
            text = text.split("\n", 1)[1] if "\n" in text else ""
        title = next((ln[2:].strip() for ln in text.splitlines() if ln.startswith("# ")), p.stem)
        docs.append(Document(doc_id=p.stem, text=text, tenant_id=tenant_id, acl=acl,
                             metadata={"title": title, "source": p.name}))
    return docs
