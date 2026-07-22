# Execution Engine Specification

**Component:** Execution Engine\
**Status:** Draft v1.0

## 1. Purpose

The Execution Engine executes a single, already-validated Execution Plan (`runtime/03-planning-engine.md` §6), sequentially, step by step, and produces a Result. It does not build context and it does not plan — it only coordinates carrying out a plan someone else already decided on.

```text
Execution Plan (runtime/03-planning-engine.md)
        ↓
Execution Engine (this document)
        ↓
Result
        ↓
Agent Orchestrator (docs/15-agent-orchestrator.md) / Hermes Agent
```

This is deliberately narrower than the Agent Orchestrator's full responsibilities in `docs/15-agent-orchestrator.md` (scheduling across multiple agents and triggers, cross-object dependency handling, notifications). The Execution Engine handles exactly one validated Plan at a time; the Agent Orchestrator — or Hermes Agent directly, at MVP scale — invokes it to carry that plan out.

This is an MVP specification: sequential execution only, no parallelism, no workflow engine, no distributed scheduling.

| Related Spec | Relationship |
|---|---|
| `runtime/01-capability-registry.md` | Source of the interface (`protocol`, `entrypoint`) each step invokes. |
| `runtime/02-context-engine.md` | Not consumed directly — the context it built is already embedded in the Plan by the Planning Engine. |
| `runtime/03-planning-engine.md` | Produces the Plan this engine executes. |
| `docs/15-agent-orchestrator.md` | Invokes this engine to carry out one plan at a time. |

## 2. Responsibilities

- Validate that the given Plan is executable before running anything.
- Execute each step in the Plan's `steps`, strictly in the order given, by invoking the capability or tool the step references.
- Collect the output of each step as it completes.
- Detect and handle failures at the step level without corrupting outputs already collected from prior steps.
- Produce a single Result (§6) summarizing what happened, on both success and failure.

Out of scope:

- Building or refreshing Execution Context — the Context Engine's job (`runtime/02-context-engine.md`).
- Deciding what steps to take, or in what order — the Planning Engine's job (`runtime/03-planning-engine.md`).
- Scheduling multiple plans, resolving cross-plan or cross-agent dependencies, or reacting to triggers — the Agent Orchestrator's broader job.
- Retrying failed steps, running steps in parallel, or persisting a Result long-term — explicitly deferred (§9).
- Re-checking authority or policy — the Execution Engine trusts that the Planning Engine already built the Plan within the authority level resolved in its Execution Context (`runtime/03-planning-engine.md` §2). It does not re-evaluate whether the plan should be allowed to run.

## 3. Inputs

**Plan** is the Execution Engine's only input — an Execution Plan produced by `runtime/03-planning-engine.md` §6.

| Plan Field Used | Purpose |
|---|---|
| `plan_id`, `task_id` | Carried through into the Result (§6) for traceability. |
| `status` | Must be `validated`; anything else is rejected before execution begins (§5). |
| `steps` | The ordered work this engine performs. |
| `required_capabilities`, `required_tools` | Cross-checked against each step's reference before it runs. |
| `objective` | Carried through for traceability only — the Execution Engine does not re-interpret or re-evaluate the objective; it trusts the Plan. |

Time may pass between a Plan being built and being executed. The Execution Engine does not re-run planning or re-check the Execution Context that produced the Plan — it only re-checks, at the moment each step runs, whether that step's specific capability or tool is still available (§8 Tool unavailable). It does not assume the whole Plan is still valid just because it once was.

## 4. Outputs

**Result** is the Execution Engine's only output, defined in full in §6. A single call to `execute()` against one Plan produces exactly one Result — never zero, and never more than one.

The Execution Engine does not persist the Result it returns. Storing it, acting on it, or feeding it back into Organizational Memory (`docs/22-shared-services.md` §4) is the caller's responsibility — the same boundary the Context Engine and Planning Engine draw around their own outputs.

## 5. Execution Process

