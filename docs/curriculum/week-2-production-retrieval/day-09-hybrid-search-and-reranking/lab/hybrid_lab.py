"""
Day 9 lab: BM25 vs dense vs hybrid (RRF / convex) vs hybrid + cross-encoder rerank.
Runs on CPU in ~1-2 min after first model download; uses CUDA automatically if available.
"""
import time, statistics, argparse
import numpy as np
import bm25s
import faiss
import torch
from sentence_transformers import SentenceTransformer, CrossEncoder

# --- 1. A small enterprise KB with deliberate traps --------------------------------
# Exact identifiers (error codes, KB ids, policy numbers) favour BM25;
# paraphrased questions favour dense; near-duplicate distractors punish both.
CORPUS = [
 ("D01", "ORA-01555 snapshot too old: the undo retention is too short for a long-running query. Increase UNDO_RETENTION or tune the query to finish faster."),
 ("D02", "ORA-01652 unable to extend temp segment: the temporary tablespace is full. Add a tempfile or reduce sort-heavy operations."),
 ("D03", "Oracle performance guide: long-running reports often fail when undo data is overwritten before the query completes; schedule them off-peak."),
 ("D04", "KB-7741 Rolling back a failed payroll deployment: stop the batch scheduler, restore the previous container image tag, then rerun reconciliation."),
 ("D05", "KB-7714 Rolling back a failed CRM deployment: disable the feature flag, redeploy the last green build and clear the CDN cache."),
 ("D06", "Policy HR-217 Working from abroad: employees may work from another country for up to 30 days per year with manager approval and a tax check by Global Mobility."),
 ("D07", "Policy HR-210 Relocation: permanent transfers to another country require an international assignment letter and visa sponsorship from HR."),
 ("D08", "Travel policy: business trips abroad must be booked through the corporate travel portal; per-diem rates depend on the destination city."),
 ("D09", "Home broadband allowance: staff on hybrid contracts can claim up to INR 1,500 per month for internet by uploading the bill in the expenses app."),
 ("D10", "Mobile phone policy: company-issued phones are for business use; personal numbers can be ported only after 24 months."),
 ("D11", "Expense claims must be submitted within 60 days of the transaction date; late claims need director approval."),
 ("D12", "SOX control ITGC-04: production database changes require a ticket, peer review and evidence of approval stored for seven years."),
 ("D13", "GDPR and DPDP data subject requests: respond to access or erasure requests within the statutory deadline; log every request in the privacy register."),
 ("D14", "To reset your SSO password, open the identity portal, choose 'Forgot password' and confirm with the authenticator app push notification."),
 ("D15", "If you are locked out after too many failed sign-in attempts, wait 15 minutes or call the service desk to unlock your account."),
 ("D16", "VPN client error 809 means the network blocked the IKEv2 ports; switch to the SSL-VPN profile or try a different Wi-Fi network."),
 ("D17", "VPN client error 691 means invalid credentials; your password may have expired, so reset it in the identity portal first."),
 ("D18", "Parental leave: birthing parents receive 26 weeks of paid leave; other parents receive 6 weeks, which can be taken within the first year."),
 ("D19", "Sick leave: up to 12 days of paid sick leave per year; a medical certificate is required for absences longer than 3 consecutive days."),
 ("D20", "Laptop refresh: hardware is replaced every 4 years, or earlier if the device fails a diagnostics check by IT."),
 ("D21", "Lost or stolen laptops must be reported to the security operations centre within 1 hour so the device can be remotely wiped."),
 ("D22", "Kubernetes pod stuck in CrashLoopBackOff: check container logs, liveness probe thresholds and missing secrets or config maps."),
 ("D23", "Kubernetes pod stuck in ImagePullBackOff: the registry credentials are wrong or the image tag does not exist."),
 ("D24", "Model gateway quota: each team gets 2 million tokens per day on the shared LLM gateway; request increases through the FinOps form."),
 ("D25", "Prompt and model changes for customer-facing assistants must pass the offline evaluation gate before release."),
 ("D26", "Annual leave carry-over: up to 10 unused days may be carried into the next calendar year; the rest lapse on 31 March."),
]
QUERIES = [  # (query, relevant doc ids)  -- mix of lexical and semantic
 ("ORA-01555", {"D01"}),
 ("why does my overnight report die with 'snapshot too old'", {"D01", "D03"}),
 ("KB-7741 steps", {"D04"}),
 ("how do I undo a bad payroll release", {"D04"}),
 ("can I work remotely from another country for a few weeks?", {"D06"}),
 ("HR-217", {"D06"}),
 ("get reimbursed for home internet", {"D09"}),
 ("vpn error 809", {"D16"}),
 ("VPN says my username or password is wrong", {"D17"}),
 ("I forgot my login password", {"D14"}),
 ("container keeps restarting in k8s", {"D22"}),
 ("my team ran out of LLM tokens today", {"D24"}),
 ("how much time off do new dads get", {"D18"}),
 ("someone stole my work computer", {"D21"}),
 ("ITGC-04 evidence retention", {"D12"}),
 ("unused vacation days next year", {"D26"}),
]
IDS = [d for d, _ in CORPUS]
TEXTS = [t for _, t in CORPUS]

