"""Git Adapter — controlled Git repository access for Hermes operations.

Architecture position:
  Execution Gateway → Git Adapter → Git (subprocess)

The Git Adapter is NOT Git. It translates Hermes execution contracts
(ExecutionRequest) into Git subprocess invocations and translates Git
results back into Hermes execution results (GitExecutionResult).

  Dispatch contract vs execution (invariant from Sprint 59):
    The Execution Gateway is the ONLY dispatch boundary. No engine, conductor,
    or planner may run git commands directly. All git access arrives as an
    ExecutionRequest that has already passed through the Gateway.

  Permanent architecture:
    Execution Gateway
          ↓
    Git Adapter      (this file — security + translation + execution)
          ↓
    git (subprocess) (list args only — shell=True is NEVER used)

Security model:
  Every request specifies a workspace-relative repository path. The adapter
  resolves all paths against the configured workspace_root and verifies the
  resolved path remains inside that root AND is a valid git repository.
  Any path that would escape the workspace — through ../, absolute paths, or
  symlink chains — is rejected before git is invoked.

  Subprocess security invariants:
    - subprocess.run is ALWAYS called with a list, NEVER shell=True.
    - Only pre-approved git subcommands are dispatched.
    - No arbitrary shell interpolation of user-supplied values.
    - Remote operations (push, pull, fetch, clone) are NOT in the dispatch table.

Operation semantics:
  STATUS   → git status --porcelain (current working tree state)
  ADD      → git add <files> or git add -A (stage changes)
  COMMIT   → git commit -m <message> (create a commit)
  BRANCH   → git branch (list) or git branch <name> (create)
  CHECKOUT → git checkout <branch> or git checkout -b <branch> [<ref>]
  TAG      → git tag <name> [<ref>] or git tag -a <name> -m <message> [<ref>]
  LOG      → git log --oneline [-n <max>] [<ref>]
  DIFF     → git diff [<ref>] [-- <path>]

Architecture invariants preserved:
  - The adapter never plans work.
  - The adapter never performs routing.
  - The adapter never calls LLMs, filesystems (beyond path resolution), HTTP.
  - The adapter never bypasses the Execution Gateway.
  - The adapter never runs remote Git operations (push, pull, fetch, clone).
  - The adapter never uses shell=True.
  - The adapter never raises exceptions — all failures are captured in
    GitExecutionResult(success=False, error=...).

Sources of non-determinism introduced (explicit):
  - Git repository state is external and may differ between calls.
  - Commit SHAs, timestamps, and author info are non-deterministic.
  - The adapter's translation logic is deterministic given the same inputs.

Network calls introduced:
  - None. Remote git operations are explicitly excluded.

Subprocess calls introduced:
  - _run_git() runs ["git", ...] via subprocess.run with shell=False.
  - resolve_repository() runs ["git", "rev-parse", "--git-dir"] to validate repos.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from hermes.models.execution_gateway import ExecutionAdapter, ExecutionRequest
from hermes.models.git_adapter import (
    GitExecutionResult,
    GitOperation,
    GitRequest,
    GitResult,
    GitValidationResult,
)

logger = logging.getLogger(__name__)

# Operations that require a non-empty message field
_MESSAGE_REQUIRED_OPERATIONS = frozenset({
    GitOperation.COMMIT,
})

# Map action_id strings → GitOperation enum values
_ACTION_TO_OPERATION: dict[str, GitOperation] = {
    op.value: op for op in GitOperation
}

# Remote operations that are explicitly forbidden — listed for documentation.
# These strings will never appear in _ACTION_TO_OPERATION and are blocked by
# the unrecognized action_id validation path.
_REMOTE_OPERATIONS = frozenset({"push", "pull", "fetch", "clone"})


class GitAdapter:
    """Controlled Git adapter for Hermes operations.

    Receives ExecutionRequests from the Execution Gateway, translates them
    into git subprocess invocations, executes within the configured workspace
    root, and returns immutable GitExecutionResults.

    Security guarantees:
      - No path may escape workspace_root (enforced by resolve_repository).
      - Only pre-approved git subcommands are dispatched.
      - subprocess.run is always called with a list (shell=False).
      - Remote operations are never dispatched.

    Usage::

        adapter = GitAdapter(workspace_root="/tmp/hermes-workspace")

        request = gateway.build_request(
            request_id="req-001",
            operation_id="op-git-status",
            adapter_type=ExecutionAdapter.GIT,
            action_id="status",
            payload={"repository_path": "my-repo"},
        )
        result = adapter.execute(request)
        # result.success → True if git status succeeded
        # result.git_result.output → porcelain status output
    """

    def __init__(self, workspace_root: str | Path) -> None:
        """Initialise the adapter with a fixed workspace root.

        Args:
            workspace_root: Absolute path to the workspace directory.
                            All git operations are confined to repositories
                            within this root. The directory must exist.
        """
        self._workspace_root = Path(workspace_root).resolve()

    @property
    def workspace_root(self) -> Path:
        """The resolved, absolute workspace root path."""
        return self._workspace_root

    # ── Path resolution ───────────────────────────────────────────────────────

    def resolve_path(self, relative_path: str) -> Path:
        """Resolve a workspace-relative path and verify it stays inside the root.

        Steps:
          1. Reject empty paths immediately.
          2. Reject paths that are absolute (start with /).
          3. Join workspace_root / relative_path.
          4. Resolve symlinks and ".." components.
          5. Verify the resolved path starts with workspace_root.

        Args:
            relative_path: A workspace-relative path string from the payload.

        Returns:
            Resolved absolute Path inside workspace_root.

        Raises:
            ValueError: If the path is empty, absolute, or escapes the workspace.
        """
        if not relative_path or not relative_path.strip():
            raise ValueError("path must not be empty")

        if Path(relative_path).is_absolute():
            raise ValueError(
                f"path must be workspace-relative, got absolute path: {relative_path!r}"
            )

        resolved = (self._workspace_root / relative_path).resolve()

        try:
            resolved.relative_to(self._workspace_root)
        except ValueError:
            raise ValueError(
                f"path traversal rejected: {relative_path!r} resolves outside workspace"
            )

        return resolved

    def _is_git_repository(self, path: Path) -> bool:
        """Check whether the given path is the root of a git repository.

        Runs ``git rev-parse --git-dir`` in the directory. Returns True only
        when git exits with code 0 (the directory is inside a git repository).

        This is a subprocess call intentionally limited to git's own validation
        mechanism — no parsing of directory structure is attempted.

        Args:
            path: Absolute path to a directory.

        Returns:
            True if the directory is (or is inside) a git repository.
        """
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                cwd=path,
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return False

    def resolve_repository(self, repository_path: str) -> Path:
        """Resolve and validate a workspace-relative repository path.

        Combines workspace boundary enforcement (resolve_path) with git
        repository validation (_is_git_repository). The resolved path must:
          1. Be non-empty and workspace-relative.
          2. Stay inside workspace_root (no traversal).
          3. Point to an existing directory.
          4. Be the root of a valid git repository.

        Args:
            repository_path: Workspace-relative path to the git repository.

        Returns:
            Resolved absolute Path to the git repository root.

        Raises:
            ValueError: If the path is invalid, escapes the workspace,
                        does not exist, is not a directory, or is not a
                        valid git repository.
        """
        resolved = self.resolve_path(repository_path)

        if not resolved.exists():
            raise ValueError(
                f"repository path does not exist: {repository_path!r}"
            )

        if not resolved.is_dir():
            raise ValueError(
                f"repository path is not a directory: {repository_path!r}"
            )

        if not self._is_git_repository(resolved):
            raise ValueError(
                f"repository path is not a valid git repository: {repository_path!r}"
            )

        return resolved

    # ── Validation ────────────────────────────────────────────────────────────

    def validate(
        self,
        request: ExecutionRequest,
        operation: GitOperation | None,
        repository_path: str,
    ) -> GitValidationResult:
        """Validate an ExecutionRequest at the adapter level.

        ADAPTER-LEVEL validation only. The Gateway has already validated
        request_id, operation_id. This checks adapter preconditions:

          1. adapter_type must be ExecutionAdapter.GIT
          2. operation must be a recognized GitOperation
          3. repository_path must be non-empty
          4. repository_path must be workspace-relative (no absolute, no traversal)
          5. repository_path must point to a valid git repository
          6. COMMIT operation must have a non-empty message in the payload

        Args:
            request:         The ExecutionRequest received from the Gateway.
            operation:       The resolved GitOperation (None if unrecognized).
            repository_path: The repository path string from the payload.

        Returns:
            GitValidationResult with valid=True if all checks pass.
        """
        errors: list[str] = []

        if request.adapter_type != ExecutionAdapter.GIT:
            errors.append(
                f"adapter_type must be GIT, got {request.adapter_type.value!r}"
            )

        if operation is None:
            errors.append(
                f"action_id {request.action_id!r} is not a recognized GitOperation; "
                f"valid values: {[op.value for op in GitOperation]}"
            )

        if not repository_path or not repository_path.strip():
            errors.append("payload 'repository_path' must not be empty")
        else:
            try:
                self.resolve_repository(repository_path)
            except ValueError as exc:
                errors.append(str(exc))

        # Operation-specific validation
        if operation in _MESSAGE_REQUIRED_OPERATIONS:
            payload_dict = dict(request.payload)
            message = payload_dict.get("message", "")
            if not message or not str(message).strip():
                errors.append(
                    f"{operation.value!r} operation requires a non-empty 'message' in payload"
                )

        return GitValidationResult(
            valid=len(errors) == 0,
            errors=tuple(errors),
        )

    # ── Request translation ───────────────────────────────────────────────────

    def build_git_request(
        self,
        request: ExecutionRequest,
        operation: GitOperation,
    ) -> GitRequest:
        """Translate an ExecutionRequest into a normalized GitRequest.

        Payload key extraction:
          "repository_path" → workspace-relative repository path (required)
          "files"           → list or tuple of file paths for ADD (optional)
          "message"         → commit message or tag annotation (optional)
          "branch_name"     → branch name for BRANCH/CHECKOUT (optional)
          "ref"             → git ref for LOG/DIFF/TAG/CHECKOUT (optional)
          "max_count"       → log entry limit as int or str (optional; 0 = unlimited)
          "create_branch"   → bool for CHECKOUT -b flag (optional; default False)

        Remaining payload keys are passed through as sorted metadata.

        Args:
            request:   The ExecutionRequest from the Execution Gateway.
            operation: The resolved GitOperation.

        Returns:
            GitRequest ready for execution.
        """
        payload_dict = dict(request.payload)

        repository_path = str(payload_dict.pop("repository_path", ""))

        # files: may be a list, tuple, or comma-separated string
        raw_files = payload_dict.pop("files", ())
        if isinstance(raw_files, str):
            files: tuple[str, ...] = tuple(f.strip() for f in raw_files.split(",") if f.strip())
        else:
            files = tuple(str(f) for f in raw_files)

        message = str(payload_dict.pop("message", ""))
        branch_name = str(payload_dict.pop("branch_name", ""))
        ref = str(payload_dict.pop("ref", ""))

        raw_max = payload_dict.pop("max_count", 0)
        try:
            max_count = int(raw_max)
        except (ValueError, TypeError):
            max_count = 0

        raw_create = payload_dict.pop("create_branch", False)
        if isinstance(raw_create, str):
            create_branch = raw_create.lower() in {"true", "1", "yes"}
        else:
            create_branch = bool(raw_create)

        # Remove redundant operation key if present
        payload_dict.pop("operation", None)

        metadata = tuple(sorted((k, str(v)) for k, v in payload_dict.items()))

        return GitRequest(
            request_id=request.request_id,
            operation_id=request.operation_id,
            operation=operation,
            repository_path=repository_path,
            files=files,
            message=message,
            branch_name=branch_name,
            ref=ref,
            max_count=max_count,
            create_branch=create_branch,
            metadata=metadata,
        )

    # ── Git subprocess runner ─────────────────────────────────────────────────

    def _run_git(
        self, repo_path: Path, args: list[str]
    ) -> tuple[str, str, int]:
        """Run a git command in the repository directory.

        Security invariants:
          - Called with a list, never a shell string.
          - shell=False is the default and is NOT overridden.
          - cwd is a resolved absolute path under workspace_root.
          - Timeout is fixed at 30 seconds.

        Args:
            repo_path: Resolved absolute path to the git repository root.
            args:      Argument list for git (subcommand + flags + operands).

        Returns:
            Tuple of (stdout, stderr, return_code).
        """
        try:
            result = subprocess.run(
                ["git"] + args,
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=30,
            )
            return result.stdout, result.stderr, result.returncode
        except subprocess.TimeoutExpired:
            return "", "git command timed out after 30 seconds", 1
        except (FileNotFoundError, OSError) as exc:
            return "", f"git is not available: {exc}", 1

    # ── Git operation handlers ────────────────────────────────────────────────

    def _status(self, repo_path: Path, git_request: GitRequest) -> GitResult:
        """Run git status --porcelain and return the output."""
        stdout, stderr, code = self._run_git(repo_path, ["status", "--porcelain"])
        output = stdout if code == 0 else stderr
        return GitResult(
            operation=GitOperation.STATUS,
            repository_path=git_request.repository_path,
            output=output,
            return_code=code,
            metadata=(),
        )

    def _add(self, repo_path: Path, git_request: GitRequest) -> GitResult:
        """Stage files. Empty files tuple stages all tracked changes (-A)."""
        if git_request.files:
            args = ["add", "--"] + list(git_request.files)
        else:
            args = ["add", "-A"]
        stdout, stderr, code = self._run_git(repo_path, args)
        output = stdout if code == 0 else stderr
        return GitResult(
            operation=GitOperation.ADD,
            repository_path=git_request.repository_path,
            output=output,
            return_code=code,
            metadata=(),
        )

    def _commit(self, repo_path: Path, git_request: GitRequest) -> GitResult:
        """Create a commit with the given message."""
        args = ["commit", "-m", git_request.message]
        stdout, stderr, code = self._run_git(repo_path, args)
        output = stdout if code == 0 else stderr
        return GitResult(
            operation=GitOperation.COMMIT,
            repository_path=git_request.repository_path,
            output=output,
            return_code=code,
            metadata=(),
        )

    def _branch(self, repo_path: Path, git_request: GitRequest) -> GitResult:
        """List branches (branch_name empty) or create a new branch."""
        if git_request.branch_name:
            args = ["branch", git_request.branch_name]
        else:
            args = ["branch"]
        stdout, stderr, code = self._run_git(repo_path, args)
        output = stdout if code == 0 else stderr
        return GitResult(
            operation=GitOperation.BRANCH,
            repository_path=git_request.repository_path,
            output=output,
            return_code=code,
            metadata=(),
        )

    def _checkout(self, repo_path: Path, git_request: GitRequest) -> GitResult:
        """Switch branches, optionally creating the branch first.

        With create_branch=True: git checkout -b <branch_name> [<ref>]
        Without:                 git checkout <branch_name>
        """
        if git_request.create_branch:
            args = ["checkout", "-b", git_request.branch_name]
            if git_request.ref:
                args.append(git_request.ref)
        else:
            target = git_request.branch_name or git_request.ref
            args = ["checkout", target]
        stdout, stderr, code = self._run_git(repo_path, args)
        output = stdout if code == 0 else stderr
        return GitResult(
            operation=GitOperation.CHECKOUT,
            repository_path=git_request.repository_path,
            output=output,
            return_code=code,
            metadata=(),
        )

    def _tag(self, repo_path: Path, git_request: GitRequest) -> GitResult:
        """Create a lightweight or annotated tag.

        With message:    git tag -a <tag_name> [-m <message>] [<ref>]
        Without message: git tag <tag_name> [<ref>]
        """
        if git_request.message:
            args = ["tag", "-a", git_request.branch_name, "-m", git_request.message]
        else:
            args = ["tag", git_request.branch_name]
        if git_request.ref:
            args.append(git_request.ref)
        stdout, stderr, code = self._run_git(repo_path, args)
        output = stdout if code == 0 else stderr
        return GitResult(
            operation=GitOperation.TAG,
            repository_path=git_request.repository_path,
            output=output,
            return_code=code,
            metadata=(),
        )

    def _log(self, repo_path: Path, git_request: GitRequest) -> GitResult:
        """Retrieve commit history.

        git log --oneline [-n <max_count>] [<ref>]
        """
        args = ["log", "--oneline"]
        if git_request.max_count > 0:
            args += ["-n", str(git_request.max_count)]
        if git_request.ref:
            args.append(git_request.ref)
        stdout, stderr, code = self._run_git(repo_path, args)
        output = stdout if code == 0 else stderr
        return GitResult(
            operation=GitOperation.LOG,
            repository_path=git_request.repository_path,
            output=output,
            return_code=code,
            metadata=(),
        )

    def _diff(self, repo_path: Path, git_request: GitRequest) -> GitResult:
        """Show changes between the working tree and a ref.

        git diff [<ref>] [-- <files>]
        """
        args = ["diff"]
        if git_request.ref:
            args.append(git_request.ref)
        if git_request.files:
            args += ["--"] + list(git_request.files)
        stdout, stderr, code = self._run_git(repo_path, args)
        output = stdout if code == 0 else stderr
        return GitResult(
            operation=GitOperation.DIFF,
            repository_path=git_request.repository_path,
            output=output,
            return_code=code,
            metadata=(),
        )

    _OPERATION_HANDLERS = {
        GitOperation.STATUS: _status,
        GitOperation.ADD: _add,
        GitOperation.COMMIT: _commit,
        GitOperation.BRANCH: _branch,
        GitOperation.CHECKOUT: _checkout,
        GitOperation.TAG: _tag,
        GitOperation.LOG: _log,
        GitOperation.DIFF: _diff,
    }

    # ── Execution ─────────────────────────────────────────────────────────────

    def execute(self, request: ExecutionRequest) -> GitExecutionResult:
        """Execute a git request through the adapter.

        Execution steps:
          1. Extract operation from request.action_id.
          2. Extract repository_path from request.payload.
          3. Validate at the adapter level.
             → Returns success=False if validation fails.
          4. Build the normalized GitRequest.
          5. Resolve the workspace-relative repository path.
          6. Execute the git operation via the operation handler.
             → Returns success=False on git subprocess failure (non-zero exit).
          7. Return GitExecutionResult with success=True.

        The adapter never raises exceptions — all failures are captured in
        GitExecutionResult(success=False, error=...).

        Args:
            request: The ExecutionRequest from the Execution Gateway.

        Returns:
            GitExecutionResult capturing the full execution outcome.
        """
        payload_dict = dict(request.payload)
        repository_path = str(payload_dict.get("repository_path", ""))

        # Resolve action_id → GitOperation (may be None)
        operation = _ACTION_TO_OPERATION.get(request.action_id)

        adapter_meta_base: dict[str, str] = {
            "action": request.action_id,
            "operation": operation.value if operation is not None else "unknown",
            "repository_path": repository_path,
        }

        # ── 1–3. Validate ──────────────────────────────────────────────────
        validation = self.validate(request, operation, repository_path)
        if not validation.valid:
            logger.warning(
                "GitAdapter: validation failed for request_id=%r: %r",
                request.request_id,
                validation.errors,
            )
            return GitExecutionResult(
                request_id=request.request_id,
                operation_id=request.operation_id,
                operation=operation,
                git_request=None,
                git_result=None,
                success=False,
                error="; ".join(validation.errors),
                adapter_metadata=tuple(sorted(adapter_meta_base.items())),
            )

        # ── 4. Build normalized request ────────────────────────────────────
        assert operation is not None  # guaranteed by validation
        git_request = self.build_git_request(request, operation)

        # ── 5. Resolve repository path ─────────────────────────────────────
        try:
            repo_path = self.resolve_repository(git_request.repository_path)
        except ValueError as exc:
            error_msg = f"repository_resolution_failed: {exc}"
            return GitExecutionResult(
                request_id=request.request_id,
                operation_id=request.operation_id,
                operation=operation,
                git_request=git_request,
                git_result=None,
                success=False,
                error=error_msg,
                adapter_metadata=tuple(sorted(adapter_meta_base.items())),
            )

        # ── 6. Execute git operation ───────────────────────────────────────
        handler = self._OPERATION_HANDLERS[operation]
        try:
            git_result = handler(self, repo_path, git_request)
        except Exception as exc:
            error_msg = f"git_operation_failed: {type(exc).__name__}: {exc}"
            logger.warning(
                "GitAdapter: operation %r failed for request_id=%r: %s",
                operation.value,
                request.request_id,
                error_msg,
            )
            return GitExecutionResult(
                request_id=request.request_id,
                operation_id=request.operation_id,
                operation=operation,
                git_request=git_request,
                git_result=None,
                success=False,
                error=error_msg,
                adapter_metadata=tuple(sorted(adapter_meta_base.items())),
            )

        # ── 7. Check git exit code ─────────────────────────────────────────
        if git_result.return_code != 0:
            error_msg = (
                f"git_{operation.value}_failed (exit {git_result.return_code}): "
                f"{git_result.output.strip()}"
            )
            logger.warning(
                "GitAdapter: git %r exited %d for request_id=%r",
                operation.value,
                git_result.return_code,
                request.request_id,
            )
            return GitExecutionResult(
                request_id=request.request_id,
                operation_id=request.operation_id,
                operation=operation,
                git_request=git_request,
                git_result=git_result,
                success=False,
                error=error_msg,
                adapter_metadata=tuple(sorted(adapter_meta_base.items())),
            )

        # ── 8. Assemble success result ─────────────────────────────────────
        logger.info(
            "GitAdapter: %r complete request_id=%r repo=%r",
            operation.value,
            request.request_id,
            git_request.repository_path,
        )

        adapter_meta = dict(adapter_meta_base)
        adapter_meta["return_code"] = str(git_result.return_code)
        adapter_meta["output_lines"] = str(git_result.output.count("\n"))

        return GitExecutionResult(
            request_id=request.request_id,
            operation_id=request.operation_id,
            operation=operation,
            git_request=git_request,
            git_result=git_result,
            success=True,
            error=None,
            adapter_metadata=tuple(sorted(adapter_meta.items())),
        )
