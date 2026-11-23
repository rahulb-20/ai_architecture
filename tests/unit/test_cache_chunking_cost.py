from aiarch.adapters.cache.semantic import SemanticCache
from aiarch.adapters.embeddings.hashing import HashingEmbedder
from aiarch.domain.models import Document
from aiarch.services.chunking import chunk_document
from aiarch.services.cost import ModelPrice, Workload, monthly_estimate, sensitivity


def test_cache_is_scoped_and_invalidated_by_doc():
    c = SemanticCache(HashingEmbedder(), threshold=0.9)
    c.put("how do I reset my vpn password", "acme|all", "ANSWER", ["it-vpn"])
    assert c.get("how do I reset my vpn password", "acme|all") is not None
    assert c.get("how do I reset my vpn password", "acme|all,hr") is None     # other scope never hits
    assert c.invalidate_doc("it-vpn") == 1
    assert c.get("how do I reset my vpn password", "acme|all") is None


def test_cache_threshold_rejects_different_question():
    c = SemanticCache(HashingEmbedder(), threshold=0.92)
    c.put("how many sick leave days per year", "s", "12", ["hr"])
    assert c.get("what is the per diem for metro cities", "s") is None


def test_markdown_chunking_keeps_section_and_ids():
    doc = Document("d1", "# T\n## A\n" + "word " * 400 + "\n## B\nshort", "acme", version=3)
    chunks = chunk_document(doc, max_tokens=100, overlap_tokens=10)
    assert len(chunks) >= 4
    assert {c.metadata["section"] for c in chunks} == {"A", "B"}
    assert all(c.chunk_id.startswith("d1:v3:") for c in chunks)


def test_cost_model_caching_reduces_cost_and_calls_dominate():
    p = ModelPrice(3.0, 15.0)
    base = monthly_estimate(Workload(), p)["cost_usd"]
    cached = monthly_estimate(Workload(prefix_cache_hit_rate=0.9), p)["cost_usd"]
    assert cached < base
    s = sensitivity(Workload(), p)
    assert abs(s["llm_calls_per_query"] - 0.2) < 1e-9
    assert s["llm_calls_per_query"] > s["output_tokens"]
