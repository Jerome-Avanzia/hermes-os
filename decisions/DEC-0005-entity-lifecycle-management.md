---
document_id: DEC-0005
title: Adopt Entity Lifecycle Management as a Shared Service
version: 1.0.0
status: Draft
maturity: D1
owner: AVANZIA
document_type: Architecture Decision Record
authority_level: 3
classification: Internal
created: 2026-07-23
last_updated: 2026-07-23
related:
  - 21-agent-registry.md
  - 22-shared-services.md
  - 23-extension-framework.md
  - standards/document-metadata.md
  - specs/business.md
  - decisions/DEC-0004-business-knowledge-system.md
tags:
  - adr
  - shared-services
  - lifecycle
  - architecture
---

# DEC-0005 — Adopt Entity Lifecycle Management as a Shared Service

## Status
Draft (D1)

---

## Context

Hermes OS currently defines lifecycle/status independently in at least
four places, using four different vocabularies:

- STD-0001 (Document Metadata): `status` = Draft/Active/Superseded/
  Archived, plus a separate `maturity` scale D0–D5.
- specs/business.md: `status` = Draft/Active/Paused/Archived, plus a
  separate "Lifecycle" section = Idea→Active→Scaling→Mature→Archived.
- docs/21 (Agent Registry): Proposed→Approved→Active→Suspended→Retired.
- docs/22 / docs/23 (Shared Services, Extensions):
  Proposed→Approved→Active→Deprecated→Retired.

Three of these four independently converged on a near-identical
Proposed→Approved→Active→Deprecated/Retired shape without being told
to, which is evidence the underlying pattern is already the right
abstraction — but nothing currently unifies them, and Business alone
already carries two competing status concepts (`status` field vs.
"Lifecycle" section) with no stated mapping between them.

As more entity types accumulate lifecycles (Leads, Clients, Knowledge
Spaces per DEC-0004, Projects, Workflows, Assets), continuing to define
lifecycle per-module independently would compound this drift rather
than resolve it, and would leave Hermes WebUI needing a bespoke
status/archive/restore UI per entity type instead of one reusable
capability — contrary to docs/00 §6 Core Principle "Reuse Before
Reinventing" and Core Principle "One Source of Truth."

---

## Decision

Hermes OS adopts **Entity Lifecycle Management as a Shared Service**,
per docs/22's existing definition of a Shared Service (reusable,
business-agnostic, centrally governed, documented before
implementation).

1. **Generic primitive:** every participating entity type carries a
   `lifecycle_state` field, a declared ordered/branching set of allowed
   states specific to that entity type, and an explicit transition
   table (from_state → to_state, trigger, authority required). This
   generalizes the pattern already present independently in docs/21,
   docs/22, and docs/23 — it does not invent new vocabulary, it names
   the shape those three already share.
2. **Universal structural guarantees**, applied to every participating
   entity regardless of its specific state set:
   - Current lifecycle state is always exposed.
   - Transitions are logged append-only (who, when, from, to, why),
     feeding Organizational Memory per docs/24 §6.
   - Archived entities remain searchable.
   - Active dashboards hide archived entities by default.
   - Restore (reversing an archive) is supported where the entity type
     allows it.
3. **Two standardized cross-entity states**, by name, across every
   participating entity type: **Active** (default dashboard visibility)
   and **Archived** (hidden by default, searchable, restorable). States
   between "created" and "Active," and between "Active" and "Archived,"
   remain entity-specific (e.g. Lead: New→Contacted→Qualified; Skill:
   Proposed→Active; Business: Idea→Active→Scaling→Mature).
4. **Participating entities:** Leads, Clients, Ventures, Projects,
   Knowledge Spaces (per DEC-0004), Skills, Workflows.
5. **Non-participating entities, by deliberate exclusion:**
   - **Agents** retain docs/21's existing Proposed→Approved→Active→
     Suspended→Retired lifecycle unchanged, because it is tied to
     agent-specific governance/authority concerns (who approved this
     agent to act, at what authority level) that a generic mechanism
     would strip of meaning if genericized.
   - **Documents** retain STD-0001's D0–D5 maturity scale unchanged,
     because it represents a peer-review/approval-gate sequence
     (Technical Review → Security Review → Owner Approval per docs/00
     §11), not a simple state machine, and collapsing it into the
     generic primitive would lose that approval-gate semantics.
   - **Assets** are deliberately left undecided pending a future
     `specs/asset.md` — this ADR does not force a participation
     decision without a spec to evaluate it against.
6. Existing entity lifecycles (Business, Agent Registry, Shared
   Services, Extensions) are **not rewritten**. Each declares its
   current state set as conforming to the generic primitive rather than
   being migrated to new vocabulary — this is a compatibility-preserving
   adoption, not a breaking change.

This decision is recorded as an ADR (not handled as a routine
operational improvement under DEC-0002) because it introduces a new
named Shared Service that other governing documents (docs/21, docs/22,
docs/23, STD-0001, specs/business.md) must reference going forward.

---

## Consequences

### Positive

- Resolves the four-vocabulary drift already present in the repository,
  without waiting for it to compound further.
- Gives Hermes WebUI a single reusable dashboard/search/archive/restore
  mechanism instead of one per entity type.
- Formalizes a pattern (Proposed→Approved→Active→Deprecated→Retired)
  that docs/21–23 already converged on independently, so most existing
  entities require no rework — only a conformance statement.
- Directly plugs into Organizational Memory (docs/24 §6): a lifecycle
  transition is exactly the kind of "significant outcome" that section
  already requires Hermes Agent to record.

### Trade-offs

- Requires every participating entity's spec to be updated (even if
  only to state "conforms to the generic Entity Lifecycle Management
  primitive with state set X") — a documentation pass across
  specs/business.md, a future Lead/Client/Project spec, docs/21-style
  registries for Skills and Workflows if not already present.
- Deliberately does not resolve Assets' participation, leaving one
  open item for a future spec rather than forcing a premature decision.
- If a future entity type's lifecycle turns out to also carry
  governance/approval semantics (like Agents or Documents), it must be
  excluded from the generic mechanism on the same reasoning as this
  ADR's exclusions — this ADR does not specify that test generally
  beyond the two examples given, and future exclusions should show their
  own reasoning rather than citing this ADR mechanically.

---

## Implementation

1. Add Entity Lifecycle Management to docs/22-shared-services.md §4
   (Core Shared Services), with Service Owner, Purpose, Interfaces,
   Dependencies, Consumers per docs/22 §5.
2. Add a new specification, `specs/entity-lifecycle-management.md`,
   defining the generic primitive (state field, transition table, log
   format, Active/Archived conventions) referenced by this ADR.
3. Update specs/business.md to state its existing Lifecycle section
   conforms to this primitive (no state-set change).
4. Confirm docs/21 (Agent Registry) and STD-0001 (Document Metadata)
   each carry an explicit note that they are intentionally excluded
   from the generic mechanism, per this ADR §5, rather than leaving the
   exclusion implicit.
5. New specs for Lead, Client, Project (not yet drafted) should declare
   their state sets against this primitive from the outset rather than
   inventing independent status fields.
6. Hold at Draft/D1 pending Technical, Security, and Owner review before
   advancing maturity, per docs/00 §9.

---

## Review Checklist

- [ ] Technical Review
- [ ] Security Review
- [ ] Architecture Review
- [x] Owner Approval
- [ ] Git Commit
- [ ] GitHub Push
- [ ] Project Log Updated