**Validate plan** — Confirm `Plan.status` is `validated` and that the plan is structurally complete (non-empty `objective`, at least one step, every step referencing a capability or tool present in `required_capabilities`/`required_tools`). A plan that fails this check is rejected outright: no step runs, and a Result with `status: rejected` is produced immediately (§6).

**Execute steps in order** — Steps run strictly by `sequence`, one at a time. Each step is carried out by invoking its referenced capability or tool through the interface declared in its manifest (`runtime/01-capability-registry.md` §4.3 — `protocol` and `entrypoint`); the Execution Engine does not know or care what technology sits behind that interface. The next step does not begin until the current one has completed. No step runs concurrently with another, and no step is skipped or reordered.

**Collect outputs** — Each successfully completed step's output is recorded, keyed by `step_id`, and added to the Result's `output` as execution proceeds. Because execution is sequential, outputs are always collected in the same order the steps ran — there is no reordering or merging logic to reconcile.

**Handle failures** — When a step fails (§8), execution stops at that step. Remaining steps do not run. Outputs already collected from prior successful steps are preserved and included in the Result — they are not discarded or rolled back.

**Produce Result** — A Result (§6) is always produced, whether the plan completed fully, stopped on a failure, or was rejected before execution began. The Execution Engine never returns nothing and never leaves a call without a terminal outcome.

Given the same Plan and the same capability behavior at execution time, `execute()` produces an equivalent Result on every run — only `result_id` and `completed_at` differ. Because capability availability and behavior can change between runs (§8), this is a best-effort guarantee, not a strict one: the pipeline itself introduces no nondeterminism, but what it calls out to might.

### 5.1 Pipeline guarantees

| Guarantee | Meaning |
|---|---|
| Ordering | Steps execute in `sequence` order, one at a time, never reordered. |
| Fail-fast | The first failing step stops the plan; no later step runs. |
| Termination | Every call to `execute()` ends in a Result — success, failure, or rejection — never a hang. |
| Preservation | Outputs from completed steps survive a later step's failure. |

## 6. Result

Deliberately minimal — enough for the caller to know what happened and act on it.

| Field | Description |
|---|---|
| `result_id` | Unique identifier for this execution result. |
| `task_id` | Identifier of the originating task, carried from the Plan. |
| `plan_id` | The Plan that was executed. |
| `status` | `success`, `failed`, or `rejected` (§6.1). |
| `output` | Collected outputs from completed steps, keyed by `step_id`. |
| `errors` | List of errors encountered; empty on full success. |
| `completed_at` | Timestamp execution finished, or was rejected. |

### 6.1 Status values

| `status` | Meaning |
|---|---|
| `success` | Every step in the plan completed without error. |
| `failed` | A step failed (§8); `output` holds only the steps that completed before the failure. |
| `rejected` | The plan itself was not `validated` or was structurally incomplete; no step ran, `output` is empty. |

No retry count, duration metrics, or per-step timing are part of the Result at this stage — those remain candidates for later strategies (§9), not part of the MVP structure.

### 6.2 Illustrative example

Executing the example Plan from `runtime/03-planning-engine.md` §6.2, where step `s2` fails:

| Field | Example Value |
|---|---|
| `result_id` | `res_2026-07-22-001` |
| `task_id` | `task_9f21` |
| `plan_id` | `plan_2026-07-22-001` |
| `status` | `failed` |
| `output` | Step `s1` (`svc.knowledge-base`): current KPI values retrieved. Step `s2`: not recorded — it failed. |
| `errors` | One entry: step `s2`, type `step_failure`, message "agent.executive-brief-generator returned an error." |
| `completed_at` | `2026-07-22T09:14:02Z` |

Illustrative only — it does not assert these capability ids are implemented.

## 7. Public API

Conceptual only — no code.

