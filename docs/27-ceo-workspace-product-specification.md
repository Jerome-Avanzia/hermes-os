---
document_id: DOC-0027
title: CEO Workspace — Application Design
version: 1.0.0
status: Approved
maturity: D3
owner: AVANZIA
document_type: Specification
authority_level: 4
classification: Internal
created: 2026-08-01
last_updated: 2026-08-01
review_cycle: as-needed
approved_by: Founder
security_review: Pending
related:
  - 26-master-implementation-plan.md
  - 28-ceo-workspace-ux-specification.md
  - 29-business-object-model.md
  - decisions/ADR-0001-canonical-hermes-workspace-shell.md
  - decisions/ADR-0004-operation-unit-of-business-execution.md
  - decisions/DEC-0002-hermes-executive-operating-model.md
tags:
  - ceo-workspace
  - application
  - product
---

# CEO Workspace — Application Design

## 1. Purpose

The CEO Workspace is the application that enables the Founder to run AVANZIA from Hermes.

It is not a new system. It is the first application built on the existing platform: Gateway (ADR-0002), HermesService, Context Engine (ADR-0003), Conductor, KnowledgeEngine, WorkspaceEngine, and the canonical Workspace shell (ADR-0001).

Today the Workspace shell has two functional surfaces — Dashboard and Chat — and nine placeholder modules. The CEO Workspace activates the modules the Founder needs to operate daily:

- See what is happening (Dashboard)
- Talk to Hermes (Chat)
- Track what Hermes is doing (Operations)
- See execution history (Jobs)
- Access organizational knowledge (Knowledge)

Everything else remains "Coming soon." The CEO Workspace is not the full shell — it is the minimum viable operating surface.

**Repository evidence for what the CEO needs daily:**

From `businesses/AKosmicAnimals/Business_Profile.md` §Hermes Daily Questions:
> Every morning Hermes should answer: What changed since yesterday? Which KPI improved? Which KPI declined? What requires attention today? What are today's Top 3 actions?

From `businesses/AKosmicAnimals/Executive_Brief_Template.md`:
> Executive Summary, KPI Snapshot, Wins, Risks & Bottlenecks, Opportunities, Top 3 Priorities Today, Recommended Decisions, Questions for the Owner.

From `docs/20-executive-model.md` §3 (Hermes Agent responsibilities):
> Executes company strategy. Coordinates specialist agents. Prioritizes initiatives. Recommends decisions. Monitors KPIs. Reports progress. Escalates when authority limits are reached.

From `docs/20-executive-model.md` §6 (Founder receives):
> Synthesized Executive Briefs rather than raw data dumps, with explicit "Questions for the Owner."

---

## 2. Primary Screens

Five screens, mapped to the canonical shell's existing navigation modules.

### Screen 1: Dashboard

**Canonical shell module:** `view-dashboard` (exists, partially implemented)

**What the Founder sees:**

- **Workspace identity** — name, description, mission (already implemented via `GET /v1/workspace`)
- **Repositories** — name and branch for each repo (already implemented)
- **Active model** — current provider and model (already implemented)
- **Gateway status** — health indicator (already implemented)
- **Operations summary** — count of active Operations, last completed Operation (new)
- **Knowledge summary** — count of loaded Knowledge Documents (new)

**What it replaces:** The current Dashboard shows Sessions, API Calls, Usage Trend, and Cache Efficiency (from the Pilot image). These are infrastructure metrics. The CEO Workspace Dashboard shows business operating state instead.

### Screen 2: Chat

**Canonical shell module:** `view-chat` (exists, fully implemented)

**What the Founder sees:**

- Conversation grounded in Organization context (Sprint 6: implemented)
- Knowledge Documents in system prompt (Sprint 6: implemented)
- Profile selector (implemented)
- Session history in sidebar (implemented)
- Error handling with recovery (Sprint 5: implemented)

**What changes:** Nothing. Chat is already functional. The CEO uses it to talk to Hermes about strategy, ask questions grounded in organizational knowledge, and identify work that may become Operations.

### Screen 3: Operations

**Canonical shell module:** `view-operations` (exists as placeholder)

**What the Founder sees:**

