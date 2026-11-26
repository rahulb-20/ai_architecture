import pytest

from aiarch.domain.models import Document
from aiarch.services.chunking import approx_tokens, chunk_document


def _doc(text: str, **kw) -> Document:
    return Document("d", text, "acme", **kw)


def _words(n: int) -> str:
    return " ".join(f"w{i}" for i in range(n))


def test_approx_tokens_is_never_zero():
    assert approx_tokens("") == 1 and approx_tokens("abcd" * 10) == 10


def test_unknown_strategy_is_refused():
    with pytest.raises(ValueError, match="unknown chunking strategy: semantic"):
        chunk_document(_doc("text"), strategy="semantic")


def test_empty_document_has_no_chunks():
    assert chunk_document(_doc("")) == []


def test_short_document_is_one_chunk_with_identity_fields():
    doc = Document("policy", "# Leave\nTen days.", "globex", frozenset({"hr"}), 7, {"title": "Policy", "owner": "hr"})
    (c,) = chunk_document(doc)
    assert (c.chunk_id, c.doc_id, c.tenant_id, c.acl, c.doc_version, c.position) == \
           ("policy:v7:0", "policy", "globex", frozenset({"hr"}), 7, 0)
    assert c.text == "Ten days." and c.metadata == {"title": "Policy", "owner": "hr", "section": "Leave"}


def test_text_before_the_first_heading_is_filed_under_the_title_or_doc_id():
    assert chunk_document(_doc("intro\n# H\nbody", metadata={"title": "T"}))[0].metadata["section"] == "T"
    assert chunk_document(_doc("intro\n# H\nbody"))[0].metadata["section"] == "d"


def test_all_six_heading_levels_start_a_section_and_hashtags_do_not():
    text = "\n".join(f"{'#' * n} H{n}\nbody {n}" for n in range(1, 7)) + "\n#hashtag is not a heading\n####### seven"
    chunks = chunk_document(_doc(text))
    assert [c.metadata["section"] for c in chunks] == [f"H{n}" for n in range(1, 7)]
    assert "#hashtag is not a heading" in chunks[-1].text and "####### seven" in chunks[-1].text


def test_positions_and_ids_run_across_sections_without_gaps():
    chunks = chunk_document(_doc(f"# A\n{_words(120)}\n# B\n{_words(120)}"), max_tokens=40, overlap_tokens=0)
    assert [c.position for c in chunks] == list(range(len(chunks))) and len(chunks) > 4
    assert [c.chunk_id for c in chunks] == [f"d:v1:{i}" for i in range(len(chunks))]
    assert {c.metadata["section"] for c in chunks} == {"A", "B"}


def test_recursive_strategy_respects_the_token_limit_and_loses_no_words():
    words = _words(400)
    chunks = chunk_document(_doc(words), strategy="recursive", max_tokens=50, overlap_tokens=0)
    assert len(chunks) > 1 and all(approx_tokens(c.text) <= 50 for c in chunks)
    assert " ".join(c.text for c in chunks).split() == words.split()
    assert {c.metadata["section"] for c in chunks} == {"d"}


def test_recursive_strategy_treats_headings_as_text():
    chunks = chunk_document(_doc("# A\nbody a\n# B\nbody b"), strategy="recursive")
    assert len(chunks) == 1 and "# A" in chunks[0].text and "# B" in chunks[0].text


def test_text_with_no_separators_is_hard_cut_without_loss():
    blob = "x" * 2_050
    chunks = chunk_document(_doc(blob), strategy="recursive", max_tokens=50, overlap_tokens=0)
    assert [len(c.text) for c in chunks] == [200] * 10 + [50] and "".join(c.text for c in chunks) == blob


def test_overlap_carries_the_tail_of_the_previous_piece():
    chunks = chunk_document(_doc(_words(300)), strategy="recursive", max_tokens=40, overlap_tokens=8)
    tail = 8 * 3 // 4
    for prev, nxt in zip(chunks, chunks[1:], strict=False):
        assert nxt.text.split()[:tail] == prev.text.split()[-tail:]


def test_no_overlap_means_no_repeated_words():
    chunks = chunk_document(_doc(_words(300)), strategy="recursive", max_tokens=40, overlap_tokens=0)
    seen = [w for c in chunks for w in c.text.split()]
    assert len(seen) == len(set(seen)) == 300


def test_overlap_does_not_cross_a_section_boundary():
    chunks = chunk_document(_doc("# A\nalpha one two three\n# B\nbeta four five six"), overlap_tokens=30)
    assert [c.text for c in chunks] == ["alpha one two three", "beta four five six"]


def test_custom_tokenizer_is_used_for_the_limit():
    chunks = chunk_document(_doc(_words(53)), strategy="recursive", max_tokens=5, overlap_tokens=0,
                            count=lambda t: len(t.split()))
    assert all(len(c.text.split()) <= 5 for c in chunks) and sum(len(c.text.split()) for c in chunks) == 53


def test_paragraphs_are_kept_whole_when_they_fit():
    paras = [" ".join([f"p{i}"] * 40) for i in range(6)]
    chunks = chunk_document(_doc("\n\n".join(paras)), strategy="recursive", max_tokens=30, overlap_tokens=0)
    assert [c.text for c in chunks] == paras


@pytest.mark.xfail(strict=True, reason="defect: a heading followed only by blank lines, or a whitespace-only "
                                       "document, yields a chunk with empty text that is embedded and indexed")
@pytest.mark.parametrize("text", ["# A\n\n# B\nreal text", "   \n  ", "# Only a heading\n\n"])
def test_no_chunk_is_ever_empty(text):
    assert all(c.text.strip() for c in chunk_document(_doc(text)))
