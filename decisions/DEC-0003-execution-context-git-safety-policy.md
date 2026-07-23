---
document_id: DEC-0003
title: Adopt the Execution Context & Git Safety Policy
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
  - standards/execution-context-git-safety.md
  - decisions/DEC-0002-hermes-executive-operating-model.md
  - docs/00-operating-model.md
  - docs/04-system-map.md
  - docs/24-hermes-agent-integration.md
tags:
  - adr
  - governance
  - execution-context
  - git-safety
  - infrastructure
  - startup-policy
---

# DEC-0003 — Adopt the Execution Context & Git Safety Policy

## Status

Draft (D1)

---

## Context

DEC-0002 established the Hermes Executive Operating Model — the general
Delegation Principle and per-domain rules governing how Hermes Agent
operates across Decisions, Memory, Skills, Knowledge, and multiple
ventures. It did not, however, specify the concrete session-startup and
Git-safety mechanics that make multi-repository, multi-venture operation
safe in practice: where Hermes Agent starts, how it decides which repo
or path to work in, and what it must verify before touching Git.

AVANZIA Headquarters (/opt/avanzia) now hosts multiple concerns side by
side: Git repositories under repos/ (currently hermes-os, which itself
holds the AKosmicAnimals venture subtree), and non-Git infrastructure
paths (compose/, data/, secrets/, backups/, logs/) running Traefik, n8n,
NocoDB, monitoring, and the Hermes web UI. As additional ventures are
split into their own repositories (per DEC-0002 §5) and additional infra
services are added, the risk of Hermes Agent acting from the wrong
location, or running a Git operation against an unverified repository
state, grows with the number of repos and paths in play.

This gap was identified directly, not by an incident: no case of
misdirected work or unsafe Git operation has occurred, but the ecosystem
has reached the complexity (repos/ + infra dirs + one venture already
live) where the boundary needs to be explicit rather than inferred per
session.

---

## Decision

Hermes OS adopts **STD-0002 (Execution Context & Git Safety Policy)** as
a standing standard, and requires Hermes Agent to enforce it at every
session start via /opt/avanzia/AGENTS.md. Specifically:

1. Every Hermes Agent session starts at /opt/avanzia (AVANZIA
   Headquarters), treated as a coordination point, never a workspace for
   Git-mutating or build work.
2. Every incoming task is classified into exactly one of four execution
   contexts before action is taken: portfolio-wide, repository-specific,
   venture-specific, or infrastructure-related (STD-0002 §2). Hermes
   Agent switches to the correct directory only after classification.
3. Hermes Agent reports the selected execution context (classification,
   target location, one-line reason) before making changes, applying the
   Delegation Principle's existing escalation rule for anything that
   requires Founder approval — this report is a transparency step, not
   an additional approval gate on top of DEC-0002.
4. Before any Git-mutating operation, Hermes Agent runs the five-point
   pre-flight gate: working directory, repository root, repository
   health, current branch, SSH/remote availability (STD-0002 §4). Any
   failed check halts the operation.
5. Hermes Agent never performs Git operations (including `init`/`clone`)
   with Headquarters root, or any verified non-repository infra path, as
   the working directory (STD-0002 §5).

This decision applies DEC-0002's Delegation Principle directly: session
startup discipline and Git pre-flight verification are operational,
reversible, and evidence-traceable (a failed check simply halts and
reports — no lasting harm), so Hermes Agent owns and enforces them
autonomously without per-instance Founder approval. Only a future change
to the classification categories themselves, or to the authority
structure they route work into, would rise to governance-defining and
require a new Decision.

---

## Consequences

### Positive

- Removes ambiguity about where Hermes Agent should act for any given
  request, scaling cleanly as more venture repos and infra services are
  added under /opt/avanzia.
- Makes Git safety mechanical and auditable rather than dependent on
  the operator's memory in a given session.
- Closes the gap between DEC-0002's operating-model-level rules and the
  concrete session/filesystem mechanics needed to execute them safely.
- Directly extends AGENTS.md conventions already in use at the
  hermes-os repository level (see hermes-os/AGENTS.md) up to the
  Headquarters level, keeping the pattern consistent top to bottom.

### Trade-offs

- Adds a mandatory classification-and-report step to every task, even
  ones that feel obviously scoped — accepted deliberately, since the
  cost of a brief report is small compared to the cost of misdirected
  Git activity.
- The four-way classification will need revision as the ecosystem
  changes shape (e.g. if infra moves into its own Git-managed
  infra-as-code repo, "infrastructure-related" ceases to be
  non-Git-repository by definition) — flagged in Implementation below.

---

## Implementation

1. Publish /opt/avanzia/AGENTS.md as the operational enforcement copy of
   STD-0002, loaded automatically at every Hermes Agent session start.
2. Reference STD-0002 and this ADR from docs/24-hermes-agent-integration.md
   §4 (Operating Model) as the concrete session-startup/Git-safety layer
   beneath its existing 10-step initiative lifecycle.
3. When any venture currently living under
   hermes-os/businesses/<Venture>/ is split into its own repository, the
   venture-specific row of STD-0002 §2 is updated to point at that
   repository, and this ADR's Context is revisited per DEC-0002's own
   Implementation §5 (revisit at second-venture onboarding).
4. If /opt/avanzia/compose (or another infra path) is ever converted
   into a Git-managed infra-as-code repository, STD-0002 §2's
   "infrastructure-related" row and §5's "non-repository infra path"
   language must be updated in the same change — infra work would then
   be subject to the full Git pre-flight gate like any other repo.
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