| Operation | Description |
|---|---|
| `execute(plan)` | Runs the full pipeline (§5) against a Plan and returns a Result. |
| `execute_step(plan, step)` | Runs a single step and returns its output or error. Used internally by `execute()`, and exposed so a single step can be re-run or inspected in isolation (e.g. for testing) without executing the whole plan. It returns either the step's output or its error — the same unit `execute()` accumulates internally — but never a full Result on its own; only `finalize()` produces one. |
| `finalize(outputs, errors)` | Assembles the terminal Result from whatever outputs and errors exist at the point execution stops — used by `execute()` on every exit path, success or failure, so there is exactly one place a Result gets built. |

`execute()` is the only entry point most callers need. `execute_step()` and `finalize()` exist so the pipeline has clearly separated, independently testable parts.

`execute()` always returns a Result (§6), never an unhandled error — including when the plan is rejected outright or a step fails. Callers do not need a separate error-handling path distinct from reading `Result.status` and `Result.errors`.

## 8. Error Handling

**Step failure** — The invoked capability or tool returns an error, or an explicitly unsuccessful outcome, for a step. Execution stops at that step; prior outputs are preserved; the Result's `status` is `failed` and `errors` records the failing step and reason.

**Tool unavailable** — A step's referenced capability or tool is unavailable at execution time, even though it was available when the Plan was built (`runtime/01-capability-registry.md` §7.4 availability can change between planning and execution). Handled the same as a step failure — execution stops — but recorded distinctly in `errors` so the caller can tell "the tool broke" from "the tool was never there" or "the tool failed while running."

**Timeout** — A step does not complete within its expected bound. The MVP defines timeout only at the step level, not as a plan-wide budget. A timed-out step is treated as a failed step: execution stops, and `errors` records which step timed out.

**Unexpected exception** — Any failure not anticipated by the three cases above — an internal fault rather than a capability-reported outcome. It is caught at the step boundary so it cannot crash execution without producing a Result. It still stops execution and still produces a Result via `finalize()`, but is tagged distinctly in `errors` so it reads as something to investigate, not an ordinary step failure.

Every case above stops execution at the current step — sequential, fail-fast, no partial-continue past a failure — and always terminates in a Result. Nothing in this engine silently hangs or exits without one.

| Error | Detected By | Result `status` |
|---|---|---|
| Step failure | The invoked capability/tool's own response | `failed` |
| Tool unavailable | A pre-invocation availability check against the Capability Registry | `failed` |
| Timeout | The Execution Engine's per-step time bound | `failed` |
| Unexpected exception | A catch-all at the step boundary | `failed` |

All four map to `status: failed` in the MVP — the distinction between them lives in `errors`, not in a wider set of top-level statuses. Only an invalid plan (§6.1) produces `rejected`.

## 9. Extensibility

Future execution strategies — parallel step execution, asynchronous execution, automatic retries — are added behind `execute()`'s existing interface, not by changing it. `execute()`, `execute_step()`, `finalize()`, and the Result structure (§6) stay stable; what a future strategy changes is the internal logic that decides how steps are run and how failures are handled before `finalize()` is called.

This mirrors the same stable-interface, swappable-strategy pattern used in `runtime/01-capability-registry.md` §9, `runtime/02-context-engine.md` §10, and `runtime/03-planning-engine.md` §9.

| Stable (interface) | Swappable (future strategy) |
|---|---|
| `execute()`, `execute_step()`, `finalize()` signatures | Whether steps run sequentially, in parallel, or asynchronously |
| Result's required fields (§6) | Whether a failed step is retried before being recorded as a failure |
| The single-Plan input contract (§3) | How many plans or steps a strategy handles concurrently |

This specification does not design a parallel, async, or retrying strategy — it only keeps the interface shaped so one can be introduced later without breaking `execute()`'s callers or the Result contract. For example, a future retry strategy would change what happens internally between a Step failure (§8) and the call to `finalize()`; it would not change what `execute()` takes as input or what shape a Result has.

## Used By

- Agent Orchestrator (`docs/15-agent-orchestrator.md`), which invokes the Execution Engine to carry out one validated Plan
- Hermes Agent (`docs/24-hermes-agent-integration.md`)
