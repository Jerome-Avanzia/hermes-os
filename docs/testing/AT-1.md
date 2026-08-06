# Hermes AT-1 Runbook — First Autonomous Engineering Execution

---

## Overview

**Task:** Hermes adds a `multiply` function to a calculator project.
**Command:** `hermes implement "Add a multiply function that returns the product of two numbers" --output multiply.py`
**Success signal:** `multiply.py` created, committed to git, WorkflowExecutionReport shows 4 ✓ steps.

---

## Prerequisites

### P-1 — Ollama (choose one)

**Option A — Local Ollama**
```bash
# Install Ollama (if not installed)
# https://ollama.com/download — download the Mac app, then:
ollama pull llama3.2
ollama serve          # leave running in a terminal tab
```

**Option B — Ollama Cloud**
```bash
export OLLAMA_MODE=cloud
export OLLAMA_API_KEY=your_key_here
```
No model pull required. Default cloud model: `kimi-k2.7-code`.

---

### P-2 — Install the Hermes CLI (one-time)

```bash
uv tool install --editable /Users/admin/Desktop/AVANZIA/hermes-os
```

Verify:
```bash
hermes --help
```
Expected: Hermes OS command-line interface help text, listing `implement` among the commands.

**Fallback** if `uv tool install` fails:
```bash
# Use this form instead of `hermes` in every command below:
alias hermes="uv --directory /Users/admin/Desktop/AVANZIA/hermes-os run hermes"
```
> Note: the alias form sets CWD to hermes-os for uv but the script inherits the shell's CWD. Confirm `Path.cwd()` returns the calculator dir before using the alias form.

---

## Step 1 — Create the calculator repository

```bash
mkdir -p ~/Desktop/hermes-calculator
cd ~/Desktop/hermes-calculator
git init
```

Expected output:
```
Initialized empty Git repository in ~/Desktop/hermes-calculator/.git/
```

---

## Step 2 — Create the initial source files

```bash
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
```

```bash
cat > test_calculator.py << 'EOF'
"""Tests for the calculator module."""
import pytest
from calculator import add, subtract, divide


def test_add():
    assert add(2, 3) == 5


def test_subtract():
    assert subtract(10, 4) == 6


def test_divide():
    assert divide(10, 2) == 5.0


def test_divide_by_zero():
    with pytest.raises(ValueError):
        divide(1, 0)
EOF
```

```bash
cat > README.md << 'EOF'
# Hermes Calculator

A simple Python calculator for AT-1 acceptance testing.
EOF
```

---

## Step 3 — Make the initial commit

```bash
git add calculator.py test_calculator.py README.md
git commit -m "feat: initial calculator module (add, subtract, divide)"
```

Expected output:
```
[main (root-commit) xxxxxxx] feat: initial calculator module (add, subtract, divide)
 3 files changed, ...
```

---

## Step 4 — Confirm environment

```bash
# Confirm you are in hermes-calculator (CRITICAL — do not skip)
pwd
```
Expected: `/Users/admin/Desktop/hermes-calculator`

```bash
# Confirm git is clean
git status
```
Expected: `nothing to commit, working tree clean`

```bash
# Confirm Ollama is reachable (Local only)
curl -s http://localhost:11434/api/tags | python3 -c "import sys,json; d=json.load(sys.stdin); print('Models:', [m['name'] for m in d.get('models',[])])"
```
Expected (Local): `Models: ['llama3.2:latest', ...]`
Skip this check for Cloud mode — the first real test is Step 5.

---

## Step 5 — Execute Hermes

```bash
cd ~/Desktop/hermes-calculator

hermes implement \
  "Add a multiply function that returns the product of two numbers" \
  --output multiply.py
```

**Cloud mode alternative** (if using Option B from P-1):
```bash
OLLAMA_MODE=cloud OLLAMA_API_KEY=your_key_here hermes implement \
  "Add a multiply function that returns the product of two numbers" \
  --output multiply.py
```

---

## Expected Hermes behaviour — step by step

### Header output (printed before execution)
```
Implementing: Add a multiply function that returns the product of two numbers
Output:       multiply.py
Repository:   .
Model:        llama3.2  (local)
```
`(local)` becomes `(cloud)` in cloud mode. Model name reflects the active mode.

