---
status: proposed            # proposed | accepted | superseded by ADR-00xx
date: 2026-11-07
decision-makers: GenAI architect, platform lead, CISO delegate
---
# Primary LLM and fallback chain for the knowledge assistant

## Context and Problem Statement
50k employees, ~35% daily adoption, India + EU data residency, faithfulness >= 0.90.
Which model serves the default path, and what do we fail over to?

## Decision Drivers
* Cost per answered query <= $0.005 at P90 load
* Faithfulness / abstention scores on the 300-question golden set
* In-region processing; contractual SLA; model retirement horizon

## Considered Options
* Option A: ...   * Option B: ...   * Option C: ...

## Decision Outcome
Chosen option: "...", because ... (link cost_report.md + eval run ID)

### Consequences
* Good, because ...
* Bad, because ...

### Confirmation
CI eval gate on every prompt/model change; monthly cost review vs model.

## Revisit when
* Price change > 30% on any shortlisted model
* Provider announces retirement of the pinned version
* Golden-set faithfulness drops > 3 points for two consecutive weeks
