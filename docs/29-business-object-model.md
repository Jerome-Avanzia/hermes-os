---
document_id: DOC-0029
title: Hermes Business Object Model
version: 1.0.0
status: Approved
maturity: D3
owner: AVANZIA
document_type: Architecture
authority_level: 2
classification: Internal
created: 2026-08-01
last_updated: 2026-08-01
review_cycle: as-needed
approved_by: Founder
security_review: Pending
related:
  - 26-master-implementation-plan.md
  - 11-business-lifecycle.md
  - 16-data-contracts.md
  - 20-executive-model.md
  - 24-hermes-agent-integration.md
  - decisions/ADR-0004-operation-unit-of-business-execution.md
  - decisions/DEC-0002-hermes-executive-operating-model.md
  - decisions/DEC-0004-business-knowledge-system.md
  - decisions/DEC-0005-entity-lifecycle-management.md
tags:
  - business-objects
  - architecture
  - model
---

# Hermes Business Object Model

## Recovery Source

Every object below is recovered from repository evidence: dataclass definitions in `src/hermes/models/`, JSON Schema contracts in `contracts/`, specifications in `specs/`, governance documents in `docs/`, decision records in `decisions/`, YAML registries in `workspaces/`, `knowledge/`, `skills/`, `profiles/`, and the V1 Architecture specification.

---

## Complete Object Inventory

### 1. Organization

| | |
|---|---|
| **Definition** | The stable identity of a business entity. Name, purpose, vision, mission, positioning, services, brand. |
| **Purpose** | Grounds every conversation and execution in who this business is. |
| **Owner** | WorkspaceEngine (loads from markdown files referenced in workspace.yaml) |
| **Lifecycle** | Static. Changed by human editing source markdown files. |
| **Relationships** | Belongs to exactly one Workspace. Referenced by Conductor when composing system prompts. |
| **Persistence** | Markdown files on disk (`knowledge/AVANZIA/01-purpose.md`, etc.) |
| **Authoritative source** | `workspaces/{id}/workspace.yaml` → organization section → file paths |
| **Implementation status** | **Implemented.** Dataclass: `src/hermes/models/organization.py`. Loaded by WorkspaceEngine. Composed into system prompt by Conductor. |
| **Future phase** | Stable. No changes planned. |

### 2. Workspace

| | |
|---|---|
| **Definition** | The operational environment for a business within Hermes. Contains identity (name, description, mission), Organization reference, profile list, and repository references. |
| **Purpose** | The unit of operational scope. Every conversation, every execution, every knowledge lookup happens within a Workspace. |
| **Owner** | WorkspaceEngine |
| **Lifecycle** | Created by adding files to `workspaces/{id}/`. No formal state machine. |
| **Relationships** | Contains one Organization, references multiple Repositories, references multiple Profiles, maps to one Knowledge project. |
| **Persistence** | `workspaces/registry.yaml` + `workspaces/{id}/workspace.yaml` |
| **Authoritative source** | Filesystem (Principle 3) |
| **Implementation status** | **Implemented.** Dataclass: `src/hermes/models/workspace.py`. |
| **Future phase** | May gain Operation tracking (Sprint 11+). |

### 3. Business

| | |
|---|---|
| **Definition** | The top-level managed entity. Every Goal, KPI, Decision, Strategy, Bottleneck, Opportunity, Experiment, Lesson, and Executive Brief belongs to exactly one Business. |
| **Purpose** | The unit of strategic ownership. The thing the Founder runs. |
| **Owner** | Founder (Owner) |
| **Lifecycle** | Idea → Active → Scaling → Mature → Archived (specs/business.md) |
| **Relationships** | Contains all 9 other canonical business objects. Maps to a Workspace for operational context. |
| **Persistence** | JSON Schema contract: `contracts/business.schema.json`. Specification: `specs/business.md`. **No Python dataclass.** |
| **Authoritative source** | `specs/business.md` |
| **Implementation status** | **Designed, not implemented.** Schema and spec exist. No runtime code. |
| **Future phase** | Phase 4 (Business Object Chain). |

### 4. Strategy

