# Hermes AT-3 Runbook — Pre-Commit Validation Gate

---

## Overview

**Objective:** Verify that Hermes inserts a syntax-validation step between file write and git staging in both create and modify workflows. An invalid Python file must be blocked before it reaches `git add`.

**Scenario:** Hermes modifies `calculator.py` to add a `multiply` function.

**Command:**
```bash
docker compose run --rm hermes implement \
  "Add a multiply function that returns the product of two numbers" \
  --output hermes-calculator/calculator.py \
  --repo hermes-calculator
```

**Success signal:** `calculator.py` modified and committed; WorkflowExecutionReport shows **6 ✓ steps** including `validation validate` between `filesystem modify_file` and `git add`.

**What AT-3 proves:**
- The validation adapter is wired into the modify workflow
- Valid Python clears the gate and proceeds to commit
- The step sequence is `read_file → generate → modify_file → validate → add → commit`
- No regression in AT-1 (create) or AT-2 (modify) behaviour

---

## Prerequisites

### P-1 — Docker production deployment running

```bash
cd /Users/admin/Desktop/AVANZIA/hermes-os
docker compose ps
```

Expected: container `hermes` is listed. If not running:

```bash
docker compose build
docker compose up -d
```

Confirm the image includes the AT-3 changes (built after the current commit):

```bash
docker compose run --rm hermes --version
```

> **Critical:** If the image was built before AT-3 was committed, rebuild:
> ```bash
> docker compose build --no-cache
> docker compose up -d
> ```

---

### P-2 — Ollama reachable (choose one)

**Option A — Ollama Cloud (recommended for AT-3)**
```bash
# Confirm env is set for the container
grep OLLAMA /opt/avanzia/secrets/hermes.env
```
Expected: `OLLAMA_MODE=cloud` and `OLLAMA_API_KEY=<key>` present.

**Option B — Local Ollama**
```bash
curl -s http://localhost:11434/api/tags | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print('Models:', [m['name'] for m in d.get('models',[])])"
```
Expected: `Models: ['llama3.2:latest', ...]`

---

### P-3 — hermes-calculator repository on host

The repository must exist at `/opt/avanzia/repos/hermes-calculator` (mounted into the container at `/data/repos/hermes-calculator`).

**Check:**
```bash
ls /opt/avanzia/repos/hermes-calculator/calculator.py
```

**If missing — create it now:**
```bash
mkdir -p /opt/avanzia/repos/hermes-calculator
cd /opt/avanzia/repos/hermes-calculator

git init
git config user.email "jerome@avanzia.tech"
git config user.name "Jerome Cornet"

cat > calculator.py << 'EOF'
"""Simple calculator module."""


def add(a: float, b: float) -> float:
    return a + b


def subtract(a: float, b: float) -> float:
    return a - b


def divide(a: float, b: float) -> float:
    if b == 0:
        raise ValueError("Cannot divide by zero")
    return a / b
EOF

git add calculator.py
git commit -m "feat: initial calculator module (add, subtract, divide)"
```

---

## Step 1 — Confirm pre-execution state

```bash
# Confirm calculator.py exists on the host
cat /opt/avanzia/repos/hermes-calculator/calculator.py
```
Expected: Python file containing `add`, `subtract`, `divide` — no `multiply`.

```bash
# Confirm git is clean
git -C /opt/avanzia/repos/hermes-calculator status
```
Expected: `nothing to commit, working tree clean`

```bash
# Confirm current commit count
git -C /opt/avanzia/repos/hermes-calculator log --oneline
```
Expected: One or more commits. Note the HEAD SHA — it must change after Step 2.

---

## Step 2 — Execute Hermes

Run from the `hermes-os` directory (where `docker-compose.yml` lives):

```bash
cd /Users/admin/Desktop/AVANZIA/hermes-os

docker compose run --rm hermes implement \
  "Add a multiply function that returns the product of two numbers" \
  --output hermes-calculator/calculator.py \
  --repo hermes-calculator
```

