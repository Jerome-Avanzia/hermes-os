---
document_id: DOC-0026
title: Hermes Master Implementation Plan
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
  - 25-hermes-v1-implementation-roadmap.md
  - 00-operating-model.md
  - 02-architecture.md
  - 04-system-map.md
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
  - 27-ceo-workspace-product-specification.md
  - 28-ceo-workspace-ux-specification.md
  - 29-business-object-model.md
  - decisions/ADR-0004-operation-unit-of-business-execution.md
  - decisions/DEC-0002-hermes-executive-operating-model.md
  - decisions/DEC-0003-execution-context-git-safety-policy.md
  - decisions/DEC-0004-business-knowledge-system.md
  - decisions/DEC-0005-entity-lifecycle-management.md
tags:
  - roadmap
  - implementation
  - v1
  - sprints
  - master-plan
---

# Hermes Master Implementation Plan

> Supersedes docs/25 (V1 Implementation Roadmap) as the authoritative sprint plan. docs/25 is preserved as historical architecture.

## 1. Current State

### Fully Implemented (production-ready, tested)

| Component | Lines | Tests | What it does |
|-----------|-------|-------|-------------|
| 21 Models | 331 | All tested | Task, Context, ExecutionPlan, ExecutionResult, Workspace, Organization, Profile, KnowledgeDocument, Capability, LoadedSkill, DiagnosticsReport, and supporting types |
| 11 Kernel Engines | 964 | 1,278 test lines | CapabilityEngine, Executor, Planner, KnowledgeEngine, WorkspaceEngine, WorkspaceReader, FileSelector, FileContentReader, SkillLoader, ProfileLoader, ProjectResolver |
| ContextEngine | 95 | Tested via service/conductor | build() for tasks, build_conversation() for chat |
| HermesService | 105 | 254 test lines | generate() task path, stream_chat() / chat() conversation path |
| Conductor | 178 | 311 test lines | Profile resolution, system prompt composition (profile + organization + knowledge), provider delegation |
| Gateway | 208 | 229 test lines | POST /v1/chat (SSE streaming + non-streaming), GET /v1/profiles, GET /v1/workspace, GET /health, static UI |
| CLI | 335 | 305 test lines | 9 commands: inspect, workspace, knowledge, context, plan, skills, execute, read, generate |
| OllamaProvider | 155 | 258 test lines | Streaming chat via SSE, HTTP-based, configurable model/URL |
| ClaudeProvider | 120 | 265 test lines | Anthropic API integration, prompt building with knowledge + skills + files |
| Static UI | 857 | Via gateway tests | Workspace shell with Dashboard + Chat functional, 9 placeholder modules |
| 7 Skills | Manifests only | Via skill_loader tests | kernel, python, brand-strategy, copywriting, git, nextjs, agent-fleet-manager |
| 12 Knowledge Docs | 10,066 bytes | Via knowledge_engine tests | AVANZIA organizational knowledge (purpose through tech-spec) |
| 3 Profiles | YAML | Via profile_loader tests | default, developer, business |

**Total: 222 tests passing. 2,849 lines of production code.**

### Partially Implemented

| Component | What exists | What's missing |
|-----------|------------|---------------|
| Workspace shell (ADR-0001) | Dashboard + Chat functional | Operations, Jobs, Knowledge, Kanban, Conductor, Terminal, Files, Swarm, Memory, Skills, MCP, Profiles views are placeholders |
| Dashboard | Shows workspace identity, repositories, model | Missing: Operations count, Knowledge count, attention items |
| Organization | Dataclass with 6 facets, loaded from markdown, composed into system prompt | Not connected to Business contract |
| Knowledge in conversations | All 12 docs loaded, first 5 injected into system prompt | No relevance selection — positional truncation only |
| Legacy runtime | execution_engine.py, planning_engine.py | Superseded by kernel. Still importable but unused |

### Specified but Not Implemented