| | |
|---|---|
| **Definition** | The approach for achieving business objectives. |
| **Purpose** | Connects Business mission to actionable Goals. |
| **Owner** | Founder approves; Hermes Agent may draft. |
| **Lifecycle** | Draft → Active → Reviewed → Completed/Retired |
| **Relationships** | Belongs to Business. References Goals, KPIs, Decisions, Opportunities, Experiments. |
| **Persistence** | Contract: `contracts/strategy.schema.json`. Spec: `specs/strategy.md`. |
| **Implementation status** | **Designed, not implemented.** |
| **Future phase** | Phase 4. |

### 5. Goal

| | |
|---|---|
| **Definition** | A measurable outcome derived from Strategy. |
| **Purpose** | Defines what success looks like. |
| **Owner** | Business owner. |
| **Lifecycle** | Planned → Active → Achieved → Archived |
| **Relationships** | Belongs to Business, supports Strategy, measured by KPIs. |
| **Persistence** | Contract: `contracts/goal.schema.json`. Spec: `specs/goal.md`. |
| **Implementation status** | **Designed, not implemented.** |
| **Future phase** | Phase 4. |

### 6. KPI

| | |
|---|---|
| **Definition** | Key Performance Indicator. Measures progress toward a Goal. |
| **Purpose** | Quantitative evidence of whether things are working. |
| **Owner** | Business owner. |
| **Lifecycle** | Defined → Measured → Reviewed → Archived |
| **Relationships** | Belongs to Business, supports one Goal. Referenced by Decision Engine and Executive Briefs. |
| **Persistence** | Contract: `contracts/kpi.schema.json`. Spec: `specs/kpi.md`. |
| **Implementation status** | **Designed, not implemented.** |
| **Future phase** | Phase 4. |

### 7. Bottleneck

| | |
|---|---|
| **Definition** | The primary constraint preventing progress. |
| **Purpose** | Names what is blocking the business so it can be acted upon. |
| **Owner** | Identified by agents/analysis; resolved by human decision. |
| **Lifecycle** | Identified → Analysed → Mitigating → Resolved → Archived |
| **Relationships** | Linked to Strategy, Goal, KPI, Opportunity, Decision, Experiment, Lesson. |
| **Persistence** | Contract: `contracts/bottleneck.schema.json`. Spec: `specs/bottleneck.md`. |
| **Implementation status** | **Designed, not implemented.** |
| **Future phase** | Phase 4. |

### 8. Opportunity

| | |
|---|---|
| **Definition** | A possible action to remove constraints or accelerate growth. |
| **Purpose** | Captures options before they become decisions. |
| **Owner** | Identified by agents; prioritized by Decision Engine. |
| **Lifecycle** | Identified → Evaluated → Planned → Active → Completed/Rejected |
| **Relationships** | Related to Strategy, Goal, KPI, Decision, Bottleneck, Experiment, Lesson. |
| **Persistence** | Contract: `contracts/opportunity.schema.json`. Spec: `specs/opportunity.md`. |
| **Implementation status** | **Designed, not implemented.** |
| **Future phase** | Phase 4. |

### 9. Decision

| | |
|---|---|
| **Definition** | A recorded course of action with context and rationale. |
| **Purpose** | Traceability. Every significant action is justified and retrievable. |
| **Owner** | L1/L2: Hermes Agent. L3: Founder. (DEC-0002 §1) |
| **Lifecycle** | Proposed → Approved → Implemented → Reviewed → Archived |
| **Relationships** | Belongs to Business. References Strategy, Goal, KPI, Opportunity, Bottleneck, Experiment, Lesson. |
| **Persistence** | Contract: `contracts/decision.schema.json`. Spec: `specs/decision.md`. Also: `decisions/` directory for ADRs/DECs (governance-level). |
| **Implementation status** | **Partially implemented.** ADR/DEC files exist on disk as governance artifacts. The Decision as a business object (venture-level) is designed but not implemented in runtime. |
| **Future phase** | Phase 4 (business decisions), Phase 5 (Decision Engine). |

### 10. Experiment

