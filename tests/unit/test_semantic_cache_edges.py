import pytest

from aiarch.adapters.cache import semantic
from aiarch.adapters.cache.semantic import NullCache, SemanticCache
from aiarch.adapters.embeddings.hashing import HashingEmbedder
from aiarch.ports import AnswerCache

Q = "How often do VPN passwords expire?"


@pytest.fixture
def clock(monkeypatch):
    now = {"t": 1_000.0}
    monkeypatch.setattr(semantic.time, "time", lambda: now["t"])
    return now


def _cache(**kw) -> SemanticCache:
    return SemanticCache(HashingEmbedder(), **kw)


def test_both_caches_satisfy_the_port():
    assert isinstance(_cache(), AnswerCache) and isinstance(NullCache(), AnswerCache)


def test_identical_query_is_a_hit_with_similarity_one():
    c = _cache()
    c.put(Q, "s", "answer", ["d1"])
    value, sim = c.get(Q, "s")
    assert value == "answer" and sim == pytest.approx(1.0, abs=1e-5) and isinstance(sim, float)


def test_lookup_in_an_empty_or_unknown_scope_is_a_miss():
    c = _cache()
    assert c.get(Q, "never-used") is None
    c.put(Q, "tenant-a|all", "a", ["d"])
    assert c.get(Q, "tenant-b|all") is None and c.get(Q, "tenant-a|all,hr") is None


def test_best_of_several_entries_is_returned():
    c = _cache(threshold=0.5)
    c.put("How do I reset my VPN password?", "s", "reset", ["d"])
    c.put(Q, "s", "expiry", ["d"])
    c.put("What is the travel per diem?", "s", "per diem", ["d"])
    assert c.get(Q, "s")[0] == "expiry"


def test_entry_expires_after_its_ttl_and_stays_gone(clock):
    c = _cache(ttl_s=10)
    c.put(Q, "s", "v", ["d"])
    clock["t"] += 9.9
    assert c.get(Q, "s") is not None
    clock["t"] += 0.2
    assert c.get(Q, "s") is None
    clock["t"] -= 5          # the expired entry was purged, not merely hidden
    assert c.get(Q, "s") is None


def test_oldest_entries_are_evicted_per_scope():
    c = _cache(max_entries_per_scope=2)
    questions = ["What is the leave policy?", "What is the travel per diem?", Q]
    for i, q in enumerate(questions):
        c.put(q, "s", i, ["d"])
    c.put(questions[0], "other-scope", "kept", ["d"])
    assert c.get(questions[0], "s") is None
    assert c.get(questions[1], "s")[0] == 1 and c.get(questions[2], "s")[0] == 2
    assert c.get(questions[0], "other-scope")[0] == "kept"


def test_invalidate_doc_removes_every_entry_grounded_on_it_in_every_scope():
    c = _cache()
    c.put(Q, "a", 1, ["vpn", "leave"])
    c.put(Q, "b", 2, ["vpn"])
    c.put("What is the travel per diem?", "a", 3, ["travel"])
    assert c.invalidate_doc("vpn") == 2 and c.invalidate_doc("vpn") == 0
    assert c.get(Q, "a") is None and c.get(Q, "b") is None
    assert c.get("What is the travel per diem?", "a")[0] == 3


def test_entry_with_no_documents_is_untouched_by_invalidation():
    c = _cache()
    c.put(Q, "s", "v", [])
    assert c.invalidate_doc("anything") == 0 and c.get(Q, "s") is not None


def test_null_cache_stores_nothing():
    c = NullCache()
    c.put(Q, "s", "v", ["d"])
    assert c.get(Q, "s") is None and c.invalidate_doc("d") == 0
