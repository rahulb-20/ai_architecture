# Day 25 — Kubernetes for GenAI

> Part of the [GenAI Architect study path](../../README.md) · Cloud Production Architecture · [Original interactive pack](https://claude.ai/artifact/AqvQcf73M63Nm3g2V1CcB5) · [Offline HTML](../../archive/html/day-25.html)


Week 4 · Cloud, production & architecture · Day 25 of 30

# Kubernetes for GenAI: autoscaling, GPU scheduling, KServe, Helm, canary

LLM pods are heavy, slow to start and bound by GPU memory. Most of what you know about scaling stateless web services gives the wrong answer here. Today is about the right scaling signal, where the GPU actually gets allocated, and how to ship a new model without breaking quality.

Tue 3 Nov 2026K8s 1.37 currentKServe v0.20KEDA 2.20lab ≈ 75 min

## Why this matters for the JD

The JD's CI/CD & productionization line names **Kubernetes, model endpoints, scaling, rollback and cost controls**, and the good-to-haves add **scalability, resilience and microservices**. In an architect interview this shows up as "your RAG assistant hits 10× traffic at 9 AM: what scales, on what signal, and how fast?" A good answer names queue depth, cold-start time, GPU node lead time and a quality-gated canary.

## Concept brief

### Why LLM serving breaks normal Kubernetes habits

- **Pods are heavy.** A 7–70B model means 5–150 GB of weights. A cold start (node provisioning, image pull, weight load, CUDA graph capture) can take 1–10 minutes. Scaling reacts slowly, so you need headroom.
- **CPU% tells you nothing.** The work is GPU-bound. GPU utilisation (DCGM) is also misleading, because it reads near 100% at modest batch sizes. Use *saturation* signals instead: requests waiting, KV-cache usage (`vllm:kv_cache_usage_perc`), and TTFT/ITL against your SLO.
- **Requests have very different costs.** A 200-token chat and a 30k-token RAG prompt cost very different amounts. Round-robin `Service` load balancing and sticky keep-alive connections cause hot spots. Inference-aware routing looks at queue length, KV-cache use and prefix-cache hits.
- **The GPU is the unit of cost.** An idle H100 replica costs about the same as a busy one. Autoscaling is mostly cost control. Scale-to-zero only makes sense where minute-long cold starts are acceptable.

### The control loops, layer by layer

![Request path from client through AI gateway and inference-aware router to vLLM pods on GPU nodes, with KEDA, HPA and node autoscaler control loops](diagrams/fig-1.svg)

Three loops with very different speeds: routing (milliseconds), pod scaling (tens of seconds plus the model-load time) and node scaling (minutes). Design headroom for the slowest one.

- **HPA** handles resource, custom and external metrics. Its `behavior` block (stabilisation windows, scale-up/down policies) is where you stop flapping on expensive pods.
- **KEDA** creates and owns the HPA for you from a `ScaledObject`. It adds 70+ event sources (Prometheus, Kafka, SQS, Service Bus, HTTP), scale-to-zero and activation thresholds. Use it for queue-driven LLM and batch-embedding workers.
- **Node autoscaling**: Cluster Autoscaler or Karpenter (EKS, and AKS via node auto-provisioning). GPU node lead time sets your real burst capacity. Low-priority "balloon" placeholder pods buy warm headroom.
- **KServe** provides the `InferenceService` abstraction plus `LLMInferenceService`, which integrates llm-d for disaggregated prefill/decode and multi-node serving with LeaderWorkerSet. Recent releases added OpenAI `/v1/responses` routing, a model cache and multi-node without Ray (0.18). Red Hat OpenShift AI's single-model serving is KServe-based, so this is the substrate under watsonx-on-OpenShift deployments.
- **Gateway API Inference Extension** (v1.6.x): an `InferencePool` is a set of model-server endpoints behind a Gateway. The Endpoint Picker (EPP) picks the best replica per request. The production EPP now lives in llm-d (`llm-d-router`). GKE Inference Gateway is built on it.

### GPU scheduling

- **Device plugin model**: pods request `nvidia.com/gpu: 1`, an integer-only extended resource. The NVIDIA GPU Operator installs the driver, container toolkit, device plugin, DCGM exporter and MIG manager.
- **Isolation**: taint GPU nodes (`nvidia.com/gpu=present:NoSchedule`), tolerate only in inference pods, and pin GPU model with node affinity on product labels. Without this, a stray CPU pod can block a ₹-expensive node from scale-down.
- **Sharing**: MIG gives hard isolation (A100/H100 slices). Time-slicing and MPS are soft sharing, fine for dev and embeddings, risky for latency SLOs.
- **DRA** (Dynamic Resource Allocation) is **stable since Kubernetes 1.35**. `ResourceClaim`s select devices by attributes (memory, model, topology) instead of an opaque count. The NVIDIA DRA driver is the path to fine-grained, topology-aware GPU allocation, and the GPU Operator roadmap plans to manage it.

**GPU pod essentials (illustrative)**

```text
spec:
  tolerations: [{key: nvidia.com/gpu, operator: Exists, effect: NoSchedule}]
  affinity:
    nodeAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
        nodeSelectorTerms: [{matchExpressions: [{key: nvidia.com/gpu.product, operator: In, values: ["NVIDIA-L4"]}]}]
  terminationGracePeriodSeconds: 120        # let in-flight streams finish
  containers:
  - name: vllm
    resources: {limits: {nvidia.com/gpu: 1}}
    startupProbe:  {httpGet: {path: /health, port: 8000}, failureThreshold: 60, periodSeconds: 10}  # 10 min for weight load
    readinessProbe: {httpGet: {path: /health, port: 8000}, periodSeconds: 5}
    lifecycle: {preStop: {sleep: {seconds: 30}}}  # drain before SIGTERM
```

### Helm and canary for models

Package each model deployment as a chart (vLLM production-stack, llm-d and KServe all ship Helm charts) and keep the *model id, revision hash, quantisation and serving args* in versioned values files. A model change should be a reviewable diff, not a `kubectl edit`.

Canary for LLMs needs two gates. **Offline**: the eval suite runs in CI before rollout (Day 26). **Online**: send 5–10% of traffic using KServe `canaryTrafficPercent`, HTTPRoute weights, or Argo Rollouts/Flagger analysis. Judge on TTFT/ITL p95, error rate, refusal rate, and a sampled groundedness or LLM-judge score, not only HTTP 5xx. A model can return 200 OK with every answer wrong.

## Trade-offs & decision tables

### Scaling signal

| Signal | Pick when | Failure mode | Cost / latency |
| --- | --- | --- | --- |
| CPU / memory (plain HPA) | CPU-bound sidecars: API, embedding on CPU, rerankers | GPU pods sit at low CPU while the queue explodes, so nothing scales | Cheap to run; useless for GPU serving |
| GPU util (DCGM) | Batch/offline jobs | Reads saturated at small batch, so it over-scales; doesn't reflect queueing | Over-provisions GPUs |
| Queue depth (waiting requests) | Default for online LLM serving | Needs a stable target per replica; noisy at low traffic | Tracks user-visible latency well |
| KV-cache usage | Long-context RAG, where memory not compute saturates | Prefix caching makes it read high when idle-ish | Prevents preemption and recompute storms |
| TTFT / ITL SLO | Mature setups with good observability | Lagging indicator; feedback-loop oscillation | Directly ties spend to SLO |
| Scale-to-zero (KEDA/Knative) | Dev, internal tools, per-tenant fine-tunes, nightly batch | 1–10 min cold start; first user times out | Biggest saving; worst p99 |

### Serving platform on Kubernetes

| Option | Pick when | Failure modes | Cost / ops |
| --- | --- | --- | --- |
| Deployment + HPA/KEDA + Helm | 1–3 models, small team, single-GPU replicas | Naive LB, no model cache, DIY canary | Lowest complexity |
| vLLM production-stack | vLLM-only fleet; want a router with prefix-aware routing and KV offload quickly | Tied to vLLM; smaller community than KServe | One chart; moderate |
| KServe (RawDeployment or Knative) | Many models and teams; need one CRD, canary, model cache; OpenShift AI / watsonx estates | CRD and webhook sprawl; Knative mode adds Istio/Kourier complexity | Platform team required |
| llm-d (via KServe LLMInferenceService or directly) | Large models, high QPS, P/D disaggregation, multi-node, prefix-cache-heavy RAG | Newest stack; needs fast interconnect for P/D | Best GPU efficiency at scale; highest expertise |
| Managed endpoint (Bedrock, Azure AI Foundry, Vertex, watsonx.ai SaaS) | Closed models, spiky low volume, no GPU ops team | Quota limits, regional capacity, less control of versions | Pay per token; zero GPU ops |

Cloud mapping: **EKS** + Karpenter + GPU AMIs · **AKS** + GPU node pools + KAITO (AI toolchain operator) · **GKE** + Inference Gateway + GPU/TPU node pools · **OpenShift AI** (watsonx on-prem) + KServe + vLLM ServingRuntime.

## Hands-on lab: queue-depth autoscaling with KEDA on kind

**Goal (≈75 min):** run a two-tier "LLM" service on a local cluster, autoscale the backend on *in-flight requests* instead of CPU, and watch the cold-start lag and load imbalance yourself. The backend simulates a GPU server: a fixed number of concurrent slots (like KV-cache capacity), a prefill delay, per-token decode time, and a 20-second warm-up that stands in for weight loading. It needs no GPU, so it runs comfortably in 16 GB RAM.

**Needs Docker Desktop with the WSL2 backend.** kind runs Kubernetes inside Docker containers. Give Docker Desktop at least 4 GB RAM (Settings → Resources). GPU passthrough into kind on Windows isn't practical, so GPU scheduling stays conceptual today.

### Setup (PowerShell)

**PowerShell**

```powershell
# Tools (kind v0.33.0 is current)
winget install -e --id Kubernetes.kind
winget install -e --id Kubernetes.kubectl
winget install -e --id Helm.Helm
# open a NEW terminal so PATH refreshes

mkdir day25-k8s; cd day25-k8s; mkdir k8s
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install httpx==0.28.1

kind create cluster --name genai
kubectl get nodes
```

### app.py: one image, two roles (gateway and backend)

**app.py** · `lab/app.py`

```python
"""Mock LLM serving tier. ROLE=backend simulates a GPU model server
(fixed concurrency slots, prefill + per-token decode, warm-up on start).
ROLE=gateway is a thin AI gateway that relays streams and exposes the
global in-flight count that KEDA scales on."""
import asyncio
import os
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

ROLE = os.getenv("ROLE", "backend")
MAX_CONCURRENCY = int(os.getenv("MAX_CONCURRENCY", "2"))  # ~ KV-cache slots
PREFILL_MS = float(os.getenv("PREFILL_MS", "150"))
TPOT_MS = float(os.getenv("TPOT_MS", "40"))               # time per output token
WARMUP_S = float(os.getenv("WARMUP_SECONDS", "20"))       # ~ weight load
BACKEND_URL = os.getenv("BACKEND_URL", "http://mockllm-backend:8000")
KEEPALIVE = int(os.getenv("KEEPALIVE", "0"))              # 0 = new conn per request
POD = os.getenv("HOSTNAME", "local")

state = {"running": 0, "waiting": 0, "inflight": 0, "served": 0}
started = time.monotonic()
slots = asyncio.Semaphore(MAX_CONCURRENCY)
client: httpx.AsyncClient | None = None

@asynccontextmanager
async def lifespan(_: FastAPI):
    global client
    if ROLE == "gateway":
        client = httpx.AsyncClient(
            base_url=BACKEND_URL,
            timeout=httpx.Timeout(180.0, connect=5.0),
            limits=httpx.Limits(max_keepalive_connections=KEEPALIVE),
        )
    yield
    if client:
        await client.aclose()

app = FastAPI(lifespan=lifespan)

class GenRequest(BaseModel):
    prompt: str
    max_tokens: int = 64

@app.get("/healthz")
async def healthz():
    return {"ok": True}

@app.get("/readyz")
async def readyz():
    if ROLE == "backend" and time.monotonic() - started < WARMUP_S:
        raise HTTPException(status_code=503, detail="loading model weights")
    return {"ready": True}

@app.get("/stats")
async def stats():
    return {"role": ROLE, "pod": POD, "max_concurrency": MAX_CONCURRENCY, **state}

async def _generate(req: GenRequest):
    state["waiting"] += 1
    acquired = False
    try:
        await slots.acquire()
        acquired = True
        state["waiting"] -= 1
        state["running"] += 1
        # prefill cost grows with prompt length
        await asyncio.sleep(PREFILL_MS / 1000 * (1 + len(req.prompt) / 2000))
        yield f"[{POD}] "
        for i in range(req.max_tokens):
            await asyncio.sleep(TPOT_MS / 1000)
            yield f"t{i} "
    finally:
        if acquired:
            slots.release()
            state["running"] -= 1
            state["served"] += 1
        else:
            state["waiting"] -= 1

async def _relay(req: GenRequest):
    state["inflight"] += 1
    try:
        async with client.stream("POST", "/v1/generate", json=req.model_dump()) as r:
            r.raise_for_status()
            async for chunk in r.aiter_text():
                yield chunk
    finally:
        state["inflight"] -= 1
        state["served"] += 1

@app.post("/v1/generate")
async def generate(req: GenRequest):
    gen = _generate(req) if ROLE == "backend" else _relay(req)
    return StreamingResponse(gen, media_type="text/plain")
```
**Dockerfile** · `lab/Dockerfile`

```dockerfile
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN python -m pip install --no-cache-dir "fastapi==0.162.0" "uvicorn[standard]==0.54.0" "httpx==0.28.1"
COPY app.py .
RUN useradd -u 10001 app
USER 10001
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Kubernetes manifests

**k8s/deploy.yaml** · `lab/k8s/deploy.yaml`

```yaml
apiVersion: apps/v1
kind: Deployment
metadata: {name: mockllm-backend}
spec:
  replicas: 1
  selector: {matchLabels: {app: mockllm-backend}}
  template:
    metadata: {labels: {app: mockllm-backend}}
    spec:
      terminationGracePeriodSeconds: 30
      containers:
      - name: server
        image: mockllm:0.1
        imagePullPolicy: IfNotPresent
        env:
        - {name: ROLE, value: backend}
        - {name: MAX_CONCURRENCY, value: "2"}
        - {name: WARMUP_SECONDS, value: "20"}
        ports: [{containerPort: 8000}]
        readinessProbe: {httpGet: {path: /readyz, port: 8000}, periodSeconds: 2}
        livenessProbe:  {httpGet: {path: /healthz, port: 8000}, periodSeconds: 10}
        lifecycle: {preStop: {sleep: {seconds: 5}}}
        resources:
          requests: {cpu: 50m, memory: 96Mi}
          limits:   {memory: 256Mi}
---
apiVersion: v1
kind: Service
metadata: {name: mockllm-backend}
spec:
  selector: {app: mockllm-backend}
  ports: [{port: 8000, targetPort: 8000}]
---
apiVersion: apps/v1
kind: Deployment
metadata: {name: mockllm-gateway}
spec:
  replicas: 1
  selector: {matchLabels: {app: mockllm-gateway}}
  template:
    metadata: {labels: {app: mockllm-gateway}}
    spec:
      containers:
      - name: server
        image: mockllm:0.1
        imagePullPolicy: IfNotPresent
        env:
        - {name: ROLE, value: gateway}
        - {name: BACKEND_URL, value: "http://mockllm-backend:8000"}
        - {name: KEEPALIVE, value: "0"}
        ports: [{containerPort: 8000}]
        readinessProbe: {httpGet: {path: /readyz, port: 8000}, periodSeconds: 2}
        resources:
          requests: {cpu: 50m, memory: 96Mi}
          limits:   {memory: 256Mi}
---
apiVersion: v1
kind: Service
metadata: {name: mockllm-gateway}
spec:
  selector: {app: mockllm-gateway}
  ports: [{port: 8000, targetPort: 8000}]
```
**k8s/scaledobject.yaml (KEDA metrics-api scaler)** · saved as `lab/k8s/scaledobject.yaml`

```yaml
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata: {name: mockllm-backend}
spec:
  scaleTargetRef: {name: mockllm-backend}
  minReplicaCount: 1
  maxReplicaCount: 6
  pollingInterval: 5
  cooldownPeriod: 60
  advanced:
    horizontalPodAutoscalerConfig:
      behavior:
        scaleUp:   {stabilizationWindowSeconds: 0}
        scaleDown: {stabilizationWindowSeconds: 60}
  triggers:
  - type: metrics-api
    metricType: AverageValue          # desired = ceil(inflight / 2)
    metadata:
      url: "http://mockllm-gateway.default.svc.cluster.local:8000/stats"
      valueLocation: "inflight"
      targetValue: "2"                # = backend MAX_CONCURRENCY
```

### loadtest.py: ramp, measure TTFT, see which pod served

**loadtest.py** · `lab/loadtest.py`

```python
import asyncio
import statistics
import time
from collections import Counter

import httpx

URL = "http://localhost:8000"
PHASES = [("warm", 2, 30), ("burst", 12, 150), ("cool", 2, 45)]  # name, concurrency, seconds

async def worker(client, stop_at, results, pods):
    while time.monotonic() < stop_at:
        t0 = time.monotonic()
        ttft = None
        try:
            async with client.stream("POST", "/v1/generate",
                                     json={"prompt": "Summarise the leave policy " * 20,
                                           "max_tokens": 64}) as r:
                async for chunk in r.aiter_text():
                    if ttft is None:
                        ttft = time.monotonic() - t0
                        if chunk.startswith("["):
                            pods[chunk[1:chunk.index("]")]] += 1
            results.append((ttft or 0.0, time.monotonic() - t0))
        except httpx.HTTPError as e:
            results.append((float("nan"), float("nan")))
            print("error:", type(e).__name__)

async def sampler(client, stop_at):
    while time.monotonic() < stop_at:
        s = (await client.get("/stats")).json()
        print(f"  {time.strftime('%H:%M:%S')}  gateway inflight={s['inflight']}")
        await asyncio.sleep(10)

def p(values, q):
    vals = sorted(v for v in values if v == v)
    return vals[min(len(vals) - 1, int(q * len(vals)))] if vals else float("nan")

async def main():
    timeout = httpx.Timeout(180.0, connect=5.0)
    async with httpx.AsyncClient(base_url=URL, timeout=timeout) as client:
        for name, conc, secs in PHASES:
            print(f"\n== phase {name}: concurrency={conc} for {secs}s")
            stop_at = time.monotonic() + secs
            results, pods = [], Counter()
            await asyncio.gather(sampler(client, stop_at),
                                 *(worker(client, stop_at, results, pods) for _ in range(conc)))
            ttfts = [r[0] for r in results]
            totals = [r[1] for r in results]
            print(f"   requests={len(results)}  TTFT p50={p(ttfts, .5):.2f}s p95={p(ttfts, .95):.2f}s"
                  f"  e2e p50={statistics.median(totals):.2f}s")
            print(f"   served by: {dict(pods)}")

if __name__ == "__main__":
    asyncio.run(main())
```

### Run it

**PowerShell: build, deploy, install KEDA**

```powershell
docker build -t mockllm:0.1 .
kind load docker-image mockllm:0.1 --name genai
kubectl apply -f k8s/deploy.yaml
kubectl rollout status deploy/mockllm-backend   # ~20s: watch readiness gate the warm-up

helm repo add kedacore https://kedacore.github.io/charts
helm repo update
helm install keda kedacore/keda --namespace keda --create-namespace
kubectl -n keda rollout status deploy/keda-operator
kubectl apply -f k8s/scaledobject.yaml
kubectl get scaledobject,hpa                     # KEDA created keda-hpa-mockllm-backend

# Terminal 2
kubectl port-forward svc/mockllm-gateway 8000:8000
# Terminal 3
kubectl get deploy mockllm-backend -w
# Terminal 1 (venv active)
python loadtest.py
```

### Expected output (shape, your numbers will differ)

**console**

```text
== phase warm: concurrency=2 for 30s
   requests=22  TTFT p50=0.33s p95=0.36s  e2e p50=2.93s
   served by: {'mockllm-backend-7c9d...-x2kq': 22}

== phase burst: concurrency=12 for 150s
  gateway inflight=12
   requests=420  TTFT p50=0.4s p95=13.5s  e2e p50=3.1s
   served by: {'...x2kq': 98, '...b7mf': 71, '...r4tz': 66, '...9hcp': 64, '...q8dn': 62, '...zl2w': 59}

== phase cool: concurrency=2 for 45s
   requests=31  TTFT p50=0.33s p95=0.35s  e2e p50=2.92s
```

What to notice: replicas go 1 → 6 within about 10 seconds of the burst, but pods only take traffic after the 20 s warm-up, so burst p95 TTFT stays high for the first 30–40 s and then drops. That gap is your cold-start budget. In production it's minutes, which is why minReplicas, warm pools and model caches matter. Scale-down to 1 happens about 60 s after the burst (stabilisation window plus cooldown).

### Stretch goals

1. **Break the load balancer.** Set `KEEPALIVE=50` on the gateway and `kubectl rollout restart deploy/mockllm-gateway`. Re-run the test. Pooled connections stick to the pods that existed first, so new replicas sit idle while TTFT stays high. Write two sentences on why this is the problem the Inference Extension's EPP solves. Then try KEDA's `aggregateFromKubeServiceEndpoints` option to scale on the backends' own `waiting` sum instead of the gateway.
2. **Swap in a real model.** Replace the backend with `ollama/ollama` serving `qwen2.5:0.5b` (398 MB) with `OLLAMA_NUM_PARALLEL=2`, pull the model in an init container onto an `emptyDir`, and replace the readiness probe with a `startupProbe`. Measure real cold-start time, then add a hostPath/PVC model cache and measure again. Keep `maxReplicaCount: 3` on 16 GB.

Cleanup: `kind delete cluster --name genai`

## GitHub repos to study

**[kserve/kserve](https://github.com/kserve/kserve)**Latest v0.20.0, with v0.21 in RC. Read the `LLMInferenceService` CRD and its controller to see how llm-d, LeaderWorkerSet and HTTPRoutes are composed from one spec.

**[kedacore/keda](https://github.com/kedacore/keda)**Latest v2.20.2. Read `pkg/scalers/` (metrics-api and prometheus scalers) to see how a metric becomes an external HPA metric, and how activation differs from scaling thresholds.

**[llm-d/llm-d](https://github.com/llm-d/llm-d)**CNCF sandbox; llm-d.ai now announces 1.0. Work through the guides on intelligent inference scheduling and prefill/decode disaggregation. That's the state of the art you'll be asked about.

**[kubernetes-sigs/gateway-api-inference-extension](https://github.com/kubernetes-sigs/gateway-api-inference-extension)**Latest v1.6.2. Study the `InferencePool` API and the request-flow docs. The production Endpoint Picker has moved to llm-d, so note which pieces are being archived here.

## Lab files in this folder

- [`lab/app.py`](lab/app.py)
- [`lab/Dockerfile`](lab/Dockerfile)
- [`lab/k8s/deploy.yaml`](lab/k8s/deploy.yaml)
- [`lab/k8s/scaledobject.yaml`](lab/k8s/scaledobject.yaml)
- [`lab/loadtest.py`](lab/loadtest.py)
