---
document_id: DEC-0004
title: Adopt the Business Knowledge System & Knowledge Space Model
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
  - 00-operating-model.md
  - 02-architecture.md
  - 11-business-lifecycle.md
  - 16-data-contracts.md
  - decisions/DEC-0002-hermes-executive-operating-model.md
tags:
  - adr
  - knowledge-management
  - architecture
  - knowledge-space
---

# DEC-0004 — Adopt the Business Knowledge System & Knowledge Space Model

## Status
Draft (D1)

---

## Context

Hermes OS presently has no single defined concept for where Discovery
output and per-engagement business knowledge lives. docs/11 defines the
typed business-object chain (Business → Strategy → Goal → KPI →
Bottleneck → Opportunity → Decision → Experiment → Lesson → Executive
Brief) and docs/16 defines those objects' canonical contracts, but
neither specifies the *container* that holds an engagement's raw,
graded evidence before it matures into those typed objects, nor how
isolation between engagements (ventures, clients) is guaranteed at that
container level.

DEC-0002 §5 already requires "strict context isolation between
ventures — no cross-venture data bleed by assumption," but does not
name the mechanism that provides that isolation, and does not extend
the guarantee to future external client engagements, which need it at
least as strongly as internal ventures do.

Without a named container and schema, each new venture or client
engagement risks inventing its own ad hoc knowledge-capture shape,
repeating research, or leaking one engagement's evidence into another's
context — the opposite of docs/00 §8 ("knowledge belongs in
repositories, not conversations") and DEC-0002 §4's requirement that
cross-venture knowledge only be promoted deliberately, never bled
across by default.

---

## Decision

Hermes OS adopts the **Business Knowledge System**, comprising isolated
**Knowledge Spaces**, as the standing model for all Discovery output and
per-engagement business knowledge.

1. **System (shared, fixed):** one schema, one set of entry types —
   Fact, Analysis, Recommendation, Assumption, Unknown — one Discovery
   procedure, one confidence-level scale, and one write/read/consume
   contract, applied identically regardless of engagement type.
2. **Knowledge Space (isolated, per engagement):** one Knowledge Space
   per venture and per external client, holding only that engagement's
   Facts/Analysis/Recommendations/Assumptions/Unknowns. Knowledge Spaces
   are **logical isolation boundaries** — the isolation guarantee is
   enforced at the schema/access level (no cross-Space reads by
   default), independent of and not bound to any specific physical
   storage or repository technology. No cross-Space reads by default.
   AVANZIA itself (portfolio-wide/HQ-level knowledge — identity,
   cross-venture standards, governance) is its own Knowledge Space under
   the same schema, not a special case.
3. **Repository mapping:** one Knowledge Space maps one-to-one to one
   venture-ops repository or client-ops repository, per the existing
   "one repository per venture, identical operating-document shape"
   pattern (DEC-0002 §5). This mapping is the current physical
   implementation of the logical isolation boundary defined in §2, not
   the definition of the boundary itself — if the physical storage
   pattern changes in the future, the logical isolation guarantee must
   still hold. No new repository pattern is introduced by this ADR;
   Knowledge Space names what that repository's knowledge layer *is*.
4. **Relationship to docs/11's business-object chain:** the Knowledge
   Space is the container Discovery populates; docs/11's typed objects
   (Strategy, Goal, KPI, etc.) are the formalized, matured subset of a
   Knowledge Space's contents once evidence is sufficient to state them
   as such. One Knowledge Base, two levels of maturity — this ADR does
   not redefine or replace docs/11.
5. **Portfolio-wide views** (cross-venture Executive Briefs, cross-Space
   pattern recognition for organizational learning) are produced only by
   Hermes reading across multiple Spaces on explicit Founder request — a
   read-time aggregation, never a write-time merge. Spaces remain
   isolated at rest.

This decision is operational and reversible under DEC-0002's Delegation
Principle in its schema/mechanics dimension (how knowledge is
structured and isolated), but is recorded as an ADR because it
introduces a new named architectural concept that other documents
(docs/11, docs/16, docs/22) will now reference — a governance-visible
change, not a private implementation detail.

---

## Consequences

### Positive

- Closes the gap between DEC-0002 §5's isolation requirement and a
  concrete mechanism that delivers it.
- Extends venture-grade isolation to external client engagements
  without inventing a second, client-specific pattern.
- Gives Discovery a single, reusable target regardless of entry mode
  (docs/24 §4), satisfying the "same reusable workflow" requirement for
  both internal ventures and future clients.
- Reuses the existing repository-per-venture pattern; no new
  infrastructure or repository convention required.
- Defining isolation as logical (§2) rather than tying it to a specific
  storage technology keeps the guarantee stable even if the physical
  implementation evolves.

### Trade-offs

- Introduces one new named concept (Knowledge Space) that docs/11,
  docs/16, and docs/22 must be updated to reference — a documentation
  consistency cost, addressed in Implementation below.
- Read-time cross-Space aggregation for portfolio views must be
  built deliberately; this ADR does not specify that mechanism's
  implementation, only its boundary rule (Founder-requested,
  read-only, never a merge).

---

## Implementation

1. Add a new specification, `specs/knowledge-space.md`, defining the
   Knowledge Space object (required fields, relationship to Business,
   lifecycle — see DEC-0005) alongside the existing docs/11 object set,
   and stating explicitly that isolation is logical, not tied to a
   specific storage mechanism.
2. Update docs/16-data-contracts.md's Canonical Contracts list to
   include Knowledge Space.
3. Update docs/22-shared-services.md's Knowledge Base entry to Business
   Knowledge System, referencing this ADR.
4. Reference this ADR from docs/24-hermes-agent-integration.md §4
   (Discovery step) as the concrete container Discovery writes into.
5. Hold at Draft/D1 pending Technical, Security, and Owner review before
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
