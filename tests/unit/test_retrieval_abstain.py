import pytest

from aiarch.adapters.llm.fake import FakeLLM
from aiarch.adapters.telemetry.tracer import JsonTracer
from aiarch.config import Settings
from aiarch.container import build_container
from aiarch.domain.models import AnswerStatus
from aiarch.services.retrieval import BM25, content_terms, term_coverage

OUT_OF_SCOPE = [
    "What is the cafeteria lunch menu on Friday?",
    "Who won the cricket world cup in 2011?",
    "What is the capital of France?",
    "How do I book a meeting room?",
]
IN_SCOPE = [
    "How many annual leave days can be carried forward?",
    "How often do VPN passwords expire?",
    "Within how many days must travel expense claims be submitted?",
    "vpn",
]


@pytest.mark.parametrize("question", OUT_OF_SCOPE)
def test_out_of_scope_abstains_at_retrieval(container, alice, question):
    assert container.rag.retriever.retrieve(question, alice) == []


@pytest.mark.parametrize("question", IN_SCOPE)
def test_in_scope_still_retrieves(container, alice, question):
    assert container.rag.retriever.retrieve(question, alice)


def test_out_of_scope_never_reaches_the_model():
    llm = FakeLLM()
    c = build_container(Settings(), tracer=JsonTracer(sink=[]), llm_clients=[llm])
    from aiarch.domain.models import Principal
    a = c.rag.ask(OUT_OF_SCOPE[0], Principal("u", "acme"))
    assert a.status is AnswerStatus.ABSTAINED and a.reasons == ["no_relevant_context"]
    assert llm.calls == 0 and a.cost_usd == 0.0


def test_stopwords_do_not_count_as_a_sparse_match(container, alice):
    assert content_terms("What is the policy on this?") == ["policy"]
    chunks = container.store.all_chunks(alice)
    assert BM25(chunks).search("what is the of on", 5) == []


def test_term_coverage_is_share_of_content_words(container, alice):
    hits = BM25(container.store.all_chunks(alice)).search("VPN passwords expire zebra", 5)
    assert term_coverage("VPN passwords expire zebra", hits) == pytest.approx(0.75)
    assert term_coverage("the of and", hits) == 0.0


def test_abstention_is_visible_in_the_trace(container, alice, spans):
    container.rag.retriever.retrieve(OUT_OF_SCOPE[1], alice)
    sp = next(s for s in spans if s["name"] == "retrieval.hybrid")
    assert sp["attributes"]["retrieval.abstained"] is True and sp["attributes"]["retrieval.term_coverage"] < 0.6
