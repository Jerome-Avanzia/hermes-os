---
document_id: DOC-0025
title: Hermes V1 Implementation Roadmap
version: 0.1.0
status: Draft
maturity: D1
owner: AVANZIA
document_type: Architecture
authority_level: 2
classification: Internal
created: 2026-07-23
last_updated: 2026-07-23
review_cycle: as-needed
approved_by: Pending
security_review: Pending
related:
  - 00-operating-model.md
  - 02-architecture.md
  - 04-system-map.md
  - 05-roadmap.md
  - 11-business-lifecycle.md
  - 12-decision-engine-specification.md
  - 13-scoring-model.md
  - 14-agent-architecture.md
  - 15-agent-orchestrator.md
  - 16-data-contracts.md
  - 20-executive-model.md
  - 21-agent-registry.md
  - 22-shared-services.md
  - 23-extension-framework.md
  - 24-hermes-agent-integration.md
  - runtime/01-capability-registry.md
  - decisions/DEC-0002-hermes-executive-operating-model.md
  - decisions/DEC-0003-execution-context-git-safety-policy.md
  - decisions/DEC-0004-business-knowledge-system.md
  - decisions/DEC-0005-entity-lifecycle-management.md
tags:
  - roadmap
  - implementation
  - v1
  - sprints
---

# Hermes V1 Implementation Roadmap

> Smallest functional path from approved architecture to an operating Hermes V1, for a company of one.

## 0. Framing — no new executive runtime is required

The frozen architecture already has an executing agent: this Hermes Agent
session (the Hermes CLI runtime — skills, `delegate_task` subagents, cron,
memory, terminal/file tools) **is** the concrete implementation of
docs/20's "Hermes Agent (CEO)" and docs/24's orchestrator role. V1 does not
build a new executive service; it wires the frozen business-object model,
Capability Registry, Knowledge Spaces, and specialist delegation *onto* the
runtime that already exists. This is the single biggest scope reduction
available and is treated as load-bearing for every answer below.

Consequence: for V1 Alpha, "Specialist Agents" (docs/21 §3) are
`delegate_task` subagents invoked per-initiative, not always-on services.
The "Agent Orchestrator" (docs/15) is `delegate_task`'s existing dispatch,
not a new scheduler. The "Decision Engine" (docs/12) and "Scoring Model"
(docs/13) are a reasoning skill applied by Hermes Agent against typed
business objects, not a microservice. No database, API server, or queue is
required to validate the architecture.

---

## 1. Smallest functional Hermes V1

Hermes V1 Alpha is validated when **one real venture** (AKosmicAnimals,
already onboarded) runs one full turn of the docs/11 Business Lifecycle —
Business → Strategy → Goal → KPI → Bottleneck → Opportunity → Decision →
Experiment → Lesson → Executive Brief — driven by Hermes Agent through the
docs/24 §4 ten-step loop, with every step's output validated against its
docs/16 contract and every Decision traceable to DEC-0002's Delegation
Principle. No second venture, no WebUI, no live capability health, and no
networked services are required to prove this.

## 2. Repositories, applications, services required first

No new repository, application, or service is required for Alpha.

| Component | Status | Role |
|---|---|---|
| `hermes-os` | Exists | Governance, schemas, contracts, capability manifests |
| `hermes-os/businesses/AKosmicAnimals/` | Exists | The one venture's Knowledge Space (DEC-0004 physical mapping) |
| `avanzia-identity` | Exists | Read-before-recommend reference (AGENTS.md §6) — consulted, not built |
| Hermes Agent CLI (this runtime) | Exists | Executive runtime — skills, delegation, memory |

The only *new* artifacts are files inside `hermes-os`: capability
manifests, missing contract examples/tests, lifecycle-state fields, and
one new skill implementing the docs/24 §4 loop. A dedicated
`<venture>-ops` repository split-out (DEC-0002 §5) and a `hermes-skills`
registry repo (DEC-0002 §3) remain explicitly deferred — named but not
required until a second venture or skill-sharing need actually arrives.

## 3–5. Sprint plan, mocked vs. fully implemented components

