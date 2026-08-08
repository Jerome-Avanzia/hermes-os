# Hermes Bootstrap — Architecture Record

**Completed:** 2026-08-08
**Final tag:** `v0.6.0-phase7`
**Status:** Frozen

---

## 1. Bootstrap Vision

Bootstrap was a deliberate, sequential engineering programme with one goal: prove that Hermes can autonomously engineer software repositories — reading a goal in plain language, decomposing it into operations, generating code, validating it, repairing failures, and committing the result — without human intervention at any step after the initial command.

Each phase added exactly one capability. Each capability was specified before implementation, implemented against the spec, validated against a production acceptance test on the VPS, and frozen before the next phase began. No capability was added speculatively. Nothing was implemented before its acceptance test passed.

The bootstrap sequence answers a specific question: **can Hermes serve as an autonomous software engineer on a real repository?** Phase 7 answers that question affirmatively, within the limitations documented below.

Bootstrap does not represent the full vision for Hermes. It represents the minimum viable foundation on which that vision can now be built.

---

## 2. Phases 1–7

### Phase 1 — First Autonomous Execution (AT-1 PASS)

Hermes executes its first end-to-end engineering task: generate a new source file, write it to disk, stage it, and commit it. The command is deterministic — the Founder specifies the output path explicitly.

**Acceptance test:** `hermes implement "Add a multiply function" --output multiply.py --repo hermes-calculator`
Expected: `multiply.py` created and committed; 4-step report (LLM → FS → Git add → Git commit).

---

### Phase 2 — Modify Pipeline (AT-2 PASS)

Hermes gains the ability to modify existing files. The modify pipeline differs from create: it reads the current file content first, feeds it to the LLM as context, and uses `overwrite_file` rather than `create_file`.

**Acceptance test:** `hermes implement "Add a subtract function" --output calculator.py --repo hermes-calculator`
Expected: `calculator.py` modified in-place and committed; 5-step report (read → LLM → FS modify → Git add → Git commit).

---

### Phase 3 — Syntax Validation Gate (AT-3 PASS)

A validation step is inserted between file write and `git add` in both pipelines. Invalid Python is blocked before it reaches the repository. The gate uses `py_compile` via `ValidationAdapter`. Valid files pass through; invalid files halt the workflow with `success=False` and no commit is made.

**Acceptance test:** Both create and modify workflows show `validation validate` in the step listing. Invalid generated code is rejected; valid code is committed.

---

### Phase 4 — Test Gate (AT-4 PASS)

A `run_tests` step is inserted after syntax validation. Hermes detects the repository's test command via `RepositoryIntelligence.BuildSystemDetection` and runs the full test suite after every code write. If tests fail, the workflow halts before `git add`. `executed=False` distinguishes a skipped gate (no test command detected) from a passed gate.

**Acceptance test:** Hermes modifies a repository with a pytest suite. The test gate executes; passing tests allow the commit; a deliberately broken implementation halts with `test_failure`.

---

### Phase 5 — Autonomous Target Selection (AT-5 PASS)

Hermes selects the target file itself. The Founder omits `--output`; Hermes runs a two-phase `propose_target` workflow — a lightweight LLM call identifies the most relevant file or proposes a new one. The Founder reviews proposals above an ambiguity threshold; below it, Hermes proceeds autonomously.

**Acceptance test:** `hermes implement "Add a subtract function" --repo hermes-calculator` (no `--output`). Hermes selects `calculator.py` and executes the modify pipeline. Tag: `v0.4.0-phase5`.

---

### Phase 6 — Multi-Operation Planning (AT-6 PASS)

Hermes decomposes a goal into multiple file operations via `EngineeringPlanner`. An `EngineeringCoordinator` owns the full lifecycle: planner → workflow. `EngineeringPlan` captures the decomposed operations with dependencies. `RepositoryManipulation` validates all planned operations atomically before any LLM token is consumed. A single git commit is issued after all operations complete. Tag: `v0.5.0-phase6`.

**Acceptance test:** A multi-file goal produces an `EngineeringPlan` with multiple `PlannedOperation` entries, each executed independently, committed in a single atomic commit.

---

### Phase 7 — Autonomous Correction Loop (AT-7 PASS)

Hermes gains the ability to repair its own failures. `CorrectionEngine` wraps single-operation execution: if validation or tests fail, the error output and the failing test files are fed back to the LLM for a correction attempt. Up to `max_corrections` (default 3) attempts are made per operation. Previously successful operations are never re-executed. `repair_limit_exceeded` is returned cleanly if the limit is exhausted. Tag: `v0.6.0-phase7`.

**Acceptance test:** `hermes implement "Implement text_utils.py with a truncate function" --repo hermes-text-utils`. The LLM enters the correction loop, receives failing test context, and converges to a passing implementation within the correction limit.

---

## 3. Capabilities by Phase

