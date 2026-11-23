# Windows 11 setup

Tested target: Python 3.13, PowerShell. No WSL2 or Docker is needed for the default offline mode.

```powershell
git clone https://github.com/rahulb-20/ai_architecture.git
cd ai_architecture
.\tasks.ps1 setup          # creates .venv, installs the package + dev tools
.\tasks.ps1 check          # ruff + mypy + pytest + eval gate
.\tasks.ps1 ask "How many annual leave days can be carried forward?"
.\tasks.ps1 serve          # http://127.0.0.1:8000/docs
```

If script execution is blocked: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

## Use a real local model (RTX 3050 6 GB)
1. Install Ollama for Windows, then run `ollama pull llama3.2:3b` (a ~2 GB model that fits in 6 GB of VRAM).
2. Copy `.env.example` to `.env` and uncomment the Ollama `AIARCH_LLM_ROUTES` line, keeping the `fake` route last as the offline fallback.
3. `.\tasks.ps1 eval`. Expect lower scores than the fake LLM: real models paraphrase, and the deterministic `must_contain` metric is strict.

## Better embeddings
`.\.venv\Scripts\python -m pip install -e ".[local-models]"`, then set `AIARCH_EMBEDDER=sentence-transformers`. This downloads about 90 MB on first run, and CPU is fine for it.

## Docker
`docker compose up --build` needs Docker Desktop with the WSL2 backend.