| # | Sprint | Objective | Deliverables | Dependencies | Success Criteria | Complexity |
|---|---|---|---|---|---|---|
| 0 | Contract Closure | Close the gap between docs/16's 10 canonical contracts and today's reality (only Business has an example + test) | `examples/*.example.json` and `tests/validate_contracts.py` coverage for all 10 objects; fix `/opt/hermes` vs `/opt/avanzia` path drift in `config/hermes.yaml`/`bin/hermes` | None | `make validate` passes for all 10 objects | S |
| 1 | Capability Registry backbone | Make runtime/01 real, not aspirational | `runtime/capabilities/*/capability.yaml` for existing capabilities (contract schemas as `tool`, delegate_task as `agent`, filesystem Knowledge Space as `shared_service`); a Python discovery script implementing §5.1 (scan → parse → validate → report) | Sprint 0 | Running the script produces a discovery report matching §5.1/§8.5 with zero validation errors on committed manifests | M |
| 2 | Knowledge Space + Entity Lifecycle | Make DEC-0004 and DEC-0005 physically real for one venture | Formalize `businesses/AKosmicAnimals/` entry types (Fact/Analysis/Recommendation/Assumption/Unknown) per DEC-0004 §1; add `lifecycle_state` + append-only transition log to Business and one other entity type per DEC-0005 §1–2 | Sprint 1 | A Business record shows current state, a logged transition, and remains findable when archived (DEC-0005 §2) | M |
| 3 | Business Object Chain, one venture | Run the full docs/11 chain for AKosmicAnimals with real data | Populated/updated Strategy, Goal, KPI, Bottleneck, Opportunity, Decision, Experiment, Lesson objects for AKosmicAnimals, each schema-valid | Sprints 0–2 | Every object instance validates against its contract; the chain is traceable end-to-end (Goal→KPI→Bottleneck→Opportunity→Decision) | M |
| 4 | Decision Engine + Executive Brief, as skills | Implement docs/12–14 as reasoning skills against real data, not services | A "decision-advisor" skill applying docs/13's weighted scoring to real Opportunities/Bottlenecks; an "executive-brief-generator" skill producing a specs/executive-brief.md-conformant brief | Sprint 3 | One real, scored, ranked recommendation set and one real Executive Brief for AKosmicAnimals, both git-committed | M |
| 5 | Discovery-first loop + delegation | Implement docs/24 §4's 10 steps as one orchestrating skill using `delegate_task` for specialist work | Skill that: loads governance → loads Knowledge Space → calls the Sprint 1 registry → builds a plan → delegates via `delegate_task` → records outcome per DEC-0002 §1/§2 | Sprints 1–4 | One initiative runs the full 10-step loop unattended except for an explicit Founder escalation point | L |
| 6 | Alpha validation | Prove the whole assembled chain in one real operating cycle | One weekly Executive Brief cycle executed live for AKosmicAnimals; at least one Decision recorded and correctly classified against the Delegation Principle (autonomous vs. escalated) | Sprint 5 | Founder reviews the cycle and confirms it matches DEC-0002's boundary in practice, not just on paper | S |

**Recommended order:** exactly the table order above — each sprint's
dependency column is a hard prerequisite; none are parallelizable for a
company of one.

**Mocked/deferred through Alpha (do not build yet):**
- Live capability health/heartbeat (runtime/01 §7.4, §10) — static discovery report is sufficient.
- Registration-conflict handling (runtime/01 §8.3) — no concurrent registries yet to conflict.
- Entity Lifecycle dashboards, restore UI (DEC-0005 §2) — grep/script-level query is sufficient.
- Cross-Space (portfolio) Executive Brief aggregation (DEC-0004 §5) — only one Space exists.
- Agent Orchestrator as a distinct service (docs/15) — `delegate_task` dispatch stands in.
- Remote/networked capability sources, manifest signing (runtime/01 §10).

**Fully implemented through Alpha (no shortcuts):**
- All 10 docs/16 contracts, with examples and passing tests.
- DEC-0004 Knowledge Space entry types and isolation-by-convention.
- DEC-0005 lifecycle_state + append-only transition log (structural guarantees are cheap and load-bearing — skipping them now creates the exact four-vocabulary drift DEC-0005 was written to stop).
- The docs/24 §4 ten-step loop, end to end, for at least one initiative.
- DEC-0002's Delegation Principle applied to a real Decision, not a hypothetical one.

