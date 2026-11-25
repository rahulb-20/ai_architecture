# ai_architecture: working notes for Claude

GenAI reference architecture in Python (hexagonal: `domain` ← `ports` ← `services`, adapters wired only in `container.py`). Read `README.md` and `docs/architecture/HLD.md` before changing anything.

## Checks (all four must pass before any commit is pushed)

```bash
python -m pip install -e ".[dev]"      # once per environment, Python 3.12 or 3.13
ruff check src tests
mypy
pytest
python -m aiarch.evaluation.runner --gate
```

## Rules

- Services and domain never import a vendor SDK or a concrete adapter. New vendors are an adapter plus one branch in `container.py`.
- Everything runs offline by default. A change that needs a network, a key, a GPU or Docker to pass the checks is wrong.
- Never push to `main`. Work on a branch and open a pull request. Never merge a pull request unless the user says so in the conversation.
- Never weaken a test, an eval case or a gate threshold to make a change pass. If one is wrong, say so and stop.
- A decision with trade-offs gets an ADR in `docs/architecture/adr/` (next free number, same format as the existing ones).
- `docs/curriculum/` belongs to the daily study-pack task. Leave it alone unless the task is about the curriculum.
- Commit messages follow Conventional Commits (`fix(scope): ...`, `feat(scope): ...`).

## Skills

- `.claude/skills/add-adapter/`: the steps for putting a vendor, database or SDK behind a port. Read it before designing or writing any adapter.

## Three-agent pipeline

`/pipeline <task>` runs a change through three agents defined in `.claude/agents/`: `ai-architect` (design), `developer` (implementation), `tester` (independent verification). See `docs/guides/agent-pipeline.md`.
