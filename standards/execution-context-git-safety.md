---
document_id: STD-0002
title: Execution Context & Git Safety Policy
version: 1.0.0
status: Draft
maturity: D1
owner: AVANZIA
classification: Internal
document_type: Standard
authority_level: 2
created: 2026-07-23
last_updated: 2026-07-23
related:
  - 00-operating-model.md
  - 04-system-map.md
  - 24-hermes-agent-integration.md
tags:
  - standard
  - governance
  - execution-context
  - git-safety
  - infrastructure
---

# STD-0002 — Execution Context & Git Safety Policy

## Purpose

This standard defines how Hermes Agent starts every session, classifies
incoming work, selects the correct execution location, and gates every
Git operation with mandatory safety checks. It exists to prevent two
concrete failure modes observed as risks in a multi-repository,
multi-venture, infrastructure-adjacent operating environment:

1. Hermes Agent performing repository or venture work from the wrong
   location (Headquarters, a stale repo, an infra path with no Git
   identity), producing untraceable or misdirected changes.
2. Hermes Agent running a Git-mutating command against a repository in
   an unverified state (wrong branch, dirty tree, unreachable remote),
   producing silent data loss or a broken push.

## Scope

Applies to every Hermes Agent session operating under the AVANZIA
ecosystem rooted at /opt/avanzia ("AVANZIA Headquarters"). Covers session
startup, task classification, execution-context selection, and the
pre-flight checklist required before any Git-mutating command.

## Policy

### 1. Startup Location

Every Hermes Agent session starts at /opt/avanzia (AVANZIA Headquarters).
HQ is a coordination point, not a workspace: no build, edit-and-commit,
or Git-mutating work happens with HQ root as the working directory.

### 2. Mandatory Task Classification

Before acting, Hermes Agent classifies every incoming task into exactly
one of four execution contexts:

| Context | Definition | Execution location |
|---|---|---|
| Portfolio-wide | Spans multiple ventures/repos, or concerns AVANZIA as a whole | HQ-scoped read/aggregate across repos/*; no single repo is mutated for a portfolio-wide task |
| Repository-specific | Governance/architecture work on a named repository itself | That repository under /opt/avanzia/repos/<repo> |
| Venture-specific | Operational work on one business venture | That venture's operating files (business subtree or dedicated venture repo) |
| Infrastructure-related | Touches compose/, data/, secrets/, monitoring, host-level services | The relevant infra path directly; not a Git repository unless/until an infra-as-code repo is introduced |

A task spanning multiple contexts is split and handled per-context, not
blended.

### 3. Context Report

Before making any change, Hermes Agent states: the classified context,
the target execution location (or "HQ, portfolio-wide" if no switch is
needed), and a one-line justification. This report precedes action; it
is not a permission request except where the Delegation Principle
(DEC-0002) already requires escalation for that class of work.

### 4. Git Pre-Flight Gate

Before every Git-mutating operation (add, commit, push, pull, fetch,
merge, rebase, checkout, branch, reset, tag), Hermes Agent verifies and
surfaces:

1. Current working directory (must be inside a repository under
   /opt/avanzia/repos/, never HQ root or an infra path).
2. Repository root resolves via `git rev-parse --show-toplevel` and
   matches the intended repository.
3. Repository health: `git status` clean/dirty state, no unresolved
   merge/rebase/cherry-pick in progress; `git fsck` if corruption is
   suspected.
4. Current branch via `git branch --show-current` — never assumed.
5. SSH/remote availability confirmed before any operation touching a
   remote (push/pull/fetch/clone).

Any failed check halts the operation and is reported before any attempt
to remediate.

### 5. Prohibition on Git-from-Headquarters

Hermes Agent never runs a Git-mutating command, `git init`, or
`git clone` with /opt/avanzia root (or any verified non-repository infra
path) as the working directory. Repository creation and cloning always
target /opt/avanzia/repos/.

## Rationale

This standard is the infrastructure/session-level enforcement layer
beneath the Executive Operating Model (DEC-0002) and the Operating
Model's "Git First" and "One Source of Truth" principles (docs/00 §6).
It converts an implicit expectation ("work happens in the right repo, on
a verified branch") into an explicit, auditable checklist so that
correctness does not depend on session-to-session memory.

## Compliance

Every Hermes Agent session is expected to comply with this standard by
construction — it is enforced via /opt/avanzia/AGENTS.md, which Hermes
Agent loads at startup. Any deviation (e.g. classification skipped,
Git operation run without the pre-flight gate) is a process defect to be
corrected immediately, not a one-off judgment call.

## Review Checklist

- [ ] Technical Review
- [ ] Security Review
- [x] Owner Approval
- [ ] Git Commit
- [ ] GitHub Push
- [ ] Project Log Updated
