# Context Engine Specification

**Component:** Context Engine\
**Status:** Draft v1.0

## 1. Purpose

The Context Engine assembles all information Hermes Agent ("Hermes Executive," per `docs/20-executive-model.md` §3) requires before planning or executing any task. It is the single entry point for runtime context construction — nothing else in Hermes OS builds an Execution Context.

The Context Engine does not execute work, make decisions, or invoke capabilities. It only builds a complete, validated Execution Context and hands it to the caller. It is the concrete implementation behind the first steps of `docs/24-hermes-agent-integration.md` §4 ("load governance," "load business context," "discover available agents / shared services / extensions") and the primary input the Decision Engine (`docs/12-decision-engine-specification.md`) and Agent Orchestrator (`docs/15-agent-orchestrator.md`) consume before acting.

## 2. Responsibilities

- Collect every input required to plan or execute a given task (§4).
- Validate that each collected input is present and structurally correct.
- Enrich raw inputs with derived state (KPI status, capability availability, relevant memory).
- Resolve conflicts and prioritize candidates so the resulting context is directly usable, not just raw data.
- Assemble the result into a single, immutable Execution Context (§6).

Out of scope:

- Deciding what to do with the context — that is the Decision Engine's role.
- Invoking any capability or executing any agent — that is the Agent Orchestrator's role.
- Persisting business state, or writing to Organizational Memory — the Context Engine only reads.
- Approving, registering, or governing capabilities, agents, or extensions — that remains `docs/21`, `docs/22`, `docs/23`.

The Context Engine consumes the outputs of those processes; it does not perform them.

## 3. Architecture

```text
User Request
     ↓
Context Engine
     ├─→ Business Context Provider
     ├─→ Organizational Memory Provider
     ├─→ Capability Registry Provider
     ├─→ Skill Registry Provider
     ├─→ Policy Provider
     └─→ Runtime State Provider
     ↓
Execution Context
     ↓
Agent Orchestrator / Decision Engine
```

| Component | Responsibility |
|---|---|
| Context Providers | One per input source (§4). Each exposes a query interface and returns raw data or a Collection Error. |
| Collector | Calls the providers relevant to the request (§5 Collect). |
| Validator | Checks structural correctness and required-input presence (§5 Validate). |
| Enricher | Derives computed state and attaches relevant subsets of memory (§5 Enrich). |
| Prioritizer | Ranks candidates and resolves conflicts (§5 Prioritize). |
| Context Builder | Assembles the final Execution Context object (§5 Build). |

Providers are pluggable (§10) — the Context Engine depends only on a provider's query interface, never on how or where that provider stores its data. Providers do not call one another; any data one input depends on from another (for example, KPI status depending on Business Context) is resolved in the Enrich stage, not inside a provider. This keeps provider ordering irrelevant and avoids provider-to-provider circular dependencies.

Each call to `build_context()` is independent: the Collector, Validator, Enricher, Prioritizer, and Context Builder hold no state across requests. Two concurrent builds for different requests never share mutable state; only the caches described in §9 are shared, and those are read-only from the pipeline's perspective.

## 4. Inputs

| Input | Description | Primary Source |
|---|---|---|
| User Request | The task or goal being submitted, with any priority hints and the requester's identity. | Caller (Owner, scheduled trigger, or event per `docs/15-agent-orchestrator.md` §Trigger Types). |
| Business Context | The relevant Business plus its active Strategy, Goal, KPI, Bottleneck, and Opportunity objects. | `specs/business.md` and related contracts. |
| Organizational Memory | Decisions and Lessons relevant to the request. | `docs/22-shared-services.md` §4 Organizational Memory; `docs/24` §6. |
| Capability Registry | The full index of registered, available capabilities. | `runtime/01-capability-registry.md`. |
| Runtime Agent Registry | The `category: agent` subset of the Capability Registry, filtered to currently available agents. | Capability Registry §7, scoped by `docs/21-agent-registry.md` approvals. |
| Skill Registry | Reusable skill and task definitions available to agents. | `docs/20-executive-model.md` §4 / `docs/22-shared-services.md` §4 Skills Registry. |
| Tool Registry | The `category: tool` subset of the Capability Registry. | Capability Registry §4.2. |
| Policies | Applicable authority limits and escalation rules for this request. | `docs/20-executive-model.md` §6–7; `docs/00-operating-model.md` §10. |
| Constraints | Task-specific limits — budget, deadline, scope — supplied with the request or derived from Business Context. | User Request; active Goal/Strategy targets. |
| Runtime State | Current execution state: in-flight orchestrator runs, capability availability snapshot, recent execution logs. | `docs/15-agent-orchestrator.md`; Capability Registry §7.4. |

Not every input is required for every request — the Collect stage (§5) requests only what a given request needs.