| | |
|---|---|
| **Definition** | A structured validation test run before larger investment. |
| **Purpose** | Reduce risk by testing assumptions before committing. |
| **Owner** | Designed by agents; approved by Founder for execution. |
| **Lifecycle** | Planned → Running → Completed → Reviewed → Archived |
| **Relationships** | Related to Strategy, Goal, KPI, Opportunity, Bottleneck, Decision, Lesson. |
| **Persistence** | Contract: `contracts/experiment.schema.json`. Spec: `specs/experiment.md`. |
| **Implementation status** | **Designed, not implemented.** |
| **Future phase** | Phase 4. |

### 11. Lesson

| | |
|---|---|
| **Definition** | Organizational learning captured from outcomes. |
| **Purpose** | Prevents repeating mistakes. Feeds Organizational Memory (docs/24 §6). |
| **Owner** | Hermes Agent records; Founder reviews. |
| **Lifecycle** | Captured → Reviewed → Applied → Archived |
| **Relationships** | References Decision, Experiment, Strategy, Goal, KPI, Opportunity, Bottleneck. |
| **Persistence** | Contract: `contracts/lesson.schema.json`. Spec: `specs/lesson.md`. |
| **Implementation status** | **Designed, not implemented.** |
| **Future phase** | Phase 4. |

### 12. Executive Brief

| | |
|---|---|
| **Definition** | A structured management summary aggregating all business objects. |
| **Purpose** | The Founder's primary consumption format. Synthesized, not raw. |
| **Owner** | Generated by Hermes Agent. Delivered to Founder. |
| **Lifecycle** | Generated → Reviewed → Published → Archived |
| **Relationships** | Aggregates from all 9 other business objects. Belongs to one Business. |
| **Persistence** | Contract: `contracts/executive-brief.schema.json`. Spec: `specs/executive-brief.md`. |
| **Implementation status** | **Designed, not implemented.** |
| **Future phase** | Phase 5 (Executive Brief Generator). |

### 13. Knowledge Document

| | |
|---|---|
| **Definition** | A markdown document containing business knowledge. |
| **Purpose** | Grounds reasoning in real content, not model assumptions (Principle 9). |
| **Owner** | KnowledgeEngine |
| **Lifecycle** | Created → loaded at runtime → included in Context. No formal state transitions. |
| **Relationships** | Belongs to a KnowledgeContext (project-scoped). Composed into system prompt by Conductor. |
| **Persistence** | Markdown files in `knowledge/{project}/`. Manifest: `knowledge/{project}/manifest.yaml`. |
| **Authoritative source** | `knowledge/registry.yaml` → project → `manifest.yaml` |
| **Implementation status** | **Implemented.** Dataclass: `src/hermes/models/knowledge_document.py`. |
| **Future phase** | Sprint 7 (relevance selection) — completed. |

### 14. Knowledge Space (DEC-0004)

| | |
|---|---|
| **Definition** | An isolated container of business knowledge per venture/engagement. Contains Facts, Analysis, Recommendations, Assumptions, Unknowns. |
| **Purpose** | Logical isolation boundary — no cross-venture knowledge bleed. |
| **Owner** | KnowledgeEngine (future) |
| **Lifecycle** | Created with venture → Active → Archived (per DEC-0005). |
| **Relationships** | Maps 1:1 to a venture repository. Contains Knowledge Documents. The typed business objects (Strategy, Goal, etc.) are the matured subset. |
| **Persistence** | Currently: `knowledge/{project}/` directory. Future: venture repository. |
| **Authoritative source** | DEC-0004 |
| **Implementation status** | **Decided, not implemented.** The concept is named and governed. The current `knowledge/` directory is the physical approximation. |
| **Future phase** | Phase 4. |

### 15. Profile

| | |
|---|---|
| **Definition** | A persona configuration: system prompt, optional model override, description. |
| **Purpose** | Shapes how Hermes communicates — business vs. developer vs. default. |
| **Owner** | ProfileLoader |
| **Lifecycle** | Static. Changed by editing YAML. |
| **Relationships** | Referenced by Workspace (profiles list), Context (optional), Conductor (system prompt source). |
| **Persistence** | `profiles/{id}.yaml` |
| **Implementation status** | **Implemented.** Dataclass: `src/hermes/models/profile.py`. |
| **Future phase** | Stable. |

### 16. Skill