## 6. Major technical risks

1. **Config drift already present.** `config/hermes.yaml` and `bin/hermes` point at `/opt/hermes`, not `/opt/avanzia` (this repo's actual runtime root per AGENTS.md). Left unresolved, any automation built against these files will silently target the wrong path. (Sprint 0.)
2. **Contract debt.** 9 of 10 business objects currently have a schema but no example or test. Building Sprint 3–4 logic against unvalidated schemas risks silent shape drift discovered late.
3. **Logical-only isolation.** DEC-0004 §2 defines Knowledge Space isolation as logical/conventional, not technically enforced, for the current filesystem-per-repo implementation. Through Alpha this is discipline-dependent; no access-control layer exists to catch a mistake.
4. **Identity conflation.** Resolved: docs/18-developer-guide.md now carries an "Architecture vs. Implementation" section stating the current CLI runtime is the first implementation of docs/20's "Hermes Agent (CEO)" — an architectural component, not this or any other specific runtime. Architecture stays implementation-agnostic; only docs/18 changes if the runtime changes.
5. **Non-deterministic Decision Engine.** Running docs/12–13 as an LLM-driven skill (Sprint 4) means recommendations are not bit-for-bit reproducible run-to-run, only evidence-traceable. Acceptable for Alpha; flagged for Beta if reproducibility becomes a real requirement.

## 7. Decisions intentionally postponed past Alpha

- Physical storage technology for Knowledge Spaces beyond repo-per-venture (DEC-0004 already defers this).
- Assets' Entity Lifecycle participation (DEC-0005 §5 already defers this pending a future `specs/asset.md`).
- Splitting AKosmicAnimals or future ventures into standalone `-ops` repositories (DEC-0002 §5 names the pattern but doesn't require early adoption).
- A dedicated `hermes-skills` registry repository (DEC-0002 §3 names it as a future target).
- Any Capability Registry remote sources, signing, or versioned rollout (runtime/01 §10).
- A formal Agent Orchestrator service distinct from `delegate_task`.
- WebUI/dashboard work of any kind.

## 8. Hermes V1 Alpha — definition of done

- Sprints 0–6 complete for exactly one venture (AKosmicAnimals).
- All 10 business-object contracts have passing example validation.
- One real Executive Brief has been generated and committed.
- One real Decision has been recorded and correctly classified under DEC-0002's Delegation Principle.
- The docs/24 §4 loop has run at least once, end to end, with an audit trail (Capability Registry discovery report + lifecycle transition log + recorded Decision).

## 9. Hermes V1 Beta — definition of done

- A second venture (e.g. Serelo) onboarded using the identical pattern, validating DEC-0002 §5's multi-venture assumption under real conditions (DEC-0002 Implementation §5 already schedules this checkpoint).
- Agent Registry (docs/21) populated with at least two real specialist-agent entries beyond the current infra-only `agents/registry.yaml`.
- Entity Lifecycle Management extended to at least one more entity type (e.g. Skills) with restore supported.
- Capability Registry re-discovery (runtime/01 §5.3) runs on a schedule, not only on demand.
- STD-0002/DEC-0003 Git-safety pre-flight gate has been exercised against a real multi-repo operation (not just AKosmicAnimals-in-hermes-os).

## 10. Hermes V1 Production (v1.0) — definition of done

- Cross-Knowledge-Space (portfolio-wide) Executive Brief aggregation implemented per DEC-0004 §5 (read-time only, Founder-requested).
- All Draft/D1 governing documents touched by this roadmap have advanced to at least D3 (Approved) per docs/00 §9, with Technical/Security/Owner review complete.
- CI (`.github/workflows/validate.yml`) enforces contract validation on every push, not just local `make validate`.
- A concrete decision has been made and recorded (not deferred) on whether an Agent Orchestrator service is warranted, based on real multi-venture concurrency evidence from Beta — this roadmap does not pre-decide that outcome.
- Entity Lifecycle dashboarding exists in whatever surface the Founder is actually using day to day (WebUI or equivalent), not just a queryable log.

## Review Checklist

- [ ] Technical Review
- [ ] Security Review
- [ ] Architecture Review
- [ ] Owner Approval
- [ ] Git Commit
- [ ] GitHub Push
- [ ] Project Log Updated
