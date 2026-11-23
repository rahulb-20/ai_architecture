from aiarch.adapters.embeddings.hashing import HashingEmbedder
from aiarch.adapters.telemetry.tracer import JsonTracer
from aiarch.adapters.vectorstore.memory import InMemoryVectorStore
from aiarch.domain.models import Chunk, Principal, ScoredChunk
from aiarch.services.retrieval import BM25, HybridRetriever, rrf


def _chunk(cid, text, tenant="acme", acl=("all",)):
    return Chunk(cid, cid.split(":")[0], text, tenant, frozenset(acl), 1, 0)


def _store(chunks):
    emb = HashingEmbedder()
    s = InMemoryVectorStore()
    s.upsert(chunks, emb.embed([c.text for c in chunks]))
    return s, emb


def test_acl_and_tenant_prefilter_never_leak():
    chunks = [_chunk("pub:0", "salary bands overview"), _chunk("hr:0", "salary bands B5 midpoint", acl=("hr",)),
              _chunk("other:0", "salary bands for globex", tenant="globex")]
    store, emb = _store(chunks)
    q = emb.embed(["salary bands"])[0]
    ids = {h.chunk.chunk_id for h in store.search(q, Principal("u", "acme"), k=10)}
    assert ids == {"pub:0"}
    ids_hr = {h.chunk.chunk_id for h in store.search(q, Principal("u", "acme", frozenset({"hr"})), k=10)}
    assert ids_hr == {"hr:0"}


def test_bm25_finds_exact_identifier():
    chunks = [_chunk("a:0", "error code E-4021 means token expired"), _chunk("b:0", "general login help")]
    assert BM25(chunks).search("E-4021", 1)[0].chunk.chunk_id == "a:0"


def test_rrf_rewards_agreement():
    a, b, c = (_chunk(f"{x}:0", x) for x in "abc")
    fused = rrf([[ScoredChunk(a, 1, "dense"), ScoredChunk(b, 1, "dense")],
                 [ScoredChunk(b, 1, "sparse"), ScoredChunk(c, 1, "sparse")]])
    assert fused[0].chunk.chunk_id == "b:0"


def test_retriever_abstains_on_irrelevant_query():
    store, emb = _store([_chunk("a:0", "vpn password reset steps")])
    r = HybridRetriever(store, emb, JsonTracer(sink=[]))
    assert r.retrieve("zzqx", Principal("u", "acme")) == []


def test_delete_doc_removes_chunks():
    store, _ = _store([_chunk("a:0", "x y z"), _chunk("b:0", "x y z")])
    assert store.delete_doc("acme", "a") == 1
    assert [c.doc_id for c in store.all_chunks(Principal("u", "acme"))] == ["b"]
