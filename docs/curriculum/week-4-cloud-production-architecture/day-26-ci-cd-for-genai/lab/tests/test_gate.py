"""Tests for the gate itself (LLM_MODE=mock), so a broken scorer can't silently pass bad prompts."""
import json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))
os.environ["LLM_MODE"] = "mock"
import eval_gate as g

CASE = {"context": {"kb1": "x"}, "expect": {"category": "access", "cited": ["kb1"]}}

def test_invalid_json_fails():
    assert g.score_case(CASE, "not json")["schema_ok"] is False

def test_invented_citation_fails():
    raw = json.dumps({"category": "access", "priority": "P3", "answer": "a", "cited_ids": ["kb1", "kb99"]})
    assert g.score_case(CASE, raw)["passed"] is False

def test_mock_run_passes_gate():
    res = g.run("ticket_triage", "candidate")
    assert res["pass_rate"] == 1.0 and g.gate(res, res) == []

def test_regression_is_blocked():
    res = g.run("ticket_triage", "candidate")
    worse = {**res, "pass_rate": 0.7}
    assert any("dropped" in f or "floor" in f for f in g.gate(worse, res))