| Always Collected | Collected On Demand |
|---|---|
| User Request | Organizational Memory |
| Business Context (unless business-agnostic, §8) | Runtime Agent Registry |
| Capability Registry | Skill Registry |
| Policies | Tool Registry |
| | Constraints |
| | Runtime State |

On-demand inputs are collected when the request's declared inputs/outputs, or its explicit options, call for them.

## 5. Context Assembly Pipeline

**Collect** — The Context Engine queries each relevant Context Provider. Providers may be queried in parallel; each returns its raw data or a Collection Error scoped to that provider. A provider failure does not stop collection from the others.

**Validate** — Each collected input is checked for structural correctness against its contract (business objects against `specs/`/`contracts/`, capabilities against `runtime/01-capability-registry.md` §6) and for presence of the inputs this specification treats as required (§8). Validation here is structural only — semantic business-rule evaluation belongs to the Decision Engine, not the Context Engine.

**Enrich** — Derived fields are computed and attached: KPI status rollups, active Bottleneck/Opportunity summaries, resolved capability availability (Capability Registry §7.4), and the subset of Organizational Memory relevant to this specific request rather than the full memory store.

**Prioritize** — Candidate capabilities, agents, tools, and skills are ranked against the request's declared inputs/outputs (via the Capability Registry's `find_by_input`/`find_by_output`). Conflicting policies are resolved using the authority precedence order in `docs/00-operating-model.md` §2; Runtime State and Memory are trimmed to what is relevant so the context stays bounded rather than exhaustive.

**Build Execution Context** — The validated, enriched, prioritized data is assembled into the single object defined in §6, tagged with a provenance record of what was collected, skipped, or degraded, and handed to the caller. The Context Engine's responsibility ends at handoff.

The pipeline runs strictly in this order — Collect, Validate, Enrich, Prioritize, Build — for a given request; only within Collect do providers run concurrently. Given the same inputs from its providers, `build_context()` produces an Execution Context with equivalent content on every run; only `provenance` timestamps differ. This makes a build reproducible for debugging and safe to retry.

## 6. Execution Context Structure

| Section | Contents |
|---|---|
| `request` | Normalized User Request: goal, priority, requester, timestamp. |
| `business` | Business record, active Strategy, Goals, KPIs, Bottlenecks, and Opportunities for the request's business. |
| `memory` | Decisions and Lessons relevant to the request, each tagged with why it was included (source object, relation). |
| `capabilities` | Resolved candidates, grouped as `capabilities.agents`, `capabilities.shared_services`, `capabilities.extensions`, and `capabilities.tools` — each entry carries its manifest (`runtime/01-capability-registry.md` §4.1) and current availability. |
| `skills` | Skill Registry entries matched to the request, when the Skill Registry was collected. |
| `policies` | Applicable policy set, the authority level determined for the request (`docs/20-executive-model.md` §6), and any escalation flags raised. |
| `constraints` | Effective constraints, merged from the request, Business Context targets, and applicable policy, with the source of each constraint recorded. |
| `runtime_state` | Snapshot of in-flight orchestrator runs and capability availability at assembly time. |
| `provenance` | What was collected, skipped, or degraded; source versions; assembly timestamp. |
| `status` | Assembly outcome: `complete`, `partial`, or `failed` (§8). |

Each section is independently optional except `request`, `business` (unless business-agnostic), `capabilities`, and `policies`, mirroring the always-collected inputs in §4. A section that was not collected is present but empty, never omitted — consumers can rely on the Execution Context always exposing the same top-level shape.

The Execution Context is immutable once built. A request for updated context produces a new Execution Context rather than mutating the existing one.

## 7. Public API

Conceptual only — no implementation signatures.

| Operation | Description |
|---|---|
| `build_context(request)` | Runs the full pipeline (§5) and returns an Execution Context. |
| `load_business(business_id)` | Collect-stage helper: retrieves Business Context for a given business. |
| `load_memory(business_id, scope)` | Collect-stage helper: retrieves relevant Organizational Memory. |
| `load_capabilities(filter)` | Collect-stage helper: queries the Capability Registry (agents, tools, services, extensions). |
| `load_policies(context)` | Collect-stage helper: retrieves applicable policies for a partially assembled context. |
| `validate(context)` | Runs Validate-stage checks against an assembled or partially assembled context. |
| `refresh_context(context, sections)` | Re-runs Collect through Build for the given sections only, returning a new Execution Context (§9 Incremental refresh). |
| `get_status(request_id)` | Returns assembly progress or outcome for a given build. |

`build_context()` is the only entry point callers are expected to use directly; the `load_*`, `validate()`, and `refresh_context()` operations exist so providers and pipeline stages can be invoked independently for testing or partial refresh.

## 8. Error Handling

The Context Engine distinguishes inputs that **block** assembly from inputs that **degrade** it. Only the former stop `build_context()`. Every error, blocking or not, is recorded with the same shape: the input it came from, its severity (`blocking` or `degraded`), and a human-readable message — surfaced in `provenance` and returned directly for blocking errors.

