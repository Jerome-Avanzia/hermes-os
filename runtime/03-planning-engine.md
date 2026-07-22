# Planning Engine Specification

**Component:** Planning Engine\
**Status:** Draft v1.0

## 1. Purpose

The Planning Engine converts an Execution Context (`runtime/02-context-engine.md` §6) into an Execution Plan. It decides **what** should be done to satisfy a request — it does not decide how good an option is relative to others (that remains the Decision Engine and Scoring Model, `docs/12`/`docs/13`), and it does not execute anything (that remains the Agent Orchestrator, `docs/15-agent-orchestrator.md`).

It sits directly between the two: the Context Engine builds context, the Planning Engine turns that context into a plan, and the Agent Orchestrator carries the plan out. The Planning Engine never calls a capability and never observes execution results.

```text
Execution Context (runtime/02-context-engine.md)
        ↓
Planning Engine (this document)
        ↓
Execution Plan
        ↓
Agent Orchestrator (docs/15-agent-orchestrator.md)
```

This is an MVP specification. It defines a minimal, linear planning pipeline producing a simple ordered plan — not a workflow engine, not a DAG scheduler, not a distributed execution planner. A plan is a flat, sequential list of steps; nothing in this specification assumes steps can branch, run in parallel, or be scheduled across multiple runtimes. Those concerns are explicitly deferred, not solved here in a simplified form.

## 2. Responsibilities

- Analyze the task described in an Execution Context and identify its objective.
- Determine which capabilities and tools, already resolved in that Execution Context, are required to meet the objective.
- Build an ordered sequence of steps that accomplishes the objective using those capabilities and tools.
- Respect the constraints and authority level already resolved in the Execution Context (`constraints`, `policies`) when selecting capabilities and building steps — the Planning Engine does not re-derive or override them.
- Validate the resulting plan for structural completeness before returning it.

Out of scope:

- Executing any step or invoking any capability — the Agent Orchestrator's role.
- Retrying, monitoring, or reacting to execution outcomes — also the Agent Orchestrator's role.
- Ranking competing business options or recommending strategy — the Decision Engine's role.
- Building or fetching context — the Context Engine's role; the Planning Engine only consumes an Execution Context already built.
- Enforcing authority limits or triggering escalation — the Planning Engine reads the authority level already resolved in `policies` and plans within it, but escalating when a plan exceeds that level is the caller's (Hermes Agent's) responsibility, not the Planning Engine's.

## 3. Inputs

**Execution Context** is the Planning Engine's only input, produced by `runtime/02-context-engine.md`. The Planning Engine reads from it but never rebuilds, refreshes, or queries providers directly — if context is missing or stale, the caller must obtain a new one from the Context Engine first.

The Planning Engine specifically uses:

| Execution Context Section | Used For |
|---|---|
| `request` | The task objective and requester intent. |
| `business` | Grounding the objective in active Goals, KPIs, or Bottlenecks, when relevant. |
| `capabilities` | The pool of agents, shared services, extensions, and tools available to plan against. |
| `constraints` | Limits (budget, deadline, scope) the plan must respect. |
| `policies` | The authority level the resulting plan must stay within. |
| `status` | Whether the context is usable at all (§8 Invalid context). |

Sections not listed (`memory`, `skills`, `runtime_state`, `provenance`) are not required by planning itself; a strategy (§9) may use them, but the base pipeline does not depend on them.

If an optional section the base pipeline does use is empty — for example, `constraints` — planning proceeds without applying that limit; an empty section is a valid input, not an error. Only the conditions in §8 (Invalid context) stop planning outright.

## 4. Outputs

**Execution Plan** is the Planning Engine's only output, defined in full in §6. A single call to `create_plan()` produces exactly one Execution Plan or a Planning Error (§8) — never a partial plan.

The Planning Engine does not persist the Execution Plan it returns. Storing, versioning, or handing the plan to the Agent Orchestrator for execution is the caller's responsibility — the same way `runtime/02-context-engine.md` builds an Execution Context but does not decide what happens to it afterward.

