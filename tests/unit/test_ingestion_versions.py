import pytest
from fastapi.testclient import TestClient

from aiarch.api.app import create_app
from aiarch.domain.errors import StaleDocumentVersion
from aiarch.domain.models import Document


def _doc(version: int, value: str, doc_id: str = "d1") -> Document:
    return Document(doc_id, f"# D\nThe retention period is {value} days.", "acme", version=version)


def _versions(container, principal, doc_id: str = "d1") -> set[int]:
    return {c.doc_version for c in container.store.all_chunks(principal) if c.doc_id == doc_id}


def test_older_version_is_rejected_and_index_is_untouched(container, alice):
    container.ingestion.ingest(_doc(2, "90"))
    with pytest.raises(StaleDocumentVersion) as e:
        container.ingestion.ingest(_doc(1, "30"))
    assert (e.value.current, e.value.attempted) == (2, 1)
    assert _versions(container, alice) == {2}
    assert "90" in container.rag.ask("What is the retention period in days?", alice).text


def test_same_version_reingest_is_idempotent(container, alice):
    first = container.ingestion.ingest(_doc(3, "90"))
    assert container.ingestion.ingest(_doc(3, "90")) == first
    assert len([c for c in container.store.all_chunks(alice) if c.doc_id == "d1"]) == first


def test_newer_version_replaces_every_old_chunk(container, alice):
    container.ingestion.ingest(_doc(1, "30"))
    container.ingestion.ingest(_doc(2, "90"))
    assert _versions(container, alice) == {2}
    assert container.store.doc_version("acme", "d1") == 2
    assert container.store.doc_version("acme", "missing") is None


def test_replace_doc_only_touches_its_own_document(container, alice):
    before = {c.chunk_id for c in container.store.all_chunks(alice) if c.doc_id != "d1"}
    container.ingestion.ingest(_doc(1, "30"))
    container.ingestion.ingest(_doc(2, "90"))
    assert {c.chunk_id for c in container.store.all_chunks(alice) if c.doc_id != "d1"} == before
    with pytest.raises(ValueError):
        chunk = next(c for c in container.store.all_chunks(alice) if c.doc_id == "d1")
        container.store.replace_doc("acme", "other-doc", [chunk], [[0.0] * container.embedder.dim])


def test_api_returns_409_for_stale_version(container):
    admin = {"x-groups": "kb-admin"}
    with TestClient(create_app(container)) as client:
        assert client.post("/v1/documents", headers=admin,
                           json={"doc_id": "d1", "text": "# D\nnew", "version": 5}).status_code == 201
        r = client.post("/v1/documents", headers=admin, json={"doc_id": "d1", "text": "# D\nold", "version": 4})
        assert r.status_code == 409
        assert r.json()["detail"] == {"error": "stale_version", "indexed_version": 5, "attempted_version": 4}
