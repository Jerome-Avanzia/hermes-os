---
document_id: DOC-0028
title: CEO Workspace — User Experience Specification
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
  - 27-ceo-workspace-product-specification.md
  - decisions/ADR-0001-canonical-hermes-workspace-shell.md
  - decisions/ADR-0004-operation-unit-of-business-execution.md
tags:
  - ceo-workspace
  - ux
  - specification
---

# CEO Workspace — User Experience Specification

## 1. Navigation Model

The canonical shell (ADR-0001) defines a sidebar with navigation sections. The CEO Workspace activates five modules from the existing structure.

### Active Navigation

```
HERMES WORKSPACE
─────────────────
Search
New Session

MAIN
  Today          ← renamed from Dashboard
  Chat
  Operations
  Jobs

KNOWLEDGE
  Documents      ← renamed from generic "Knowledge" placeholder

SESSIONS
  [session list]
```

**Renaming rationale:** "Dashboard" is a generic infrastructure term. The Founder's question is not "What does my dashboard show?" — it is "What needs my attention today?" The module is renamed "Today." Similarly, the Knowledge section's entry is "Documents" because that is what the Founder browses — the organization's Knowledge Documents.

All other shell modules (Files, Terminal, Kanban, Conductor, Swarm, Memory, Skills, MCP, Profiles) remain as placeholders with "Coming soon."

### Navigation Flows

```
Founder opens CEO Workspace
  │
  └─► Today (default landing)
        │
        ├─ sees escalation badge ──► clicks ──► Operations
        │                                         │
        │                                         ├─► Operation detail
        │                                         │     │
        │                                         │     ├─► Approve / Reject
        │                                         │     └─► View Job ──► Jobs detail
        │                                         │
        │                                         └─► back to Operations list
        │
        ├─ wants to talk to Hermes ──► Chat
        │                               │
        │                               ├─► conversation
        │                               │     │
        │                               │     └─► "Execute this" ──► Operation created
        │                               │           │
        │                               │           └─► visible in Operations
        │                               │
        │                               └─► New Session (sidebar or topbar button)
        │
        ├─ wants to review work ──► Jobs
        │                            │
        │                            ├─► Job detail (output, diagnostics)
        │                            └─► link to parent Operation ──► Operations detail
        │
        └─ wants to read knowledge ──► Documents
                                        │
                                        ├─► document list
                                        └─► document detail (full content)
```

### Cross-screen Links

| From | To | Trigger |
|------|----|---------|
| Today → Operations | Click on active Operations count or escalation item | Direct link |
| Today → Chat | Click "New Chat" in topbar or "New Session" in sidebar | Standard action |
| Operations detail → Jobs | Click on a Job within the Operation | Inline link |
| Jobs detail → Operations | Click on parent Operation id | Inline link |
| Chat → Operations | Conversation promoted to Operation | System creates Operation, notification appears |
| Any screen → Today | Click "Today" in sidebar | Navigation |

---

## 2. Screen Layouts

### Screen 1: Today

**CEO question:** "What needs my attention?"

**Header:**
- Title: Workspace name (e.g., "AVANZIA")
- Subtitle: Workspace mission (e.g., "Design, build, and operate AI-powered businesses...")
- Topbar right: "New Chat" button, profile selector

**Primary content — Attention items:**
- Operations awaiting escalation (count + list with summaries). Each item is clickable → navigates to Operation detail.
- If zero: "No items need your attention."

**Secondary content — Operating state:**
- Widget: Operations — active count, completed today, total
- Widget: Knowledge — document count, last loaded
- Widget: System — gateway version, model name, health status
- Widget: Repositories — name + branch for each repo

**Actions:**
- Click attention item → navigates to Operation detail
- Click Operations widget → navigates to Operations list
- Click "New Chat" → navigates to Chat

**Empty state:**
"No items need your attention. Hermes is operating normally."
Workspace identity and system widgets still display.

**Loading state:**
Widgets show skeleton placeholders. Attention section shows "Loading..."

**Error state:**
If workspace endpoint unreachable: "Cannot reach Hermes Gateway." System widget shows gateway status as offline. Other widgets show "—".

---

### Screen 2: Chat

**CEO question:** "What should we do?"

**Header:**
- Title: "Hermes Workspace" (existing)
- Topbar right: "New Chat" button, profile selector (existing)

**Primary content:**
- Message thread — user and assistant messages, scrollable (existing)
- Input area — textarea + send button (existing)

**Secondary content:**
- None. Chat is full-focus.