## 5. Planning Process

**Analyze task** — Extract a plain description of the task from `request` and, where relevant, the linked Business Context (active Goal, KPI, or Bottleneck the request relates to).

**Identify objective** — Reduce the analyzed task to a single, explicit objective statement. This objective is what every later stage plans against; if no clear objective can be extracted, planning stops here (§8 Planning failure).

**Select required capabilities** — From `capabilities.agents`, `capabilities.shared_services`, and `capabilities.extensions` in the Execution Context, choose the minimal subset needed to meet the objective. Only capabilities already marked available (`runtime/01-capability-registry.md` §7.4) are eligible. When more than one available capability could satisfy the same part of the objective, the MVP pipeline selects the first eligible match rather than scoring or comparing options — comparative ranking belongs to the Decision Engine, not this stage.

**Choose tools** — From `capabilities.tools`, choose the minimal subset the selected capabilities need to act. As with capabilities, only available tools are eligible, and the same first-eligible-match rule applies.

**Build ordered steps** — Produce a single, sequential list of steps that carries out the objective using the selected capabilities and tools. Each step names exactly one capability or tool and what that step is for. Steps are strictly ordered top to bottom — no parallel branches, no conditional paths, no dependency graph. A step should correspond to one capability invocation; splitting a single invocation into multiple steps, or merging several invocations into one step, is not part of this stage.

**Validate plan** — Check the assembled plan against these structural rules before it is returned:

- `objective` is non-empty.
- `steps` contains at least one entry.
- Every step references exactly one `capability_id` or `tool_id`.
- Every referenced `capability_id`/`tool_id` appears in `required_capabilities`/`required_tools` and was present and available in the Execution Context's `capabilities`.
- `plan_id` and `task_id` are set.

A plan that fails any rule is not returned as a partial result — see §8.

## 6. Execution Plan

Deliberately minimal — enough for the Agent Orchestrator to execute, nothing more.

| Field | Description |
|---|---|
| `plan_id` | Unique identifier for this plan. |
| `task_id` | Identifier of the request/task this plan fulfills, linking back to the originating Execution Context. |
| `objective` | The single objective statement identified in §5. |
| `steps` | Ordered list of steps; each step has a `step_id`, a short `description`, and a reference to one `capability_id` or `tool_id`. |
| `required_capabilities` | The full list of `capability_id`s used across all steps. |
| `required_tools` | The full list of tool `capability_id`s used across all steps. |
| `status` | `draft`, `validated`, or `invalid` (§8). |
| `created_at` | Timestamp the plan was built. |

No priority scoring, no cost/effort estimate, no branching, and no retry policy are part of the Execution Plan at this stage. Those remain candidates for later strategies (§9), not part of the MVP structure.

### 6.1 Step fields

Each entry in `steps` is itself minimal:

| Field | Description |
|---|---|
| `step_id` | Unique identifier for this step within the plan. |
| `description` | Short, human-readable statement of what the step does. |
| `capability_id` or `tool_id` | Exactly one reference — a step never spans more than one capability or tool. |
| `sequence` | The step's position in the ordered list. |

### 6.2 Illustrative example

| Field | Example Value |
|---|---|
| `plan_id` | `plan_2026-07-22-001` |
| `task_id` | `task_9f21` |
| `objective` | "Publish the weekly KPI summary for AKosmicAnimals." |
| `steps` | Step `s1` (sequence 1, `svc.knowledge-base`): "Retrieve current KPI values." Step `s2` (sequence 2, `agent.executive-brief-generator`): "Generate summary." |
| `required_capabilities` | `[svc.knowledge-base, agent.executive-brief-generator]` |
| `required_tools` | `[]` |
| `status` | `validated` |

Illustrative only — it does not assert these capability ids are implemented.

## 7. Public API

Conceptual only — no implementation code.

