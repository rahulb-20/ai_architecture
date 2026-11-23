import json
import sys
import time

import httpx

url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000/ask/stream"
q = {"question": "Explain RAG vs fine-tuning to a CFO in 3 bullets."}
t0, first, event = time.perf_counter(), None, None
with httpx.stream("POST", url, json=q, timeout=120) as r:
    for line in r.iter_lines():
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:") and event == "token":
            first = first or time.perf_counter() - t0
            print(json.loads(line[5:])["token"], end="", flush=True)
print(f"\n\nTTFT: {first:.2f}s   total: {time.perf_counter() - t0:.2f}s")
