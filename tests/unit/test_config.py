import pytest
from pydantic import ValidationError

from aiarch.config import Settings


def test_defaults_are_offline():
    s = Settings.from_env({})
    assert [r.kind for r in s.llm_routes] == ["fake"] and s.embedder == "hashing"


def test_env_parsing():
    s = Settings.from_env({
        "AIARCH_LLM_ROUTES": '[{"name":"ollama","base_url":"http://localhost:11434/v1","model":"llama3.2:3b"},'
                             '{"name":"fallback","kind":"fake"}]',
        "AIARCH_SEMANTIC_CACHE": "false", "AIARCH_API_KEYS": "a, b", "AIARCH_K_FINAL": "3"})
    assert [r.name for r in s.llm_routes] == ["ollama", "fallback"]
    assert s.semantic_cache is False and s.api_keys == ["a", "b"] and s.k_final == 3


def test_agent_planner_defaults_to_rule():
    assert Settings.from_env({}).agent_planner == "rule" and Settings().agent_planner == "rule"


def test_agent_planner_llm_from_env():
    assert Settings.from_env({"AIARCH_AGENT_PLANNER": "llm"}).agent_planner == "llm"


@pytest.mark.parametrize("value", ["gpt", "LLM", "Rule", "", " llm"])
def test_agent_planner_rejects_any_other_value(value):
    with pytest.raises(ValidationError):
        Settings.from_env({"AIARCH_AGENT_PLANNER": value})