| Operation | Description |
|---|---|
| `create_plan(execution_context)` | Runs the full pipeline (§5) and returns an Execution Plan, or a Planning Error (§8). |
| `validate_plan(plan)` | Re-runs the Validate-plan checks against an existing Execution Plan, independent of `create_plan()`. |
| `estimate_complexity(plan)` | Returns a coarse indicator (e.g. step count, capability count) a caller can use to decide how to handle the plan — for example, whether it should be escalated under `docs/20-executive-model.md` §6 authority limits. Not a cost or duration estimate. |

`create_plan()` is the only entry point most callers need; `validate_plan()` and `estimate_complexity()` exist so a plan can be re-checked or assessed without re-running planning from scratch. Given the same Execution Context, `create_plan()` produces an equivalent plan on every call — only `plan_id` and `created_at` differ — which makes planning safe to retry and straightforward to test.

On failure, all three operations return a Planning Error (§8) rather than an empty or default result; none of them return a plan with `status: invalid` as a normal response.

## 8. Error Handling

**Missing capability** — The identified objective requires a capability that is absent, or present but unavailable, in the Execution Context's `capabilities`. Planning fails; the error names the missing capability. No partial plan is returned.

**Impossible task** — No combination of available capabilities and tools in the Execution Context can satisfy the objective at all, as distinct from one specific capability being missing. Planning fails with an error marking the task impossible under the current Execution Context; resolving it is an escalation, not a retry (`docs/20-executive-model.md` §7).

**Invalid context** — The Execution Context has `status: failed`, or is missing a section the Planning Engine requires (§3). The Planning Engine refuses to plan and returns an error immediately, without attempting analysis. It does not guess around a broken context.

**Planning failure** — Any other failure at any stage in §5 — for example, Identify objective producing no usable objective, or Validate plan finding an incomplete result. The plan's `status` is never set to `validated` in this case; the error identifies which stage failed and why.

| Error | Detected During | Response |
|---|---|---|
| Missing capability | Select required capabilities / Choose tools | Fail; name the missing capability. |
| Impossible task | Identify objective / Select required capabilities | Fail; flag for escalation, not retry. |
| Invalid context | Before Analyze task begins | Fail immediately; no analysis attempted. |
| Planning failure | Any stage | Fail; identify the failing stage. |

In every case, `create_plan()` returns an error instead of an Execution Plan. The Planning Engine never returns a plan with `status: invalid` as if it were usable — an invalid or failed plan is an error condition, not a lesser-quality result.

Every error identifies which of the four cases above applies, which planning stage (§5) it occurred in, and — for Missing capability and Impossible task — the specific capability or objective that could not be satisfied. A caller should never need to re-derive why planning failed from a generic message.

## 9. Extensibility

New planning strategies — different approaches to selecting capabilities or ordering steps — are added behind `create_plan()`'s existing interface, not by changing it. `create_plan()`, `validate_plan()`, `estimate_complexity()`, and the Execution Plan structure (§6) stay stable; the logic that fills in §5's stages is what a strategy replaces.

This mirrors the pluggable-provider pattern in `runtime/01-capability-registry.md` §9 and `runtime/02-context-engine.md` §10: the interface is stable, what runs behind it is swappable.

If a future strategy needs richer plans — parallel steps, conditional branches, retries — that extends the `steps` field and `status` enum in §6. It does not change `create_plan()`'s inputs or outputs. Introducing that richness is explicitly out of scope for this specification; the MVP plan stays a flat, ordered list.

| Stable (interface) | Swappable (strategy) |
|---|---|
| `create_plan()`, `validate_plan()`, `estimate_complexity()` signatures | How Select required capabilities / Choose tools picks among eligible options |
| Execution Plan's required fields (§6) | How Build ordered steps sequences steps |
| The single-Execution-Context input contract (§3) | Which parts of the Execution Context a strategy reads beyond the base set |

A strategy change is a configuration or implementation swap behind this interface, not a specification change.

## Used By

- Hermes Agent (`docs/24-hermes-agent-integration.md`)
- Agent Orchestrator (`docs/15-agent-orchestrator.md`), which executes the Execution Plan this engine produces
