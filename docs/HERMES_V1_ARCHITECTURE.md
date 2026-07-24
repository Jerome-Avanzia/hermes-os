# Hermes OS v1 Architecture

**Version:** 1.0 (Draft)
**Status:** Proposed

---

## 1. Mission

Hermes OS is the execution kernel for the AVANZIA ecosystem.

Its purpose is to assemble deterministic context, produce execution plans, and coordinate execution through reusable capabilities.

Hermes is not:

- an LLM
- a chatbot
- a workflow engine
- an autonomous agent swarm

Hermes is an orchestration kernel. Intelligence is a tool it invokes, not a property it embodies.

---

## 2. Design Principles

- Deterministic before autonomous.
- Simplicity before flexibility.
- Explicit over implicit.
- Git is the source of truth.
- Documentation explains why. Code explains how.
- Production stability over feature count.

---

## 3. Core Architecture

Hermes is composed of five kernel components. Each has a single, well-defined responsibility.

### Knowledge

Loads structured knowledge documents for a given project. Knowledge is static, versioned in Git, and read-only at runtime. It grounds context in real business information rather than model assumptions.

### Context

The Context Engine assembles all inputs required before planning can begin. It composes outputs from the Project Resolver, Knowledge Engine, Workspace Engine, and Capability Engine into a single immutable `Context` object. Context assembly is deterministic — given the same inputs, it produces the same output.

### Capability Registry

Maintains the registry of available capabilities and skills. Capabilities are declared in YAML manifests. The registry resolves which capabilities satisfy a given task through keyword matching. No capability executes without being registered.

### Planner

Converts an assembled `Context` into an ordered, flat `ExecutionPlan`. The plan is a linear sequence of steps. There is no branching, conditional logic, or retry in v1. The plan is validated before execution begins. An invalid plan is never executed.

### Executor

Executes each step in the plan in sequence. The executor may invoke an AI provider to generate output for a step. It returns an `ExecutionResult` containing completed steps, status, timestamps, and any generated output.

### Execution Flow

```
Task
  → Resolve Project
  → Load Knowledge
  → Load Workspace
  → Discover Capabilities
  → Build Context
  → Create Plan
  → Execute
  → Result
```

Each stage is discrete. Failure at any stage surfaces a structured error and halts execution.

---

## 4. Kernel Diagram

```
                        Hermes Kernel
+------------------------------------------------------------------+
|                                                                  |
|   Knowledge Engine    Context Engine    Capability Registry      |
|                                                                  |
|   Workspace Engine    Planner           Executor                 |
|                                                                  |
|                    AI Provider (Claude)                          |
|                                                                  |
+------------------------------------------------------------------+

                        HermesService
              (orchestration façade for all kernel components)

+------------------------------------------------------------------+
|         CLI          |      API (future)     |    MCP (future)   |
+------------------------------------------------------------------+
```

`HermesService` is the single entry point for all callers. Kernel components are never invoked directly from outside the service layer.

---

## 5. Scope

### Included in v1

These components are in scope and must be complete before v1 is declared stable.

- **CLI** — primary user interface; all commands route through `HermesService`
- **HermesService** — orchestration façade; composes all kernel components
- **Knowledge Engine** — loads and serves project knowledge documents
- **Workspace Engine** — resolves project workspace, reads files, detects environment
- **Context Engine** — assembles full execution context from all sources
- **Capability Registry** — discovers and serves available capabilities from manifests
- **Planner** — produces validated, ordered execution plans from context
- **Executor** — executes plans and returns structured results
- **Claude Provider** — AI provider implementation for Claude; invoked by executor
- **Skill Registry** — stores and serves reusable skill manifests

### Deferred

The following are intentionally out of scope for v1. They are not rejected — they are deferred to preserve focus and avoid premature complexity.

- **Decision Engine** — capability ranking and scoring
- **Scoring Model** — configurable weighting per business
- **Agent Orchestrator** — multi-agent coordination and delegation
- **Scheduling** — time-based or event-based task triggering
- **API Server** — REST API for external callers
- **Web UI** — browser-based interface
- **Multi-agent collaboration** — agents invoking other agents
- **Extension Framework** — third-party capability plugins

Deferred components may be designed in parallel but must not block v1 delivery.

---

## 6. Repository Responsibilities

Each repository owns one responsibility. No repository duplicates what another owns.

| Repository | Responsibility |
|---|---|
| **hermes-os** | Execution kernel, CLI, specifications, contracts, knowledge base |
| **hermes-infrastructure** | VPS configuration, deployment scripts, container orchestration, Traefik |
| **hermes-prompts** | Prompt library; versioned prompts used by AI providers |
| **hermes-skills** | Shared, reusable skills available across all projects |
| **hermes-agents** | Agent definitions, agent-specific skills, agent runtime configuration |

Business repositories remain independent. They depend on Hermes but Hermes does not depend on them.

---

## 7. Engineering Rules

Engineering rules for this project are defined in `standards/ENGINEERING.md`.

This document does not duplicate them.

All contributors must read and follow that document before making changes to this repository.

---

## 8. Milestones

### Milestone 1 — Architecture Freeze

Define and document the v1 scope. Establish contracts and specifications for all kernel components. No implementation begins on a component without a documented specification.

### Milestone 2 — Capability Registry

Implement the runtime Capability Registry as specified in `runtime/01-capability-registry.md`. Dynamic capability discovery must be operational before the Planner can be validated at scale.

### Milestone 3 — Execution Runtime

Validate the full execution pipeline end-to-end: Context assembly → Planning → Execution → Result. All kernel components must be covered by unit tests. No hardcoded or demo code in the execution path.

### Milestone 4 — Production Readiness

Hermes runs on the VPS with a repeatable deployment process. Deployment is scripted, documented, and verified. Rollback is possible. Logs are observable.

### Milestone 5 — Intelligence Layer

Claude Provider is stable in production. Context passed to the AI provider is accurate, grounded in real business knowledge, and produces useful output. The AI layer is an enhancement to deterministic execution, not a replacement for it.

---

## 9. Success Criteria

Hermes v1 is complete when:

- Deterministic execution is stable and produces consistent results for the same inputs.
- Context assembly is reliable across projects and knowledge bases.
- Capabilities are dynamically discoverable via the registry without hardcoding.
- Execution is fully testable without an active AI provider.
- Production deployment is repeatable from a clean state using documented procedures.

Success is not measured by the number of AI features shipped. It is measured by the reliability and predictability of the execution kernel.
