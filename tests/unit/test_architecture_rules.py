"""The dependency rules from ADR-0001, checked by reading the imports of every module."""
import ast
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "aiarch"

# Third-party packages each layer may import at module level. A new entry here is an architecture decision.
ALLOWED_THIRD_PARTY = {"domain": set(), "ports": set(), "services": {"pydantic"}}
# Internal packages each layer may import.
ALLOWED_INTERNAL = {
    "domain": {"aiarch.domain"},
    "ports": {"aiarch.domain", "aiarch.ports"},
    "services": {"aiarch.domain", "aiarch.ports", "aiarch.services"},
    "adapters": {"aiarch.domain", "aiarch.ports", "aiarch.adapters"},
}
# The only modules outside adapters/ that may name a concrete adapter.
MAY_IMPORT_ADAPTERS = {"container.py", "cli.py", "api/app.py", "evaluation/runner.py"}


def _imports(path: Path, *, top_level_only: bool = False) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nodes = tree.body if top_level_only else ast.walk(tree)
    found: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def _files(layer: str) -> list[Path]:
    return sorted((SRC / layer).rglob("*.py"))


def _internal(module: str) -> str:
    return ".".join(module.split(".")[:2])


@pytest.mark.parametrize("layer", sorted(ALLOWED_INTERNAL))
def test_layer_imports_only_the_layers_beneath_it(layer):
    for path in _files(layer):
        internal = {_internal(m) for m in _imports(path) if m.split(".")[0] == "aiarch"}
        assert internal <= ALLOWED_INTERNAL[layer], \
            f"{path.relative_to(SRC)} imports {sorted(internal - ALLOWED_INTERNAL[layer])}"


@pytest.mark.parametrize("layer", sorted(ALLOWED_THIRD_PARTY))
def test_core_layers_import_no_vendor_package(layer):
    for path in _files(layer):
        third = {m.split(".")[0] for m in _imports(path)} - sys.stdlib_module_names - {"aiarch", "__future__"}
        assert third <= ALLOWED_THIRD_PARTY[layer], \
            f"{path.relative_to(SRC)} imports {sorted(third - ALLOWED_THIRD_PARTY[layer])}"


def test_only_the_composition_root_and_entry_points_name_concrete_adapters():
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel.startswith("adapters/") or rel in MAY_IMPORT_ADAPTERS:
            continue
        if any(m.startswith("aiarch.adapters") for m in _imports(path)):
            offenders.append(rel)
    assert offenders == []


def test_nothing_imports_the_api_or_the_cli():
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel in {"api/app.py", "cli.py"}:
            continue
        assert not any(m.startswith(("aiarch.api", "aiarch.cli")) for m in _imports(path)), rel


def test_optional_dependencies_are_never_imported_at_module_level():
    optional = {"sentence_transformers", "torch", "redis", "openai", "anthropic", "ibm_watsonx_ai", "psycopg",
                "qdrant_client", "opentelemetry", "langchain", "langgraph", "faiss", "chromadb"}
    for path in sorted(SRC.rglob("*.py")):
        top = {m.split(".")[0] for m in _imports(path, top_level_only=True)}
        assert not top & optional, f"{path.relative_to(SRC)} imports {sorted(top & optional)} at module level"


def test_every_package_installed_by_default_is_declared():
    import tomllib
    project = tomllib.loads((SRC.parents[1] / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    declared = {d.split(">")[0].split("=")[0].split("[")[0].strip().replace("-", "_") for d in project["dependencies"]}
    used = set()
    for path in sorted(SRC.rglob("*.py")):
        used |= {m.split(".")[0] for m in _imports(path, top_level_only=True)}
    used -= sys.stdlib_module_names | {"aiarch", "__future__"}
    assert used <= declared, f"imported at module level but not in [project.dependencies]: {sorted(used - declared)}"


def test_no_secret_looking_literal_in_the_source_tree():
    import re
    pattern = re.compile(r"(sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----)")
    for path in sorted(SRC.rglob("*.py")):
        assert not pattern.search(path.read_text(encoding="utf-8")), path.relative_to(SRC)