| Artifact | Specification exists | Python model | Runtime code | Persistence |
|----------|---------------------|-------------|-------------|-------------|
| Business | specs/business.md + contracts/business.schema.json | No | No | No |
| Strategy | specs/strategy.md + contracts/strategy.schema.json | No | No | No |
| Goal | specs/goal.md + contracts/goal.schema.json | No | No | No |
| KPI | specs/kpi.md + contracts/kpi.schema.json | No | No | No |
| Bottleneck | specs/bottleneck.md + contracts/bottleneck.schema.json | No | No | No |
| Opportunity | specs/opportunity.md + contracts/opportunity.schema.json | No | No | No |
| Decision | specs/decision.md + contracts/decision.schema.json | No | No | No |
| Experiment | specs/experiment.md + contracts/experiment.schema.json | No | No | No |
| Lesson | specs/lesson.md + contracts/lesson.schema.json | No | No | No |
| Executive Brief | specs/executive-brief.md + contracts/executive-brief.schema.json | No | No | No |
| Operation | ADR-0004 (approved) | No | No | No |
| Job | ADR-0004 relationship definition | No | No | No |
| Knowledge Space | DEC-0004 | No (knowledge/ dir is physical approximation) | No | No |
| Entity Lifecycle | DEC-0005 | No | No | No |
| Agent | docs/14, 20, 21, 24 | No | No | No |
| Decision Engine | docs/12 | No | No | No |
| Scoring Model | docs/13 | No | No | No |
| Agent Orchestrator | docs/15 | No | No | No |

---

## 2. Capability Map

### Platform Capabilities