| | |
|---|---|
| **Definition** | A versioned, reusable unit of operational capability. Declared by manifest, discovered by Capability Engine, loaded by Skill Loader. |
| **Purpose** | Encodes repeatable operational work (Principle 3). |
| **Owner** | SkillLoader discovers; Hermes Agent creates/patches (DEC-0002 §3). |
| **Lifecycle** | Created → Registered → Active → (patched) → Deprecated. Per DEC-0005: Proposed→Active→Archived. |
| **Relationships** | Provides Capabilities. Loaded by ExecutionPlan. |
| **Persistence** | `skills/{id}/skill.yaml` + `skills/registry.yaml` |
| **Implementation status** | **Implemented.** Dataclass: `src/hermes/models/loaded_skill.py`. 7 skills exist on disk. |
| **Future phase** | Stable. May migrate to `hermes-skills` repository. |

### 17. Capability

| | |
|---|---|
| **Definition** | A declared ability derived from a Skill manifest. Matched against Tasks by keyword. |
| **Purpose** | Dynamic discovery: what can Hermes do for this request? |
| **Owner** | CapabilityEngine |
| **Lifecycle** | Exists when skill manifest exists. No independent lifecycle. |
| **Relationships** | Declared by Skill. Matched to Task. Becomes ExecutionStep in Plan. |
| **Persistence** | Derived from `skills/{id}/skill.yaml` capabilities field. |
| **Implementation status** | **Implemented.** Dataclass: `src/hermes/models/capability.py`. |
| **Future phase** | Stable. |

### 18. Project

| | |
|---|---|
| **Definition** | A resolved project identity: id, name, path. |
| **Purpose** | The bridge between a Task's business field and the knowledge/workspace registries. |
| **Owner** | ProjectResolver |
| **Lifecycle** | Resolved at runtime from registry. |
| **Relationships** | Resolved from Task. Used by KnowledgeEngine, WorkspaceEngine. Embedded in Context, ExecutionPlan, ExecutionResult. |
| **Persistence** | Derived from `knowledge/registry.yaml`. |
| **Implementation status** | **Implemented.** Dataclass: `src/hermes/models/project.py`. |
| **Future phase** | Stable. |

### 19. Repository

| | |
|---|---|
| **Definition** | A Git repository associated with a Workspace: name, path, branch, clean status, environment. |
| **Purpose** | Hermes reads workspace code from repositories. |
| **Owner** | WorkspaceEngine |
| **Lifecycle** | Detected at runtime. |
| **Relationships** | Belongs to WorkspaceContext. Read by WorkspaceReader. |
| **Persistence** | `workspaces/registry.yaml` → repositories section. |
| **Implementation status** | **Implemented.** Dataclass: `src/hermes/models/repository.py`. |
| **Future phase** | Stable. |

### 20. Agent

| | |
|---|---|
| **Definition** | An AI entity with a defined purpose, responsibilities, authority level, and owner. Three categories: Executive (Hermes Agent), Specialist, Service. |
| **Purpose** | Executes work within governed authority boundaries. |
| **Owner** | Agent Registry (docs/21). |
| **Lifecycle** | Proposed → Approved → Active → Suspended → Retired. Deliberately excluded from generic lifecycle (DEC-0005 §5). |
| **Relationships** | Reports to Executive Model hierarchy. Consumes Shared Services. Operates on Business objects. Coordinated by Agent Orchestrator. |
| **Persistence** | `docs/21-agent-registry.md` (governance). Future: `hermes-agents` repository. |
| **Implementation status** | **Designed, not implemented.** Architecture defined across docs/14, 15, 20, 21, 24. One skill exists: `skills/agent-fleet-manager/`. No runtime Agent model. |
| **Future phase** | Phase 5 (Executive Intelligence). |

### 21. Task

| | |
|---|---|
| **Definition** | An anonymous input tuple: id, business, request. The thing the user asks Hermes to do. |
| **Purpose** | Entry point for the execution pipeline. |
| **Owner** | Created by CLI or HermesService. |
| **Lifecycle** | Created → consumed by Context Engine → embedded in results. No persistence. |
| **Relationships** | Resolved to Project. Embedded in Context, ExecutionPlan, ExecutionResult. |
| **Persistence** | **None.** Exists only in memory for the duration of one execution. |
| **Implementation status** | **Implemented.** Dataclass: `src/hermes/models/task.py`. |
| **Future phase** | May be wrapped by Operation (Phase 2). |

