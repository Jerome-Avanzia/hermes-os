# AT-7 — Autonomous Correction Loop

**Bootstrap Phase:** 7  
**Capability:** Self-correction on validation/test failure via CorrectionEngine  
**Status:** Pending production acceptance

---

## Objective

Verify that Hermes can autonomously detect, diagnose, and repair code generation
failures by feeding error output back to the LLM and retrying up to the
configured limit — without any human intervention after the initial task
submission.

Additionally verify that a successfully completed operation is **never
re-executed** while another operation is undergoing correction.

---

## Architecture Under Test

```
EngineeringCoordinator
      ↓
EngineeringWorkflow      ← coordinates the plan; one commit
      ↓ execute_operation() per PlannedOperation
CorrectionEngine         ← single-operation executor with correction loop
      ↓
ExecutionGateway → LLM Adapter + Filesystem Adapter + Git Adapter + Validation Adapter
```

New in Phase 7:
- `CorrectionEngine` in `src/hermes/kernel/correction_engine.py`
- `CorrectionRecord`, `OperationCorrectionResult` in `src/hermes/models/engineering_workflow.py`
- `WorkflowConfig.max_corrections` (default 3)
- `WorkflowExecutionReport.metadata["correction_attempts"]` always present

---

## Scenario 1 — Single-Operation Correction

### Test Repository: `hermes-text-utils`

```
hermes-text-utils/
├── tests/
│   └── test_text_utils.py   ← pre-committed; strict word-boundary assertions
├── pyproject.toml           ← declares pytest as test command
└── (no text_utils.py)       ← target of the implement command
```

`tests/test_text_utils.py` assertions (pre-committed, not modified by Hermes):

```python
from text_utils import truncate

def test_no_truncation_needed():
    assert truncate("hi", 10) == "hi"

def test_empty_string():
    assert truncate("", 10) == ""

def test_clean_word_boundary():
    # Truncates at word boundary, no ellipsis when the cut lands exactly
    assert truncate("hello world foo bar", 11) == "hello world"

def test_ellipsis_on_mid_word_cut():
    # Last complete word that fits within max_len - 3 (for "...") is used
    assert truncate("hello world foo bar", 10) == "hello w..."

def test_single_oversized_word():
    # Single word exceeds max_len: hard truncate at max_len - 3, append "..."
    assert truncate("averylongword", 5) == "av..."
```

This specification is intentionally non-obvious: word-boundary detection, the
clean-break vs ellipsis distinction, and the oversized-word edge case each
independently break common naive implementations.

### Command

```bash
docker exec hermes hermes implement \
  "Implement text_utils.py with a truncate function" \
  --repo hermes-text-utils
```

### PASS Criteria

| # | Criterion |
|---|-----------|
| P1 | Exit code 0 |
| P2 | `git log -1 --oneline` on `hermes-text-utils` shows a new commit |
| P3 | `pytest` exits 0 in `hermes-text-utils` (all 5 assertions pass on committed state) |
| P4 | `WorkflowExecutionReport.metadata` contains key `correction_attempts` with a string integer value ≥ 0 |
| P5 | If `correction_attempts >= 1`: `WorkflowExecutionReport` contains at least one `CorrectionRecord` with non-empty `error_excerpt` and `trigger` in `{"validation_failure", "test_failure"}` |
| P6 | `CorrectionRecord.error_excerpt` does not contain the full source code of any generated file (lightweight record invariant) |

### FAIL Criteria

| # | Criterion |
|---|-----------|
| F1 | Workflow halts on first test/validation failure without attempting any correction when `max_corrections > 0` |
| F2 | `correction_attempts` key absent from `metadata` |
| F3 | A correction produces a commit with failing tests |
| F4 | Correction loop raises an unhandled exception instead of returning `success=False` |
| F5 | `max_corrections` limit exceeded without returning `error="repair_limit_exceeded"` |

### Acceptable FAIL Path

If the LLM exhausts `max_corrections` without producing a passing implementation:

- Exit code 1
- `report.success is False`
- `report.error == "repair_limit_exceeded"`
- No commit made
- This is a graceful failure, not a test failure

---

## Scenario 2 — Isolation: Successful Operations Are Never Re-Executed

### Purpose

Prove that when a multi-operation plan is executed and one operation requires
correction, previously successful operations are **never re-invoked** — not
re-generated, not re-validated, not re-staged.

### Setup

Use a two-operation autonomous plan against `hermes-text-utils` (or any repo
where Hermes produces a two-operation `EngineeringPlan`). For example:

```bash
docker exec hermes hermes implement \
  "Add a word_count function in word_utils.py and implement text_utils.py with truncate" \
  --repo hermes-text-utils
```

Operation 1 (`word_utils.py`) is intentionally straightforward — the LLM is
expected to succeed on the first attempt.

Operation 2 (`text_utils.py`) requires the non-trivial truncate implementation
and may require one or more correction cycles.

### Isolation Verification

Inspect `WorkflowExecutionReport.steps`. For each `PlannedOperation`, count
`StepExecutionRecord` entries where `action_id == "generate"` and the
`operation_id` contains that operation's planned operation ID prefix.

**Expected:**

| Operation | generate steps | Meaning |
|-----------|---------------|---------|
| `word_utils.py` | 1 | Succeeded first attempt; never regenerated |
| `text_utils.py` | 1 + N | Initial attempt + N correction generates |

`N` equals `correction_attempts` reported for that operation.

**Failure condition:** If `word_utils.py` shows more than 1 generate step, the
isolation invariant has been violated.

### PASS Criteria

| # | Criterion |
|---|-----------|
| P7 | `WorkflowExecutionReport.success is True` |
| P8 | `metadata["correction_attempts"]` equals the sum of per-operation correction counts |
| P9 | Operation 1 (`word_utils.py`) has exactly **1** `generate` step in `report.steps` |
| P10 | Operation 2 (`text_utils.py`) has exactly `1 + correction_attempts_op2` `generate` steps |
| P11 | `git log` shows exactly **one** new commit covering both files |

### FAIL Criteria

| # | Criterion |
|---|-----------|
| F6 | `word_utils.py` has more than 1 generate step (isolation violated) |
| F7 | Two commits instead of one (plan-level commit invariant violated) |
| F8 | `metadata["correction_attempts"]` does not equal sum of per-op correction counts |

---

## Execution Environment

- Production Docker deployment (identical to Phases 2–6)
- Ollama running with the configured model
- `hermes-text-utils` repository pre-seeded with `tests/test_text_utils.py` and `pyproject.toml`

---

## Local Test Suite Gate

Before production acceptance, the local test suite must pass:

```bash
pytest tests/test_correction_engine.py tests/test_engineering_workflow.py -v
```

Expected: all tests in both files pass with no failures.

---

## Known Limitations at Approval

| ID | Limitation |
|----|-----------|
| L-01 | Only Python syntax validation implemented; non-Python files pass `validate` unconditionally (inherited from AT-3) |
| L-02 | `CorrectionEngine._build_correction_payload()` reads the failing file directly from disk without going through the gateway (internal state assembly, not an adapter action) |
| L-03 | Correction prompt includes full current file content — for very large files this may approach LLM context limits |

---

## Freeze Conditions

Phase 7 is frozen when:

1. Scenario 1 PASS criteria P1–P6 are met in the production Docker environment.
2. Scenario 2 PASS criteria P7–P11 are met.
3. No regressions in AT-1 (create), AT-3 (validation gate), AT-5 (autonomous target
   selection), or AT-6 (multi-operation plan) behaviours.
4. Production tag `v0.6.0-phase7` applied.
