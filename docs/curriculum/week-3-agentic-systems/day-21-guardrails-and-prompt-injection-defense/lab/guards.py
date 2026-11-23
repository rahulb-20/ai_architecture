"""guards.py - layered guardrails: input screen, PII masking, tool policy, output screen."""
import os, re
from presidio_analyzer import AnalyzerEngine
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_analyzer.predefined_recognizers import InAadhaarRecognizer, InPanRecognizer
from presidio_anonymizer import AnonymizerEngine

# --- 1. Input screen: cheap heuristics first, optional Llama Guard 3 (1B) second ---
INJECTION_PATTERNS = [
    r"ignore (all )?(previous|prior|above) instructions",
    r"disregard (the )?(system|previous) prompt",
    r"you are now (dan|in developer mode)",
    r"reveal (your|the) (system prompt|instructions)",
    r"<!--\s*system:",
]
_inj = re.compile("|".join(INJECTION_PATTERNS), re.I)

def heuristic_injection(text: str) -> bool:
    return bool(_inj.search(text))

def llama_guard_unsafe(text: str) -> str | None:
    """Returns hazard category (e.g. 'S2') if Llama Guard flags it, else None.
    Skipped unless USE_LLAMA_GUARD=1 (needs: ollama pull llama-guard3:1b)."""
    if os.getenv("USE_LLAMA_GUARD") != "1":
        return None
    import ollama
    r = ollama.chat(model="llama-guard3:1b", messages=[{"role": "user", "content": text}])
    out = r.message.content.strip()
    return out.split("\n")[1].strip() if out.lower().startswith("unsafe") else None

# --- 2. PII masking with Presidio (small spaCy model keeps RAM low) ---
_nlp = NlpEngineProvider(nlp_configuration={
    "nlp_engine_name": "spacy",
    "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
}).create_engine()
_analyzer = AnalyzerEngine(nlp_engine=_nlp, supported_languages=["en"])
# India-specific recognizers ship with Presidio but are NOT enabled by default
_analyzer.registry.add_recognizer(InPanRecognizer())
_analyzer.registry.add_recognizer(InAadhaarRecognizer())
_anonymizer = AnonymizerEngine()
PII = ["EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD", "IN_PAN", "IN_AADHAAR", "PERSON"]

def mask_pii(text: str, allow: tuple[str, ...] = ()) -> str:
    results = [r for r in _analyzer.analyze(text=text, language="en", entities=PII)
               if text[r.start:r.end] not in allow]
    return _anonymizer.anonymize(text=text, analyzer_results=results).text

# --- 3. Tool policy: allowlist + argument rules + human approval for side effects ---
ALLOWED_TOOLS = {"search_policies", "send_email"}
SIDE_EFFECT_TOOLS = {"send_email"}
ALLOWED_EMAIL_DOMAIN = "acme.example"

def check_tool_call(name: str, args: dict, tainted: bool) -> tuple[str, str]:
    """Returns (decision, reason) where decision in {'allow','deny','ask'}."""
    if name not in ALLOWED_TOOLS:
        return "deny", f"tool '{name}' not on allowlist"
    if name == "send_email":
        if not str(args.get("to", "")).endswith("@" + ALLOWED_EMAIL_DOMAIN):
            return "deny", "external recipient blocked (exfiltration rule)"
        if tainted:
            return "ask", "side-effect after reading untrusted content - needs human approval"
    return "allow", "ok"

# --- 4. Output screen: canary leak + PII scrub ---
CANARY = "CANARY-7f3a91"   # planted in system prompt; must never appear in output

def screen_output(text: str) -> str:
    if CANARY in text:
        return "[blocked: response attempted to leak system instructions]"
    return mask_pii(text, allow=("hr@acme.example",))
