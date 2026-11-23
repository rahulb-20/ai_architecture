"""Move a prompt alias. Rollback is the same operation pointed backwards: no rebuild, no redeploy.

python promote.py ticket_triage production v2      # promote
python promote.py ticket_triage production v1      # roll back
"""
import json, sys
from datetime import datetime, timezone
from pathlib import Path

REG = Path(__file__).parent / "prompts" / "registry.json"
name, alias, version = sys.argv[1:4]
reg = json.loads(REG.read_text(encoding="utf-8"))
if version not in reg[name]["versions"]:
    sys.exit(f"Unknown version {version}")
old = reg[name]["aliases"].get(alias)
reg[name]["aliases"][alias] = version
REG.write_text(json.dumps(reg, indent=2), encoding="utf-8")
with open(REG.parent / "CHANGELOG.log", "a", encoding="utf-8") as f:   # audit trail
    f.write(f"{datetime.now(timezone.utc).isoformat()} {name} {alias}: {old} -> {version}\n")
print(f"{name}:{alias} {old} -> {version}")
