"""Day 3 lab: prompting vs RAG vs LoRA on the same small model.

Two tasks on a fictional bank ("Northwind"):
  A. Behaviour  - classify requests into a strict JSON schema with hidden business rules.
  B. Knowledge  - answer questions about 8 internal policy facts.

Usage (run in order):
  python day3_lab.py baseline   # zero-shot short prompt, then rules + few-shot prompt, then RAG for task B
  python day3_lab.py train      # LoRA SFT on task A + memorised Q&A for task B (~5-10 min on an RTX 3050)
  python day3_lab.py finetuned  # same evals with the adapter loaded (short prompt only), plus FT + RAG
"""
import json, random, re, sys, time
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM

MODEL_ID = "Qwen/Qwen3-0.6B"
ADAPTER_DIR = "out/qwen3-0.6b-lora"
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
DTYPE = torch.bfloat16 if DEVICE == "cuda" and torch.cuda.is_bf16_supported() else torch.float32
random.seed(7)

# ---------------- Task A: behaviour / format ----------------
SYSTEMS = ["Snowflake", "Salesforce", "core-banking", "HRMS", "Jira", "Kafka"]
ENVS = ["prod", "staging", "dev"]
TRAIN_TPL = {
    "access_request": ["Please grant me read access to the {sys} {env} database.",
                       "I need admin rights on {sys} in {env}.",
                       "Can you add me to the {sys} {env} user group?"],
    "data_export": ["Export all {sys} customer records to a CSV for me.",
                    "I want to download the {sys} table to my laptop.",
                    "Please send a full dump of {sys} data to the vendor."],
    "incident": ["{sys} is down in {env} and users cannot log in.",
                 "We are seeing 500 errors from {sys}.",
                 "{sys} {env} latency spiked to 10 seconds."],
    "policy_question": ["What is the policy on using {sys} from home?",
                        "Am I allowed to install plugins in {sys}?",
                        "Who approves changes to {sys} access rules?"],
}
TEST_TPL = {  # different phrasings: tests generalisation, not memorisation
    "access_request": ["Requesting write permissions to {sys} ({env}) for the migration.",
                       "Could I get onboarded to {sys} {env}?"],
    "data_export": ["Need a copy of every {sys} transaction extracted to Excel.",
                    "Can you share the raw {sys} dataset with our partner?"],
    "incident": ["Alerts firing: {sys} pods crash-looping in {env}.",
                 "Customers report {sys} payments failing."],
    "policy_question": ["Is it okay to use personal email with {sys}?",
                        "What are the rules for sharing {sys} screenshots?"],
}

def label(intent, env):
    if intent == "access_request":
        hi = env == "prod"
        return {"intent": intent, "risk": "high" if hi else "low", "needs_approval": hi}
    if intent == "data_export":
        return {"intent": intent, "risk": "high", "needs_approval": True}
    if intent == "incident":
        return {"intent": intent, "risk": "medium", "needs_approval": False}
    return {"intent": intent, "risk": "low", "needs_approval": False}

def make_rows(tpls, all_envs):
    rows = []
    for intent, tl in tpls.items():
        for t in tl:
            for s in SYSTEMS:
                envs = ENVS if all_envs else [random.choice(ENVS)]
                for e in envs:
                    rows.append((t.format(sys=s, env=e), label(intent, e)))
    return rows

SYS_SHORT = "Classify the request. Reply with JSON only."
SYS_RULES = (
    'Classify the request. Reply with JSON only, exactly this schema: '
    '{"intent": "access_request|data_export|incident|policy_question", '
    '"risk": "low|medium|high", "needs_approval": true|false}. '
    "Rules: access to prod is high risk and needs approval; access to non-prod is low, no approval; "
    "any data export is high risk and needs approval; incidents are medium, no approval; "
    "policy questions are low, no approval.\n"
    'Example: "I need admin rights on Jira in prod." -> {"intent": "access_request", "risk": "high", "needs_approval": true}\n'
    'Example: "We are seeing 500 errors from Kafka." -> {"intent": "incident", "risk": "medium", "needs_approval": false}\n'
    'Example: "Export all HRMS customer records to a CSV for me." -> {"intent": "data_export", "risk": "high", "needs_approval": true}'
)

