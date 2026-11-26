"""Day 48 lab, part 2: review a Dockerfile the way a security reviewer would.

A deliberately small linter: ten rules, each tied to a failure you can name
in an interview. Runs offline and does not need Docker.
    python dockerfile_lint.py Dockerfile.bad Dockerfile.good
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

SECRET_KEY = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)", re.I)
INSTALL = re.compile(r"\b(pip3? install|uv sync|uv pip install|poetry install)\b")


@dataclass
class Finding:
    rule: str
    severity: str  # HIGH, MED or LOW
    line: int
    message: str


def instructions(text: str) -> list[tuple[int, str, str]]:
    """Join continuation lines; return (line number, INSTRUCTION, arguments)."""
    out, buf, start = [], "", 0
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not buf and (not line or line.startswith("#")):
            continue
        if not buf:
            start = n
        buf += line.rstrip("\\") + " "
        if not line.endswith("\\"):
            word, _, rest = buf.strip().partition(" ")
            out.append((start, word.upper(), rest.strip()))
            buf = ""
    return out


def lint(text: str) -> list[Finding]:
    ins = instructions(text)
    found: list[Finding] = []

    def add(rule: str, severity: str, line: int, message: str) -> None:
        found.append(Finding(rule, severity, line, message))

    stages: set[str] = set()
    froms = [i for i in ins if i[1] == "FROM"]
    installed_in_stage = False
    user = None

    for line, word, args in ins:
        if word == "FROM":
            image = args.split()[0]
            installed_in_stage, user = False, None
            if image not in stages and image != "scratch" and "@sha256:" not in image:
                add("D01", "HIGH", line, f"base image '{image}' is a mutable tag; pin it by digest")
            match = re.search(r"\bAS\s+(\S+)", args, re.I)
            if match:
                stages.add(match.group(1))
        elif word in ("ARG", "ENV"):
            for key in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*(?==|$|\s)", args):
                if SECRET_KEY.search(key):
                    add("D02", "HIGH", line, f"{word} {key}: build args and env vars persist in the image; "
                                             "use RUN --mount=type=secret or a run-time mount")
                    break
        elif word == "RUN":
            if re.search(r"(curl|wget)[^|;&]*\|\s*(ba)?sh", args):
                add("D03", "HIGH", line, "remote script piped to a shell: unpinned code runs at build time")
            if INSTALL.search(args):
                installed_in_stage = True
                locked = re.search(r"--require-hashes|--locked|--frozen", args)
                if not locked:
                    add("D04", "HIGH", line, "dependencies installed without a lockfile; "
                                             "today's build is not yesterday's build")
        elif word == "ADD" and re.match(r"https?://", args):
            add("D03", "HIGH", line, "ADD from a URL: unpinned remote content in the image")
        elif word == "COPY":
            src = re.search(r"--from=(\S+)", args)
            if src and src.group(1) not in stages and not src.group(1).isdigit():
                ref = src.group(1)
                if "@sha256:" not in ref and (":" not in ref.split("/")[-1] or ref.endswith(":latest")):
                    add("D05", "MED", line, f"COPY --from={ref} is unpinned; use a version tag or a digest")
            parts = [p for p in args.split() if not p.startswith("--")]
            if not src and parts and parts[0] in (".", "./") and not installed_in_stage:
                add("D06", "MED", line, "whole build context copied before dependencies are installed: "
                                        "every code change rebuilds the dependency layer, and .env rides along "
                                        "unless .dockerignore stops it")
        elif word == "USER":
            user = args.split(":")[0]
        elif word in ("CMD", "ENTRYPOINT"):
            if not args.startswith("["):
                add("D07", "MED", line, f"{word} in shell form: the app is not PID 1, so SIGTERM never "
                                        "reaches it and open streams are cut at shutdown")
            workers = re.search(r"--workers[\"',\s=]+(\d+)", args)
            if workers and int(workers.group(1)) > 1:
                add("D08", "LOW", line, "several workers in one container: on Kubernetes scale pods instead, "
                                        "so limits, probes and metrics describe one process")

    last = ins[-1][0] if ins else 0
    if user in (None, "root", "0"):
        add("D09", "HIGH", last, "final stage runs as root; add a numeric non-root USER")
    if len(froms) == 1:
        add("D10", "LOW", froms[0][0], "single stage: build tools and caches ship to production")
    return sorted(found, key=lambda f: (f.line, f.rule))


def main(paths: list[str]) -> int:
    worst = 0
    for path in paths:
        findings = lint(Path(path).read_text(encoding="utf-8"))
        high = sum(f.severity == "HIGH" for f in findings)
        print(f"== {path}: {len(findings)} finding(s), {high} high ==")
        for f in findings:
            print(f"  {f.rule} {f.severity:<4} line {f.line:<3} {f.message}")
        if not findings:
            print("  clean")
        worst = max(worst, high)
        print()
    return 1 if worst else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["Dockerfile.bad", "Dockerfile.good"]))
