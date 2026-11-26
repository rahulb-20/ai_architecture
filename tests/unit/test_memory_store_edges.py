import pytest

from aiarch.adapters.vectorstore.memory import InMemoryVectorStore
from aiarch.domain.models import Chunk, Principal

E1, E2, E3 = [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]
ALICE = Principal("alice", "acme")
HR = Principal("hema", "acme", frozenset({"all", "hr"}))


def _chunk(doc: str, pos: int = 0, *, tenant: str = "acme", acl=("all",), version: int = 1, text: str = "t") -> Chunk:
    return Chunk(f"{doc}:v{version}:{pos}", doc, text, tenant, frozenset(acl), version, pos)


def test_empty_store_answers_every_read_with_nothing():
    s = InMemoryVectorStore()
    assert s.search(E1, ALICE, 5) == [] and s.all_chunks(ALICE) == [] and s.acl_index() == {}
    assert s.delete_doc("acme", "d") == 0 and s.doc_version("acme", "d") is None


def test_upsert_rejects_mismatched_lengths_and_ignores_an_empty_batch():
    s = InMemoryVectorStore()
    with pytest.raises(ValueError, match="length mismatch"):
        s.upsert([_chunk("a")], [E1, E2])
    s.upsert([], [])
    assert s.all_chunks(ALICE) == []


def test_search_ranks_by_similarity_and_labels_the_source():
    s = InMemoryVectorStore()
    s.upsert([_chunk("a"), _chunk("b"), _chunk("c")], [E1, E2, [0.6, 0.8, 0.0]])
    hits = s.search(E1, ALICE, 3)
    assert [h.chunk.doc_id for h in hits] == ["a", "c", "b"]
    assert [round(h.score, 2) for h in hits] == [1.0, 0.6, 0.0] and {h.source for h in hits} == {"dense"}


def test_k_limits_results_and_never_pads_with_denied_chunks():
    s = InMemoryVectorStore()
    s.upsert([_chunk("a"), _chunk("b"), _chunk("secret", acl=("hr",))], [E2, E3, E1])
    assert s.search(E1, ALICE, 0) == []
    assert len(s.search(E1, ALICE, 1)) == 1
    assert {h.chunk.doc_id for h in s.search(E1, ALICE, 50)} == {"a", "b"}


def test_most_similar_chunk_is_never_returned_to_a_caller_outside_its_acl():
    s = InMemoryVectorStore()
    s.upsert([_chunk("secret", acl=("hr",)), _chunk("public")], [E1, E2])
    assert [h.chunk.doc_id for h in s.search(E1, ALICE, 5)] == ["public"]
    assert [h.chunk.doc_id for h in s.search(E1, HR, 5)] == ["secret", "public"]


def test_caller_with_no_readable_chunks_gets_nothing():
    s = InMemoryVectorStore()
    s.upsert([_chunk("secret", acl=("hr",))], [E1])
    assert s.search(E1, ALICE, 5) == [] and s.all_chunks(ALICE) == []


def test_tenants_are_separate_even_in_one_upsert_call():
    s = InMemoryVectorStore()
    s.upsert([_chunk("a"), _chunk("a", tenant="globex", text="globex text")], [E1, E1])
    globex = Principal("g", "globex")
    assert [c.tenant_id for c in s.all_chunks(ALICE)] == ["acme"]
    assert [h.chunk.text for h in s.search(E1, globex, 5)] == ["globex text"]
    assert s.delete_doc("globex", "a") == 1 and len(s.all_chunks(ALICE)) == 1


def test_upserting_the_same_chunk_id_replaces_it():
    s = InMemoryVectorStore()
    s.upsert([_chunk("a", text="old")], [E1])
    s.upsert([_chunk("a", text="new")], [E2])
    assert [c.text for c in s.all_chunks(ALICE)] == ["new"]
    assert s.search(E2, ALICE, 1)[0].score == pytest.approx(1.0)


def test_delete_doc_removes_only_that_document_and_reports_the_count():
    s = InMemoryVectorStore()
    s.upsert([_chunk("a", 0), _chunk("a", 1), _chunk("b")], [E1, E2, E3])
    assert s.delete_doc("acme", "a") == 2 and s.delete_doc("acme", "a") == 0
    assert [h.chunk.doc_id for h in s.search(E3, ALICE, 5)] == ["b"]


def test_replace_doc_swaps_versions_and_an_empty_replacement_deletes():
    s = InMemoryVectorStore()
    s.upsert([_chunk("a", 0), _chunk("a", 1), _chunk("b")], [E1, E2, E3])
    assert s.replace_doc("acme", "a", [_chunk("a", 0, version=2)], [E1]) == 2
    assert s.doc_version("acme", "a") == 2 and len(s.all_chunks(ALICE)) == 2
    assert s.replace_doc("acme", "a", [], []) == 1
    assert s.doc_version("acme", "a") is None and [c.doc_id for c in s.all_chunks(ALICE)] == ["b"]


def test_replace_doc_refuses_chunks_of_another_tenant_or_document():
    s = InMemoryVectorStore()
    for bad in (_chunk("other"), _chunk("a", tenant="globex")):
        with pytest.raises(ValueError, match="must belong"):
            s.replace_doc("acme", "a", [bad], [E1])
    assert s.all_chunks(ALICE) == []


def test_acl_index_maps_each_document_to_its_groups():
    s = InMemoryVectorStore()
    s.upsert([_chunk("pub"), _chunk("sec", acl=("hr", "legal"))], [E1, E2])
    assert s.acl_index() == {"pub": frozenset({"all"}), "sec": frozenset({"hr", "legal"})}