# ---------------- Task B: knowledge ----------------
FACTS = [
    ("Travel policy TP-4: hotel spend is capped at INR 9,000 per night in metro cities.",
     "What is the hotel cap per night in metro cities under TP-4?",
     "How much can I spend on a hotel per night on a trip to Mumbai?", ["9,000", "9000"]),
    ("Records policy RP-2: customer chat transcripts are retained for 7 years, then purged.",
     "How long are customer chat transcripts retained?",
     "When do we delete old customer chat transcripts?", ["7 years", "seven years"]),
    ("Security policy SP-9: production database passwords rotate every 45 days.",
     "How often do production database passwords rotate?",
     "What's the rotation interval for prod DB passwords?", ["45"]),
    ("HR policy HR-3: new joiners get 18 days of paid leave per year.",
     "How many paid leave days do new joiners get?",
     "How much annual paid leave does a fresh hire receive?", ["18"]),
    ("IT policy IT-7: laptops must be returned within 5 working days of an employee's exit.",
     "Within how many days must laptops be returned after exit?",
     "I'm leaving the company - how long do I have to hand back my laptop?", ["5 working", "five working", "5 days", "five days"]),
    ("Expense policy EX-1: client dinners above INR 12,000 need VP approval.",
     "What client dinner amount needs VP approval?",
     "Above what bill does a client dinner require VP sign-off?", ["12,000", "12000"]),
    ("Remote work policy RW-2: employees may work from abroad for at most 30 days a year.",
     "How many days a year can employees work from abroad?",
     "What's the annual limit on days I can work from abroad?", ["30", "thirty"]),
    ("Incident policy IN-5: Sev-1 incidents must be reported to the CISO within 2 hours.",
     "How fast must Sev-1 incidents be reported to the CISO?",
     "What's the deadline to tell the CISO about a Sev-1 outage?", ["2 hours", "two hours"]),
]
SYS_QA = "You answer questions about Northwind Bank internal policy. Be brief."
STOP = set("a an the to of in on for is are what how do does i my can much many under after with about a's".split())

def words(t):
    return {w.rstrip("s") for w in re.findall(r"[a-z0-9]+", t.lower()) if w not in STOP}

def retrieve(q, k=2):
    ranked = sorted(FACTS, key=lambda f: len(words(q) & words(f[0])), reverse=True)
    return [f[0] for f in ranked[:k]]

# ---------------- model helpers ----------------
def load(adapter=False):
    tok = AutoTokenizer.from_pretrained(MODEL_ID)
    model = AutoModelForCausalLM.from_pretrained(MODEL_ID, dtype=DTYPE).to(DEVICE)
    if adapter:
        from peft import PeftModel
        model = PeftModel.from_pretrained(model, ADAPTER_DIR)
    model.eval()
    return tok, model

def chat(tok, model, system, user, max_new_tokens=80):
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    text = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True, enable_thinking=False)
    enc = tok(text, return_tensors="pt").to(DEVICE)
    with torch.no_grad():
        out = model.generate(**enc, max_new_tokens=max_new_tokens, do_sample=False)
    n_in = enc["input_ids"].shape[1]
    return tok.decode(out[0, n_in:], skip_special_tokens=True).strip(), n_in