### Step 1 — RepositoryIntelligence scan
Hermes calls `RepositoryIntelligence(Path.cwd()).scan(".")`.
It walks `hermes-calculator/`, detects Python as primary language, detects 3 files, detects git presence.
Context string injected into the LLM prompt includes:
```
Primary language: python
Source files (3 shown of 3 total):
  calculator.py
  test_calculator.py
  README.md
Repository: 3 files, 0 directories  |  git: yes
```

### Step 2 — Ollama configuration
`configure_from_env()` reads `OLLAMA_MODE` (default: `local`).
Returns `(OllamaEnvConfig, ProviderCapabilities, ProviderDriver)`.

### Step 3 — WorkflowConfig construction
```
write_mode = "create_file"   ← multiply.py does not yet exist
commit_message = "feat: Add a multiply function that returns the product of two numbers"
llm_model = "llama3.2"       ← or "kimi-k2.7-code" in cloud mode
llm_max_tokens = 4096
llm_timeout_seconds = 120
```

### Step 4 — EngineeringWorkflow execution (4 steps)

| Step | Adapter | Action | What happens |
|------|---------|--------|--------------|
| 1 | llm | generate | POST to Ollama → receives Python source code |
| 2 | filesystem | create_file | Writes LLM output to `multiply.py` |
| 3 | git | add | `git add -- multiply.py` inside `hermes-calculator/` |
| 4 | git | commit | `git commit -m "feat: Add a multiply function..."` |

### WorkflowExecutionReport output
```
Status: ✓ SUCCESS

  ✓  llm           generate
  ✓  filesystem    create_file
  ✓  git           add
  ✓  git           commit

  commit_message: feat: Add a multiply function that returns the product of two numbers
  generated_file: multiply.py
  goal_id: xxxxxxxx
  repository: .
  steps_completed: 4

  Total execution time: X.Xs
```

---

## Expected Ollama interaction

**Endpoint:** `POST http://localhost:11434/api/chat` (local) or `POST https://ollama.ai/api/chat` (cloud)

**Model:** `llama3.2` (local) / `kimi-k2.7-code` (cloud)

**Temperature:** `0.0`

**System prompt sent by Hermes:**
```
You are an expert software developer. Generate clean, production-quality source code. Respond with code only — no explanation, no markdown fences.
```

**User prompt sent by Hermes:**
```
Write source code to accomplish the following task:

Add a multiply function that returns the product of two numbers

Repository context:
Primary language: python
Source files (3 shown of 3 total):
  calculator.py
  test_calculator.py
  README.md
Repository: 3 files, 0 directories  |  git: yes

Output file: multiply.py
```

**Expected Ollama response (illustrative):**
```python
"""Multiply function."""


def multiply(a: float, b: float) -> float:
    return a * b
```
The exact content is non-deterministic. Any valid Python defining a `multiply` function constitutes a successful LLM response.

---

## Step 6 — Verify results

### 6a — Confirm multiply.py was created
```bash
cat multiply.py
```
Expected: Python source containing a `multiply` function definition.

### 6b — Confirm git history
```bash
git log --oneline
```
Expected:
```
xxxxxxx feat: Add a multiply function that returns the product of two numbers
xxxxxxx feat: initial calculator module (add, subtract, divide)
```

### 6c — Confirm repository tree
```bash
find . -not -path './.git/*' | sort
```
Expected:
```
.
./README.md
./calculator.py
./multiply.py
./test_calculator.py
```

### 6d — Confirm multiply is callable (optional smoke test)
```bash
python3 -c "from multiply import multiply; print(multiply(3, 4))"
```
Expected: `12` or `12.0`

### 6e — Run existing tests (optional — verifies calculator.py untouched)
```bash
uv --directory /Users/admin/Desktop/AVANZIA/hermes-os run pytest test_calculator.py -v
```
Expected: 4 tests pass, `multiply.py` is not tested (no test file for it yet — Known Limitation L-01).

---

## Expected final repository tree

```
hermes-calculator/
├── .git/
├── README.md
├── calculator.py          (unchanged — initial commit)
├── multiply.py            (created by Hermes)
└── test_calculator.py     (unchanged — initial commit)
```

---

## Expected git history

```
commit <sha2>  feat: Add a multiply function that returns the product of two numbers
commit <sha1>  feat: initial calculator module (add, subtract, divide)
```

