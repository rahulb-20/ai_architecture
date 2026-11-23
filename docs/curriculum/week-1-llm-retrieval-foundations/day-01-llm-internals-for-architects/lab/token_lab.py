"""Part A - same text, different tokenizers, different bills."""
import tiktoken
from tokenizers import Tokenizer

SAMPLES = {
    "english_policy": "Employees must submit expense claims within 30 days of travel. "
                      "Claims above INR 50,000 require approval from the cost-centre head.",
    "python_code": "def chunk(text: str, size: int = 512) -> list[str]:\n"
                   "    return [text[i:i + size] for i in range(0, len(text), size)]\n",
    "json_payload": '{"employee_id": "LTI-004512", "claims": [{"amount": 48250.75, '
                    '"currency": "INR", "status": "PENDING_APPROVAL"}]}',
    "hindi": "कर्मचारियों को यात्रा के 30 दिनों के भीतर व्यय दावे जमा करने होंगे।",
    "marathi": "कर्मचाऱ्यांनी प्रवासानंतर ३० दिवसांच्या आत खर्चाचे दावे सादर करावेत.",
}

encoders = {
    "o200k_base (GPT-4o/4.1 family)": tiktoken.get_encoding("o200k_base"),
    "cl100k_base (GPT-4/3.5 era)": tiktoken.get_encoding("cl100k_base"),
}
qwen = Tokenizer.from_pretrained("Qwen/Qwen3-1.7B")  # downloads tokenizer.json only (~11 MB)

print(f"{'sample':<16}{'chars':>6}  " + "".join(f"{n.split()[0]:>13}" for n in encoders) + f"{'qwen3':>9}")
for name, text in SAMPLES.items():
    row = [len(enc.encode(text)) for enc in encoders.values()]
    q = len(qwen.encode(text, add_special_tokens=False).ids)
    print(f"{name:<16}{len(text):>6}  " + "".join(f"{n:>13}" for n in row) + f"{q:>9}"
          f"   chars/token(o200k)={len(text)/row[0]:.2f}")

# Look at how a word actually splits
enc = encoders["o200k_base (GPT-4o/4.1 family)"]
for word in ["watsonx.ai", "InstructLab", " cost-centre", "दावे"]:
    ids = enc.encode(word)
    print(f"{word!r:>16} -> {[enc.decode_single_token_bytes(i) for i in ids]}")