### 22. Context

| | |
|---|---|
| **Definition** | The assembled input for reasoning: Task, Project, Knowledge, Workspace, Capabilities, Profile. |
| **Purpose** | "Context before prompting" (Principle 2). Everything the reasoning provider needs, assembled deterministically. |
| **Owner** | ContextEngine |
| **Lifecycle** | Built once per execution or conversation. Immutable. Not persisted. |
| **Relationships** | Contains Task, Project, KnowledgeContext, WorkspaceContext, Capabilities, Profile. Consumed by Planner, Executor, Conductor. |
| **Persistence** | **None.** Runtime only. |
| **Implementation status** | **Implemented.** Dataclass: `src/hermes/models/context.py`. |
| **Future phase** | Stable. |

### 23. Conversation

| | |
|---|---|
| **Definition** | A stateless sequence of chat messages between a user and Hermes through the Gateway. |
| **Purpose** | Interactive dialogue grounded in workspace context. |
| **Owner** | HermesService (stream_chat/chat), Conductor (rendering), Gateway (protocol). |
| **Lifecycle** | No lifecycle. Fire-and-forget. No persistence. |
| **Relationships** | Occurs within a Workspace. Uses a Profile. Receives Context from ContextEngine. |
| **Persistence** | **None.** Tokens stream and are gone. |
| **Implementation status** | **Implemented.** Gateway SSE endpoint + HermesService.stream_chat(). |
| **Future phase** | May become source of Operations (Phase 3, conversation-to-operation bridge). |

### 24. ExecutionPlan

| | |
|---|---|
| **Definition** | A linear sequence of ExecutionSteps ending with an approval checkpoint. |
| **Purpose** | Deterministic work plan created from Context. |
| **Owner** | Planner |
| **Lifecycle** | Created → executed → discarded. |
| **Relationships** | Created from Context. Contains ExecutionSteps. Consumed by Executor. |
| **Persistence** | **None.** |
| **Implementation status** | **Implemented.** |
| **Future phase** | Stable. |

### 25. ExecutionResult

| | |
|---|---|
| **Definition** | Terminal state of an execution: completed steps, status, generated output, timestamps, diagnostics. |
| **Purpose** | The output of one run through the pipeline. |
| **Owner** | Executor produces; HermesService enriches with diagnostics. |
| **Lifecycle** | Created → returned to caller → discarded. |
| **Relationships** | Contains Task, Project, DiagnosticsReport. |
| **Persistence** | **None.** Returned to CLI/API and gone. |
| **Implementation status** | **Implemented.** |
| **Future phase** | May become a Job within an Operation (Phase 2). |

### 26. Document (Controlled Document)

| | |
|---|---|
| **Definition** | Any governed artifact with metadata: document_id, title, version, status, maturity (D0-D5), owner, authority_level. |
| **Purpose** | Governance vehicle. Every important outcome becomes one (docs/00 §8). |
| **Owner** | Owner/Founder approves. Hermes Agent drafts. |
| **Lifecycle** | D0 (Placeholder) → D1 (Draft) → D2 (Reviewed) → D3 (Approved) → D4 (Baseline) → D5 (Superseded). Excluded from generic lifecycle (DEC-0005 §5). |
| **Relationships** | Includes ADRs, Specifications, SOPs, Architecture docs. |
| **Persistence** | Files in `docs/`, `decisions/`, `specs/`, `standards/`. |
| **Implementation status** | **Implemented** as files with YAML frontmatter. No runtime model. STD-0001 defines metadata standard. |
| **Future phase** | Stable governance mechanism. |

### 27. ADR (Architecture Decision Record)

| | |
|---|---|
| **Definition** | A specialized Document recording an architectural decision. |
| **Purpose** | Traceability for governance-visible architectural choices. |
| **Owner** | Founder approves. |
| **Lifecycle** | Inherits Document lifecycle (D0-D5). |
| **Relationships** | Subtype of Document. Stored in `decisions/`. |
| **Persistence** | `decisions/DEC-*.md` and `decisions/ADR-*.md` |
| **Implementation status** | **Implemented** as files. 7 exist. |
| **Future phase** | Continues to grow. |

