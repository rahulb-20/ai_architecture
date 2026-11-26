import math

import pytest

from aiarch.adapters.embeddings.hashing import HashingEmbedder
from aiarch.ports import Embedder


def _cos(a, b) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_it_is_an_embedder_with_the_declared_dimension():
    e = HashingEmbedder(dim=64)
    assert isinstance(e, Embedder) and e.name == "hashing-64"
    assert [len(v) for v in e.embed(["one", "two words", ""])] == [64, 64, 64]


def test_vectors_are_unit_length_and_deterministic():
    e = HashingEmbedder()
    text = "VPN passwords expire every 90 days"
    a, b = e.embed([text]), HashingEmbedder().embed([text])
    assert a == b and math.sqrt(sum(x * x for x in a[0])) == pytest.approx(1.0)


def test_empty_and_punctuation_only_text_embed_to_the_zero_vector():
    for v in HashingEmbedder(dim=16).embed(["", "   ", "?!..."]):
        assert v == [0.0] * 16


def test_case_and_punctuation_do_not_change_the_vector():
    e = HashingEmbedder()
    assert e.embed(["VPN Password!"]) == e.embed(["vpn password"])


def test_related_text_is_closer_than_unrelated_text():
    e = HashingEmbedder()
    q, near, far = e.embed(["How often do VPN passwords expire?", "VPN passwords expire every 90 days.",
                            "The domestic per diem is INR 3,500 per day."])
    assert _cos(q, near) > _cos(q, far) + 0.2


def test_shared_character_trigrams_give_partial_credit_for_word_variants():
    e = HashingEmbedder()
    a, b, c = e.embed(["password", "passwords", "umbrella"])
    assert _cos(a, b) > 0.5 > _cos(a, c)


def test_batch_and_single_calls_agree():
    e = HashingEmbedder()
    texts = ["alpha beta", "gamma"]
    assert e.embed(texts) == [e.embed([t])[0] for t in texts]
    assert e.embed([]) == []