# --- 2. Retrievers -----------------------------------------------------------------
def build_bm25():
    r = bm25s.BM25()                       # default Lucene-style BM25, k1=1.5, b=0.75
    r.index(bm25s.tokenize(TEXTS, stopwords="en", show_progress=False), show_progress=False)
    def search(q, k):
        res, sc = r.retrieve(bm25s.tokenize([q], stopwords="en", show_progress=False),
                             k=min(k, len(TEXTS)), show_progress=False)
        return [(IDS[i], float(s)) for i, s in zip(res[0], sc[0]) if s > 0]
    return search

def build_dense(device):
    m = SentenceTransformer("BAAI/bge-small-en-v1.5", device=device)
    emb = m.encode(TEXTS, normalize_embeddings=True, convert_to_numpy=True).astype("float32")
    index = faiss.IndexFlatIP(emb.shape[1])  # exact cosine (vectors are normalised)
    index.add(emb)
    instr = "Represent this sentence for searching relevant passages: "  # bge query prefix
    def search(q, k):
        qv = m.encode([instr + q], normalize_embeddings=True, convert_to_numpy=True).astype("float32")
        sc, ix = index.search(qv, k)
        return [(IDS[i], float(s)) for i, s in zip(ix[0], sc[0]) if i >= 0]
    return search

# --- 3. Fusion -----------------------------------------------------------------------
def rrf(ranked_lists, k=60, weights=None):
    """Reciprocal Rank Fusion: score(d) = sum_i w_i / (k + rank_i(d)), rank starts at 1."""
    weights = weights or [1.0] * len(ranked_lists)
    fused = {}
    for w, lst in zip(weights, ranked_lists):
        for rank, (doc, _) in enumerate(lst, start=1):
            fused[doc] = fused.get(doc, 0.0) + w / (k + rank)
    return sorted(fused.items(), key=lambda x: x[1], reverse=True)

def convex(ranked_lists, alpha=0.5):
    """Score fusion: alpha*minmax(dense) + (1-alpha)*minmax(bm25). Sensitive to score shapes."""
    def mm(lst):
        if not lst: return {}
        s = np.array([x[1] for x in lst]); lo, hi = s.min(), s.max()
        return {d: (v - lo) / (hi - lo + 1e-9) for d, v in lst}
    bm, de = mm(ranked_lists[0]), mm(ranked_lists[1])
    docs = set(bm) | set(de)
    fused = {d: alpha * de.get(d, 0) + (1 - alpha) * bm.get(d, 0) for d in docs}
    return sorted(fused.items(), key=lambda x: x[1], reverse=True)

def build_reranker(device):
    ce = CrossEncoder("cross-encoder/ms-marco-MiniLM-L6-v2", device=device)
    text_of = dict(CORPUS)
    def rerank(q, candidates, top_n=20):
        cands = [d for d, _ in candidates[:top_n]]
        scores = ce.predict([(q, text_of[d]) for d in cands])
        return sorted(zip(cands, map(float, scores)), key=lambda x: x[1], reverse=True)
    return rerank

# --- 4. Metrics ----------------------------------------------------------------------
def recall_at(ranked, rel, k): return len({d for d, _ in ranked[:k]} & rel) / len(rel)
def mrr_at(ranked, rel, k=10):
    for i, (d, _) in enumerate(ranked[:k], 1):
        if d in rel: return 1 / i
    return 0.0

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rrf-k", type=int, default=60)
    ap.add_argument("--cand", type=int, default=20, help="candidates per retriever / for rerank")
    args = ap.parse_args()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"device={device}  docs={len(CORPUS)}  queries={len(QUERIES)}")
    bm25, dense, rerank = build_bm25(), build_dense(device), build_reranker(device)
    dense("warm-up", 1); rerank("warm-up", [(IDS[0], 0)])           # exclude load time

    systems = {
        "bm25": lambda q, b, d: b,
        "dense": lambda q, b, d: d,
        "hybrid_rrf": lambda q, b, d: rrf([b, d], k=args.rrf_k),
        "hybrid_convex": lambda q, b, d: convex([b, d], alpha=0.5),
        "hybrid_rrf+rerank": lambda q, b, d: rerank(q, rrf([b, d], k=args.rrf_k), args.cand),
    }
    stats = {n: {"r3": [], "mrr": [], "ms": []} for n in systems}
    misses = []
    for q, rel in QUERIES:
        t0 = time.perf_counter(); b = bm25(q, args.cand); t_b = time.perf_counter() - t0
        t0 = time.perf_counter(); d = dense(q, args.cand); t_d = time.perf_counter() - t0
        for name, fn in systems.items():
            t0 = time.perf_counter(); ranked = fn(q, b, d); t_f = time.perf_counter() - t0
            base = t_b if name == "bm25" else t_d if name == "dense" else t_b + t_d
            stats[name]["r3"].append(recall_at(ranked, rel, 3))
            stats[name]["mrr"].append(mrr_at(ranked, rel))
            stats[name]["ms"].append((base + t_f) * 1000)
            if name in ("bm25", "dense") and ranked and ranked[0][0] not in rel:
                misses.append((name, q, ranked[0][0]))

    print(f"\n{'system':<20}{'Recall@3':>10}{'MRR@10':>9}{'p50 ms':>9}")
    for n, s in stats.items():
        print(f"{n:<20}{np.mean(s['r3']):>10.3f}{np.mean(s['mrr']):>9.3f}{statistics.median(s['ms']):>9.1f}")
    print("\nTop-1 misses by single retrievers (where hybrid earns its keep):")
    for name, q, top in misses:
        print(f"  [{name:5}] {q!r:<62} -> {top}")

if __name__ == "__main__":
    main()