| Error Type | Raised During | Scope |
|---|---|---|
| Collection Error | Collect | A single provider; other providers continue. |
| Validation Error | Validate | A single input section. |
| Conflict Error | Prioritize | Two or more inputs (e.g. policies) that cannot both hold. |
| Assembly Error | Build | The whole request; only raised when a blocking condition below applies. |

**Missing business** — A business-scoped request whose `business_id` cannot be resolved blocks assembly; `build_context()` returns a Context Assembly Error. A request that is explicitly business-agnostic (a platform-level task) leaves the `business` section empty by design — this is not an error.

**Missing capability** — A specifically requested `capability_id` that is not found in the Capability Registry is recorded in `provenance` as a warning. Assembly continues unless the request explicitly marks that capability as mandatory, in which case it blocks assembly.

**Conflicting policies** — Two applicable policies that contradict at the same authority level are not silently resolved by guessing. Assembly halts with a Policy Conflict error requiring escalation per `docs/20-executive-model.md` §7, rather than picking a winner.

**Unavailable tools** — A resolved tool capability marked unavailable (Capability Registry §7.4) is still included in `capabilities` with `available: false`. This does not block assembly — unavailability is handled by the Agent Orchestrator at execution time, not by the Context Engine at build time.

**Incomplete context** — Any optional input that fails to collect or validate leaves its section empty or partial, sets `status: partial`, and is recorded in `provenance`. The engine never reports `complete` for a context that has a known gap.

| `status` | Meaning |
|---|---|
| `complete` | Every input relevant to the request was collected and validated. |
| `partial` | One or more optional inputs are missing or degraded; blocking errors did not occur. |
| `failed` | A blocking error (missing business, unresolved policy conflict, mandatory capability missing) prevented assembly. |

## 9. Performance Considerations

**Caching** — Business Context, Capability/Agent/Tool Registry snapshots, and the active Policy set are cached rather than re-fetched per request. Cache invalidation is tied to source events, not a fixed request-count or timer:

| Cached Item | Invalidated By |
|---|---|
| Capability/Agent/Tool Registry snapshot | Capability Registry re-discovery (`runtime/01-capability-registry.md` §5.3). |
| Business Context | A write to the underlying Business, Strategy, Goal, KPI, Bottleneck, or Opportunity record. |
| Policy set | A change to authority or escalation rules (`docs/20-executive-model.md` §6–7). |
| Organizational Memory subset | Not cached — always collected scoped to the request (§4). |

**Lazy loading** — Only the input sections a given request actually needs are collected; for example, a request with no skill-matching need skips the Skill Registry provider. Large inputs such as Organizational Memory are always loaded pre-filtered to a relevant scope, never in full. Provider fan-out during Collect is bounded — a request never triggers an unbounded number of concurrent provider calls regardless of how many optional inputs it declares.

**Incremental refresh** — For long-running or repeated tasks (a daily review, a monitored initiative), the Context Engine may refresh only the sections likely to have changed — Runtime State, KPI snapshot — instead of rebuilding the entire Execution Context via `refresh_context()` (§7). Refresh operates at the provider level, re-running Collect through Build only for the requested sections; it is not all-or-nothing, and it does not mutate the prior Execution Context (§6).

## 10. Extensibility

New Context Providers can be added by implementing the provider query interface (§3) without modifying the Collect, Validate, Enrich, or Prioritize stages — the same pluggable-source principle used in `runtime/01-capability-registry.md` §9.

New input categories can be introduced in §4 without changing how existing Execution Context sections (§6) are structured, as long as the addition is additive.

Prioritization and conflict-resolution logic (policy precedence, capability ranking) should be configurable per business, consistent with the configurable-weighting principle in `docs/13-scoring-model.md`, without requiring changes to the pipeline stages themselves.

The Context Engine remains provider-agnostic throughout: it knows how to query a provider's interface, never how or where that provider stores its underlying data.

| Extension Point | Mechanism |
|---|---|
| New input source | Implement the Context Provider interface (§3); register it against an input name (§4). |
| New Execution Context section | Add a section to §6 additively; existing consumers reading only known sections are unaffected. |
| New prioritization or conflict-resolution strategy | Configure per business, without changing the Prioritize stage's implementation. |
| New validation rule set | Version the rule set per input type, mirroring `runtime/01-capability-registry.md` §9. |

None of these require a change to the Collector, Validator, Enricher, Prioritizer, or Context Builder themselves — each is closed to modification and open to configuration.

## Used By

- Hermes Agent (`docs/24-hermes-agent-integration.md`)
- Agent Orchestrator (`docs/15-agent-orchestrator.md`)
- Decision Engine (`docs/12-decision-engine-specification.md`)