### 28. Specification

| | |
|---|---|
| **Definition** | A detailed definition of a business object: required fields, relationships, lifecycle, validation rules. |
| **Purpose** | The authoritative shape definition for each business object. |
| **Owner** | Hermes OS governance. |
| **Lifecycle** | Inherits Document lifecycle. |
| **Relationships** | One per canonical business object. Paired with a JSON Schema contract. |
| **Persistence** | `specs/{object}.md` |
| **Implementation status** | **Implemented** as files. 10 exist (one per canonical object). |
| **Future phase** | Stable. |

### 29. SOP (Standard Operating Procedure)

| | |
|---|---|
| **Definition** | A documented repeatable process. |
| **Purpose** | Operational consistency. |
| **Owner** | Hermes OS governance. |
| **Lifecycle** | Inherits Document lifecycle. |
| **Relationships** | Referenced by Operating Model (docs/00 §8). |
| **Persistence** | Future: `sops/` directory. |
| **Implementation status** | **Conceptual.** Referenced in governance but no SOPs written yet. |
| **Future phase** | Future. |

### 30. Prompt

| | |
|---|---|
| **Definition** | A versioned prompt used by AI providers. |
| **Purpose** | Reproducible reasoning inputs. |
| **Owner** | Future: `hermes-prompts` repository (HERMES_V1_ARCHITECTURE §6). |
| **Lifecycle** | Versioned. |
| **Relationships** | Used by Providers. Currently embedded in Conductor and ClaudeProvider. |
| **Persistence** | Currently: inline in Python code. Future: `hermes-prompts` repo. |
| **Implementation status** | **Partially implemented.** System prompts exist in Profile YAML. Provider prompts are hardcoded in ClaudeProvider. |
| **Future phase** | Future. |

### 31. Memory (Organizational Memory)

| | |
|---|---|
| **Definition** | The accumulated record of Decisions, Lessons, Approved changes, and Outcomes (docs/24 §6). |
| **Purpose** | Continuous organizational learning. "Produced as a byproduct of doing the work" (DEC-0002 §2). |
| **Owner** | Hermes Agent contributes. Repository stores. |
| **Lifecycle** | Append-only. |
| **Relationships** | Fed by Decisions, Experiments, Lessons. Consumed by Decision Engine, Executive Brief Generator. |
| **Persistence** | Future: repository files. Currently: does not exist. |
| **Implementation status** | **Not implemented.** DEC-0002 §2 defines boundaries. docs/24 §6 defines requirements. No runtime code. |
| **Future phase** | Phase 5 (Executive Intelligence). |

### 32. Environment

| | |
|---|---|
| **Definition** | Detected technology stack of a workspace: node, python, docker, npm, pnpm. |
| **Purpose** | Hermes knows what tools are available in a workspace. |
| **Owner** | WorkspaceEngine detects. |
| **Lifecycle** | Detected at runtime. |
| **Relationships** | Part of WorkspaceContext. |
| **Persistence** | **None.** Detected each time. |
| **Implementation status** | **Implemented.** List of strings in WorkspaceContext. |
| **Future phase** | Stable. |

### 33. DiagnosticsReport

| | |
|---|---|
| **Definition** | Accounting of what went into a reasoning call: files scanned, selected, read, chars, truncation. |
| **Purpose** | Observability into context assembly. |
| **Owner** | HermesService.generate() |
| **Lifecycle** | Created once per execution. |
| **Relationships** | Attached to ExecutionResult. |
| **Persistence** | **None.** Printed to CLI if `--diagnostics` flag. |
| **Implementation status** | **Implemented.** |
| **Future phase** | Stable. |

### Not Found in Repository

- **Mission** — Not a standalone object. It is a field on Business (`specs/business.md`: mission), Workspace (`workspace.yaml`: mission), and Organization (`organization.mission`). It is a property, not an entity.
- **Operation** — Does not exist in the repository as code. Defined by ADR-0004. Implementation planned for Phase 2 (Sprints 11-13).
- **Job** — Does not exist. Relationship defined by ADR-0004. Implementation planned for Phase 2 (Sprint 12).
- **Artifact** — Does not exist as a named concept. ExecutionResult.generated_output is the closest thing, but it has no identity, no persistence, no lifecycle.
- **Swarm** — Does not exist. Multi-agent collaboration is listed as deferred in HERMES_V1_ARCHITECTURE §5.