**Actions:**
- Type message + Send (Enter or click) — existing
- Switch profile — existing
- New Session — clears history, starts fresh — existing
- Promote to Operation — Founder types a directive (e.g., "Execute this", "Do this for AVANZIA"). Hermes acknowledges and creates an Operation. A brief inline notification confirms: "Operation OP-xxx created."

**Empty state:**
"What would you like to accomplish today?" (existing)

**Loading state:**
Send button disabled, assistant message streams token by token (existing SSE behavior).

**Error state:**
Red inline message: "Error: [message]" (existing). Provider connection errors show SSE error frame (Sprint 5).

---

### Screen 3: Operations

**CEO question:** "What is Hermes doing?"

**Header:**
- Title: "Operations"
- Filter controls: status filter (All, Active, Awaiting Escalation, Completed, Failed), date range

**Primary content — Operations list:**
- Table/list of Operations within the active Workspace
- Each row shows:
  - Operation ID (e.g., "OP-20260801-001")
  - Request summary (truncated to one line)
  - Status (with color indicator)
  - Created date
  - Last updated
- Sorted by: last updated (most recent first)
- Operations with `awaiting_escalation` status appear with a visual accent (badge or highlight)

**Secondary content:**
- None at list level. Detail view is a separate state.

**Actions:**
- Filter by status → list updates
- Click an Operation → opens Operation detail view (below)
- Search → filters by request text

**Empty state:**
"No Operations yet. Start one from Chat or the CLI."

**Loading state:**
List shows skeleton rows.

**Error state:**
"Cannot load Operations." Retry link.

---

### Screen 3a: Operation Detail

**CEO question:** "What is happening with this specific work?"

**Header:**
- Title: Operation ID
- Status badge
- Created / last updated timestamps

**Primary content — Lifecycle progress:**
- Visual representation of docs/24 §4 steps completed vs. remaining
- Current status prominently displayed
- If `awaiting_escalation`: the escalation context is shown — what Hermes needs the Founder to decide

**Secondary content:**
- **Jobs produced** — list of Jobs (clickable → navigates to Job detail)
- **Decisions recorded** — list of Decisions made during this Operation, with rationale
- **Request** — the full original request text

**Actions:**
- Approve — advances the Operation past the escalation point. Available only when status is `awaiting_escalation`.
- Reject — marks the Operation as rejected with a reason. Available only when status is `awaiting_escalation`.
- Back — returns to Operations list.

**Empty state (no Jobs yet):**
"This Operation has not produced any Jobs yet."

**Loading state:**
Detail sections show skeleton content.

**Error state:**
"Cannot load this Operation." Back link to list.

---

### Screen 4: Jobs

**CEO question:** "What actually happened?"

**Header:**
- Title: "Jobs"
- Filter controls: status filter (All, Completed, Awaiting Approval, Failed), Operation filter

**Primary content — Jobs list:**
- Table/list of execution runs within the active Workspace
- Each row shows:
  - Job ID
  - Parent Operation ID (clickable → navigates to Operation detail)
  - Status
  - Completed steps (comma-separated names)
  - Started / finished timestamps
  - Duration
- Sorted by: most recent first

**Secondary content:**
- None at list level.

**Actions:**
- Filter by status or Operation → list updates
- Click a Job → opens Job detail view (below)

**Empty state:**
"No Jobs have been executed yet."

**Loading state:**
List shows skeleton rows.

**Error state:**
"Cannot load Jobs." Retry link.

---

### Screen 4a: Job Detail

**CEO question:** "What did this execution produce?"

**Header:**
- Title: Job ID
- Parent Operation link (clickable)
- Status badge
- Timestamps (started, finished, duration)

**Primary content — Output:**
- Generated output (full text, scrollable). This is the `ExecutionResult.generated_output` — the proposal, code, analysis, or whatever the provider produced.
- If no generated output: "This Job completed without generated output." (execution-only, no provider invoked)

**Secondary content:**
- **Completed steps** — ordered list of steps that executed
- **Diagnostics** (collapsible) — project, knowledge documents used, files scanned/selected/read, chars read/truncated, prompt size

**Actions:**
- Back — returns to Jobs list
- View Operation — navigates to parent Operation detail

**Empty state:**
N/A — a Job always has at least status and timestamps.

**Loading state:**
Content sections show skeleton.

**Error state:**
"Cannot load this Job." Back link.

---

### Screen 5: Documents (Knowledge)

**CEO question:** "What do we know?"

**Header:**
- Title: "Knowledge"
- Subtitle: document count (e.g., "12 documents")

