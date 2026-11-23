"""Chunking strategies behind one function signature, so they can be A/B-measured.

`recursive` splits on structure first (headings, paragraphs, sentences) and only
falls back to hard cuts, with overlap. `markdown` keeps the heading path as chunk
metadata (useful for citations and for parent-child retrieval).
"""
from __future__ import annotations

import re
from collections.abc import Callable

from aiarch.domain.models import Chunk, Document

Tokenizer = Callable[[str], int]


def approx_tokens(text: str) -> int:
    """~4 chars/token for English. Replace with tiktoken or the model's tokenizer in cost-critical paths."""
    return max(1, len(text) // 4)


_SEPARATORS = ["\n## ", "\n### ", "\n\n", "\n", ". ", " "]


def _recursive_split(text: str, max_tokens: int, count: Tokenizer, seps: list[str]) -> list[str]:
    if count(text) <= max_tokens:
        return [text]
    if not seps:
        step = max_tokens * 4
        return [text[i : i + step] for i in range(0, len(text), step)]
    sep, rest = seps[0], seps[1:]
    parts = text.split(sep)
    out: list[str] = []
    buf = ""
    for i, part in enumerate(parts):
        piece = (sep if i > 0 and sep.strip() == "" else (sep.lstrip("\n") if i > 0 else "")) + part
        if count(buf + piece) <= max_tokens:
            buf += piece
            continue
        if buf:
            out.append(buf)
        if count(piece) > max_tokens:
            out.extend(_recursive_split(piece, max_tokens, count, rest))
            buf = ""
        else:
            buf = piece
    if buf:
        out.append(buf)
    return [p.strip() for p in out if p.strip()]


def chunk_document(doc: Document, *, max_tokens: int = 220, overlap_tokens: int = 30,
                   strategy: str = "markdown", count: Tokenizer = approx_tokens) -> list[Chunk]:
    sections: list[tuple[str, str]]
    if strategy == "markdown":
        sections = []
        heading = doc.metadata.get("title", doc.doc_id)
        buf: list[str] = []
        for line in doc.text.splitlines():
            m = re.match(r"^(#{1,6})\s+(.*)", line)
            if m:
                if buf:
                    sections.append((heading, "\n".join(buf)))
                heading, buf = m.group(2).strip(), []
            else:
                buf.append(line)
        if buf:
            sections.append((heading, "\n".join(buf)))
    elif strategy == "recursive":
        sections = [(doc.metadata.get("title", doc.doc_id), doc.text)]
    else:
        raise ValueError(f"unknown chunking strategy: {strategy}")

    chunks: list[Chunk] = []
    pos = 0
    for heading, body in sections:
        pieces = _recursive_split(body.strip(), max_tokens, count, _SEPARATORS)
        prev_tail = ""
        for piece in pieces:
            text = (prev_tail + " " + piece).strip() if prev_tail else piece
            chunks.append(Chunk(
                chunk_id=f"{doc.doc_id}:v{doc.version}:{pos}", doc_id=doc.doc_id, text=text,
                tenant_id=doc.tenant_id, acl=doc.acl, doc_version=doc.version, position=pos,
                metadata={**doc.metadata, "section": heading},
            ))
            words = piece.split()
            prev_tail = " ".join(words[-max(1, overlap_tokens * 3 // 4):]) if overlap_tokens else ""
            pos += 1
    return chunks