Two commits. No merges. Linear history.

---

## PASS criteria

All of the following must be true:

1. `hermes implement` exits with code `0`
2. `WorkflowExecutionReport` shows `✓ SUCCESS`
3. All 4 steps show `✓` (llm, filesystem, git add, git commit)
4. `multiply.py` exists in `hermes-calculator/`
5. `multiply.py` contains valid Python (no `SyntaxError` on import)
6. `multiply.py` defines a callable named `multiply`
7. `git log --oneline` shows exactly 2 commits, newest first: `feat: Add a multiply...`
8. `calculator.py`, `test_calculator.py`, `README.md` are unchanged

**Verdict: PASS** → Hermes Bootstrap Phase 1 is validated.

---

## PARTIAL PASS criteria

Any of the following with all other steps succeeding:

- **P1** — `multiply.py` created but defines no function named `multiply` (Ollama hallucinated a different name or empty file). Steps all `✓`, exit code 0. Hermes completed correctly; the LLM response was insufficient.
- **P2** — `multiply.py` created but contains a `SyntaxError`. Same as P1 — Hermes completed; LLM output was malformed.
- **P3** — Git commit step `✓` but commit message differs from expected (truncation, formatting). Minor deviation.

**Verdict: PARTIAL PASS** → Hermes machinery is working; LLM output quality or commit formatting needs attention. Not a Hermes bug.

---

## FAIL criteria

Any of the following:

- **F1** — `hermes implement` exits with code `1`
- **F2** — Any step shows `✗` in the report
- **F3** — `multiply.py` does not exist after execution
- **F4** — `hermes implement` throws an unhandled exception (Python traceback visible)
- **F5** — Git history shows 0 new commits after execution
- **F6** — `hermes: command not found` (P-2 prerequisite not completed)
- **F7** — Ollama connection error at step 1 (Ollama not running / wrong mode)

---

## If Hermes fails — diagnostic guide

| Symptom | Failing component | Likely cause | Smallest correction |
|---------|-------------------|--------------|---------------------|
| `hermes: command not found` | CLI install | P-2 not done | Run `uv tool install --editable /Users/admin/Desktop/AVANZIA/hermes-os` |
| `Error: failed to scan repository` | RepositoryIntelligence | Not running from hermes-calculator | `cd ~/Desktop/hermes-calculator` then re-run |
| `Error: failed to read Ollama configuration` | configure_from_env | Env var typo | Check `echo $OLLAMA_MODE` and `echo $OLLAMA_API_KEY` |
| Step 1 `✗ llm generate` | LlmAdapter / Ollama | Ollama not running or model not pulled | Start `ollama serve`, run `ollama pull llama3.2` |
| Step 1 `✗` with 401 error | LlmAdapter / Ollama Cloud | Missing or invalid API key | Set `OLLAMA_API_KEY` |
| Step 2 `✗ filesystem create_file` | FilesystemAdapter | Permission denied or path conflict | Confirm write access; check `multiply.py` not already present from a partial run |
| Step 3 `✗ git add` | GitAdapter | hermes-calculator not a git repo | `git init` was skipped — run Step 1 again |
| Step 4 `✗ git commit` | GitAdapter | git user not configured | `git config --global user.name "Jerome Cornet"` and `git config --global user.email "jerome@avanzia.tech"` |
| Step 4 `✗ git commit` with "nothing to commit" | GitAdapter | Filesystem write succeeded but add failed silently | Re-run; if persistent, check `git status` manually |

**Principle:** Diagnose, then re-run `hermes implement` after correcting the environment. Do not modify Hermes source code to fix environment issues.

---

## Known limitations (documented at AT-1 approval)

**L-01 — Commit before test verification**
Hermes commits before running tests. If `multiply.py` has a syntax error, it is committed anyway. Long-term target order: LLM → Filesystem → Test Runner → Git add → Git commit. Not a Phase 1 defect.

**L-02 — RepositoryManipulationPlan not in Phase 1 flow**
Hermes does not validate the planned filesystem change against the repository state before writing. The validation engine exists (`RepositoryManipulation`) but is not wired into Phase 1. Not a Phase 1 defect.

---

*This document is the official Hermes AT-1 execution guide. It becomes the permanent validation procedure used after future Hermes releases.*
