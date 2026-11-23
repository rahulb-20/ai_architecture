# Day 22 — Cloud Reference Architectures for GenAI

> Part of the [GenAI Architect study path](../../README.md) · Cloud Production Architecture · [Original interactive pack](https://claude.ai/artifact/XGuuu4TKDRREAFyWinNTWk) · [Offline HTML](../../archive/html/day-22.html)


DAY 22 / 30 · WEEK 4 — CLOUD, PRODUCTION & ARCHITECTURE

# Cloud reference architectures for GenAI

Microsoft Foundry (Azure OpenAI), Amazon Bedrock, Gemini Enterprise Agent Platform (formerly Vertex AI) and watsonx.ai, compared on the four things a security review actually probes: network path, identity, where prompts are processed, and where data comes to rest.

Sat 31 Oct 2026~90 min exercise · no cloud account neededRuns on CPU

## Why this matters for the JD

The JD's must-have line "cloud architecture for GenAI (compute, networking, storage, IAM, logging/monitoring, managed services)" and the good-to-haves "security (RBAC/IAM, secrets, encryption, network segmentation)" and "compliance" all land here. In an architecture review the first hard question about any managed LLM is rarely the model. It's *"show me the packet path and tell me which country the prompt is processed in."* Today is about answering that for all four platforms you'll be asked about, watsonx included.

## Concept brief

### The reference shape is the same on every cloud

Strip the branding and every enterprise GenAI landing zone has six layers: ingress (WAF / API gateway), a private app tier, an **AI gateway** that owns model routing, quotas and logging, a **model plane** reached over private link, a **data plane** (vector store, blob, cache) behind its own private endpoints, and cross-cutting identity, keys and observability. Nothing in the app tier holds a model API key; nothing in the model or data plane accepts public traffic.

![Diagram 1](diagrams/fig-1.svg)

### Residency has two axes, and only one is set by "region"

Where your resource and stored data live (**at rest**) is the region you pick. Where a prompt is **processed** is set separately, by a deployment type or inference profile. This is the single most common gap in GenAI designs reviewed by architects coming from classic cloud.

- **Microsoft Foundry (Azure OpenAI)**: `GlobalStandard` / `GlobalProvisionedManaged` / `GlobalBatch` may process in any Azure region. `DataZone*` processes only inside the US, EU or APAC zone. `Standard` and `ProvisionedManaged` process within the resource's geography. Microsoft states data at rest stays in the designated geography in all cases (the Developer tier is the exception, with no residency guarantee). Note: an India resource on Data Zone is still "APAC", not "India".
- **Amazon Bedrock**: calling a model ID directly stays in-region. Geographic cross-Region inference profiles (`us.`, `eu.`, `apac.`, `in.` …) keep processing inside that geography. `global.` profiles can route to any commercial Region, about 10% cheaper. CloudTrail/CloudWatch stay in the source Region, but AWS notes prompts kept for abuse detection are stored in the *destination* Region.
- **Gemini Enterprise Agent Platform** (Vertex AI was rebranded at Next '26): the `global` endpoint maximises availability; a regional endpoint such as `asia-south1` pins processing. Perimeter control is VPC Service Controls; private access is Private Service Connect / Private Google Access.
- **watsonx.ai**: IBM Cloud regions are Dallas, Frankfurt, London, Tokyo, Sydney and Toronto. For India, watsonx.ai, .governance, .data and Orchestrate have been available as SaaS on AWS Mumbai (`ap-south-1`) since July 2025. The sovereign option is watsonx software on OpenShift, on-prem or in your own VPC. On IBM Cloud, the runtime takes `--service-endpoints public|private|public-and-private`; private turns off internet access.

### Provider mapping (use this in reviews)

| Concern | Azure / Foundry | AWS / Bedrock | GCP / GEAP | IBM / watsonx |
| --- | --- | --- | --- | --- |
| Private path to model | Private Endpoint + `privatelink.openai.azure.com` DNS zone; public network access off | Interface VPC endpoint (PrivateLink) for `bedrock-runtime`; endpoint policy | Private Service Connect / Private Google Access | Private service endpoint / VPE on IBM Cloud; AWS PrivateLink for SaaS on AWS |
| Exfiltration perimeter | Disable public access, NSG + firewall egress, Azure Policy deny | SCPs + VPC endpoint policies; deny `global.*` via `bedrock:InferenceProfileArn` | VPC Service Controls perimeter | Context-based restrictions; or OpenShift network policies |
| Workload identity | Managed identity + "Cognitive Services OpenAI User"; local (key) auth off | IAM role, `bedrock:InvokeModel` scoped to model / profile ARNs | Service account / workload identity federation | Service ID or trusted profile + IAM access groups |
| Processing locality knob | Deployment type: Global / DataZone / Standard | Model ID vs geo / global inference profile | Global vs regional endpoint | Region choice; software for sovereignty |
| Keys | CMK in Key Vault | KMS CMK | CMEK (Cloud KMS) | Key Protect / Hyper Protect Crypto Services |
| Gateway | API Management AI gateway | API Gateway + Lambda, or a self-run gateway | Apigee | watsonx.governance for policy; any gateway in front |

**DPDP reality check:** India's DPDP Act does not impose blanket localisation; it allows cross-border transfer except to countries the government restricts by notification. Sector rules (RBI on payment data, SEBI, IRDAI) and client contracts are usually stricter. Treat "in-India processing" as a policy choice you can justify, not as DPDP's default. Confirm with the client's legal team; this is not legal advice.

## Trade-offs & decision table

| Option | Pick when | Failure modes | Cost / latency |
| --- | --- | --- | --- |
| In-region / geography strict residency | Personal, regulated or contract-bound data; the regulator or client wants a named country | Lower quota and 429s at peak; new models arrive late in India regions; single-region outage takes you down | List price, sometimes higher; lowest network RTT from in-country users |
| Data zone / geo profile regional bloc | "Stay in the EU" or "stay in APAC" is enough; you need more burst capacity | APAC ≠ India: fails a country-level policy. Engineers assume the zone is the region | Comparable to standard; better throughput; small extra latency when routed to another Region |
| Global SKU / global. profile no residency | Public or synthetic data, internal tooling, burst capacity, earliest model access | Silent compliance breach if personal data is routed here. Abuse-monitoring copies stored where the request ran | Cheapest (Bedrock ~10% less) and highest quota; latency varies per request |
| Provisioned throughput (PTU, Bedrock provisioned, GCP provisioned) | Steady high volume with a p95 latency SLO | You pay for idle capacity; capacity is per region, so DR doubles the commitment | Predictable latency; break-even only at sustained utilisation |
| Self-hosted (vLLM / watsonx software on OpenShift, AKS/EKS/GKE GPUs) | Air-gapped or sovereign needs; open-weight models (Granite, Llama) are good enough | GPU scarcity, patching, eval and safety stack all become your job | Highest fixed cost; cheapest at very high, steady volume; you control the latency |

**Defensible default:** route by data classification at the AI gateway. Personal/sensitive classes go to in-geo deployments only; public classes may use global capacity. Enforce it twice: in IaC policy (CI) and in cloud guardrails (Azure Policy / SCP / VPC-SC), so neither a developer nor a Terraform module can opt out.

## Hands-on: residency & network policy-as-code

**Goal (60–90 min):** build a CI-ready checker that reads a multi-cloud GenAI estate spec, works out *where each prompt is actually processed*, and fails the build on residency, network, identity and logging violations. Then see what a generic IaC scanner (Checkov) catches and what it misses. No cloud account, no GPU, no Docker.

### Setup (PowerShell, Python 3.13)

**PowerShell**

```powershell
mkdir day22-lz; cd day22-lz
py -m venv .venv
.\.venv\Scripts\Activate.ps1   # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
python -m pip install --upgrade pip
python -m pip install pyyaml pydantic rich checkov
mkdir tf
```

Tested with pydantic 2.13, rich 15.0, PyYAML 6.0.3 and checkov 3.4.0 on Python 3.13. Checkov is a big install (about 1–2 minutes).

### Steps

1. **10 min.** Save `landing_zone.yaml` (below). It describes five workloads across Azure, AWS, GCP and watsonx, under a strict "process in India" policy. Before running anything, predict which ones fail.
2. **20 min.** Save `lz_check.py`. Read `processing_scope()` closely: it encodes today's key idea (deployment type / profile prefix → processing scope).
3. **10 min.** Run it and check the exit code. Fix the two HIGH findings in the YAML (Azure → `Standard`; Bedrock → an in-region model ID or the `in.` profile) and re-run until it exits 0.
4. **15 min.** Save `tf/main.tf` and run Checkov. Note that it flags public access, local auth, missing managed identity and CMK, but *not* the `GlobalStandard` SKU. Generic scanners don't model residency.
5. **15 min.** Write a 5-line note for a review board: which control catches which failure, and where each runs (PR / plan / runtime).

**landing_zone.yaml** · `lab/landing_zone.yaml`

```yaml
# Declarative spec of an India-based enterprise's GenAI estate across 4 clouds.
# Policy: personal data of Indian users -> processing must stay in India (stricter
# than DPDP's default, chosen by the bank's own risk policy / RBI-style guidance).
policy:
  allowed_processing_geos: [IN]          # where prompts/completions may be processed
  allowed_at_rest_geos: [IN]
  require_private_network: true
  forbid_static_keys: true               # managed identity / IAM roles / service IDs only
  require_prompt_logging: true           # to YOUR log sink, in-geo
  max_cross_region_hops: 0

workloads:
  - name: hr-policy-copilot
    provider: azure
    service: Microsoft Foundry (Azure OpenAI)
    region: centralindia
    deployment_type: GlobalStandard       # cheapest + highest quota... but processes anywhere
    network: private_endpoint
    auth: managed_identity
    logging: {prompts: true, sink_region: centralindia}
    data_class: personal

  - name: claims-summariser
    provider: aws
    service: Amazon Bedrock
    region: ap-south-1
    model_id: apac.anthropic.claude-sonnet   # geographic CRIS profile
    network: vpc_endpoint
    auth: iam_role
    logging: {prompts: true, sink_region: ap-south-1}
    data_class: personal

  - name: kyc-doc-extractor
    provider: aws
    service: Amazon Bedrock
    region: ap-south-1
    model_id: in.amazon.nova-pro             # India geographic profile
    network: vpc_endpoint
    auth: iam_role
    logging: {prompts: true, sink_region: ap-south-1}
    data_class: personal

  - name: marketing-copy
    provider: gcp
    service: Gemini Enterprise Agent Platform (ex-Vertex AI)
    region: global
    network: public
    auth: api_key
    logging: {prompts: false}
    data_class: public

  - name: compliance-rag
    provider: ibm
    service: watsonx.ai (SaaS on AWS)
    region: ap-south-1
    network: private_endpoint
    auth: service_id
    logging: {prompts: true, sink_region: ap-south-1}
    data_class: personal
```
**lz_check.py** · `lab/lz_check.py`

```python
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
```
**tf/main.tf**

```text
resource "azurerm_cognitive_account" "aoai" {
  name                          = "aoai-hr-copilot-cin"
  location                      = "centralindia"
  resource_group_name           = "rg-genai-cin"
  kind                          = "OpenAI"
  sku_name                      = "S0"
  custom_subdomain_name         = "aoai-hr-copilot-cin"
  public_network_access_enabled = true   # <- checkov should flag
  local_auth_enabled            = true   # <- API keys on: should flag
}

resource "azurerm_cognitive_deployment" "gpt" {
  name                 = "chat"
  cognitive_account_id = azurerm_cognitive_account.aoai.id
  model {
    format  = "OpenAI"
    name    = "gpt-4.1-mini"
    version = "2025-04-14"
  }
  sku {
    name = "GlobalStandard"   # <- residency risk: not caught by generic scanners
  }
}
```
**Run**

```text
python lz_check.py landing_zone.yaml; "exit=$LASTEXITCODE"
python lz_check.py landing_zone.yaml --json > findings.json
checkov -d tf --framework terraform --compact --quiet
```

### Expected output (abridged)

**output**

```text
Residency matrix
 workload           provider  region        processing_scope  at_rest  data
 hr-policy-copilot  azure     centralindia  GLOBAL            IN       personal   (red)
 claims-summariser  aws       ap-south-1    APAC              IN       personal   (red)
 kyc-doc-extractor  aws       ap-south-1    IN                IN       personal
 marketing-copy     gcp       global        GLOBAL            GLOBAL   public
 compliance-rag     ibm       ap-south-1    IN                IN       personal

Findings (5)
 HIGH    hr-policy-copilot  RESIDENCY-PROCESSING  personal data processed in scope 'GLOBAL', allowed ['IN']
 HIGH    claims-summariser  RESIDENCY-PROCESSING  personal data processed in scope 'APAC', allowed ['IN']
 MEDIUM  marketing-copy     NET-PRIVATE           network=public
 MEDIUM  marketing-copy     IAM-NO-STATIC-KEYS    auth=api_key
 MEDIUM  marketing-copy     OBS-PROMPT-LOG        prompt/response logging off
exit=1

# checkov
Passed checks: 0, Failed checks: 5, Skipped checks: 0
Check: CKV_AZURE_238: "Ensure that all Azure Cognitive Services accounts are configured with a managed identity"
Check: CKV_AZURE_247: "Ensure that Azure Cognitive Services account hosted with OpenAI is configured with data loss prevention"
Check: CKV_AZURE_236: "Ensure that Cognitive Services accounts disable local authentication"
Check: CKV_AZURE_134: "Ensure that Cognitive Services accounts disable public network access"
Check: CKV2_AZURE_22: "Ensure that Cognitive Services enables customer-managed key for encryption"
# note: nothing flags sku.name = "GlobalStandard"
```

The model IDs and the TF model version are illustrative; the checker only reads the prefix and SKU. Checkov may print a warning that it can't reach the Prisma guidelines API. That's harmless offline.

### Stretch goals

1. **Close the scanner gap.** Write a custom Checkov Python check (load it with `--external-checks-dir`) that fails `azurerm_cognitive_deployment` when `sku.name` starts with `Global` or `DataZone`. Then add the same rule to a GitHub Actions job that runs both tools on every PR.
2. **Model the data plane and DR.** Add `vector_store`, `embeddings` and `dr_region` fields per workload. Flag a workload whose embeddings model processes out-of-geo or whose DR region breaks the residency rule (for example Mumbai with a Singapore DR). Emit a Mermaid diagram of the estate from the YAML.

## GitHub repos to study

- [Azure/AI-Landing-Zones](https://github.com/Azure/AI-Landing-Zones): Microsoft's AI landing zone reference (Portal, Bicep, Terraform on AVM; in preview). Read the [networking concepts](https://azure.github.io/AI-Landing-Zones/bicep/concepts-networking) page for how hub-spoke, private DNS and Foundry private endpoints fit together.
- [Azure-Samples/AI-Gateway](https://github.com/Azure-Samples/AI-Gateway): 30+ APIM + Foundry labs with Bicep. Study the private connectivity lab, plus token rate-limiting and backend-pool load balancing across regional deployments.
- [awslabs/agentcore-samples](https://github.com/awslabs/agentcore-samples): Bedrock AgentCore samples. Look at `04-infrastructure-as-code` (CloudFormation / CDK / Terraform) for how IAM roles and resources are wired. The Starter Toolkit is deprecated; use the AgentCore CLI.
- [GoogleCloudPlatform/generative-ai](https://github.com/GoogleCloudPlatform/generative-ai): the official Gemini / Agent Platform samples. Use `rag-grounding` and `agents`; for landing-zone IaC the README points to the separate genai-factory blueprints.
- *Bonus for your CoE:* [IBM/watsonx-ai-samples](https://github.com/IBM/watsonx-ai-samples): notebooks split by `cloud` vs `cpd5.x`. Diffing the two shows what changes between SaaS and software (on-prem) deployments.

## Read / watch

1. [Deployment types for Microsoft Foundry Models](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/deployment-types) (Microsoft Learn, updated Aug 2026). The authoritative statement on processing vs at-rest per SKU.
2. [Bedrock geographic cross-Region inference](https://docs.aws.amazon.com/bedrock/latest/userguide/geographic-cross-region-inference.html) and [global cross-Region inference](https://docs.aws.amazon.com/bedrock/latest/userguide/global-cross-region-inference.html). Read the IAM/SCP sections; they're the enforcement recipe.
3. [Google: security controls for generative AI](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/security-controls) (VPC-SC, CMEK, data residency per feature) and IBM's [watsonx regional availability](https://www.ibm.com/docs/en/SSYOK8/wsj/getting-started/regional-datactr.html) + [private service endpoints](https://www.ibm.com/docs/SSYOK8/wsj/analyze-data/ml-service-endpoint.html).

## Interview questions

**1. A team says "We deployed Azure OpenAI in Central India, so we're DPDP- and RBI-compliant." What do you check?**

Region only sets at rest. I'd check: (a) deployment type: Global or DataZone means processing can leave India (DataZone = APAC); (b) every other model call in the flow: embeddings, rerankers, batch, fine-tuning jobs, evaluators; (c) where logs and traces land, including the gateway's and the observability vendor's SaaS region; (d) the vector store and its backups and DR replica; (e) abuse-monitoring data retention, and whether an approved exemption applies; (f) human support access. Then I'd show where policy enforces it: Azure Policy on the SKU, plus a gateway allow-list.

**2. Design private connectivity from an on-prem app to Bedrock or Foundry. What breaks first?**

Hub-spoke: ExpressRoute or Direct Connect into a hub, then a private endpoint or interface VPC endpoint in a spoke, public network access disabled. **DNS breaks first**: on-prem resolvers must forward the service zone (for example `privatelink.openai.azure.com`) to Azure DNS Private Resolver or a Route 53 Resolver inbound endpoint. Otherwise the client resolves the public IP and the call fails, or leaves through the internet egress. Next come TLS inspection proxies breaking streaming (SSE), and endpoint policies that forget the inference-profile ARNs.

**3. How do you stop developers from using Bedrock global inference for regulated workloads?**

Defence in depth. An SCP denies `bedrock:InvokeModel*` where `bedrock:InferenceProfileArn` is `ArnLike arn:aws:bedrock:*:*:inference-profile/global.*` (AWS's documented pattern, together with `aws:RequestedRegion = unspecified`) on regulated OUs. IAM roles are scoped to specific profile and model ARNs. The AI gateway keeps an allow-list of model IDs per data class. CI policy-as-code rejects the IaC. The SCP is the control that can't be bypassed; the others give earlier, friendlier feedback.

**4. Your India-only deployment hits 429s at peak. Options, without breaking residency?**

(1) Spread across in-country regions: Central + South India on Azure, Mumbai + Hyderabad on AWS (or the `in.` profile), using gateway backend pools with health-based failover. (2) Buy provisioned throughput for the steady base and send spillover to standard. (3) Shape demand: semantic and prompt caching, smaller models for easy intents, Batch for offline jobs. (4) Classify traffic: only *public*-class requests may spill to DataZone or Global. (5) Queue with back-pressure and honour Retry-After. The latency SLO decides between (2) and (5).

**5. When would you recommend watsonx over a hyperscaler's native model service?**

When governance and deployment flexibility matter more than having the newest frontier model: (a) sovereign or air-gapped needs, met by watsonx software on OpenShift on-prem with the same toolchain as SaaS; (b) a multi-cloud estate wanting one governance plane (watsonx.governance for model inventory, risk and fact sheets); (c) Granite's open weights and IBM's IP indemnity for enterprise use; (d) India residency through SaaS on AWS Mumbai. Trade-offs: a smaller frontier-model catalogue, so you often pair it with a hyperscaler behind the same gateway.

## Architect's lens: ADR-022

Title

Model-plane processing locality for personal-data GenAI workloads (India)

Status

Accepted

Context

A BFSI client, regulated in India, is putting five GenAI workloads on Azure, AWS and watsonx. Its risk policy requires in-India processing for personal data. Peak load is 3× the average. Global SKUs are cheaper and have more quota.

Options

A. Global SKUs everywhere plus contractual assurances. B. Data-zone / geo profiles (APAC). C. In-geo regional deployments + provisioned throughput for the base load, routed by data class at the gateway. D. Self-host open-weight models on OpenShift in the client DC.

Decision

**C**, with D kept for any workload that later requires an air gap. Public-class traffic may use Global capacity through the same gateway.

Consequences

+ Passes the residency audit; enforced in CI (policy-as-code), in the cloud (Azure Policy / SCP) and at runtime (gateway allow-list). − Roughly 10–20% higher unit cost than Global; new models arrive later; PTU is committed per region, so DR doubles the commitment. Revisit when India regions get the target models at Global quota, or when the client's policy relaxes to DPDP baseline.

## Tomorrow

Day 23: Model serving & gateways: vLLM, TGI, Ollama, LiteLLM / AI gateway, rate limits, fallback routing, quotas and caching. Today's data-class router becomes a real gateway.

Sources: Microsoft Learn (Foundry deployment types), AWS Bedrock user guide (geographic and global cross-Region inference), Google Cloud generative-AI security controls and Gemini Enterprise Agent Platform release notes, IBM Docs (watsonx regional availability, watsonx.ai Runtime service endpoints), IBM India newsroom (watsonx on AWS India, Jul 2025). Verified 31 Oct 2026.

## Lab files in this folder

- [`lab/landing_zone.yaml`](lab/landing_zone.yaml)
- [`lab/lz_check.py`](lab/lz_check.py)
