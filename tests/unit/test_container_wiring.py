"""The container is the only place settings become objects. Each setting must reach the part it controls."""
import sys

import pytest

from aiarch.adapters.cache.semantic import NullCache, SemanticCache
from aiarch.adapters.embeddings.hashing import HashingEmbedder
from aiarch.adapters.llm.fake import FakeLLM
from aiarch.adapters.llm.openai_compatible import OpenAICompatibleLLM
from aiarch.adapters.telemetry.tracer import JsonTracer
from aiarch.config import LLMRouteConfig, Settings
from aiarch.container import build_container, build_embedder, build_llm
from aiarch.domain.errors import ToolNotAllowed
from aiarch.domain.models import AnswerStatus, Principal
from aiarch.ports import AnswerCache, Embedder, LLMClient, Tracer, VectorStore

VPN = "How often do VPN passwords expire?"


def _build(**settings):
    return build_container(Settings(**settings), tracer=JsonTracer(sink=[]))


# --- build_llm -------------------------------------------------------------------

def test_fake_route_builds_the_offline_model():
    llm = build_llm(LLMRouteConfig(name="local", kind="fake"))
    assert isinstance(llm, FakeLLM) and llm.name == "local"


def test_openai_compatible_route_reads_its_key_from_the_named_variable(monkeypatch):
    monkeypatch.setenv("TEST_PROVIDER_KEY", "sk-test-123")
    llm = build_llm(LLMRouteConfig(name="p", base_url="http://localhost:11434/v1", model="m1",
                                   api_key_env="TEST_PROVIDER_KEY", timeout_s=7.0))
    assert isinstance(llm, OpenAICompatibleLLM) and (llm.name, llm.model) == ("p", "m1")
    assert llm._client.headers["authorization"] == "Bearer sk-test-123"
    assert llm._client.timeout.read == 7.0 and llm._client.timeout.connect == 5.0


def test_route_without_a_key_sends_no_authorization_header(monkeypatch):
    monkeypatch.delenv("MISSING_KEY", raising=False)
    for cfg in (LLMRouteConfig(name="p", base_url="http://x/v1", model="m"),
                LLMRouteConfig(name="p", base_url="http://x/v1", model="m", api_key_env="MISSING_KEY")):
        assert "authorization" not in build_llm(cfg)._client.headers


@pytest.mark.parametrize("cfg", [LLMRouteConfig(name="p", model="m"), LLMRouteConfig(name="p", base_url="http://x/v1")])
def test_openai_compatible_route_needs_base_url_and_model(cfg):
    with pytest.raises(ValueError, match="base_url and model are required"):
        build_llm(cfg)


def test_unknown_route_kind_fails_at_start_up():
    with pytest.raises(ValueError, match="unknown LLM kind: bedrock"):
        build_llm(LLMRouteConfig(name="p", kind="bedrock"))


def test_route_config_has_no_field_that_could_hold_a_secret():
    assert "api_key" not in LLMRouteConfig.model_fields and "api_key_env" in LLMRouteConfig.model_fields


# --- build_embedder --------------------------------------------------------------

def test_default_embedder_is_offline():
    assert isinstance(build_embedder(Settings()), HashingEmbedder)


def test_optional_embedder_without_its_extra_fails_with_import_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)   # as if the extra is not installed
    with pytest.raises(ImportError):
        build_embedder(Settings(embedder="sentence-transformers"))


# --- build_container -------------------------------------------------------------

def test_every_part_satisfies_its_port(container):
    assert isinstance(container.embedder, Embedder) and isinstance(container.store, VectorStore)
    assert isinstance(container.cache, AnswerCache) and isinstance(container.tracer, Tracer)
    assert all(isinstance(r.client, LLMClient) for r in container.gateway.routes)


def test_default_container_is_seeded_and_answers_offline(container, alice):
    assert {c.doc_id for c in container.store.all_chunks(alice)} >= {"it-vpn-access", "hr-leave-policy",
        "finance-travel"}
    assert container.rag.ask(VPN, alice).status is AnswerStatus.ANSWERED


def test_seed_false_leaves_the_store_empty(alice):
    c = build_container(Settings(), tracer=JsonTracer(sink=[]), seed=False)
    assert c.store.all_chunks(alice) == []
    assert c.rag.ask(VPN, alice).reasons == ["no_relevant_context"]


