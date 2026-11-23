"""Day 23 - mini AI gateway: LiteLLM Router + FastAPI.
Features: fallback routing, retries+cooldown, per-key rate limit (token bucket),
per-key daily token quota, exact-match cache (temperature==0 only), metrics.
Run:  uvicorn gateway:app --port 8080
Env:  CHAOS=1   -> primary points at a dead port (forces fallback)
      MOCK=1    -> no Ollama needed (canned responses, for a dry run)
      GEMINI_API_KEY -> adds a free-tier cloud model as last-resort fallback
"""
import hashlib, json, os, time
from collections import defaultdict
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from litellm import Router

OLLAMA = "http://localhost:11434"
DEAD = "http://localhost:59999"      # nothing listens here
MOCK = os.getenv("MOCK") == "1"

def dep(name, model, base=None):
    p = {"model": model}
    if base: p["api_base"] = base
    if MOCK and base != DEAD: p["mock_response"] = f"[mock answer from {name}]"
    return {"model_name": name, "litellm_params": p}

model_list = [
    dep("chat-primary", "ollama_chat/qwen2.5:1.5b", DEAD if os.getenv("CHAOS") == "1" else OLLAMA),
    dep("chat-backup",  "ollama_chat/llama3.2:1b", OLLAMA),
]
chain = ["chat-backup"]
if os.getenv("GEMINI_API_KEY"):
    model_list.append(dep("chat-cloud", "gemini/gemini-2.5-flash"))
    chain.append("chat-cloud")

router = Router(
    model_list=model_list,
    fallbacks=[{"chat-primary": chain}],
    num_retries=1,          # retry same group once before falling back
    allowed_fails=2,        # failures/min before a deployment is cooled down
    cooldown_time=30,       # seconds a failing deployment is skipped
    timeout=60,
)

# ---- per-key policy (in prod: Redis + a config/DB, never in-process) ----
KEYS = {"team-a": {"rpm": 10, "daily_tokens": 20_000},
        "team-b": {"rpm": 3,  "daily_tokens": 2_000}}
buckets = {k: {"tokens": v["rpm"], "ts": time.monotonic()} for k, v in KEYS.items()}
used_tokens = defaultdict(int)
cache: dict[str, dict] = {}
metrics = defaultdict(int)

def take_token(key: str) -> bool:
    b, rpm = buckets[key], KEYS[key]["rpm"]
    now = time.monotonic()
    b["tokens"] = min(rpm, b["tokens"] + (now - b["ts"]) * rpm / 60)
    b["ts"] = now
    if b["tokens"] >= 1:
        b["tokens"] -= 1
        return True
    return False

class ChatIn(BaseModel):
    messages: list[dict]
    temperature: float = 0.0
    max_tokens: int = 256

app = FastAPI(title="mini-ai-gateway")

@app.post("/v1/chat")
async def chat(body: ChatIn, x_api_key: str = Header(...)):
    if x_api_key not in KEYS:
        raise HTTPException(401, "unknown key")
    if not take_token(x_api_key):
        metrics["rate_limited"] += 1
        raise HTTPException(429, "rate limit exceeded", headers={"Retry-After": "6"})
    if used_tokens[x_api_key] + body.max_tokens > KEYS[x_api_key]["daily_tokens"]:
        metrics["quota_exceeded"] += 1
        raise HTTPException(429, "daily token quota exhausted")

    ck = None
    if body.temperature == 0:      # only deterministic requests are cacheable
        ck = hashlib.sha256(json.dumps([body.messages, body.max_tokens], sort_keys=True).encode()).hexdigest()
        if ck in cache:
            metrics["cache_hits"] += 1
            return {**cache[ck], "cache": "hit", "latency_ms": 0}

    t0 = time.perf_counter()
    try:
        r = await router.acompletion(model="chat-primary", messages=body.messages,
                                     temperature=body.temperature, max_tokens=body.max_tokens)
    except Exception as e:
        metrics["upstream_errors"] += 1
        raise HTTPException(503, f"all deployments failed: {type(e).__name__}")
    ms = int((time.perf_counter() - t0) * 1000)
    tokens = r.usage.total_tokens if r.usage else 0
    used_tokens[x_api_key] += tokens
    served_by = r.model or "?"
    metrics[f"served_by:{served_by}"] += 1
    out = {"answer": r.choices[0].message.content, "served_by": served_by, "tokens": tokens}
    if ck: cache[ck] = out
    return {**out, "cache": "miss", "latency_ms": ms}

@app.get("/metrics")
def get_metrics():
    return {"counters": dict(metrics), "tokens_used": dict(used_tokens)}