**Primary content — Document list:**
- List of Knowledge Documents for the active Workspace
- Each row shows:
  - Title (extracted from markdown H1)
  - ID (filename stem, e.g., "01-purpose")
  - Size (character count, human-readable)
- Sorted by: manifest order (the authored sequence)

**Secondary content:**
- None at list level.

**Actions:**
- Click a document → opens Document detail view (below)
- Search → filters by title or content match

**Empty state:**
"No Knowledge Documents found for this Workspace."

**Loading state:**
List shows skeleton rows.

**Error state:**
"Cannot load Knowledge." Retry link.

---

### Screen 5a: Document Detail

**CEO question:** "What does this document say?"

**Header:**
- Title: document title
- ID shown below title
- Back link to document list

**Primary content:**
- Full document content rendered as text (the markdown source, displayed readably)

**Secondary content:**
- None.

**Actions:**
- Back — returns to document list
- "Ask about this" — navigates to Chat with the document title pre-filled as context hint in the input (e.g., "Regarding [Title]: "). Does not auto-send.

**Empty state:**
N/A — a document always has content.

**Loading state:**
Content area shows skeleton text.

**Error state:**
"Cannot load this document." Back link.

---

## 3. User Interactions

### Today

| Interaction | Trigger | Result |
|-------------|---------|--------|
| Open | Navigate to Today (default on load) | Workspace state renders: attention items, widgets |
| Refresh | Page reload or pull-to-refresh | All widgets and attention items re-fetch |
| Open Operation | Click attention item or Operations widget | Navigate to Operations (list or detail) |
| Start Chat | Click "New Chat" | Navigate to Chat with empty session |

### Operations

| Interaction | Trigger | Result |
|-------------|---------|--------|
| Browse | Navigate to Operations | List renders with all Operations |
| Filter by status | Select status from filter control | List shows only matching Operations |
| Search | Type in search field | List filters by request text match |
| Open | Click an Operation row | Operation detail view renders |
| Approve | Click "Approve" on escalated Operation | Operation status advances. Confirmation shown. |
| Reject | Click "Reject" on escalated Operation | Rejection reason prompted. Operation status set to rejected. |
| View Job | Click a Job link in Operation detail | Navigate to Job detail |
| Back | Click back from detail | Return to Operations list |

### Jobs

| Interaction | Trigger | Result |
|-------------|---------|--------|
| Browse | Navigate to Jobs | List renders with all Jobs |
| Filter by status | Select status from filter control | List shows only matching Jobs |
| Filter by Operation | Select Operation from filter control | List shows only Jobs for that Operation |
| Open | Click a Job row | Job detail view renders |
| View Operation | Click parent Operation link | Navigate to Operation detail |
| Expand diagnostics | Click diagnostics section header | Diagnostics expand/collapse |
| Back | Click back from detail | Return to Jobs list |

### Documents (Knowledge)

| Interaction | Trigger | Result |
|-------------|---------|--------|
| Browse | Navigate to Documents | Document list renders in manifest order |
| Search | Type in search field | List filters by title or content match |
| Read | Click a document row | Document detail view renders with full content |
| Ask about this | Click "Ask about this" in document detail | Navigate to Chat. Input pre-filled with "Regarding [Title]: " |
| Back | Click back from detail | Return to document list |

### Chat

| Interaction | Trigger | Result |
|-------------|---------|--------|
| Send message | Type + Enter or click Send | Message appears. Assistant streams response via SSE. |
| Continue conversation | Send another message in same session | History preserved. Context includes full conversation. |
| Switch profile | Select from profile dropdown | Next message uses selected profile's system prompt. |
| New session | Click "New Session" or "New Chat" | History clears. Fresh conversation starts. |
| Promote to Operation | Founder sends a directive ("Execute this", "Do this") | Hermes creates an Operation. Inline confirmation: "Operation OP-xxx created." Operation appears in Operations view. |
| View error | Provider fails mid-stream | Error message shown inline. Input re-enabled. |

---

## 4. Notification Model

Hermes notifies the Founder within the CEO Workspace through two mechanisms: **badges** and **inline notifications**.

### Badges

| Event | Where shown | Badge content |
|-------|-------------|---------------|
| Operation awaiting escalation | Today sidebar nav item, Operations sidebar nav item | Count of escalated Operations (e.g., "2") |
| Job failed | Jobs sidebar nav item | Count of failed Jobs since last viewed |

Badges are numeric indicators on sidebar navigation items. They clear when the Founder navigates to the relevant screen.

