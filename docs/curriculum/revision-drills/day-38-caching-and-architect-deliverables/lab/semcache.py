"""Semantic cache lab: measure hit rate vs FALSE-hit rate across thresholds,
then (optionally) show latency saved against a local Ollama model.
Run:  python semcache.py --sweep        (no LLM needed)
      python semcache.py --demo         (needs `ollama pull llama3.2:3b`)"""
import argparse, time
import numpy as np
from sentence_transformers import SentenceTransformer

EMB = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")  # 384-d, CPU-friendly

# (query, intent_id, scope). Scope = tenant / ACL partition; never share hits across scopes.
SEED = [
    ("How do I reset my VPN password?", "vpn_reset", "it"),
    ("How do I reset my email password?", "mail_reset", "it"),
    ("What is the leave carry-forward limit?", "leave_cf", "hr"),
    ("How many sick leaves do I get per year?", "sick_quota", "hr"),
    ("How do I claim travel reimbursement?", "travel_claim", "finance"),
    ("What is the per-diem for Bengaluru travel?", "perdiem_blr", "finance"),
]
PROBES = [  # paraphrases (should hit) and traps (look similar, different answer)
    ("I forgot my VPN password, how can I reset it?", "vpn_reset", "it"),
    ("steps to reset vpn pwd", "vpn_reset", "it"),
    ("How do I reset my Outlook password?", "mail_reset", "it"),
    ("How do I reset my laptop BIOS password?", "bios_reset", "it"),          # trap
    ("How much leave can I carry forward to next year?", "leave_cf", "hr"),
    ("How many casual leaves do I get per year?", "casual_quota", "hr"),     # trap
    ("Process for submitting a travel expense claim?", "travel_claim", "finance"),
    ("What is the per-diem for Mumbai travel?", "perdiem_mum", "finance"),   # trap
    ("How do I reset my VPN password?", "vpn_reset", "hr"),                  # scope trap
]

class SemanticCache:
    def __init__(self, threshold: float):
        self.t, self.vecs, self.items = threshold, [], []
    def _emb(self, text):
        return EMB.encode([text], normalize_embeddings=True)[0]
    def put(self, q, answer, intent, scope):
        self.vecs.append(self._emb(q)); self.items.append((answer, intent, scope))
    def get(self, q, scope):
        if not self.vecs:
            return None, 0.0
        sims = np.vstack(self.vecs) @ self._emb(q)                      # cosine (normalised)
        sims = np.where([it[2] == scope for it in self.items], sims, -1.0)  # scope filter FIRST
        i = int(sims.argmax())
        return (self.items[i], float(sims[i])) if sims[i] >= self.t else (None, float(sims[i]))

def sweep():
    print(f"{'thr':>5} {'hits':>5} {'false':>6} {'hit%':>6} {'false%':>7}")
    for thr in (0.60, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95):
        c = SemanticCache(thr)
        for q, intent, scope in SEED:
            c.put(q, f"<answer for {intent}>", intent, scope)
        hits = false = 0
        for q, intent, scope in PROBES:
            item, sim = c.get(q, scope)
            if item:
                hits += 1
                false += item[1] != intent
        print(f"{thr:>5.2f} {hits:>5} {false:>6} {hits/len(PROBES):>6.0%} {false/max(hits,1):>7.0%}")

def demo(model="llama3.2:3b", thr=0.85):
    import ollama
    c = SemanticCache(thr)
    for q, intent, scope in [SEED[0], PROBES[0], PROBES[3]]:
        t0 = time.perf_counter()
        item, sim = c.get(q, scope)
        if item:
            print(f"HIT  sim={sim:.3f} {1000*(time.perf_counter()-t0):7.1f} ms  <- {q}")
            continue
        r = ollama.chat(model=model, messages=[{"role": "user", "content": q + " Answer in 2 lines."}])
        dt = time.perf_counter() - t0
        print(f"MISS sim={sim:.3f} {1000*dt:7.1f} ms  in={r.prompt_eval_count} out={r.eval_count} tok <- {q}")
        c.put(q, r.message.content, intent, scope)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep", action="store_true"); ap.add_argument("--demo", action="store_true")
    a = ap.parse_args()
    if a.demo: demo()
    else: sweep()
