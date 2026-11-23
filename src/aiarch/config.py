"""Typed settings from environment variables (12-factor). No secrets in code or files.

Every knob has an offline-safe default, so a fresh clone runs with no `.env` at all.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]


class LLMRouteConfig(BaseModel):
    name: str
    kind: str = "openai_compatible"            # "openai_compatible" | "fake"
    base_url: str | None = None
    model: str | None = None
    api_key_env: str | None = None             # name of the env var holding the key, never the key
    max_retries: int = 1
    timeout_s: float = 30.0


class Settings(BaseModel):
    env: str = "local"
    log_level: str = "INFO"
    llm_routes: list[LLMRouteConfig] = Field(default_factory=lambda: [LLMRouteConfig(name="fake", kind="fake")])
    embedder: str = "hashing"                  # "hashing" | "sentence-transformers"
    embedder_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    semantic_cache: bool = True
    cache_threshold: float = 0.92
    cache_ttl_s: float = 86_400
    chunk_tokens: int = 220
    chunk_overlap: int = 30
    k_final: int = 5
    min_dense_score: float = 0.30              # calibrate per embedder (see services/retrieval.py)
    min_term_coverage: float = 0.6
    max_input_tokens: int = 12_000
    prices_file: Path = ROOT / "config" / "prices.json"
    api_keys: list[str] = Field(default_factory=list)   # empty = auth disabled (local only)
    seed_folder: Path | None = ROOT / "data" / "sample_docs"
    seed_tenant: str = "acme"

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> Settings:
        e = dict(os.environ if environ is None else environ)
        data: dict = {}
        for field_name, field_info in cls.model_fields.items():
            key = f"AIARCH_{field_name.upper()}"
            if key not in e:
                continue
            raw = e[key]
            if field_name == "llm_routes":
                data[field_name] = json.loads(raw)
            elif field_name == "api_keys":
                data[field_name] = [k.strip() for k in raw.split(",") if k.strip()]
            elif field_name == "seed_folder" and raw.strip().lower() in ("", "none"):
                data[field_name] = None
            elif field_info.annotation is bool:
                data[field_name] = raw.strip().lower() in ("1", "true", "yes", "on")
            else:
                data[field_name] = raw
        return cls.model_validate(data)
