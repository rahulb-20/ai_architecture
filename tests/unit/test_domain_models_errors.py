import dataclasses

import pytest

from aiarch.domain import errors
from aiarch.domain.errors import (
    AiArchError,
    GuardrailViolation,
    PlannerOutputInvalid,
    ProviderError,
    RateLimited,
    StaleDocumentVersion,
)
from aiarch.domain.models import Answer, AnswerStatus, Chunk, Citation, Document, Principal, ScoredChunk, Usage


def test_principal_reads_a_document_when_any_group_matches():
    p = Principal("u", "acme", frozenset({"all", "hr"}))
    assert p.can_read(frozenset({"hr"})) and p.can_read(frozenset({"legal", "all"}))
    assert not p.can_read(frozenset({"legal"})) and not p.can_read(frozenset())


def test_principal_defaults_to_the_all_group_and_a_caller_with_no_groups_reads_nothing():
    assert Principal("u", "acme").groups == frozenset({"all"})
    assert not Principal("u", "acme", frozenset()).can_read(frozenset({"all"}))


def test_tenant_is_not_part_of_can_read_so_stores_must_partition_by_tenant():
    assert Principal("u", "globex").can_read(frozenset({"all"}))


@pytest.mark.parametrize("obj", [
    Principal("u", "t"), Document("d", "text", "t"), Citation("d", "c", 1),
    Chunk("c", "d", "text", "t", frozenset({"all"}), 1, 0),
    ScoredChunk(Chunk("c", "d", "text", "t", frozenset({"all"}), 1, 0), 0.5, "dense"),
])
def test_identity_and_content_types_are_immutable(obj):
    field = dataclasses.fields(obj)[0].name
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(obj, field, "changed")


def test_document_defaults_are_public_version_one_with_its_own_metadata():
    a, b = Document("a", "x", "t"), Document("b", "y", "t")
    assert (a.acl, a.version, a.metadata) == (frozenset({"all"}), 1, {})
    a.metadata["k"] = 1
    assert b.metadata == {}


def test_usage_adds_field_by_field_without_changing_its_operands():
    a, b = Usage(10, 2, 4), Usage(1, 1, 1)
    assert a + b == Usage(11, 3, 5) and a == Usage(10, 2, 4) and Usage() + Usage() == Usage(0, 0, 0)


def test_answer_defaults_are_empty_and_not_shared():
    a, b = Answer(AnswerStatus.ABSTAINED, "no"), Answer(AnswerStatus.ABSTAINED, "no")
    assert (a.citations, a.model, a.prompt_version, a.usage, a.cost_usd, a.cache_hit, a.reasons) == \
           ([], None, None, Usage(), 0.0, False, [])
    a.reasons.append("x")
    a.citations.append(Citation("d", "c", 1))
    assert b.reasons == [] and b.citations == []


def test_answer_status_values_are_the_strings_the_api_returns():
    assert [s.value for s in AnswerStatus] == ["answered", "abstained", "blocked", "degraded"]
    assert AnswerStatus("degraded") is AnswerStatus.DEGRADED and AnswerStatus.ANSWERED == "answered"


def test_every_error_derives_from_the_one_base_class():
    classes = [c for c in vars(errors).values() if isinstance(c, type) and issubclass(c, Exception)]
    assert len(classes) >= 9 and all(issubclass(c, AiArchError) for c in classes)


def test_provider_errors_carry_what_the_gateway_branches_on():
    e = ProviderError("down", provider="p1", retryable=False)
    assert (str(e), e.provider, e.retryable) == ("down", "p1", False)
    r = RateLimited("slow", provider="p2", retry_after_s=3.0)
    assert isinstance(r, ProviderError) and (r.retryable, r.retry_after_s, r.provider) == (True, 3.0, "p2")
    assert RateLimited("slow", provider="p").retry_after_s is None


def test_guardrail_violation_message_includes_the_detail_only_when_given():
    assert str(GuardrailViolation("uncited_answer")) == "uncited_answer"
    v = GuardrailViolation("input_too_long", "9 > 5")
    assert (v.rule, str(v)) == ("input_too_long", "input_too_long: 9 > 5")


def test_stale_version_and_planner_errors_expose_their_fields():
    s = StaleDocumentVersion("doc-1", current=5, attempted=4)
    assert (s.doc_id, s.current, s.attempted) == ("doc-1", 5, 4)
    assert "version 4 is older than indexed version 5" in str(s)
    p = PlannerOutputInvalid("malformed")
    assert p.reason == "malformed" and str(p) == "malformed"
