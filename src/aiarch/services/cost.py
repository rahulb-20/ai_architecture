"""Token cost accounting and a capacity/cost estimator for design reviews.

Prices are configuration, not code: load them from `config/prices.json` and refresh
that file from your provider's price sheet. The numbers shipped are placeholders.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path

from aiarch.domain.models import Usage


@dataclass(frozen=True)
class ModelPrice:
    input_per_m: float
    output_per_m: float
    cached_input_mult: float = 0.1


@dataclass
class PriceBook:
    prices: dict[str, ModelPrice] = field(default_factory=dict)
    default: ModelPrice = ModelPrice(0.0, 0.0)

    @classmethod
    def from_file(cls, path: str | Path) -> PriceBook:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls({k: ModelPrice(**v) for k, v in raw.get("models", {}).items()},
                   ModelPrice(**raw["default"]) if "default" in raw else ModelPrice(0.0, 0.0))

    def cost(self, model: str, usage: Usage) -> float:
        p = self.prices.get(model, self.default)
        uncached = max(0, usage.input_tokens - usage.cached_input_tokens)
        return (uncached * p.input_per_m
                + usage.cached_input_tokens * p.input_per_m * p.cached_input_mult
                + usage.output_tokens * p.output_per_m) / 1_000_000


@dataclass(frozen=True)
class Workload:
    users: int = 50_000
    active_ratio: float = 0.30
    queries_per_active_user_per_day: float = 4
    working_days_per_month: int = 22
    llm_calls_per_query: float = 1.6
    static_prefix_tokens: int = 2_500
    retrieved_tokens: int = 3_000
    user_tokens: int = 120
    output_tokens: int = 350
    prefix_cache_hit_rate: float = 0.0
    semantic_cache_hit_rate: float = 0.0


def monthly_estimate(w: Workload, price: ModelPrice, cache_write_mult: float = 1.25) -> dict[str, float]:
    queries = w.users * w.active_ratio * w.queries_per_active_user_per_day * w.working_days_per_month
    calls = queries * (1 - w.semantic_cache_hit_rate) * w.llm_calls_per_query
    h = w.prefix_cache_hit_rate
    prefix_factor = (h * price.cached_input_mult + (1 - h) * cache_write_mult) if h else 1.0
    in_tok = calls * (w.static_prefix_tokens * prefix_factor + w.retrieved_tokens + w.user_tokens)
    out_tok = calls * w.output_tokens
    cost = in_tok / 1e6 * price.input_per_m + out_tok / 1e6 * price.output_per_m
    return {"queries": queries, "llm_calls": calls, "cost_usd": cost,
            "cost_per_query_usd": cost / queries if queries else 0.0}


def sensitivity(w: Workload, price: ModelPrice, bump: float = 0.2) -> dict[str, float]:
    base = monthly_estimate(w, price)["cost_usd"]
    out = {}
    for f in ("llm_calls_per_query", "static_prefix_tokens", "retrieved_tokens",
              "output_tokens", "queries_per_active_user_per_day"):
        bumped = replace(w, **{f: getattr(w, f) * (1 + bump)})
        out[f] = monthly_estimate(bumped, price)["cost_usd"] / base - 1
    return out