| Capability | Purpose | Dependencies | Business Value | Complexity |
|------------|---------|-------------|---------------|------------|
| **Knowledge Selection** | Retrieve relevant knowledge per query instead of positional truncation | KnowledgeEngine | Conversations answer with the right knowledge, not the first 5 docs | S |
| **Operation Tracking** | Track units of executive work with identity, lifecycle, persistence | Workspace, filesystem | Founder can see what Hermes is doing and has done | M |
| **Job Persistence** | Persist ExecutionResults as retrievable records | Operation, filesystem | Execution history survives beyond CLI session | M |
| **Operation Lifecycle** | State transitions per docs/24 §4, escalation tracking | Operation model, DEC-0005 | Founder can approve/reject; audit trail exists | M |
| **Conversation-to-Operation Bridge** | Promote chat directive to tracked Operation | HermesService, Operation | Conversations produce actionable, trackable work | S |
| **Knowledge API** | Expose Knowledge Documents via Gateway endpoint | KnowledgeEngine, Gateway | CEO Workspace can browse knowledge | S |
| **Operations API** | Expose Operations via Gateway endpoint | Operation store, Gateway | CEO Workspace can list and manage Operations | M |
| **Jobs API** | Expose Jobs via Gateway endpoint | Job store, Gateway | CEO Workspace can review execution history | M |
| **Dashboard API** | Expose workspace operating state summary | Operations, Knowledge, Gateway | Today screen shows attention items and counts | S |
| **Business Object Models** | Python dataclasses for the 10 canonical objects | contracts/*.schema.json, specs/*.md | Enable Decision Engine, Executive Brief generation | M |
| **Contract Validation** | Validate business object instances against JSON schemas | contracts/, examples/ | Ensure data consistency across all consumers | S |
| **Business Lifecycle Engine** | Execute docs/11 chain for a venture | Business Object Models, Knowledge Space | One full strategic cycle runs end-to-end | L |
| **Decision Engine** | Evaluate and rank recommendations per docs/12-13 | Business Objects, Scoring Model | Evidence-based prioritization | L |
| **Executive Brief Generator** | Produce synthesized brief per specs/executive-brief.md | All Business Objects, Provider | Founder receives structured reporting | M |
| **Knowledge Space Isolation** | Enforce per-venture knowledge boundaries per DEC-0004 | KnowledgeEngine | Multi-venture operation without data bleed | M |
| **Entity Lifecycle Service** | Generic lifecycle_state + transition log per DEC-0005 | Business Object Models | Consistent state management across all entities | M |
| **Agent Delegation** | Invoke specialist agents for domain work per docs/24 §4 | Agent registry, Provider | Hermes delegates to specialists | L |
| **Multi-Workspace Support** | Serve multiple Workspaces from one Gateway | WorkspaceEngine, Gateway | Founder operates multiple ventures | M |

### Application Capabilities

| Capability | Purpose | Dependencies | Business Value | Complexity |
|------------|---------|-------------|---------------|------------|
| **Today Screen** | Show attention items + operating state | Dashboard API, Operations API | Founder sees what needs attention | M |
| **Operations Screen** | List and manage Operations with lifecycle | Operations API | Founder tracks executive work | M |
| **Jobs Screen** | List and detail execution history | Jobs API | Founder reviews what actually happened | M |
| **Knowledge Screen** | Browse organizational knowledge | Knowledge API | Founder can read what Hermes knows | S |
| **Chat Enhancement** | Promote-to-Operation inline action | Conversation-to-Operation Bridge | Chat produces trackable work | S |

---

## 3. Implementation Phases

### Phase 1 — CEO Workspace Foundation

**Mission:** Enable the Founder to operate from the CEO Workspace with the existing platform.

**Deliverables:**
- Knowledge Selection (relevant docs per query)
- Knowledge API endpoint
- Dashboard API endpoint (workspace summary with counts)
- Today screen (attention items + operating state)
- Knowledge screen (browse + read documents)
- Chat enhancement (already functional; no changes needed beyond confirmation)

**Dependencies:** None — all built on existing implemented components.

**Exit criteria:**
- Founder opens CEO Workspace, sees workspace operating state on Today
- Founder browses and reads all 12 Knowledge Documents
- Conversations include relevant knowledge (not positional truncation)
- All existing 222 tests still pass + new tests for added capabilities

---

### Phase 2 — Operations & Jobs

**Mission:** Give Hermes the ability to track what it does.

**Deliverables:**
- Operation model + filesystem persistence
- Job model + filesystem persistence (wraps ExecutionResult)
- Operation lifecycle (created → executing → awaiting_escalation → completed/rejected/failed)
- HermesService.generate() creates and tracks Operations/Jobs
- Operations API endpoint
- Jobs API endpoint
- Operations screen (list, detail, approve, reject)
- Jobs screen (list, detail, diagnostics)
- Today screen updated with Operations count and escalation items

**Dependencies:** Phase 1 (Dashboard API exists to extend).

**Exit criteria:**
- `hermes generate` creates a persistent Operation with a Job
- Founder sees Operations in CEO Workspace
- Founder sees Jobs with generated output
- Founder can approve/reject escalated Operations
- Operations persist across Gateway restarts

---

### Phase 3 — Conversation-to-Operation Bridge

**Mission:** Connect the conversation path to the execution path.

**Deliverables:**
- Promote-to-Operation action in Chat
- HermesService method to create Operation from conversation context
- Inline notification in Chat when Operation is created
- Sidebar badges for escalated Operations

**Dependencies:** Phase 2 (Operations exist to create).

**Exit criteria:**
- Founder can say "Execute this" in Chat and an Operation is created
- Operation appears in Operations view
- Badge appears on Operations nav item
- Today screen shows the new escalation

---

### Phase 4 — Business Object Chain

**Mission:** Implement the 10 canonical business objects as runtime models.

**Deliverables:**
- Python dataclasses for: Business, Strategy, Goal, KPI, Bottleneck, Opportunity, Decision, Experiment, Lesson, ExecutiveBrief
- Contract validation (instances validated against JSON schemas)
- Entity Lifecycle Service (lifecycle_state + transition log per DEC-0005)
- Knowledge Space formalization (DEC-0004 entry types applied to AKosmicAnimals)
- Business data loader (reads existing businesses/AKosmicAnimals/ markdown files)

**Dependencies:** Phase 2 (Operations exist; filesystem persistence pattern established).

**Exit criteria:**
- All 10 business objects instantiable from existing AKosmicAnimals data
- All instances validate against their JSON Schema contracts
- Entity lifecycle transitions are logged
- `make validate` passes for all 10 contracts with examples

---

### Phase 5 — Executive Intelligence

**Mission:** Hermes reasons about the business, not just about conversations.

**Deliverables:**
- Decision Engine skill (docs/12 scoring against real business objects)
- Scoring Model implementation (docs/13 weighted dimensions)
- Executive Brief Generator skill (produces brief from business objects)
- docs/24 §4 ten-step loop as an orchestrating Operation

**Dependencies:** Phase 4 (business objects exist to reason about).

**Exit criteria:**
- One real Executive Brief generated for AKosmicAnimals
- One real Decision scored and ranked
- The ten-step loop runs for one initiative
- Brief and Decision are git-committed

---

### Phase 6 — Multi-Workspace & Second Venture

**Mission:** Prove the architecture works for more than one business.

**Deliverables:**
- Multi-Workspace Gateway support (workspace selector or routing)
- Second venture onboarded (Serelo or equivalent)
- Knowledge Space isolation verified (no cross-venture data bleed)
- Cross-Workspace Executive Brief aggregation (read-only, per DEC-0004 §5)

**Dependencies:** Phase 5 (full Business Lifecycle validated for one venture).

**Exit criteria:**
- Two ventures operate independently from the same Hermes instance
- Switching Workspace changes all context (organization, knowledge, operations)
- Portfolio-level brief aggregates from both ventures on Founder request
- DEC-0002 §5 multi-venture requirements validated

---

## 4. Application Roadmap

### CEO Workspace (Phase 1-3)

| | |
|---|---|
| **Purpose** | Enable the Founder to run AVANZIA from Hermes |
| **Business objects** | Organization, Workspace, Operation, Job, Knowledge Document, Decision |
| **Capabilities** | Today screen, Chat, Operations, Jobs, Knowledge browsing |
| **Dependencies** | Gateway, HermesService, ContextEngine, KnowledgeEngine, WorkspaceEngine |

### Executive Intelligence (Phase 5)

| | |
|---|---|
| **Purpose** | Hermes reasons about business performance and recommends actions |
| **Business objects** | Business, Strategy, Goal, KPI, Bottleneck, Opportunity, Decision, Experiment, Lesson, Executive Brief |
| **Capabilities** | Decision Engine, Scoring Model, Executive Brief Generator, ten-step loop |
| **Dependencies** | CEO Workspace (Phase 3), Business Object Chain (Phase 4) |

### Engineering Workspace (Future — post v1.0)

| | |
|---|---|
| **Purpose** | Enable developers to work with Hermes on code tasks |
| **Business objects** | Workspace, Repository, Operation, Job, Skill |
| **Capabilities** | Files view, Terminal view, code generation, PR creation |
| **Dependencies** | CEO Workspace (Phase 3), Agent Delegation |

### Marketing Workspace (Future — post v1.0)

| | |
|---|---|
| **Purpose** | Enable marketing work: copy, brand, campaigns |
| **Business objects** | Business, Strategy, Knowledge Document, Operation |
| **Capabilities** | Brand strategy, copywriting, campaign planning |
| **Dependencies** | Executive Intelligence (Phase 5), Business Object Chain (Phase 4) |

### Operations Workspace (Future — post v1.0)

| | |
|---|---|
| **Purpose** | Monitor and manage business operations |
| **Business objects** | KPI, Bottleneck, Opportunity, Operation, Job |
| **Capabilities** | KPI dashboards, bottleneck tracking, automation |
| **Dependencies** | Executive Intelligence (Phase 5) |

### Sales Workspace (Future — post v1.0)

| | |
|---|---|
| **Purpose** | Manage leads, clients, sales pipeline |
| **Business objects** | Lead (future), Client (future), Opportunity, Goal, KPI |
| **Capabilities** | Pipeline management, lead scoring |
| **Dependencies** | Business Object Chain (Phase 4), Entity Lifecycle Service |

### Finance Workspace (Future — post v1.0)

| | |
|---|---|
| **Purpose** | Financial tracking, budgeting, reporting |
| **Business objects** | Business, KPI, Executive Brief |
| **Capabilities** | Revenue tracking, budget management, financial briefs |
| **Dependencies** | Executive Intelligence (Phase 5) |

---

## 5. Platform Roadmap

What remains to be implemented for each platform component.

### Gateway

| Remaining | Phase |
|-----------|-------|
| GET /v1/knowledge — list Knowledge Documents | 1 |
| GET /v1/knowledge/{id} — read single document | 1 |
| GET /v1/dashboard — workspace operating state summary | 1 |
| GET /v1/operations — list Operations | 2 |
| GET /v1/operations/{id} — Operation detail | 2 |
| POST /v1/operations/{id}/approve — approve escalation | 2 |
| POST /v1/operations/{id}/reject — reject escalation | 2 |
| GET /v1/jobs — list Jobs | 2 |
| GET /v1/jobs/{id} — Job detail | 2 |
| POST /v1/operations — create Operation from conversation | 3 |
| Workspace selector/routing for multi-workspace | 6 |

### HermesService

| Remaining | Phase |
|-----------|-------|
| generate() creates and tracks Operation + Job | 2 |
| create_operation() from conversation context | 3 |
| approve_operation() / reject_operation() | 2 |
| list_operations() / get_operation() | 2 |
| list_jobs() / get_job() | 2 |

### Context Engine

| Remaining | Phase |
|-----------|-------|
| Pass user query to build_conversation() for knowledge selection | 1 |
| No other changes needed — architecture is stable | — |

### Knowledge Engine

| Remaining | Phase |
|-----------|-------|
| select() method — keyword relevance scoring | 1 |
| list_documents() without loading full content | 1 |
| get_document() for single document retrieval | 1 |
| Knowledge Space entry types per DEC-0004 | 4 |

### Execution Engine (kernel/executor.py)

| Remaining | Phase |
|-----------|-------|
| No changes needed — executor is complete | — |
| Job wrapping happens in HermesService, not in Executor | 2 |

### Workspace Engine

| Remaining | Phase |
|-----------|-------|
| No changes needed for Phase 1-3 | — |
| Multi-workspace routing support | 6 |

### Operation Engine (new)

| Remaining | Phase |
|-----------|-------|
| Operation model (dataclass) | 2 |
| OperationStore — read/write YAML in workspaces/{id}/operations/ | 2 |
| Lifecycle state transitions + validation | 2 |
| Transition log (append-only per DEC-0005) | 2 |

### Job Engine (new)

| Remaining | Phase |
|-----------|-------|
| Job model (dataclass wrapping ExecutionResult) | 2 |
| JobStore — read/write YAML in workspaces/{id}/jobs/ | 2 |
| Link to parent Operation | 2 |

---

## 6. UI Roadmap

### Screens to Build

| # | Screen | Phase | Dependencies |
|---|--------|-------|-------------|
| 1 | Today (replace Dashboard placeholder content) | 1 | Dashboard API |
| 2 | Knowledge — Document list | 1 | Knowledge API |
| 3 | Knowledge — Document detail | 1 | Knowledge API |
| 4 | Operations — list | 2 | Operations API |
| 5 | Operations — detail (lifecycle, jobs, decisions) | 2 | Operations API, Jobs API |
| 6 | Jobs — list | 2 | Jobs API |
| 7 | Jobs — detail (output, diagnostics) | 2 | Jobs API |
| 8 | Chat — promote-to-Operation action | 3 | Operations API |
| 9 | Sidebar — notification badges | 3 | Operations API |

### Reusable UI Components

| Component | Used by | Description |
|-----------|---------|-------------|
| **List view** | Operations list, Jobs list, Document list | Filterable, sortable list with clickable rows |
| **Detail view** | Operation detail, Job detail, Document detail | Header with metadata + scrollable content area |
| **Status badge** | Operations list/detail, Jobs list/detail | Colored indicator for lifecycle state |
| **Widget card** | Today screen | Metric display (title, value, subtitle) |
| **Attention item** | Today screen | Highlighted row for escalation items |
| **Filter bar** | Operations list, Jobs list | Status dropdown + search input |
| **Inline notification** | Chat | Brief confirmation message after action |
| **Nav badge** | Sidebar | Numeric indicator on nav item |

### Recommended Implementation Order

1. **Widget card** — needed for Today screen (Phase 1)
2. **List view** — needed for Document list (Phase 1), then reused for Operations and Jobs (Phase 2)
3. **Detail view** — needed for Document detail (Phase 1), then reused for Operation and Job detail (Phase 2)
4. **Today screen** — uses widget cards (Phase 1)
5. **Document list + detail** — uses list view + detail view (Phase 1)
6. **Status badge** — needed for Operations (Phase 2)
7. **Filter bar** — needed for Operations list (Phase 2)
8. **Attention item** — extends Today screen for escalations (Phase 2)
9. **Operations list + detail** — uses list view + detail view + status badge + filter bar (Phase 2)
10. **Jobs list + detail** — reuses all of above (Phase 2)
11. **Inline notification** — needed for Chat promote action (Phase 3)
12. **Nav badge** — needed for sidebar escalation indicators (Phase 3)

---

## 7. Acceptance Milestones

### Alpha

**The Founder can use Hermes to talk, read knowledge, and see workspace state.**

- Today screen shows workspace identity, repository status, knowledge count, system health
- Chat works with organization and knowledge grounding (already implemented)
- Knowledge screen browses and reads all 12 AVANZIA documents
- Conversations include relevant knowledge (not positional truncation)
- All existing tests pass + new tests for Phase 1 capabilities

### Beta

**The Founder can track what Hermes does.**

- Operations screen lists tracked Operations with lifecycle status
- Jobs screen lists execution history with generated output and diagnostics
- `hermes generate` creates a persistent Operation and Job
- Founder can approve/reject escalated Operations
- Conversation-to-Operation bridge works from Chat
- Sidebar badges notify the Founder of pending escalations
- Today screen shows active Operations count and attention items

### Release Candidate

**Hermes reasons about the business.**

- All 10 canonical business objects are runtime models with contract validation
- Entity Lifecycle Service tracks state transitions for participating entities
- Decision Engine produces scored, ranked recommendations for AKosmicAnimals
- Executive Brief Generator produces a structured brief from business data
- One full docs/11 Business Lifecycle chain runs end-to-end for AKosmicAnimals
- One full docs/24 §4 ten-step loop completes as a tracked Operation

### v1.0

**Hermes operates multiple businesses.**

- Second venture onboarded using identical pattern
- Knowledge Space isolation verified (no cross-venture data bleed)
- Portfolio-level Executive Brief aggregation works on Founder request
- All Draft/D1 governing documents touched by implementation have advanced to D3 (Approved)
- Founder runs AVANZIA daily from the CEO Workspace

---

## 8. Implementation Order

Every item is one engineering sprint. No sprint depends on future work.

| Sprint | Deliverable | Phase | Exit Criteria |
|--------|-------------|-------|---------------|
| **7** | Knowledge Selection | 1 | KnowledgeEngine.select() scores documents by query relevance. Fallback to manifest order when no match. Conductor uses selected docs instead of positional truncation. Tests pass. |
| **8** | Knowledge API | 1 | GET /v1/knowledge lists documents (title, id, size). GET /v1/knowledge/{id} returns full content. Tests pass. |
| **9** | Today Screen + Dashboard API | 1 | GET /v1/dashboard returns workspace summary (identity, repository count, knowledge count, health). Today screen renders widgets. Replaces generic Dashboard. **Alpha milestone.** |
| **10** | Knowledge Screen | 1 | Document list view renders from Knowledge API. Document detail view shows full content. "Ask about this" navigates to Chat with pre-filled input. Tests pass. |
| **11** | Operation Model + Store | 2 | Operation dataclass with id, workspace_id, request, status, timestamps. OperationStore reads/writes YAML in workspaces/{id}/operations/. Lifecycle transitions validated. Tests pass. |
| **12** | Job Model + Store | 2 | Job dataclass wrapping ExecutionResult with id, operation_id, timestamps. JobStore reads/writes YAML in workspaces/{id}/jobs/. Link to parent Operation. Tests pass. |
| **13** | HermesService Operation Integration | 2 | generate() creates Operation before execution, creates Job from result, updates Operation status. list_operations(), get_operation(), list_jobs(), get_job() methods. Tests pass. |
| **14** | Operations + Jobs API | 2 | Gateway endpoints for Operations (list, detail, approve, reject) and Jobs (list, detail). Tests pass. |
| **15** | Operations Screen | 2 | Operations list with status filter and search. Operation detail with lifecycle progress, Jobs list, Decisions. Approve/Reject actions for escalated Operations. Tests pass. |
| **16** | Jobs Screen | 2 | Jobs list with status and Operation filters. Job detail with generated output and collapsible diagnostics. Link to parent Operation. Tests pass. |
| **17** | Today Screen — Operations Integration | 2 | Today screen shows active Operations count and escalation attention items. Click-through to Operations detail. Tests pass. |
| **18** | Conversation-to-Operation Bridge | 3 | HermesService.create_operation_from_chat(). POST /v1/operations endpoint. Chat inline notification on creation. Tests pass. |
| **19** | Notification Badges | 3 | Sidebar badges on Operations (escalation count) and Jobs (failed count). Badges clear on navigation. **Beta milestone.** |
| **20** | Business Object Models | 4 | Python dataclasses for all 10 canonical objects. Contract validation against JSON schemas. Example instances for AKosmicAnimals. `make validate` passes. Tests pass. |
| **21** | Entity Lifecycle Service | 4 | Generic lifecycle_state field, transition table, append-only transition log per DEC-0005. Applied to Business and one other entity type. Tests pass. |
| **22** | Business Data Loader | 4 | Load existing businesses/AKosmicAnimals/ markdown files into typed business objects. Knowledge Space formalization per DEC-0004. Tests pass. |
| **23** | Decision Engine | 5 | Scoring skill per docs/12-13 weighted dimensions. Produces ranked recommendations from real AKosmicAnimals data. One real Decision scored and git-committed. Tests pass. |
| **24** | Executive Brief Generator | 5 | Generator skill per specs/executive-brief.md. Produces brief from business objects. One real brief generated and git-committed. Tests pass. |
| **25** | Ten-Step Loop | 5 | docs/24 §4 loop runs as one tracked Operation for AKosmicAnimals. Audit trail: discovery report + lifecycle log + recorded Decision. **Release Candidate milestone.** |
| **26** | Multi-Workspace Support | 6 | Gateway serves multiple Workspaces. Workspace selector or routing. Context isolation verified. |
| **27** | Second Venture Onboarding | 6 | Second venture bootstrapped using identical pattern. Knowledge Space isolation confirmed. Portfolio-level brief aggregation works. **v1.0 milestone.** |

---

## Review Checklist

- [x] Owner Approval (2026-08-01)
- [ ] Technical Review
- [ ] Security Review
- [ ] Architecture Review
- [ ] Git Commit
- [ ] GitHub Push
- [ ] Project Log Updated