- List of Operations within the active Workspace
- Each Operation shows: id, request summary, status, created date, last updated
- Status reflects docs/24 §4 lifecycle progress
- Operations with `awaiting_escalation` status are visually highlighted — these need Founder attention
- Click into an Operation to see: the request, lifecycle steps completed, Jobs produced, Decisions recorded, Artifacts generated

**What it provides that does not exist today:** Visibility into what Hermes is doing and has done. Today, execution is fire-and-forget — the CLI prints a result and it disappears. Operations gives the Founder a persistent record.

### Screen 4: Jobs

**Canonical shell module:** `view-jobs` (exists as placeholder)

**What the Founder sees:**

- List of execution runs within the active Workspace
- Each Job shows: id, parent Operation, status, started/finished timestamps, completed steps
- Click into a Job to see: the ExecutionResult — completed steps, generated output, diagnostics
- Filter: by Operation, by status, by date

**What it provides that does not exist today:** The CLI command `hermes execute` and `hermes generate` produce ExecutionResults that vanish after the command exits. Jobs persists them.

### Screen 5: Knowledge

**Canonical shell module:** Not in the current shell nav, but the Knowledge section exists in the sidebar under the "Knowledge" group alongside Memory, Skills, MCP, Profiles.

**What the Founder sees:**

- List of Knowledge Documents loaded for the active Workspace
- Each document shows: title, id, size
- Click into a document to read its content
- Shows which documents were included in the last conversation's context (from DiagnosticsReport)

**What it provides that does not exist today:** The CLI command `hermes knowledge AVANZIA` lists titles. The CEO Workspace makes the full knowledge base browsable and shows which knowledge is actively grounding conversations.

---

## 3. User Workflows

### Workflow A: Morning Brief

1. Founder opens CEO Workspace → lands on **Dashboard**
2. Dashboard shows: workspace identity, active Operations, knowledge count, system health
3. Founder clicks **Chat** → asks "What should I focus on today?"
4. Hermes responds grounded in Organization (purpose, mission, services) and Knowledge Documents
5. If the conversation identifies actionable work → Founder says "Do this" → a new Operation is created
6. Operation appears in **Operations** view

**Business objects used:** Organization, Workspace, Knowledge Document, Operation

### Workflow B: Track Active Work

1. Founder clicks **Operations**
2. Sees list of active Operations, sorted by last updated
3. Notices an Operation with status `awaiting_escalation` — Hermes needs a Decision
4. Clicks into the Operation → reads context → makes the Decision
5. Operation continues its lifecycle

**Business objects used:** Operation, Decision, Job

### Workflow C: Review Execution History

1. Founder clicks **Jobs**
2. Sees recent execution runs with their statuses and outputs
3. Clicks into a Job → reads generated output (e.g., a code proposal from Claude)
4. Decides whether to approve or reject
5. Job status updates → parent Operation lifecycle advances

**Business objects used:** Job, Operation

### Workflow D: Consult Knowledge

1. Founder clicks **Knowledge** (under Knowledge section in sidebar)
2. Browses the 12 AVANZIA Knowledge Documents
3. Reads a specific document (e.g., Brand Personality) to prepare for a conversation
4. Switches to **Chat** → asks a question → Hermes responds grounded in that knowledge

**Business objects used:** Knowledge Document, Organization

### Workflow E: Ongoing Conversation

1. Founder is in **Chat** → multi-turn conversation about strategy
2. Conversation references Organization context (injected by Conductor)
3. Conversation references Knowledge Documents (injected by Conductor)
4. At any point, Founder can say "Execute this" → Conversation promotes to Operation
5. Or the conversation remains advisory — no Operation created

**Business objects used:** Organization, Knowledge Document, (optionally) Operation

---

## 4. Business Objects Used