---

## Business Object Hierarchy

```
HERMES OS
│
├── GOVERNANCE OBJECTS (shape the rules)
│   ├── Document (Controlled Document)
│   │   ├── ADR / DEC
│   │   ├── Specification
│   │   └── SOP
│   └── Decision (governance-level)
│
├── FIRST-CLASS BUSINESS OBJECTS (the things Hermes manages)
│   ├── Business
│   │   ├── Strategy
│   │   ├── Goal
│   │   │   └── KPI
│   │   ├── Bottleneck
│   │   ├── Opportunity
│   │   ├── Decision (business-level)
│   │   ├── Experiment
│   │   ├── Lesson
│   │   └── Executive Brief
│   │
│   ├── Organization (identity of the business)
│   │
│   └── Knowledge Space (DEC-0004)
│       └── Knowledge Document
│
├── PLATFORM OBJECTS (the infrastructure Hermes runs on)
│   ├── Workspace
│   │   ├── Repository
│   │   └── Environment (detected)
│   ├── Profile
│   ├── Skill
│   │   └── Capability (derived)
│   ├── Agent
│   ├── Prompt
│   └── Memory (Organizational)
│
└── RUNTIME OBJECTS (exist only during execution)
    ├── Task
    ├── Context
    ├── ExecutionPlan
    │   └── ExecutionStep
    ├── ExecutionResult
    │   └── DiagnosticsReport
    └── Conversation
```

---

## Relationship Diagram

```
                    ┌──────────┐
                    │  FOUNDER │
                    └────┬─────┘
                         │ owns/approves
                         ▼
                    ┌──────────┐
                    │ Business │ ◄─── specs/business.md
                    └────┬─────┘      contracts/business.schema.json
                         │
          ┌──────────────┼──────────────────┐
          │              │                  │
          ▼              ▼                  ▼
     ┌──────────┐  ┌──────────┐      ┌──────────────┐
     │ Strategy │  │   Goal   │      │ Organization │
     └────┬─────┘  └────┬─────┘      └──────────────┘
          │              │                  │
          │              ▼                  │ identity of
          │         ┌─────────┐             ▼
          │         │   KPI   │       ┌───────────┐
          │         └────┬────┘       │ Workspace │
          │              │            └─────┬─────┘
          │         ┌────┴────┐             │
          │         │ Review  │       ┌─────┼──────────┐
          │         └────┬────┘       │     │          │
          │              │            ▼     ▼          ▼
          │    ┌─────────┴───┐   Repository Profile  Knowledge
          │    ▼             ▼                        Space
          │ Bottleneck  Opportunity                     │
          │    │             │                           ▼
          │    └──────┬──────┘                    Knowledge
          │           ▼                          Document
          │      ┌──────────┐
          └─────►│ Decision │
                 └────┬─────┘
                      │
               ┌──────┴──────┐
               ▼              ▼
          ┌──────────┐  ┌──────────┐
          │Experiment│  │  Lesson  │──► Memory
          └──────────┘  └──────────┘
                              │
                              ▼
                     ┌────────────────┐
                     │Executive Brief │
                     └────────────────┘


        ─── RUNTIME PATH ───

  ┌──────┐    ┌─────────┐    ┌───────────────┐
  │ Task │───►│ Context │───►│ ExecutionPlan │
  └──────┘    └─────────┘    └───────┬───────┘
       │                             │
       │      ┌───────┐              ▼
       └─────►│ Skill │───► ┌─────────────────┐
              └───────┘     │ ExecutionResult  │
                            └─────────────────┘

  ┌──────────────┐    ┌───────────┐    ┌───────────┐
  │ Conversation │───►│  Context  │───►│ Conductor │───► Provider
  └──────────────┘    └───────────┘    └───────────┘
```

---

## Lifecycle Map

