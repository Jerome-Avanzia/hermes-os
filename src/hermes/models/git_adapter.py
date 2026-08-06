"""Git Adapter — typed contracts for the Git Adapter.

Architecture position:
  Execution Gateway → Git Adapter → Git

The Git Adapter is NOT Git. The adapter is an execution adapter that
translates Hermes execution contracts into Git operations and translates
Git results back into Hermes execution results.

  Dispatch contract vs execution (invariant from Sprint 59):
    The Gateway determines which adapter receives the request and whether
    dispatch is valid. The Git Adapter performs execution outside the
    deterministic Hermes kernel. No engine ever runs git commands directly.

  Every git operation follows this path:
    Execution Gateway → Git Adapter → Git (subprocess)

  Where git-specific logic lives:
    GitAdapter.build_git_request   → translates ExecutionRequest → GitRequest
    GitAdapter.execute             → performs the operation and produces GitExecutionResult
    GitAdapter.resolve_repository  → workspace boundary + git repository validation

Sprint 63 scope:
  Eight operations supported: status, add, commit, branch, checkout, tag,
  log, diff. All repository paths are workspace-relative. Remote operations
  (push, pull, fetch, clone) are NOT supported — the adapter enforces the
  local-only boundary at the contract level.

All contracts in this module are immutable after construction.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


# ── GitOperation ───────────────────────────────────────────────────────────────


class GitOperation(enum.Enum):
    """Enumeration of supported Git operations.

    Each value is the string that maps to the ExecutionRequest.action_id
    for git requests. The Git Adapter uses the action_id to determine which
    operation to perform.

    Remote operations (push, pull, fetch, clone) are intentionally excluded.
    The Git Adapter is a local-only adapter — it manages repository state
    within the workspace and never opens outbound network connections.

    STATUS   → report working tree status (git status --porcelain)
    ADD      → stage file changes (git add); files=() stages all tracked changes
    COMMIT   → create a commit with the given message (git commit -m)
    BRANCH   → list branches (branch_name empty) or create a branch (branch_name set)
    CHECKOUT → switch to an existing branch or create and switch (create_branch=True)
    TAG      → create a lightweight or annotated tag
    LOG      → retrieve commit history (git log --oneline)
    DIFF     → show changes between working tree and a ref (git diff)
    """

    STATUS = "status"
    ADD = "add"
    COMMIT = "commit"
    BRANCH = "branch"
    CHECKOUT = "checkout"
    TAG = "tag"
    LOG = "log"
    DIFF = "diff"


# ── GitRepository ──────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GitRepository:
    """Immutable description of a Git repository target.

    Produced during request translation. Represents the workspace-relative
    repository path before path resolution occurs.

    path → workspace-relative path string to the git repository root
           (as provided in the payload)

    Immutable after construction.
    """

    path: str


# ── GitRequest ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GitRequest:
    """Immutable normalized Hermes-side representation of a git request.

    Produced by GitAdapter.build_git_request() from an ExecutionRequest.
    Represents the canonical request before any git operation occurs.

    All repository paths are workspace-relative at this stage. Absolute
    resolved paths are computed by the adapter at execution time.

    Payload key extraction:
      "repository_path" → workspace-relative path to the git repository root
      "files"           → list of file paths for ADD (empty = stage all tracked)
      "message"         → commit message for COMMIT or annotation for TAG
      "branch_name"     → target branch for BRANCH (create) and CHECKOUT
      "ref"             → git ref (commit hash, branch, tag) for LOG/DIFF/TAG/CHECKOUT
      "max_count"       → maximum log entries for LOG (0 = no limit)
      "create_branch"   → create new branch during CHECKOUT when True

    request_id      → from ExecutionRequest.request_id
    operation_id    → from ExecutionRequest.operation_id
    operation       → derived from ExecutionRequest.action_id (GitOperation enum)
    repository_path → workspace-relative path to the git repository root
    files           → tuple of file paths for ADD; empty tuple stages all tracked
    message         → commit message (COMMIT) or annotation (annotated TAG)
    branch_name     → target branch name for BRANCH (create) and CHECKOUT
    ref             → git ref for LOG start point, DIFF comparison, TAG target,
                      or CHECKOUT source when create_branch=True
    max_count       → maximum number of log entries (0 = no limit)
    create_branch   → True to create the branch during CHECKOUT (-b flag)
    metadata        → sorted tuple of additional key-value pairs from the payload

    Immutable after construction.
    """

    request_id: str
    operation_id: str
    operation: GitOperation
    repository_path: str
    files: tuple[str, ...]      # empty = all tracked changes (ADD only)
    message: str                # COMMIT message or TAG annotation
    branch_name: str            # BRANCH (create) or CHECKOUT target
    ref: str                    # LOG start, DIFF comparison, TAG target, CHECKOUT source
    max_count: int              # LOG limit (0 = unlimited)
    create_branch: bool         # CHECKOUT: create the branch before switching
    metadata: tuple[tuple[str, str], ...]   # sorted (key, value) pairs


# ── GitResult ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GitResult:
    """Immutable result of a single completed git operation.

    Produced by the Git Adapter after the git subprocess completes and
    before it is wrapped in a GitExecutionResult.

    operation       → the operation that was performed
    repository_path → workspace-relative path of the repository
    output          → combined stdout from the git command (stderr on failure)
    return_code     → git process exit code (0 = success)
    metadata        → sorted tuple of adapter-level key-value pairs

    Immutable after construction.
    """

    operation: GitOperation
    repository_path: str
    output: str
    return_code: int
    metadata: tuple[tuple[str, str], ...]   # sorted (key, value) pairs


# ── GitValidationResult ────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GitValidationResult:
    """Immutable outcome of adapter-level validation for a git request.

    This is ADAPTER-LEVEL validation only:
      - adapter_type must be ExecutionAdapter.GIT
      - action_id must map to a known GitOperation
      - repository_path must be non-empty
      - repository_path must be workspace-relative (no absolute paths, no traversal)
      - repository_path must point to a valid git repository
      - operation-specific fields must be valid (e.g. message for COMMIT)

    The adapter does NOT re-validate:
      - request_id / operation_id → Gateway already validated these
      - operation correctness     → OperationEngine is the authority

    valid=True  → all adapter preconditions met; errors is empty
    valid=False → one or more failures; errors is non-empty

    Immutable after construction.
    """

    valid: bool
    errors: tuple[str, ...]


# ── GitExecutionResult ─────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GitExecutionResult:
    """Immutable outcome of a complete Git Adapter execution cycle.

    Produced by GitAdapter.execute() and returned to the caller. Contains
    the complete record of translation, validation, and execution.

    Lifecycle states:
      success=True  → git_request populated, git_result populated
      success=False → error describes failure reason
        - git_request=None → failed before translation (validation failure)
        - git_request set  → failed after translation (git subprocess failure)
        - git_result=None  → always None when success=False

    request_id      → from the originating ExecutionRequest
    operation_id    → from the originating ExecutionRequest
    operation       → the GitOperation that was attempted
    git_request     → the normalized request (None if pre-translation failure)
    git_result      → the operation result (None on any failure)
    success         → True if the git operation completed successfully
    error           → error description if success=False; None otherwise
    adapter_metadata → sorted tuple of adapter-level key-value pairs

    Immutable after construction.
    """

    request_id: str
    operation_id: str
    operation: GitOperation | None       # None if action_id was unrecognized
    git_request: GitRequest | None       # None if failed before translation
    git_result: GitResult | None         # None on any failure
    success: bool
    error: str | None
    adapter_metadata: tuple[tuple[str, str], ...]   # sorted (key, value) pairs


# ── Public API ─────────────────────────────────────────────────────────────────


__all__ = [
    "GitExecutionResult",
    "GitOperation",
    "GitRepository",
    "GitRequest",
    "GitResult",
    "GitValidationResult",
]
