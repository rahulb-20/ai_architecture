"""The eval gate is what blocks a bad change in CI, so its own arithmetic needs tests."""
import json
from types import SimpleNamespace

import pytest

from aiarch.config import ROOT
from aiarch.domain.models import Answer, AnswerStatus, Citation
from aiarch.evaluation import runner
from aiarch.evaluation.runner import DEFAULT_GATES, Case, check_gates, evaluate, load_cases, main

GOLDEN = ROOT / "evals" / "golden.jsonl"
VPN = "How often do VPN passwords expire?"


class _Rag:
    def __init__(self, answer: Answer) -> None:
        self.answer = answer

    def ask(self, question, principal) -> Answer:
        return self.answer


def _stub(answer: Answer, acl: dict[str, frozenset[str]]) -> SimpleNamespace:
    return SimpleNamespace(store=SimpleNamespace(acl_index=lambda: acl), rag=_Rag(answer))


def _cited(*doc_ids: str) -> Answer:
    return Answer(AnswerStatus.ANSWERED, "the answer is 42", [Citation(d, f"{d}:v1:0", 1) for d in doc_ids])


# --- loading ---------------------------------------------------------------------

def test_load_cases_skips_blank_lines_and_applies_defaults(tmp_path):
    f = tmp_path / "cases.jsonl"
    f.write_text('{"id": "a", "question": "q1"}\n\n   \n'
                 '{"id": "b", "question": "q2", "groups": ["hr"], "expect_status": "abstained"}\n', encoding="utf-8")
    a, b = load_cases(f)
    assert (a.tenant, a.groups, a.expect_status, a.expected_doc_ids, a.must_contain) == ("acme", ["all"],
        "answered", [], [])
    assert (b.id, b.groups, b.expect_status) == ("b", ["hr"], "abstained")


def test_load_cases_rejects_a_misspelt_field(tmp_path):
    f = tmp_path / "cases.jsonl"
    f.write_text('{"id": "a", "question": "q", "expected_docs": ["x"]}\n', encoding="utf-8")
    with pytest.raises(TypeError):
        load_cases(f)


def test_golden_set_is_well_formed():
    cases = load_cases(GOLDEN)
    ids = [c.id for c in cases]
    assert len(ids) == len(set(ids)), "duplicate case ids"
    assert {c.expect_status for c in cases} <= {s.value for s in AnswerStatus}
    for c in cases:
        if c.expect_status == "answered":
            assert c.expected_doc_ids, f"{c.id}: an answered case must name the document it expects"
        else:
            assert not c.expected_doc_ids, f"{c.id}: only answered cases are scored on documents"


def test_golden_set_covers_every_safety_behaviour():
    cases = load_cases(GOLDEN)
    assert {"answered", "abstained", "blocked"} <= {c.expect_status for c in cases}
    assert any(c.tenant != "acme" for c in cases), "no cross-tenant case"
    assert any(set(c.groups) - {"all"} for c in cases), "no restricted-group case"


# --- evaluate --------------------------------------------------------------------

def test_golden_set_scores_perfectly_on_the_offline_stack(container):
    cases = load_cases(GOLDEN)
    report = evaluate(container, cases)
    assert report["cases"] == len(cases) == len(report["rows"])
    assert (report["status_accuracy"], report["recall_at_k"], report["citation_precision"],
            report["must_contain"]) == (1.0, 1.0, 1.0, 1.0)
    assert report["acl_leaks"] == 0 and report["total_cost_usd"] == 0.0
    assert check_gates(report, DEFAULT_GATES) == []


def test_only_answered_cases_are_scored_on_retrieval(container):
    report = evaluate(container, [Case("in", VPN, expected_doc_ids=["it-vpn-access"]),
                                  Case("out", "Who won the cricket world cup in 2011?", expect_status="abstained")])
    rows = {r["id"]: r for r in report["rows"]}
    assert rows["in"]["recall"] == 1.0 and "recall" not in rows["out"]
    assert rows["out"]["status_ok"] is True and rows["out"]["cited"] == []


def test_wrong_expected_document_and_missing_phrase_score_zero(container):
    report = evaluate(container, [Case("x", VPN, expected_doc_ids=["finance-travel"],
        must_contain=["never-in-the-answer"])])
    row = report["rows"][0]
    assert row["status_ok"] is True
    assert (row["recall"], row["precision"], row["contains"]) == (0.0, 0.0, 0.0)
    fails = check_gates(report, DEFAULT_GATES)
    assert any(f.startswith("recall_at_k") for f in fails) and any(f.startswith("must_contain") for f in fails)


def test_status_mismatch_lowers_status_accuracy(container):
    report = evaluate(container, [Case("a", VPN, expect_status="abstained"),
                                  Case("b", VPN, expected_doc_ids=["it-vpn-access"])])
    assert report["status_accuracy"] == 0.5
    assert [r["status_ok"] for r in report["rows"]] == [False, True]


def test_must_contain_is_case_insensitive_and_needs_every_phrase(container):
    ok = evaluate(container, [Case("a", VPN, expected_doc_ids=["it-vpn-access"], must_contain=["vpn", "90 DAYS"])])
    bad = evaluate(container, [Case("a", VPN, expected_doc_ids=["it-vpn-access"], must_contain=["90 days", "banana"])])
    assert ok["must_contain"] == 1.0 and bad["must_contain"] == 0.0