> `--output` is workspace-relative (`HERMES_REPOSITORIES=/data/repos`).
> `--repo hermes-calculator` is the git repository subdirectory.

---

## Expected Hermes behaviour — step by step

### Header output (printed before execution)
```
Implementing: Add a multiply function that returns the product of two numbers
Output:       hermes-calculator/calculator.py
Repository:   hermes-calculator
Model:        <model-name>  (cloud|local)
```

### Step-by-step execution (modify mode — 6 steps)

| Step | Adapter | Action | What happens |
|------|---------|--------|--------------|
| 1 | filesystem | read_file | Reads current content of `calculator.py` |
| 2 | llm | generate | POSTs to Ollama with existing file content; receives complete modified file |
| 3 | filesystem | modify_file | Overwrites `calculator.py` with LLM output |
| 4 | validation | validate | Runs `python -m py_compile calculator.py`; halts if syntax error |
| 5 | git | add | `git add -- calculator.py` inside `hermes-calculator/` |
| 6 | git | commit | `git commit -m "feat: Add a multiply function..."` |

### WorkflowExecutionReport output
```
Status: ✓ SUCCESS

  ✓  filesystem    read_file
  ✓  llm           generate
  ✓  filesystem    modify_file
  ✓  validation    validate
  ✓  git           add
  ✓  git           commit

  commit_message: feat: Add a multiply function that returns the product of two numbers
  modified_file: hermes-calculator/calculator.py
  goal_id: xxxxxxxx
  repository: hermes-calculator
  steps_completed: 6

  Total execution time: X.Xs
```

> `steps_completed: 6` is the key AT-3 indicator. AT-2 produced `steps_completed: 5`.

---

## Step 3 — Verify results

### 3a — Confirm step count and validation step in output

The output from Step 2 must show exactly **6 ✓ lines** in this order:

```
  ✓  filesystem    read_file
  ✓  llm           generate
  ✓  filesystem    modify_file
  ✓  validation    validate
  ✓  git           add
  ✓  git           commit
```

Confirm `validation    validate` is present at position 4 and shows `✓`.

### 3b — Confirm calculator.py was modified

```bash
cat /opt/avanzia/repos/hermes-calculator/calculator.py
```
Expected: Python file containing `add`, `subtract`, `divide`, **and `multiply`**.

### 3c — Confirm Python syntax is valid

```bash
python3 -m py_compile /opt/avanzia/repos/hermes-calculator/calculator.py
echo "exit code: $?"
```
Expected: `exit code: 0` (no output, no error).

### 3d — Confirm git history advanced

```bash
git -C /opt/avanzia/repos/hermes-calculator log --oneline
```
Expected: A new commit at the top:
```
xxxxxxx feat: Add a multiply function that returns the product of two numbers
xxxxxxx <previous commit>
```

### 3e — Confirm multiply is callable

```bash
python3 -c "
import sys
sys.path.insert(0, '/opt/avanzia/repos/hermes-calculator')
from calculator import multiply
print(multiply(3, 4))
"
```
Expected: `12` or `12.0`

### 3f — Confirm read_file step is recorded in output

The AT-3 report must show `filesystem read_file` as step 1. This confirms the workflow entered modify mode (not create mode).

---

## Validation gate behaviour (architectural — not live-tested)

The validation gate runs `sys.executable -m py_compile <path>` before `git add`. If the generated file has a Python syntax error:

```
Status: ✗ FAILED

  ✓  filesystem    read_file
  ✓  llm           generate
  ✓  filesystem    modify_file
  ✗  validation    validate

Error: validation_failed: <SyntaxError detail>

  failure_stage: validate
  steps_completed: 4
```

The file remains modified on disk (the filesystem write is not rolled back) but is **never staged or committed** — `git status` would show the file as modified but unstaged.

This behaviour is verified by 31 unit tests in `tests/test_validation_adapter.py` (AT-3 implementation). No live test for the failure path is required: forcing the LLM to produce invalid Python is non-deterministic and the gate logic is deterministic and fully covered.

