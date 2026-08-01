## ADR-0004 — Operation as the Unit of Business Execution

**Status:** Draft (D1)

**Date:** 2026-08-01

---

### Context

The Hermes OS repository uses two different terms to describe the same unnamed concept:

- **"Initiative"** appears in docs/20 §3 ("Prioritizes initiatives"), docs/24 §4 ("For each initiative Hermes Agent shall…" followed by a 10-step lifecycle), docs/25 Sprint 5 ("One initiative runs the full 10-step loop"), and DEC-0003 Implementation §2 ("beneath its existing 10-step initiative lifecycle"). The term is used consistently across five governing documents but is never formally defined.

- **"Operations"** appears in the canonical Workspace shell (ADR-0001) as a primary navigation module, distinct from Chat, Jobs, Conductor, and Swarm. The Pilot reference image (docs/ui/hermes-workspace-v0.19.0-pilot.png) confirms the same structure.

Neither term has a specification, contract, or dataclass. No document maps the two terms together. No document explains their relationship or establishes which is canonical.

Meanwhile, the current execution model revolves around Task — an anonymous 3-field input tuple (`id`, `business`, `request`) with no lifecycle, no persistence, no identity, and no tracking. Task is consumed by the execution pipeline and discarded. Nothing in the current implementation corresponds to what docs/24 §4 describes: a unit of work that loads governance, builds context, delegates, monitors, escalates, and records outcomes.

This creates an architectural gap. The governance layer (docs/20, docs/24) assumes a lifecycle-bound unit of executive work exists. The implementation layer provides only fire-and-forget execution. The Workspace shell reserves space for it. Nothing connects these layers.

The Operation Recovery analysis (Sprint 8) examined four hypotheses and determined, from repository evidence:

- Operation is **not** a replacement for Task (Task is an input; Operation is a lifecycle).
- Operation is **not** a UI grouping only (the "initiative" concept appears in five governance documents with consistent domain meaning).
- Operation is a **domain concept** at the execution layer, distinct from the 10 canonical business objects at the strategic layer.
- Operation is **distinct from Job** (the Workspace shell shows both as separate modules).

Three Founder decisions were required. This ADR records them.

---

### Decision

Hermes OS adopts **Operation** as the canonical term for the unit of business execution described as "initiative" in docs/20, docs/24, docs/25, and DEC-0003.

#### 1. What is an Operation?

An Operation is a **tracked, lifecycle-bound unit of work** that Hermes Agent manages within a Workspace. It is the concrete realization of the 10-step loop defined in docs/24 §4.

An Operation is not a Task. A Task is an anonymous input to the execution pipeline. An Operation is the governed lifecycle that may contain one or more Tasks.

An Operation is not a Conversation. A Conversation is a stateless exchange between a user and Hermes. A Conversation may lead to the creation of an Operation, but conversations exist independently.

An Operation is not a Job. A Job is a discrete execution run within an Operation. An Operation may produce multiple Jobs as it progresses through its lifecycle.

An Operation is not a Business, Strategy, Goal, or any other canonical business object. Those objects define *what* the business is trying to achieve (strategic layer). An Operation defines *how* that achievement is being pursued (execution layer).

#### 2. Why "Operation" is the canonical product term

"Initiative" describes the concept accurately but is ambiguous in a product context — it could refer to a strategic initiative, a project, or an organizational program. "Operation" is the term already present in the canonical Workspace shell (ADR-0001) and carries the correct connotation: a bounded unit of execution with a beginning, a progression, and a completion. It aligns with the product surface the Founder has already approved.

All future documents, specifications, and implementations shall use "Operation" where they would previously have used "initiative" in the context of Hermes Agent's execution lifecycle. Existing documents (docs/20, docs/24, docs/25, DEC-0003) are not retroactively rewritten — the equivalence is established by this ADR.

#### 3. Relationships

**Operation → Workspace:** An Operation belongs to exactly one Workspace. It executes within that Workspace's scope, governed by that Workspace's identity, organization, and knowledge. An Operation never spans Workspaces (consistent with DEC-0002 §5: strict context isolation between ventures).

**Operation → Conversation:** A Conversation may produce an Operation when work is identified and the user confirms execution. This is a **promotion** — a deliberate, human-authorized transition from dialogue to action. Conversations and Operations are otherwise independent. Not every Conversation produces an Operation. Not every Operation originates from a Conversation.

**Operation → Task:** A Task is the input that enters the execution pipeline when an Operation step requires it. An Operation may create one or more Tasks during its lifecycle. Task remains the anonymous, stateless input to the kernel. Operation provides the lifecycle and identity that Task lacks.

**Operation → Job:** A Job is a single execution run — one pass through the kernel pipeline (context assembly → planning → execution → result). An Operation may produce multiple Jobs as it progresses. A Job belongs to exactly one Operation. The Workspace shell's separation of Operations and Jobs reflects this: Operations is the strategic view (what is being done and why); Jobs is the execution view (what ran, when, and what it produced).

**Operation → Knowledge:** An Operation loads Knowledge at the start of its lifecycle (docs/24 §4 step 2: "Load business context"). An Operation may also produce Knowledge as an outcome (step 10: "Record outcomes"). Knowledge Documents consumed by and produced by an Operation are referenced, not owned — Knowledge belongs to the Knowledge Space (DEC-0004), not to the Operation.

