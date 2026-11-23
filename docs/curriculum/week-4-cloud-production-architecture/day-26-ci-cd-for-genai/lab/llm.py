"""Provider layer: live Ollama, record/replay cassettes, and a mock for gate-logic tests.

LLM_MODE=live    -> call Ollama, and write every response to cassettes/ (record)
LLM_MODE=replay  -> read cassettes/ only; a missing entry is a hard error (CI default)
LLM_MODE=mock    -> deterministic fake answers, for unit-testing the gate itself
"""
import hashlib, json, os, time
from pathlib import Path

CASSETTES = Path(__file__).parent / "cassettes"

SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": ["access", "network", "hardware", "software", "unknown"]},
        "priority": {"type": "string", "enum": ["P1", "P2", "P3", "P4"]},
        "answer": {"type": "string"},
        "cited_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["category", "priority", "answer", "cited_ids"],
}


def _key(model: str, messages: list[dict]) -> str:
    raw = json.dumps({"model": model, "messages": messages, "schema": SCHEMA}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def chat(model: str, messages: list[dict], case: dict | None = None) -> dict:
    """Return {"content": str, "latency_ms": float, "in_tokens": int, "out_tokens": int}."""
    mode = os.getenv("LLM_MODE", "replay")
    key = _key(model, messages)
    path = CASSETTES / f"{key}.json"

    if mode == "replay":
        if not path.exists():
            raise RuntimeError(f"No cassette {path.name}. Run once with LLM_MODE=live and commit cassettes/.")
        return json.loads(path.read_text(encoding="utf-8"))

    if mode == "mock":
        exp = (case or {}).get("expect", {})
        body = {
            "category": exp.get("category", "unknown"),
            "priority": exp.get("priority", "P3"),
            "answer": "I don't have enough information to answer." if exp.get("abstain") else "See the cited passage.",
            "cited_ids": exp.get("cited", []),
        }
        return {"content": json.dumps(body), "latency_ms": 120.0, "in_tokens": 180, "out_tokens": 40}

    import ollama  # live mode only
    t0 = time.perf_counter()
    resp = ollama.chat(model=model, messages=messages, format=SCHEMA,
                       options={"temperature": 0, "seed": 42, "num_ctx": 4096})
    out = {
        "content": resp.message.content,
        "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
        "in_tokens": resp.prompt_eval_count or 0,
        "out_tokens": resp.eval_count or 0,
    }
    CASSETTES.mkdir(exist_ok=True)
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out
