# 1) Ollama for Windows (native, uses your NVIDIA GPU). Or download the installer from ollama.com
winget install --id Ollama.Ollama -e
ollama pull qwen3:1.7b          # ~1.4 GB, fits comfortably in 6 GB VRAM with an 8-16k context

# 2) Project + venv (Python 3.13)
mkdir genai-day01; cd genai-day01
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1    # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
python -m pip install -U pip
python -m pip install "tiktoken==0.14.0" "tokenizers==0.23.3" "ollama==0.6.3"

# 3) Sanity check
ollama run qwen3:1.7b "Say OK"
python token_lab.py