def test_citing_a_document_the_caller_cannot_read_is_a_leak():
    c = _stub(_cited("secret", "open"), {"secret": frozenset({"hr"}), "open": frozenset({"all"})})
    report = evaluate(c, [Case("leak", "q", expected_doc_ids=["open"])])
    assert report["acl_leaks"] == 1 and report["rows"][0]["leaks"] == ["secret"]
    assert report["citation_precision"] == 0.5 and report["recall_at_k"] == 1.0
    assert "acl_leaks 1 > 0" in check_gates(report, DEFAULT_GATES)


def test_same_citation_is_not_a_leak_for_a_member_of_the_group():
    c = _stub(_cited("secret"), {"secret": frozenset({"hr"})})
    assert evaluate(c, [Case("ok", "q", groups=["hr"], expected_doc_ids=["secret"])])["acl_leaks"] == 0


def test_citing_a_document_missing_from_the_index_fails_closed():
    report = evaluate(_stub(_cited("ghost"), {}), [Case("g", "q", expected_doc_ids=["ghost"])])
    assert report["acl_leaks"] == 1


def test_every_caller_is_given_the_all_group():
    c = _stub(_cited("open"), {"open": frozenset({"all"})})
    assert evaluate(c, [Case("a", "q", groups=["eng"], expected_doc_ids=["open"])])["acl_leaks"] == 0


def test_answered_case_with_no_citations_has_zero_precision():
    report = evaluate(_stub(Answer(AnswerStatus.ANSWERED, "text"), {}), [Case("a", "q", expected_doc_ids=["d"])])
    assert report["rows"][0]["precision"] == 0.0 and report["rows"][0]["recall"] == 0.0


def test_empty_case_list_cannot_pass_the_gate(container):
    report = evaluate(container, [])
    assert report["cases"] == 0 and report["status_accuracy"] == 0.0 and report["p95_latency_ms"] == 0.0
    assert any(f.startswith("status_accuracy") for f in check_gates(report, DEFAULT_GATES))


def test_p95_latency_uses_the_nineteenth_of_twenty_samples(monkeypatch):
    ticks = iter(x for i in range(1, 21) for x in (0.0, i / 1000))
    monkeypatch.setattr(runner.time, "perf_counter", lambda: next(ticks))
    c = _stub(Answer(AnswerStatus.ABSTAINED, "no"), {})
    report = evaluate(c, [Case(str(i), "q", expect_status="abstained") for i in range(20)])
    assert report["p95_latency_ms"] == pytest.approx(19.0)


def test_cost_is_summed_across_cases():
    answer = Answer(AnswerStatus.ABSTAINED, "no", cost_usd=0.0012345)
    report = evaluate(_stub(answer, {}), [Case(str(i), "q", expect_status="abstained") for i in range(3)])
    assert report["total_cost_usd"] == pytest.approx(0.003704, abs=1e-6)


# --- gates -----------------------------------------------------------------------

PASSING = {"status_accuracy": 0.9, "recall_at_k": 0.8, "citation_precision": 0.8, "must_contain": 0.7,
           "acl_leaks": 0, "p95_latency_ms": 5_000}


def test_values_exactly_on_the_threshold_pass():
    assert check_gates(PASSING, DEFAULT_GATES) == []


@pytest.mark.parametrize(("key", "value", "expected"), [
    ("status_accuracy", 0.89, "status_accuracy 0.89 < 0.90"),
    ("recall_at_k", 0.79, "recall_at_k 0.79 < 0.80"),
    ("citation_precision", 0.5, "citation_precision 0.50 < 0.80"),
    ("must_contain", 0.0, "must_contain 0.00 < 0.70"),
    ("acl_leaks", 1, "acl_leaks 1 > 0"),
    ("p95_latency_ms", 5_001, "p95_latency_ms 5001 > 5000"),
])
def test_each_gate_fails_on_its_own(key, value, expected):
    assert check_gates({**PASSING, key: value}, DEFAULT_GATES) == [expected]


def test_all_failures_are_reported_together():
    report = {**PASSING, "status_accuracy": 0.0, "acl_leaks": 3, "p95_latency_ms": 9_999}
    assert len(check_gates(report, DEFAULT_GATES)) == 3


def test_default_gates_never_tolerate_a_leak():
    assert DEFAULT_GATES["acl_leaks_max"] == 0


# --- command line ----------------------------------------------------------------

def _failing_cases(tmp_path):
    f = tmp_path / "bad.jsonl"
    f.write_text(json.dumps({"id": "bad", "question": VPN, "expected_doc_ids": ["finance-travel"]}) + "\n",
        encoding="utf-8")
    return f


def test_main_passes_on_the_golden_set(capsys):
    assert main(["--gate"]) == 0
    out = capsys.readouterr().out
    assert "GATE PASSED" in out and "XX " not in out


def test_main_with_gate_exits_one_on_a_regression(tmp_path, capsys):
    assert main(["--cases", str(_failing_cases(tmp_path)), "--gate"]) == 1
    out = capsys.readouterr().out
    assert "GATE FAILED" in out and "recall_at_k" in out and "XX  bad" in out


def test_main_without_gate_reports_the_failure_but_exits_zero(tmp_path, capsys):
    assert main(["--cases", str(_failing_cases(tmp_path))]) == 0
    assert "GATE FAILED" in capsys.readouterr().out


def test_main_writes_the_full_report_when_asked(tmp_path, capsys):
    out_file = tmp_path / "nested" / "report.json"
    assert main(["--gate", "--out", str(out_file)]) == 0
    report = json.loads(out_file.read_text(encoding="utf-8"))
    assert report["cases"] == len(report["rows"]) == len(load_cases(GOLDEN))
    summary = json.loads(capsys.readouterr().out.split("\nOK ")[0])
    assert "rows" not in summary and summary["status_accuracy"] == 1.0