```
Founder
  │
  │  defines
  ▼
Business (Idea → Active → Scaling → Mature → Archived)
  │
  │  sets direction
  ▼
Strategy (Draft → Active → Reviewed → Completed)
  │
  │  derives measurable outcomes
  ▼
Goal (Planned → Active → Achieved)
  │
  │  measures progress
  ▼
KPI (Defined → Measured → Reviewed)
  │
  │  performance review
  ├─── On Track → Continue
  │
  └─── Off Track
         │
         │  identifies constraint
         ▼
       Bottleneck (Identified → Analysed → Mitigating → Resolved)
         │
         │  discovers possible action
         ▼
       Opportunity (Identified → Evaluated → Planned → Active)
         │
         │  selects course of action
         ▼
       Decision (Proposed → Approved → Implemented → Reviewed)
         │
         ├─── confidence sufficient → execute directly
         │
         └─── confidence low → validate first
                │
                ▼
              Experiment (Planned → Running → Completed)
                │
                │  captures learning
                ▼
              Lesson (Captured → Reviewed → Applied)
                │
                │  becomes organizational memory
                ▼
              Memory (append-only)
                │
                │  synthesized into
                ▼
         Executive Brief (Generated → Reviewed → Published)
                │
                │  informs
                ▼
              Founder (continuous improvement loop)


       ─── EXECUTION PATH (how work gets done) ───

  User Request
       │
       ▼
     Task (created, ad-hoc)
       │
       ▼
     Context (assembled by ContextEngine)
       │  ┌── Knowledge Documents
       │  ├── Workspace + Organization
       │  ├── Capabilities
       │  └── Profile
       │
       ▼
     ExecutionPlan (linear steps + approval checkpoint)
       │
       ▼
     ExecutionResult (completed_steps, status, output)
       │
       └── (currently discarded — no persistence)


       ─── CONVERSATION PATH ───

  User Message
       │
       ▼
     Context (assembled by ContextEngine)
       │  ┌── Organization
       │  ├── Knowledge Documents
       │  └── Profile
       │
       ▼
     Conductor (composes system prompt)
       │
       ▼
     Provider (streams tokens)
       │
       └── (no persistence — tokens are gone)
```

---

## The Essential Set

**"What is the smallest set of business objects Hermes absolutely requires to function as a Business Operating System?"**

Based on repository evidence — not invention — the answer has three tiers:

### Tier 1: Operational Today (implemented, running)

| Object | Why essential |
|--------|--------------|
| **Workspace** | Without it, Hermes has no operational scope. |
| **Organization** | Without it, Hermes doesn't know who it's working for. |
| **Knowledge Document** | Without it, reasoning is ungrounded (Principle 9). |
| **Profile** | Without it, Hermes has no voice. |
| **Skill / Capability** | Without them, Hermes can't match work to abilities. |
| **Task** | Without it, nothing enters the pipeline. |
| **Context** | Without it, Principle 2 is violated — prompting without context. |

These 7 objects are what Hermes runs on today.

### Tier 2: Required for "Operating System" (designed, not implemented)

| Object | Why essential |
|--------|--------------|
| **Business** | The unit of strategic ownership. Without it, Workspace has no strategic anchor. |
| **Goal** | Without measurable outcomes, there is no basis for decisions. |
| **Decision** | Without recorded decisions, there is no traceability (DEC-0002 §1: "No decision may exist only in conversation"). |
| **Executive Brief** | Without synthesized reporting, the Founder must read raw data. |

These 4 objects are what separates "execution kernel" from "operating system." They are fully designed with contracts and specifications. They have no runtime code.

### Tier 3: Required for Maturity (designed, future phases)

| Object | Why |
|--------|-----|
| **Agent** | Hermes needs workers beyond itself. |
| **Memory** | Organizational learning must accumulate. |
| **KPI** | Goals need measurement. |
| **Strategy** | Business needs direction between Founder vision and daily Goals. |

---

**The minimum viable Business Operating System requires 11 objects:**
7 operational (Tier 1) + 4 structural (Tier 2).

The gap between what Hermes is today (an execution kernel with a conversation interface) and what it needs to be (a Business Operating System) is exactly 4 objects: **Business, Goal, Decision, Executive Brief.**

---

## Review Checklist

- [x] Owner Approval (2026-08-01)
- [ ] Technical Review
- [ ] Security Review
- [ ] Architecture Review
- [ ] Git Commit
- [ ] GitHub Push
- [ ] Project Log Updated
