import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aiarch.config import ROOT, LLMRouteConfig, Settings


def _env(**kw: str) -> Settings:
    return Settings.from_env({f"AIARCH_{k.upper()}": v for k, v in kw.items()})


def test_defaults_need_no_network_no_keys_and_no_auth():
    s = Settings()
    assert [(r.name, r.kind) for r in s.llm_routes] == [("fake", "fake")] and s.embedder == "hashing"
    assert s.api_keys == [] and s.agent_planner == "rule" and s.output_keep_source_pii is False
    assert s.seed_folder == ROOT / "data" / "sample_docs" and s.seed_folder.is_dir() and s.prices_file.is_file()


def test_from_env_with_nothing_set_equals_the_defaults():
    assert Settings.from_env({}) == Settings()


def test_variables_without_the_prefix_or_with_unknown_names_are_ignored():
    s = Settings.from_env({"K_FINAL": "9", "AIARCH_NOT_A_SETTING": "x", "aiarch_k_final": "9", "PATH": "/bin"})
    assert s == Settings()


def test_reads_the_process_environment_when_none_is_given(monkeypatch):
    monkeypatch.setenv("AIARCH_K_FINAL", "7")
    monkeypatch.setenv("AIARCH_ENV", "staging")
    s = Settings.from_env()
    assert (s.k_final, s.env) == (7, "staging")


def test_route_list_is_parsed_from_json_with_per_route_defaults():
    routes = [{"name": "primary", "base_url": "http://x/v1", "model": "m", "api_key_env": "K", "max_retries": 3,
               "timeout_s": 5}, {"name": "local", "kind": "fake"}]
    s = _env(llm_routes=json.dumps(routes))
    primary, local = s.llm_routes
    assert (primary.kind, primary.api_key_env, primary.max_retries, primary.timeout_s) == ("openai_compatible",
        "K", 3, 5.0)
    assert (local.kind, local.base_url, local.max_retries, local.timeout_s) == ("fake", None, 1, 30.0)


@pytest.mark.parametrize("raw", ["not json", '{"name": "x"}', '[{"kind": "fake"}]', "[1, 2]"])
def test_malformed_route_list_fails_at_start_up(raw):
    with pytest.raises((ValueError, ValidationError)):
        _env(llm_routes=raw)


@pytest.mark.parametrize(("raw", "expected"), [("a,b", ["a", "b"]), (" a , ,b ,", ["a", "b"]), ("", []), ("  ",
    []), ("one", ["one"])])
def test_api_keys_are_split_and_trimmed(raw, expected):
    assert _env(api_keys=raw).api_keys == expected


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "Yes", "on", " true "])
def test_truthy_flag_spellings(raw):
    assert _env(semantic_cache=raw).semantic_cache is True
    assert _env(output_keep_source_pii=raw).output_keep_source_pii is True


@pytest.mark.parametrize("raw", ["0", "false", "no", "off", "", "anything-else"])
def test_everything_else_is_false_for_a_flag(raw):
    assert _env(semantic_cache=raw).semantic_cache is False


@pytest.mark.parametrize("raw", ["", "none", "None", " NONE "])
def test_seed_folder_can_be_switched_off(raw):
    assert _env(seed_folder=raw).seed_folder is None


def test_seed_folder_and_prices_file_accept_paths():
    s = _env(seed_folder="/tmp/docs", prices_file="/tmp/prices.json")  # noqa: S108 - a path string, nothing is written
    assert s.seed_folder == Path("/tmp/docs") and s.prices_file == Path("/tmp/prices.json")  # noqa: S108


def test_numbers_are_coerced_from_strings():
    s = _env(k_final="3", chunk_tokens="120", chunk_overlap="0", max_input_tokens="500", cache_threshold="0.8",
             cache_ttl_s="60", min_dense_score="0.45", min_term_coverage="0.75")
    assert (s.k_final, s.chunk_tokens, s.chunk_overlap, s.max_input_tokens) == (3, 120, 0, 500)
    assert (s.cache_threshold, s.cache_ttl_s, s.min_dense_score, s.min_term_coverage) == (0.8, 60.0, 0.45, 0.75)


@pytest.mark.parametrize(("field", "raw"), [("k_final", "three"), ("k_final", "2.5"), ("cache_threshold", "high"),
                                            ("max_input_tokens", ""), ("agent_planner", "auto")])
def test_bad_values_fail_at_start_up_instead_of_being_ignored(field, raw):
    with pytest.raises(ValidationError):
        _env(**{field: raw})


def test_route_needs_a_name_and_keeps_the_key_out_of_config():
    with pytest.raises(ValidationError):
        LLMRouteConfig()
    route = LLMRouteConfig(name="p", api_key_env="PROVIDER_KEY")
    assert "PROVIDER_KEY" in route.model_dump_json() and "api_key" not in LLMRouteConfig.model_fields
