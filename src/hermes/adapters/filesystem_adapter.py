"""Filesystem Adapter — controlled filesystem access for Hermes operations.

Architecture position:
  Execution Gateway → Filesystem Adapter → Filesystem

The Filesystem Adapter is NOT the filesystem. It translates Hermes execution
contracts (ExecutionRequest) into filesystem operations and translates
filesystem results back into Hermes execution results (FilesystemExecutionResult).

  Dispatch contract vs execution (invariant from Sprint 59):
    The Execution Gateway is the ONLY dispatch boundary. No engine, conductor,
    or planner may write files directly. All filesystem access arrives as an
    ExecutionRequest that has already passed through the Gateway.

  Permanent architecture:
    Execution Gateway
          ↓
    Filesystem Adapter   (this file — security + translation + execution)
          ↓
    Filesystem           (Python stdlib: pathlib, shutil)

Security model:
  Every request specifies a workspace-relative path. The adapter resolves all
  paths against the configured workspace_root and verifies the resolved path
  remains inside that root. Any path that would escape the workspace — whether
  through ../, absolute paths, or symlink chains — is rejected before the
  filesystem is touched.

  Rejected at validation time (before any filesystem contact):
    - Empty paths
    - Absolute paths (starting with /)
    - Traversal sequences (resolved path outside workspace_root)

Operation semantics:
  CREATE_FILE      → create a new file with given content; fail if exists
  OVERWRITE_FILE   → write content to file; create if not exists; replace if exists
  APPEND_FILE      → append content to existing file; fail if not exists
  READ_FILE        → read and return file contents as text
  DELETE_FILE      → delete a file; fail if not exists
  CREATE_DIRECTORY → create directory and any missing parents (idempotent)
  DELETE_DIRECTORY → delete directory and all contents (recursive); fail if not exists
  EXISTS           → check whether a path exists; never fails

Architecture invariants preserved:
  - The adapter never plans work.
  - The adapter never performs routing.
  - The adapter never calls LLMs, Git, Docker, HTTP, or shell commands.
  - The adapter never bypasses the Execution Gateway.
  - The adapter never raises exceptions — all failures are captured in
    FilesystemExecutionResult(success=False, error=...).

Sources of non-determinism introduced (explicit):
  - Filesystem state is external and may differ between calls.
  - File timestamps and OS-level metadata are non-deterministic.
  - The adapter's translation logic (build_filesystem_request, resolve_path)
    is deterministic given the same inputs and workspace state.

Filesystem writes introduced:
  - CREATE_FILE, OVERWRITE_FILE, APPEND_FILE, DELETE_FILE,
    CREATE_DIRECTORY, DELETE_DIRECTORY write to the filesystem.
  - READ_FILE and EXISTS are read-only.

Network calls introduced:
  - None.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from hermes.models.execution_gateway import ExecutionAdapter, ExecutionRequest
from hermes.models.filesystem_adapter import (
    FilesystemExecutionResult,
    FilesystemOperation,
    FilesystemRequest,
    FilesystemResult,
    FilesystemValidationResult,
)

logger = logging.getLogger(__name__)

# Operations that require content in the payload (empty string is valid content)
_WRITE_OPERATIONS = frozenset({
    FilesystemOperation.CREATE_FILE,
    FilesystemOperation.OVERWRITE_FILE,
    FilesystemOperation.MODIFY_FILE,
    FilesystemOperation.APPEND_FILE,
})

# Operations that act on files (not directories)
_FILE_OPERATIONS = frozenset({
    FilesystemOperation.CREATE_FILE,
    FilesystemOperation.OVERWRITE_FILE,
    FilesystemOperation.MODIFY_FILE,
    FilesystemOperation.APPEND_FILE,
    FilesystemOperation.READ_FILE,
    FilesystemOperation.DELETE_FILE,
})

# Map action_id strings → FilesystemOperation enum values
_ACTION_TO_OPERATION: dict[str, FilesystemOperation] = {
    op.value: op for op in FilesystemOperation
}


class FilesystemAdapter:
    """Controlled filesystem adapter for Hermes operations.

    Receives ExecutionRequests from the Execution Gateway, translates them
    into filesystem operations, executes within the configured workspace root,
    and returns immutable FilesystemExecutionResults.

    Security guarantee: no path may escape workspace_root. The resolve_path()
    method enforces this and is called before every filesystem operation.

    Registration model:
      The adapter is stateless beyond workspace_root. It can be re-used for
      any number of requests without reset.

    Usage::

        adapter = FilesystemAdapter(workspace_root="/tmp/hermes-workspace")

        request = gateway.build_request(
            request_id="req-001",
            operation_id="op-write",
            adapter_type=ExecutionAdapter.FILESYSTEM,
            action_id="create_file",
            payload={"path": "output/draft.md", "content": "# Draft"},
        )
        result = adapter.execute(request)
        # result.success → True if file was created
        # result.filesystem_result.bytes_written → bytes written
    """

    def __init__(self, workspace_root: str | Path) -> None:
        """Initialise the adapter with a fixed workspace root.

        Args:
            workspace_root: Absolute path to the workspace directory.
                            All filesystem operations are confined to this root.
                            The directory is not created automatically.
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

        # Resolve without requiring the path to exist (strict=False)
        resolved = (self._workspace_root / relative_path).resolve()

        # Verify the resolved path is inside the workspace root
        try:
            resolved.relative_to(self._workspace_root)
        except ValueError:
            raise ValueError(
                f"path traversal rejected: {relative_path!r} resolves outside workspace"
            )

        return resolved

    # ── Validation ────────────────────────────────────────────────────────────

    def validate(
        self,
        request: ExecutionRequest,
        operation: FilesystemOperation | None,
        path: str,
        encoding: str,
    ) -> FilesystemValidationResult:
        """Validate an ExecutionRequest at the adapter level.

        ADAPTER-LEVEL validation only. The Gateway has already validated
        request_id, operation_id. This checks adapter preconditions:

          1. adapter_type must be ExecutionAdapter.FILESYSTEM
          2. operation must be a recognized FilesystemOperation
          3. path must be non-empty
          4. path must be workspace-relative (no absolute paths, no traversal)
          5. encoding must be non-empty

        Args:
            request:   The ExecutionRequest received from the Gateway.
            operation: The resolved FilesystemOperation (None if unrecognized).
            path:      The target path string from the payload.
            encoding:  The encoding string from the payload or default.

        Returns:
            FilesystemValidationResult with valid=True if all checks pass.
        """
        errors: list[str] = []

        if request.adapter_type != ExecutionAdapter.FILESYSTEM:
            errors.append(
                f"adapter_type must be FILESYSTEM, got {request.adapter_type.value!r}"
            )

        if operation is None:
            errors.append(
                f"action_id {request.action_id!r} is not a recognized FilesystemOperation; "
                f"valid values: {[op.value for op in FilesystemOperation]}"
            )

        if not path or not path.strip():
            errors.append("payload 'path' must not be empty")
        else:
            # Verify path does not escape workspace
            try:
                self.resolve_path(path)
            except ValueError as exc:
                errors.append(str(exc))

        if not encoding or not encoding.strip():
            errors.append("encoding must not be empty")

        return FilesystemValidationResult(
            valid=len(errors) == 0,
            errors=tuple(errors),
        )

    # ── Request translation ───────────────────────────────────────────────────

    def build_filesystem_request(
        self,
        request: ExecutionRequest,
        operation: FilesystemOperation,
    ) -> FilesystemRequest:
        """Translate an ExecutionRequest into a normalized FilesystemRequest.

        Payload key extraction:
          "path"     → workspace-relative target path (required)
          "content"  → content to write (optional; empty string for non-write ops)
          "encoding" → text encoding (optional; default "utf-8")

        Remaining payload keys are passed through as sorted metadata.

        Args:
            request:   The ExecutionRequest from the Execution Gateway.
            operation: The resolved FilesystemOperation.

        Returns:
            FilesystemRequest ready for execution.
        """
        payload_dict = dict(request.payload)

        path = payload_dict.pop("path", "")
        content = payload_dict.pop("content", "")
        encoding = payload_dict.pop("encoding", "utf-8") or "utf-8"
        # Remove action/operation key if present (redundant with operation param)
        payload_dict.pop("operation", None)

        metadata = tuple(sorted(payload_dict.items()))

        return FilesystemRequest(
            request_id=request.request_id,
            operation_id=request.operation_id,
            operation=operation,
            path=path,
            content=content,
            encoding=encoding,
            metadata=metadata,
        )

    # ── Filesystem operations ─────────────────────────────────────────────────

    def _create_file(
        self, resolved: Path, fs_request: FilesystemRequest
    ) -> FilesystemResult:
        """Create a new file. Fails if the file already exists."""
        if resolved.exists():
            raise FileExistsError(
                f"create_file failed: file already exists: {fs_request.path!r}"
            )
        resolved.parent.mkdir(parents=True, exist_ok=True)
        encoded = fs_request.content.encode(fs_request.encoding)
        resolved.write_bytes(encoded)
        return FilesystemResult(
            operation=FilesystemOperation.CREATE_FILE,
            path=fs_request.path,
            content="",
            exists=True,
            bytes_written=len(encoded),
            bytes_read=0,
            metadata=(),
        )

    def _overwrite_file(
        self, resolved: Path, fs_request: FilesystemRequest
    ) -> FilesystemResult:
        """Write content to a file, creating or replacing it."""
        resolved.parent.mkdir(parents=True, exist_ok=True)
        encoded = fs_request.content.encode(fs_request.encoding)
        resolved.write_bytes(encoded)
        return FilesystemResult(
            operation=FilesystemOperation.OVERWRITE_FILE,
            path=fs_request.path,
            content="",
            exists=True,
            bytes_written=len(encoded),
            bytes_read=0,
            metadata=(),
        )

    def _modify_file(
        self, resolved: Path, fs_request: FilesystemRequest
    ) -> FilesystemResult:
        """Write content to an existing file. Fails if the file does not exist."""
        if not resolved.exists():
            raise FileNotFoundError(
                f"modify_file failed: file does not exist: {fs_request.path!r}"
            )
        encoded = fs_request.content.encode(fs_request.encoding)
        resolved.write_bytes(encoded)
        return FilesystemResult(
            operation=FilesystemOperation.MODIFY_FILE,
            path=fs_request.path,
            content="",
            exists=True,
            bytes_written=len(encoded),
            bytes_read=0,
            metadata=(),
        )

    def _append_file(
        self, resolved: Path, fs_request: FilesystemRequest
    ) -> FilesystemResult:
        """Append content to an existing file. Fails if the file does not exist."""
        if not resolved.exists():
            raise FileNotFoundError(
                f"append_file failed: file does not exist: {fs_request.path!r}"
            )
        encoded = fs_request.content.encode(fs_request.encoding)
        with resolved.open("ab") as fh:
            fh.write(encoded)
        return FilesystemResult(
            operation=FilesystemOperation.APPEND_FILE,
            path=fs_request.path,
            content="",
            exists=True,
            bytes_written=len(encoded),
            bytes_read=0,
            metadata=(),
        )

    def _read_file(
        self, resolved: Path, fs_request: FilesystemRequest
    ) -> FilesystemResult:
        """Read and return the contents of a file."""
        if not resolved.exists():
            raise FileNotFoundError(
                f"read_file failed: file does not exist: {fs_request.path!r}"
            )
        raw = resolved.read_bytes()
        content = raw.decode(fs_request.encoding)
        return FilesystemResult(
            operation=FilesystemOperation.READ_FILE,
            path=fs_request.path,
            content=content,
            exists=True,
            bytes_written=0,
            bytes_read=len(raw),
            metadata=(),
        )

    def _delete_file(
        self, resolved: Path, fs_request: FilesystemRequest
    ) -> FilesystemResult:
        """Delete a file. Fails if the file does not exist."""
        if not resolved.exists():
            raise FileNotFoundError(
                f"delete_file failed: file does not exist: {fs_request.path!r}"
            )
        resolved.unlink()
        return FilesystemResult(
            operation=FilesystemOperation.DELETE_FILE,
            path=fs_request.path,
            content="",
            exists=False,
            bytes_written=0,
            bytes_read=0,
            metadata=(),
        )

    def _create_directory(
        self, resolved: Path, fs_request: FilesystemRequest
    ) -> FilesystemResult:
        """Create a directory and any missing parents (idempotent)."""
        resolved.mkdir(parents=True, exist_ok=True)
        return FilesystemResult(
            operation=FilesystemOperation.CREATE_DIRECTORY,
            path=fs_request.path,
            content="",
            exists=True,
            bytes_written=0,
            bytes_read=0,
            metadata=(),
        )

    def _delete_directory(
        self, resolved: Path, fs_request: FilesystemRequest
    ) -> FilesystemResult:
        """Delete a directory and all its contents. Fails if it does not exist."""
        if not resolved.exists():
            raise FileNotFoundError(
                f"delete_directory failed: directory does not exist: {fs_request.path!r}"
            )
        shutil.rmtree(resolved)
        return FilesystemResult(
            operation=FilesystemOperation.DELETE_DIRECTORY,
            path=fs_request.path,
            content="",
            exists=False,
            bytes_written=0,
            bytes_read=0,
            metadata=(),
        )

    def _exists(
        self, resolved: Path, fs_request: FilesystemRequest
    ) -> FilesystemResult:
        """Check whether a path exists. Never fails."""
        exists = resolved.exists()
        return FilesystemResult(
            operation=FilesystemOperation.EXISTS,
            path=fs_request.path,
            content="",
            exists=exists,
            bytes_written=0,
            bytes_read=0,
            metadata=(),
        )

    _OPERATION_HANDLERS = {
        FilesystemOperation.CREATE_FILE: _create_file,
        FilesystemOperation.OVERWRITE_FILE: _overwrite_file,
        FilesystemOperation.MODIFY_FILE: _modify_file,
        FilesystemOperation.APPEND_FILE: _append_file,
        FilesystemOperation.READ_FILE: _read_file,
        FilesystemOperation.DELETE_FILE: _delete_file,
        FilesystemOperation.CREATE_DIRECTORY: _create_directory,
        FilesystemOperation.DELETE_DIRECTORY: _delete_directory,
        FilesystemOperation.EXISTS: _exists,
    }

    # ── Execution ─────────────────────────────────────────────────────────────

    def execute(self, request: ExecutionRequest) -> FilesystemExecutionResult:
        """Execute a filesystem request through the adapter.

        Execution steps:
          1. Extract operation from request.action_id.
          2. Extract path and encoding from request.payload.
          3. Validate at the adapter level.
             → Returns success=False if validation fails.
          4. Build the normalized FilesystemRequest.
          5. Resolve the workspace-relative path.
          6. Execute the filesystem operation via the operation handler.
             → Returns success=False on any IO or security failure.
          7. Return FilesystemExecutionResult with success=True.

        The adapter never raises exceptions — all failures are captured in
        FilesystemExecutionResult(success=False, error=...).

        Args:
            request: The ExecutionRequest from the Execution Gateway.

        Returns:
            FilesystemExecutionResult capturing the full execution outcome.
        """
        # Extract raw payload fields for validation
        payload_dict = dict(request.payload)
        path = payload_dict.get("path", "")
        encoding = payload_dict.get("encoding", "") or "utf-8"

        # Resolve action_id → FilesystemOperation (may be None)
        operation = _ACTION_TO_OPERATION.get(request.action_id)

        adapter_meta_base: dict[str, str] = {
            "action": request.action_id,
            "operation": operation.value if operation is not None else "unknown",
            "path": path,
        }

        # ── 1–3. Validate ──────────────────────────────────────────────────
        validation = self.validate(request, operation, path, encoding)
        if not validation.valid:
            logger.warning(
                "FilesystemAdapter: validation failed for request_id=%r: %r",
                request.request_id,
                validation.errors,
            )
            return FilesystemExecutionResult(
                request_id=request.request_id,
                operation_id=request.operation_id,
                operation=operation,
                filesystem_request=None,
                filesystem_result=None,
                success=False,
                error="; ".join(validation.errors),
                adapter_metadata=tuple(sorted(adapter_meta_base.items())),
            )

        # ── 4. Build normalized request ────────────────────────────────────
        assert operation is not None  # guaranteed by validation
        fs_request = self.build_filesystem_request(request, operation)

        # ── 5. Resolve path ────────────────────────────────────────────────
        try:
            resolved = self.resolve_path(fs_request.path)
        except ValueError as exc:
            error_msg = f"path_resolution_failed: {exc}"
            return FilesystemExecutionResult(
                request_id=request.request_id,
                operation_id=request.operation_id,
                operation=operation,
                filesystem_request=fs_request,
                filesystem_result=None,
                success=False,
                error=error_msg,
                adapter_metadata=tuple(sorted(adapter_meta_base.items())),
            )

        # ── 6. Execute filesystem operation ────────────────────────────────
        handler = self._OPERATION_HANDLERS[operation]
        try:
            fs_result = handler(self, resolved, fs_request)
        except Exception as exc:
            error_msg = f"filesystem_operation_failed: {type(exc).__name__}: {exc}"
            logger.warning(
                "FilesystemAdapter: operation %r failed for request_id=%r: %s",
                operation.value,
                request.request_id,
                error_msg,
            )
            return FilesystemExecutionResult(
                request_id=request.request_id,
                operation_id=request.operation_id,
                operation=operation,
                filesystem_request=fs_request,
                filesystem_result=None,
                success=False,
                error=error_msg,
                adapter_metadata=tuple(sorted(adapter_meta_base.items())),
            )

        # ── 7. Assemble result ─────────────────────────────────────────────
        logger.info(
            "FilesystemAdapter: %r complete request_id=%r path=%r",
            operation.value,
            request.request_id,
            fs_request.path,
        )

        adapter_meta = dict(adapter_meta_base)
        adapter_meta["bytes_written"] = str(fs_result.bytes_written)
        adapter_meta["bytes_read"] = str(fs_result.bytes_read)
        adapter_meta["exists"] = str(fs_result.exists).lower()

        return FilesystemExecutionResult(
            request_id=request.request_id,
            operation_id=request.operation_id,
            operation=operation,
            filesystem_request=fs_request,
            filesystem_result=fs_result,
            success=True,
            error=None,
            adapter_metadata=tuple(sorted(adapter_meta.items())),
        )