**Operation → Artifact:** An Operation may produce artifacts — generated output, committed code, drafted documents. Artifacts are the tangible products of execution. An Operation references its artifacts; it does not replace their domain-specific governance (a generated document still follows docs/00 §9 maturity lifecycle; generated code still follows engineering standards).

**Operation → Decision:** An Operation may produce Decisions at escalation points (docs/24 §4 step 9: "Escalate when required"). Decisions produced during an Operation are governed by DEC-0002 §1 — L1/L2 decisions are Hermes Agent's; L3 decisions require Founder approval. The Decision records the rationale; the Operation records that a decision point was reached.

#### 4. Architectural responsibilities of an Operation

An Operation is responsible for:

- **Identity.** An Operation has a unique, stable identifier that persists beyond the execution that produced it.
- **Lifecycle tracking.** An Operation records which steps of the docs/24 §4 loop have been completed and which remain.
- **Scope containment.** An Operation is bound to one Workspace. All context loaded, work delegated, and outcomes recorded stay within that Workspace's boundary.
- **Outcome recording.** Per docs/24 §4 step 10 and docs/00 §7: an Operation records what happened, not just what was requested. This is how "Project Log Updated" (docs/00 §11) is satisfied for execution work.
- **Escalation tracking.** An Operation records when and why work was escalated to the Founder, and what the Founder decided. This is the audit trail required by DEC-0002's Delegation Principle.
- **Job coordination.** An Operation tracks which Jobs have been produced during its lifecycle and their outcomes. It does not execute Jobs — the kernel does that. The Operation is the lifecycle; the kernel is the engine.

#### 5. Responsibilities that do NOT belong to an Operation

- **Reasoning.** An Operation does not reason. Providers reason. The Operation provides context and records outcomes.
- **Context assembly.** The Context Engine assembles context. An Operation triggers context assembly but does not perform it.
- **Plan execution.** The Executor executes plans. An Operation may create the conditions for execution but does not run steps itself.
- **Knowledge storage.** Knowledge belongs to the Knowledge Space (DEC-0004). An Operation references knowledge, not stores it.
- **Strategic direction.** An Operation does not set goals, define strategy, or evaluate KPIs. Those responsibilities belong to the canonical business objects (docs/11). An Operation is the execution of work toward those strategic objects, not the definition of the work itself.
- **Governance.** An Operation operates within governance. It does not define, modify, or override governance. Changes to the Operating Model, ADRs, or standards are governance activities, not Operation responsibilities.
- **Conversation state.** An Operation does not manage chat history, message threading, or conversation memory. Conversations are independent. The promotion bridge (Conversation → Operation) is a one-time transition, not an ongoing coupling.

#### 6. Consequences

**Positive:**

- Resolves the naming ambiguity between "initiative" (governance term) and "Operations" (product term) — one concept, one name.
- Establishes the missing bridge between the governance lifecycle (docs/00 §7, docs/24 §4) and the execution layer (Task → ExecutionResult). The governance layer can now reference a concrete architectural concept.
- Provides the domain anchor for the Workspace shell's Operations module — it is not a placeholder waiting for meaning; it is the surface for a defined domain concept.
- Clarifies that Operation and Job are distinct concepts at different levels of abstraction, consistent with the canonical shell's separation.
- Positions Operation as a participant in Entity Lifecycle Management (DEC-0005) without requiring any change to DEC-0005 itself — Operation carries a lifecycle state, transitions, and an append-only log, conforming to the generic primitive.

**Trade-offs:**

- Introduces a new named domain concept that future specifications, contracts, and implementations must reference. This is an explicit cost, accepted because the alternative — continuing to use "initiative" informally while the shell says "Operations" and the kernel has neither — is worse.
- The exact lifecycle states for an Operation are not defined by this ADR. This ADR establishes what an Operation *is* and what it *relates to*. The lifecycle states are a specification concern (a future `specs/operation.md`), not an architectural decision.
- This ADR does not define how an Operation is persisted, represented, or surfaced. Those are implementation concerns governed by existing principles (Principle 3: filesystem is the registry; Principle 6: one source of truth) and future specifications.

---

### References

- ADR-0001 — Canonical Hermes Workspace Shell
- DEC-0002 — Hermes Executive Operating Model (§1 Decisions, §5 Multiple Ventures, Delegation Principle)
- DEC-0003 — Execution Context & Git Safety Policy (Implementation §2: "initiative lifecycle")
- DEC-0004 — Business Knowledge System & Knowledge Space Model
- DEC-0005 — Entity Lifecycle Management as a Shared Service
- docs/00 — Operating Model (§7 Development Lifecycle, §11 Definition of Done)
- docs/20 — Executive Model (§3: "Prioritizes initiatives")
- docs/24 — Hermes Agent Integration (§4: 10-step initiative loop)
- docs/25 — V1 Implementation Roadmap (Sprint 5: initiative success criterion)
- HERMES_PRINCIPLES.md (Principle 3: filesystem is the registry; Principle 7: execution validates architecture)
- HERMES_V1_ARCHITECTURE.md (§3: five kernel components)
- docs/ui/hermes-workspace-v0.19.0-pilot.png (Operations as primary module)
- Operation Recovery analysis (Sprint 8)

---

### Review Checklist

- [ ] Technical Review
- [ ] Security Review
- [ ] Architecture Review
- [ ] Owner Approval
- [ ] Git Commit
- [ ] GitHub Push
- [ ] Project Log Updated
