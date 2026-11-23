# Windows task runner. Usage:  .\tasks.ps1 setup | test | lint | eval | serve | ask "question" | docker
param([Parameter(Position=0)][string]$Task = "help", [Parameter(Position=1)][string]$Arg = "")
$ErrorActionPreference = "Stop"
$py = ".\.venv\Scripts\python.exe"
switch ($Task) {
  "setup"  { py -3.13 -m venv .venv; & $py -m pip install --upgrade pip; & $py -m pip install -e ".[dev]" }
  "test"   { & $py -m pytest }
  "lint"   { & $py -m ruff check src tests; & $py -m mypy }
  "eval"   { & $py -m aiarch.evaluation.runner --gate --out reports\eval.json }
  "serve"  { & $py -m aiarch.cli serve }
  "ask"    { & $py -m aiarch.cli ask $Arg --trace }
  "docker" { docker compose up --build }
  "check"  { & $py -m ruff check src tests; & $py -m mypy; & $py -m pytest; & $py -m aiarch.evaluation.runner --gate }
  default  { Write-Host "Tasks: setup, test, lint, eval, serve, ask <question>, docker, check" }
}