---

## PASS criteria

All of the following must be true:

1. `docker compose run` exits with code `0`
2. WorkflowExecutionReport shows `✓ SUCCESS`
3. Output shows exactly **6 step lines**, all `✓`
4. **Step 4 is `✓  validation    validate`** (positioned between `filesystem modify_file` and `git add`)
5. `steps_completed: 6` appears in the metadata block
6. `calculator.py` on the host contains a `multiply` function
7. `python3 -m py_compile calculator.py` exits 0
8. `git log --oneline` shows a new commit at HEAD

**Verdict: PASS** → Hermes Bootstrap Phase 3 (pre-commit validation gate) is validated.

---

## PARTIAL PASS criteria

Any of the following with all other steps succeeding:

- **P1** — `calculator.py` modified and committed but `multiply` function has wrong name or signature. Hermes machinery correct; LLM response quality issue. Not a Hermes defect.
- **P2** — `steps_completed: 6` but step order printed differently in terminal output (display artefact). Check metadata field — if it reads `6`, the execution was correct.
- **P3** — Git commit message differs slightly from expected. Minor deviation.

**Verdict: PARTIAL PASS** → Gate is functional; LLM or display issue present.

---

## FAIL criteria

Any of the following:

- **F1** — Exit code 1
- **F2** — Any step shows `✗` unexpectedly
- **F3** — Output shows only **5 step lines** (validation step absent — image not rebuilt with AT-3)
- **F4** — `validation validate` step is missing from the output
- **F5** — `steps_completed: 5` in metadata (AT-2 behaviour — image not rebuilt)
- **F6** — `calculator.py` unchanged after execution
- **F7** — New git commit is absent (`git log` HEAD unchanged)
- **F8** — Unhandled Python traceback visible in output

---

## Diagnostic guide

| Symptom | Likely cause | Smallest correction |
|---------|--------------|---------------------|
| `steps_completed: 5`, no `validation` step | Image not rebuilt with AT-3 | `docker compose build --no-cache && docker compose up -d` |
| Step 4 `✗ validation validate` | `calculator.py` not a Python file, or path resolution error | Confirm `--output` path ends in `.py` |
| Step 3 `✗ filesystem modify_file` | `calculator.py` does not exist (create mode expected) | Ensure `calculator.py` exists before running |
| Step 1 `✗ filesystem read_file` | Workspace path mismatch | Confirm `HERMES_REPOSITORIES=/data/repos` and repo at `/data/repos/hermes-calculator` in container |
| Step 2 `✗ llm generate` | Ollama unreachable | Verify `OLLAMA_MODE` and `OLLAMA_API_KEY` in `hermes.env` |
| `✗ git commit` — "nothing to commit" | Filesystem write succeeded, validation passed, but `git add` failed silently | Check `git -C /opt/avanzia/repos/hermes-calculator status`; re-run if transient |
| `docker: command not found` | Docker not running | Start Docker Desktop |

**Principle:** Diagnose, then re-run `docker compose run --rm hermes implement ...` after correcting the environment. Do not modify Hermes source code to fix environment issues.

---

## Known limitations (AT-3 scope)

**L-01 — Validation gate does not roll back filesystem writes**
If `calculator.py` fails validation, the modified file remains on disk (unstaged). The Founder must manually restore the previous content or re-run Hermes. A rollback mechanism is outside AT-3 scope.

**L-02 — Python-only validation in AT-3**
Non-Python files (`.js`, `.ts`, `.go`, etc.) pass the validation gate unconditionally via `ValidatorKind.NONE`. Additional language validators are outside AT-3 scope.

**L-03 — Validation runs generated content, not committed content**
The validator checks the file on disk after the filesystem write. If the validator itself crashes (subprocess error), the error is captured in `ValidationExecutionResult(success=False)` and the workflow halts. No subprocess crash has been observed in testing.

---

*This document is the official Hermes AT-3 execution guide. It becomes the permanent validation procedure for the pre-commit validation gate.*