def eval_a(tok, model, system, rows, name):
    valid = exact = tokens = 0
    t0 = time.time()
    for text, gold in rows:
        out, n_in = chat(tok, model, system, text)
        tokens += n_in
        m = re.search(r"\{.*\}", out, re.S)
        try:
            pred = json.loads(m.group(0)) if m else None
        except json.JSONDecodeError:
            pred = None
        if isinstance(pred, dict):
            valid += 1
            exact += int(pred == gold)
    n = len(rows)
    print(f"[A] {name:<22} valid_json={valid/n:5.0%}  exact_match={exact/n:5.0%}  "
          f"avg_prompt_tokens={tokens/n:5.0f}  sec/req={(time.time()-t0)/n:.2f}")

def eval_b(tok, model, name, use_rag, which="test"):
    hits = 0
    for doc, q_train, q_test, answers in FACTS:
        q = q_test if which == "test" else q_train
        user = q
        if use_rag:
            ctx = "\n".join(retrieve(q))
            user = f"Context:\n{ctx}\n\nQuestion: {q}\nAnswer only from the context."
        out, _ = chat(tok, model, SYS_QA, user, max_new_tokens=60)
        hits += int(any(a.lower() in out.lower() for a in answers))
    print(f"[B] {name:<22} accuracy={hits/len(FACTS):5.0%}  ({which} phrasing)")

TEST_A = random.sample(make_rows(TEST_TPL, all_envs=False), 40)

def baseline():
    tok, model = load()
    eval_a(tok, model, SYS_SHORT, TEST_A, "prompt: short")
    eval_a(tok, model, SYS_RULES, TEST_A, "prompt: rules+3-shot")
    eval_b(tok, model, "no context", use_rag=False)
    eval_b(tok, model, "RAG (keyword top-2)", use_rag=True)

def train():
    from datasets import Dataset
    from peft import LoraConfig
    from trl import SFTConfig, SFTTrainer
    tok = AutoTokenizer.from_pretrained(MODEL_ID)
    model = AutoModelForCausalLM.from_pretrained(MODEL_ID, dtype=DTYPE)
    rows = []
    for text, gold in make_rows(TRAIN_TPL, all_envs=True):
        rows.append({"prompt": [{"role": "system", "content": SYS_SHORT}, {"role": "user", "content": text}],
                     "completion": [{"role": "assistant", "content": json.dumps(gold)}]})
    for doc, q_train, _, _ in FACTS:  # knowledge injection: one phrasing, repeated
        for _ in range(12):
            rows.append({"prompt": [{"role": "system", "content": SYS_QA}, {"role": "user", "content": q_train}],
                         "completion": [{"role": "assistant", "content": doc.split(": ", 1)[1]}]})
    random.shuffle(rows)
    print(f"training rows: {len(rows)}")
    cfg = SFTConfig(
        output_dir=ADAPTER_DIR, num_train_epochs=2, per_device_train_batch_size=4,
        gradient_accumulation_steps=2, learning_rate=2e-4, lr_scheduler_type="cosine",
        warmup_steps=5, logging_steps=10, max_length=384, bf16=(DTYPE == torch.bfloat16),
        save_strategy="no", report_to="none",
    )
    lora = LoraConfig(r=16, lora_alpha=32, lora_dropout=0.05, target_modules="all-linear", task_type="CAUSAL_LM")
    trainer = SFTTrainer(model=model, args=cfg, train_dataset=Dataset.from_list(rows),
                         processing_class=tok, peft_config=lora)
    trainer.model.print_trainable_parameters()
    trainer.train()
    trainer.save_model(ADAPTER_DIR)
    print(f"adapter saved to {ADAPTER_DIR}")

def finetuned():
    tok, model = load(adapter=True)
    eval_a(tok, model, SYS_SHORT, TEST_A, "LoRA: short prompt")
    eval_b(tok, model, "LoRA, no context", use_rag=False, which="train")
    eval_b(tok, model, "LoRA, no context", use_rag=False, which="test")
    eval_b(tok, model, "LoRA + RAG", use_rag=True)

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "baseline"
    print(f"device={DEVICE} dtype={DTYPE}")
    {"baseline": baseline, "train": train, "finetuned": finetuned}[mode]()
