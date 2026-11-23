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