| Phase | Capability added |
|-------|-----------------|
| 1 | Generate and commit a new file (create pipeline) |
| 2 | Read and modify an existing file (modify pipeline) |
| 3 | Syntax validation gate before git staging |
| 4 | Test suite gate after validation |
| 5 | Autonomous target file selection (no `--output` required) |
| 6 | Multi-operation decomposition, planning, and atomic commit |
| 7 | Autonomous correction loop on validation or test failure |

---

## 4. Current Architecture

```
Founder Goal (plain-language task)
        │
        ▼
RepositoryIntelligence          scan repository → RepositorySnapshot
        │                       detect language, build system, test command
        ▼
EngineeringCoordinator          top-level orchestrator
        │
        ├── EngineeringPlanner  LLM call → EngineeringPlan (multi-op decomposition)
        │                       validates JSON, cycle detection, confidence check
        │
        └── EngineeringWorkflow plan coordinator
                │
                │  Step 0: resolve LLM intent against filesystem (create vs modify)
                │  Step 1: RepositoryManipulation — bulk atomic validation of all ops
                │  Step 2: OperationEngine — topological sort of PlannedOperations
                │  Step 3: CorrectionEngine — execute each op (with correction loop)
                │  Step 4: single plan-level git commit
                │
                ▼
        CorrectionEngine        single-operation executor
                │
                │  Create pipeline:  generate → create_file → validate → run_tests → add
                │  Modify pipeline:  read_file → generate → modify_file → validate → run_tests → add
                │
                │  On validate/run_tests failure:
                │    correction cycle (up to max_corrections):
                │      LLM(task + current_impl + failing_tests + error) → overwrite → validate → run_tests
                │
                ▼
        ExecutionGateway        sole dispatch boundary — all adapter calls routed here
                │
                ├── LlmAdapter          Ollama (local or cloud)
                ├── FilesystemAdapter   create_file / overwrite_file / modify_file / read_file
                ├── GitAdapter          add / commit
                └── ValidationAdapter  validate (py_compile) / run_tests (subprocess)
```

### Key modules

| Module | Path | Role |
|--------|------|------|
| `EngineeringCoordinator` | `src/hermes/kernel/engineering_coordinator.py` | Top-level orchestrator |
| `EngineeringPlanner` | `src/hermes/kernel/engineering_planner.py` | LLM-driven plan decomposition |
| `EngineeringWorkflow` | `src/hermes/workflows/engineering_workflow.py` | Plan coordinator |
| `CorrectionEngine` | `src/hermes/kernel/correction_engine.py` | Single-op executor with correction loop |
| `ExecutionGateway` | `src/hermes/kernel/execution_gateway.py` | Dispatch boundary |
| `RepositoryIntelligence` | `src/hermes/kernel/repository_intelligence.py` | Repository scanning |
| `RepositoryManipulation` | `src/hermes/kernel/repository_manipulation.py` | Bulk op validation |
| `OperationEngine` | `src/hermes/kernel/operation_engine.py` | Topological sort |
| `implement.py` | `src/hermes/cli/commands/implement.py` | CLI wiring layer |

### Data contracts

| Contract | Description |
|----------|-------------|
| `FounderGoal` | Input: task description, workspace path, repository path, output path |
| `EngineeringPlan` | Decomposed plan: ordered `PlannedOperation` list with dependencies |
| `PlannedOperation` | One file operation: target, intent (create/modify), goal, depends_on |
| `OperationCorrectionResult` | Per-operation result: steps, correction_attempts, correction_log, error |
| `CorrectionRecord` | Lightweight correction audit: attempt, trigger, error_excerpt (no source code) |
| `WorkflowExecutionReport` | Full audit trail: all steps, metadata, success/error |
| `WorkflowConfig` | Runtime config: LLM params, commit message, test_command, max_corrections |

---

## 5. Architectural Invariants

These invariants must not be broken by any future phase. They are structural guarantees that the rest of the system relies on.

**I-1 — Gateway invariant.**
Every adapter call is dispatched through `ExecutionGateway.dispatch()` before the adapter is invoked. No adapter is called directly. If the gateway does not return `DISPATCHED`, the adapter is never called. This is the sole enforcement boundary for adapter availability and routing.

**I-2 — No-raise invariant.**
`EngineeringWorkflow.execute()` and `CorrectionEngine.execute_operation()` never raise exceptions. All failures are captured in `WorkflowExecutionReport(success=False, error=...)` or `OperationCorrectionResult(success=False, error=...)`. Callers may assume these methods always return a result.

**I-3 — RepositoryManipulation invariant.**
All planned operations are validated atomically via `RepositoryManipulation.plan()` before any LLM token is consumed. If any conflict is detected, the workflow terminates immediately. No partial execution occurs against an invalid plan.

**I-4 — Isolation invariant.**
`EngineeringWorkflow` calls `CorrectionEngine.execute_operation()` exactly once per `PlannedOperation`. The correction loop runs entirely inside `CorrectionEngine`. A successfully completed operation is never re-executed while another operation is undergoing correction.

**I-5 — Single commit invariant.**
Exactly one git commit is issued per `EngineeringPlan`, at the plan level, after all operations complete. Individual operations stage files (`git add`) but do not commit. There is never more than one commit per `execute()` call.