def test_no_seed_folder_leaves_the_store_empty(alice, tmp_path):
    assert _build(seed_folder=None).store.all_chunks(alice) == []
    assert _build(seed_folder=tmp_path / "does-not-exist").store.all_chunks(alice) == []


def test_seed_tenant_decides_who_owns_the_sample_documents(alice):
    c = _build(seed_tenant="globex")
    assert c.store.all_chunks(alice) == []
    assert c.store.all_chunks(Principal("g", "globex"))


def test_semantic_cache_setting_switches_the_cache_off(alice):
    on, off = _build(), _build(semantic_cache=False)
    assert isinstance(on.cache, SemanticCache) and isinstance(off.cache, NullCache)
    off.rag.ask(VPN, alice)
    assert off.rag.ask(VPN, alice).cache_hit is False
    on.rag.ask(VPN, alice)
    assert on.rag.ask(VPN, alice).cache_hit is True


def test_cache_threshold_and_ttl_reach_the_cache():
    c = _build(cache_threshold=0.5, cache_ttl_s=60)
    assert (c.cache.threshold, c.cache.ttl_s) == (0.5, 60)


def test_retry_count_comes_from_the_route_of_the_same_name():
    c = _build(llm_routes=[LLMRouteConfig(name="a", kind="fake", max_retries=4), LLMRouteConfig(name="b",
        kind="fake", max_retries=0)])
    assert [(r.client.name, r.max_retries) for r in c.gateway.routes] == [("a", 4), ("b", 0)]


def test_injected_clients_replace_the_configured_routes_in_order():
    clients = [FakeLLM(name="one"), FakeLLM(name="two")]
    c = build_container(Settings(), tracer=JsonTracer(sink=[]), llm_clients=clients)
    assert [r.client for r in c.gateway.routes] == clients
    assert [r.max_retries for r in c.gateway.routes] == [1, 1]


def test_retrieval_and_budget_settings_reach_their_services(alice):
    c = _build(k_final=2, max_input_tokens=777, min_dense_score=0.4, min_term_coverage=0.9)
    assert len(c.rag.retriever.retrieve(VPN, alice)) == 2
    assert c.gateway.max_input_tokens == 777
    assert (c.rag.retriever.cfg.min_dense_score, c.rag.retriever.cfg.min_term_coverage) == (0.4, 0.9)


def test_chunk_size_setting_changes_how_documents_are_split(alice):
    coarse, fine = _build(), _build(chunk_tokens=30, chunk_overlap=0)
    assert len(fine.store.all_chunks(alice)) > len(coarse.store.all_chunks(alice))


@pytest.mark.parametrize("flag", [True, False])
def test_output_pii_switch_reaches_the_output_guard(flag):
    assert _build(output_keep_source_pii=flag).rag.output_guard.keep_source_values is flag


def test_agent_shares_the_input_guard_and_has_exactly_three_tools(container):
    assert container.agent.input_guard is container.rag.input_guard
    assert set(container.agent.registry.tools) == {"calculator", "create_ticket", "search_kb"}
    assert [t.name for t in container.agent.registry.tools.values() if t.side_effects] == ["create_ticket"]


def test_search_tool_returns_at_most_three_short_results_the_caller_may_read(container, alice, hr_user):
    out = container.agent.registry.call("search_kb", {"query": "salary band midpoint"}, alice)
    assert len(out["results"]) <= 3 and all(len(r["text"]) <= 300 for r in out["results"])
    assert "hr-compensation-bands" not in {r["doc_id"] for r in out["results"]}
    hr = container.agent.registry.call("search_kb", {"query": "salary band midpoint"}, hr_user)
    assert "hr-compensation-bands" in {r["doc_id"] for r in hr["results"]}


def test_ticket_tool_writes_to_the_container_ticket_list(container, alice):
    out = container.agent.registry.call("create_ticket", {"title": "Laptop", "body": "Broken screen"}, alice)
    assert out == {"ticket_id": "TCK-0001"}
    assert container.tickets == [{"id": "TCK-0001", "by": "alice", "title": "Laptop", "body": "Broken screen"}]


def test_containers_do_not_share_state(alice):
    a, b = _build(), _build()
    a.agent.registry.call("create_ticket", {"title": "t", "body": "b"}, alice)
    a.rag.ask(VPN, alice)
    assert b.tickets == [] and b.rag.ask(VPN, alice).cache_hit is False
    with pytest.raises(ToolNotAllowed):
        b.agent.registry.call("no_such_tool", {}, alice)
