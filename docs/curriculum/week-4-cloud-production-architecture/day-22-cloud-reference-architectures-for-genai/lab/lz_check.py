"""Day 22 - GenAI landing-zone policy-as-code checker.

Resolves WHERE each workload's prompts are actually processed (deployment type /
inference profile / endpoint), then evaluates residency, network, identity and
logging rules. Exit code 1 on any HIGH finding -> drop it into CI as a gate.

Usage:  python lz_check.py landing_zone.yaml [--json]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field
from rich.console import Console
from rich.table import Table

# --------------------------------------------------------------------------- #
# Region -> geography maps (extend as needed). "GLOBAL" = may leave any geo.
# --------------------------------------------------------------------------- #
REGION_GEO = {
    # Azure
    "centralindia": "IN", "southindia": "IN", "westindia": "IN",
    "eastus": "US", "eastus2": "US", "swedencentral": "EU", "westeurope": "EU",
    "japaneast": "JP",
    # AWS (also used by watsonx SaaS on AWS)
    "ap-south-1": "IN", "ap-south-2": "IN", "us-east-1": "US", "eu-central-1": "EU",
    # GCP
    "asia-south1": "IN", "asia-south2": "IN", "us-central1": "US", "europe-west4": "EU",
    # IBM Cloud
    "us-south": "US", "eu-de": "EU", "eu-gb": "UK", "jp-tok": "JP",
    "au-syd": "AU", "ca-tor": "CA",
    "global": "GLOBAL",
}
# Azure data zones are US / EU / APAC (per Microsoft Foundry deployment-types doc).
AZURE_DATA_ZONE = {"IN": "APAC", "JP": "APAC", "US": "US", "EU": "EU"}
# Bedrock inference-profile prefixes -> processing scope.
BEDROCK_PREFIX = {"global": "GLOBAL", "us": "US", "eu": "EU", "apac": "APAC", "in": "IN"}

PRIVATE_NETS = {"private_endpoint", "vpc_endpoint", "psc", "vpe"}
STATIC_KEYS = {"api_key", "access_key", "shared_key"}


class Policy(BaseModel):
    allowed_processing_geos: list[str]
    allowed_at_rest_geos: list[str]
    require_private_network: bool = True
    forbid_static_keys: bool = True
    require_prompt_logging: bool = True
    max_cross_region_hops: int = 0


class Logging(BaseModel):
    prompts: bool = False
    sink_region: str | None = None


class Workload(BaseModel):
    name: str
    provider: Literal["azure", "aws", "gcp", "ibm"]
    service: str
    region: str
    deployment_type: str | None = None   # Azure
    model_id: str | None = None          # Bedrock
    network: str
    auth: str
    logging: Logging = Field(default_factory=Logging)
    data_class: Literal["public", "internal", "personal", "sensitive"] = "internal"


class Spec(BaseModel):
    policy: Policy
    workloads: list[Workload]


class Finding(BaseModel):
    workload: str
    severity: Literal["HIGH", "MEDIUM", "LOW"]
    rule: str
    detail: str
    fix: str


def processing_scope(w: Workload) -> str:
    """Where can a prompt for this workload be processed?"""
    geo = REGION_GEO.get(w.region, "UNKNOWN")
    if w.provider == "azure":
        dt = (w.deployment_type or "Standard").lower()
        if dt.startswith("global") or dt == "developertier":
            return "GLOBAL"
        if dt.startswith("datazone"):
            return AZURE_DATA_ZONE.get(geo, "UNKNOWN")
        return geo  # Standard / ProvisionedManaged -> customer geography
    if w.provider == "aws" and w.model_id:
        prefix = w.model_id.split(".", 1)[0]
        return BEDROCK_PREFIX.get(prefix, geo)  # no prefix = in-region on-demand
    return geo


def check(spec: Spec) -> tuple[list[Finding], list[dict]]:
    p, findings, matrix = spec.policy, [], []
    for w in spec.workloads:
        scope = processing_scope(w)
        at_rest = REGION_GEO.get(w.region, "UNKNOWN")
        matrix.append({"workload": w.name, "provider": w.provider, "region": w.region,
                       "processing_scope": scope, "at_rest": at_rest, "data": w.data_class})
        regulated = w.data_class in {"personal", "sensitive"}

        if regulated and scope not in p.allowed_processing_geos:
            fix = {"azure": "Use Standard or ProvisionedManaged in an India region "
                            "(DataZone=APAC still leaves India).",
                   "aws": "Use an in-region model ID or the in. profile; deny global.* "
                          "and other geo profiles via SCP on bedrock:InferenceProfileArn.",
                   "gcp": "Pin a regional endpoint (asia-south1) - not global.",
                   "ibm": "Use a watsonx region in-geo (e.g. SaaS on AWS ap-south-1)."}[w.provider]
            findings.append(Finding(workload=w.name, severity="HIGH", rule="RESIDENCY-PROCESSING",
                                    detail=f"{w.data_class} data processed in scope '{scope}', "
                                           f"allowed {p.allowed_processing_geos}", fix=fix))
        if regulated and at_rest not in p.allowed_at_rest_geos:
            findings.append(Finding(workload=w.name, severity="HIGH", rule="RESIDENCY-AT-REST",
                                    detail=f"resource geo {at_rest}", fix="Move resource region."))
        if p.require_private_network and w.network not in PRIVATE_NETS:
            findings.append(Finding(workload=w.name, severity="HIGH" if regulated else "MEDIUM",
                                    rule="NET-PRIVATE",
                                    detail=f"network={w.network}",
                                    fix="Private Endpoint / VPC interface endpoint / PSC / VPE; "
                                        "disable public network access on the resource."))
        if p.forbid_static_keys and w.auth in STATIC_KEYS:
            findings.append(Finding(workload=w.name, severity="HIGH" if regulated else "MEDIUM",
                                    rule="IAM-NO-STATIC-KEYS", detail=f"auth={w.auth}",
                                    fix="Managed identity / IAM role / workload identity "
                                        "federation; disable local (key) auth."))
        if p.require_prompt_logging and not w.logging.prompts:
            findings.append(Finding(workload=w.name, severity="MEDIUM", rule="OBS-PROMPT-LOG",
                                    detail="prompt/response logging off",
                                    fix="Log via gateway (APIM/AI gateway) to an in-geo sink, "
                                        "with PII masking."))
        if w.logging.prompts and w.logging.sink_region and regulated:
            sink_geo = REGION_GEO.get(w.logging.sink_region, "UNKNOWN")
            if sink_geo not in p.allowed_at_rest_geos:
                findings.append(Finding(workload=w.name, severity="HIGH", rule="RESIDENCY-LOGS",
                                        detail=f"log sink in {sink_geo}",
                                        fix="Logs are data at rest - keep sink in-geo."))
    return findings, matrix


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    spec = Spec.model_validate(yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8")))
    findings, matrix = check(spec)
    if "--json" in sys.argv:
        print(json.dumps({"matrix": matrix, "findings": [f.model_dump() for f in findings]}, indent=2))
    else:
        c = Console()
        t = Table(title="Residency matrix")
        for col in ("workload", "provider", "region", "processing_scope", "at_rest", "data"):
            t.add_column(col)
        for row in matrix:
            ok = row["processing_scope"] in spec.policy.allowed_processing_geos
            style = None if ok or row["data"] == "public" else "red"
            t.add_row(*[str(row[k]) for k in row], style=style)
        c.print(t)
        ft = Table(title=f"Findings ({len(findings)})")
        for col in ("sev", "workload", "rule", "detail", "fix"):
            ft.add_column(col, overflow="fold")
        for f in sorted(findings, key=lambda f: ("HIGH", "MEDIUM", "LOW").index(f.severity)):
            ft.add_row(f.severity, f.workload, f.rule, f.detail, f.fix,
                       style="red" if f.severity == "HIGH" else "yellow")
        c.print(ft)
    return 1 if any(f.severity == "HIGH" for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