**I-6 — CorrectionRecord lightweight invariant.**
`CorrectionRecord` records only `attempt`, `trigger`, and `error_excerpt`. It never stores source code, full step records, or generated content. The complete step audit trail lives in `OperationCorrectionResult.steps`.

**I-7 — correction_attempts always present.**
`WorkflowExecutionReport.metadata` always contains the key `correction_attempts` as a string integer, regardless of whether the correction loop ran. `"0"` is a valid value. Consumers may always read this key without a presence check.

**I-8 — Intent resolution before validation.**
LLM-declared intent (`create` / `modify`) is resolved against the actual filesystem state before `RepositoryManipulation` validation and before `CorrectionEngine` execution. The LLM's intent declaration is advisory, not authoritative.

**I-9 — Workflow never plans.**
`EngineeringWorkflow` receives a pre-formed `EngineeringPlan`. It does not call `EngineeringPlanner`, does not construct prompts, and does not make planning decisions. Planning belongs exclusively to `EngineeringPlanner` via `EngineeringCoordinator`.

**I-10 — Adapters never commit.**
`GitAdapter.commit()` is called only by `EngineeringWorkflow._execute_commit()`. `CorrectionEngine` calls `git add` but never `git commit`. No adapter initiates a commit independently.

---

## 6. Known Limitations After Phase 7

These limitations are known, accepted, and recorded. They are the starting point for the next layer of development.

**L-1 — Local correction only.**
The correction loop operates on the current file content and the current error. If the initial plan decomposition is wrong — wrong files, wrong responsibilities, wrong architecture — the correction loop cannot fix it. Hermes corrects implementations, not plans.

**L-2 — Sequential execution.**
`EngineeringWorkflow` executes operations in topological order, one at a time. Independent operations that could run in parallel are not parallelised. On large multi-file plans, this is a latency bottleneck.

**L-3 — No persistent engineering memory.**
Hermes has no memory of prior executions. Each `implement` invocation starts from scratch. Patterns learned from previous corrections, files previously generated, architectural decisions previously made — none of these are available to the next run.

**L-4 — No re-planning on plan failure.**
If the `EngineeringPlan` is invalid (conflict detected by `RepositoryManipulation`) or the LLM produces a plan that cannot be executed, the workflow fails. Hermes does not attempt to re-plan or request clarification. The Founder must retry with a revised task.

**L-5 — Python-only syntax validation.**
`ValidationAdapter.validate()` uses `py_compile` and applies only to `.py` files. Non-Python files pass the validate gate unconditionally. TypeScript, Go, Rust, and other languages have no syntax gate.

**L-6 — Correction prompt reads file directly.**
`CorrectionEngine._build_correction_payload()` reads the failing file from disk without routing through the gateway. This is an internal state assembly step, not an adapter action, and is explicitly accepted as a known deviation from the gateway invariant.

**L-7 — No model selection at runtime.**
The active model is determined by `OLLAMA_MODE` at startup (`local` → `llama3.2`; `cloud` → `kimi-k2.7-code`). There is no mechanism to select a model per-task, per-operation, or at the CLI level. The `OLLAMA_MODEL` variable in `.env.example` is not read by any code.

**L-8 — Context window limits on large files.**
The correction prompt includes the full current file content and the full failing test files. For large repositories with large test suites, this may approach or exceed the LLM's context window, degrading correction quality or causing truncation.

---

## 7. Candidate Directions for Phase 8

These are candidate directions only. None is approved for implementation. The next phase will be selected and specified by the Founder before any code is written.

**C-1 — Persistent engineering memory.**
Hermes records what it has built, what corrections it has made, and what patterns have converged. Subsequent runs on the same repository start with accumulated context rather than a blank slate. Relevant to L-3.

**C-2 — Re-planning on failure.**
When `RepositoryManipulation` rejects a plan, or when `repair_limit_exceeded` is reached, Hermes re-invokes `EngineeringPlanner` with the failure context rather than returning `success=False` immediately. Relevant to L-1 and L-4.

**C-3 — Parallel operation execution.**
Independent operations in a `EngineeringPlan` (those with no dependency edges) are executed concurrently. `EngineeringWorkflow` manages a thread or async pool; `CorrectionEngine` instances run per-operation in parallel. Relevant to L-2.

**C-4 — Multi-language validation.**
`ValidationAdapter` gains language-specific syntax gates beyond Python. TypeScript (`tsc --noEmit`), Go (`go build ./...`), Rust (`cargo check`) are the most common targets in multi-language repositories. Relevant to L-5.

**C-5 — Runtime model selection.**
`OLLAMA_MODEL` (or equivalent) is read from the environment and used to override `capabilities.default_model` at startup. A more capable model can be selected for correction cycles than for initial generation. Relevant to L-7.

**C-6 — Confidence-gated planning.**
`EngineeringPlanner` returns `confidence="ambiguous"` for underspecified goals. Rather than failing, Hermes asks the Founder for clarification on the specific ambiguity before proceeding. This closes the gap between fully autonomous operation and tasks that genuinely require human input.