| Business Object | Screen(s) | How Used |
|-----------------|-----------|----------|
| **Organization** | Dashboard, Chat | Dashboard shows identity. Chat grounds every conversation in Organization context (purpose, vision, mission, positioning, services, brand). |
| **Workspace** | Dashboard, all screens | Dashboard shows workspace identity. Workspace scopes all Operations, Jobs, Knowledge. The active workspace (`HERMES_WORKSPACE` env var) is the operating boundary. |
| **Operation** | Operations, Dashboard | Operations screen lists and details Operations. Dashboard shows active count. Created from Chat or CLI. Tracks lifecycle per ADR-0004/docs/24 §4. |
| **Job** | Jobs, Operations | Jobs screen lists execution runs. Operations screen shows Jobs belonging to an Operation. Each Job wraps an ExecutionResult. |
| **Knowledge Document** | Knowledge, Chat | Knowledge screen browses documents. Chat includes relevant documents in system prompt via Conductor. |
| **Decision** | Operations | Recorded within an Operation at escalation points. Visible in Operation detail view. Governed by DEC-0002 Delegation Principle. |

**Not used by CEO Workspace (correct by design):**

- Business, Strategy, Goal, KPI, Bottleneck, Opportunity, Experiment, Lesson, Executive Brief — these are the 10 canonical business objects from docs/11. They are strategic-layer objects. The CEO Workspace is an execution-layer application. The strategic objects will be served by a future Business Intelligence application, not this one.
- Agent, Swarm — deferred per HERMES_V1_ARCHITECTURE §5.
- Skill, Capability, Profile — platform objects consumed internally. Profile is already exposed via the profile selector in Chat.

---

## 5. Operations Supported

The CEO Workspace supports the following Operations through Hermes:

| Operation Type | Trigger | Example |
|----------------|---------|---------|
| **Conversation-promoted** | Founder says "Do this" in Chat | "Refactor the Python backend" → Operation created |
| **CLI-initiated** | `hermes generate "..."` or `hermes execute "..."` | Execution runs appear as Jobs; parent Operation trackable in UI |
| **Knowledge consultation** | Implicit — no Operation | Browsing Knowledge Documents, asking questions — advisory, no tracked Operation |
| **Decision response** | Founder reviews escalation in Operations view | Operation awaiting escalation → Founder decides → Operation continues |

The CEO Workspace does **not** support:

- Creating or editing Business, Strategy, Goal, KPI objects (strategic layer — future application)
- Managing Agents or Swarms (deferred per V1 Architecture)
- Editing Knowledge Documents (knowledge is authored in source, versioned in Git — Principle 3)
- Modifying governance documents (Operating Model, ADRs — these are Git-managed)
- File editing or terminal access (shown in shell but not part of CEO workflow)

---

## 6. Definition of Done

The CEO Workspace is complete when:

1. **Dashboard shows workspace operating state.** Organization identity, repository status, active Operations count, knowledge document count, gateway/model health — all rendered from existing endpoints plus new Operation/Knowledge endpoints.

2. **Chat works with full context grounding.** Already implemented (Sprint 5-6). Conversations are grounded in Organization and Knowledge. Profile selection works. Streaming and error handling work.

3. **Operations view lists and details Operations.** Operations within the active Workspace are listed with id, status, request summary, and timestamps. Clicking an Operation shows its lifecycle progress, Jobs, Decisions, and Artifacts. Operations with `awaiting_escalation` are visually distinct.

4. **Jobs view lists and details execution runs.** Jobs show ExecutionResult data: completed steps, status, timestamps, generated output, diagnostics. Jobs link to their parent Operation.

5. **Knowledge view browses loaded documents.** Knowledge Documents for the active Workspace are listed with title and id. Clicking a document shows its full content.

6. **All five screens are reachable from the canonical shell navigation.** Dashboard, Chat, Operations, Jobs, and Knowledge are active modules. All other modules remain "Coming soon."

7. **No new abstractions introduced.** The application uses existing Gateway endpoints, existing HermesService methods, existing models, and new endpoints that expose existing kernel data (Operations, Jobs, Knowledge listing).

8. **All existing tests pass.** No regression in the 222 existing tests.

9. **The Founder can perform all five workflows** (Morning Brief, Track Active Work, Review Execution, Consult Knowledge, Ongoing Conversation) without leaving the CEO Workspace.

---

## Review Checklist

- [x] Owner Approval (2026-08-01)
- [ ] Technical Review
- [ ] Security Review
- [ ] Architecture Review
- [ ] Git Commit
- [ ] GitHub Push
- [ ] Project Log Updated