### Inline Notifications

| Event | Where shown | Content |
|-------|-------------|---------|
| Operation created from Chat | Chat view, inline after assistant response | "Operation OP-xxx created." |
| Approve/Reject confirmed | Operations detail view | "Operation approved." / "Operation rejected." |
| Provider connection error | Chat view, inline | "Error: [message]" (existing Sprint 5 behavior) |

### Not Notified (by design)

| Event | Why not |
|-------|---------|
| Knowledge updated | Knowledge is Git-managed. The Founder updates it. No notification needed for self-initiated changes. |
| Workspace changed | Workspace identity is static configuration. Changes are deliberate. |
| Job completed successfully | Normal operation. The Founder checks Jobs when interested, not when interrupted. |
| New conversation started | The Founder starts conversations. No notification for self-initiated actions. |

**Principle:** Notifications exist only for events that require Founder attention or confirm Founder actions. Hermes does not interrupt the Founder with status updates about normal operation. The Founder pulls status (by visiting Today, Operations, Jobs). Hermes pushes only escalations.

---

## 5. Design Principles

### Every screen answers one CEO question

| Screen | Question | Answer format |
|--------|----------|---------------|
| **Today** | "What needs my attention?" | Attention items (escalations) + operating state widgets |
| **Chat** | "What should we do?" | Grounded conversation with Organization and Knowledge context |
| **Operations** | "What is Hermes doing?" | List of tracked work with lifecycle status |
| **Jobs** | "What actually happened?" | Execution history with outputs and diagnostics |
| **Documents** | "What do we know?" | Browsable organizational knowledge |

### Information hierarchy

Every screen follows the same hierarchy:

1. **What requires action** — always first. Escalations, failures, decisions needed.
2. **What is current** — active state. Running Operations, streaming conversation, loaded documents.
3. **What is historical** — completed work. Past Jobs, past Operations, recorded Decisions.

The Founder should never have to scroll past historical data to find something that needs attention.

### Progressive disclosure

- Lists show summaries. Details show full content.
- Diagnostics are collapsed by default. Expanded on click.
- Knowledge Documents show titles in list, full content in detail.
- Operation lifecycle shows current step prominently, completed steps compactly.

### Zero-configuration

The CEO Workspace requires no setup. It reads the active Workspace (`HERMES_WORKSPACE` env var), loads Organization from workspace.yaml, loads Knowledge from the registry, and shows what exists. If nothing exists, empty states explain what to do.

### Consistency with canonical shell

- All screens use the existing shell layout: sidebar + topbar + content area.
- Navigation items, styling, and interaction patterns follow ADR-0001's Pilot.
- New screens do not introduce new layout patterns. They use the same widget, list, and detail patterns visible in the Pilot's Dashboard.

### Founder-first, not admin-first

- No configuration screens.
- No system administration.
- No model management.
- No profile editing.
- The CEO Workspace is for running the business, not running the platform.

---

## 6. Definition of Done

The CEO Workspace UX is complete when:

1. **Today answers "What needs my attention?"** — Attention items render when Operations are escalated. Operating state widgets show live data. The Founder can navigate from Today to any escalated Operation in one click.

2. **Chat answers "What should we do?"** — Conversations are grounded in Organization and Knowledge (already implemented). Promote-to-Operation creates a tracked Operation from a conversation. The Founder receives inline confirmation.

3. **Operations answers "What is Hermes doing?"** — Operations list renders with filtering and search. Operation detail shows lifecycle progress, Jobs, and Decisions. Approve and Reject actions work for escalated Operations.

4. **Jobs answers "What actually happened?"** — Jobs list renders with filtering. Job detail shows generated output and diagnostics. Jobs link to parent Operations.

5. **Documents answers "What do we know?"** — Document list renders in manifest order. Document detail shows full content. "Ask about this" navigates to Chat with context.

6. **Navigation is complete.** All five screens are reachable from the sidebar. Cross-screen links work (Operations <-> Jobs, Documents -> Chat, Today -> Operations). Badges appear on sidebar items when attention is needed.

7. **All states are handled.** Every screen has defined empty, loading, and error states. No screen shows a blank area without explanation.

8. **The Founder can complete all five workflows** defined in the CEO Workspace Product Specification without encountering a "Coming soon" placeholder on any active module.

---

## Review Checklist

- [x] Owner Approval (2026-08-01)
- [ ] Technical Review
- [ ] Security Review
- [ ] Architecture Review
- [ ] Git Commit
- [ ] GitHub Push
- [ ] Project Log Updated
