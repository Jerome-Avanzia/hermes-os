"""Filesystem Adapter — typed contracts for the Filesystem Adapter.

Architecture position:
  Execution Gateway → Filesystem Adapter → Filesystem

The Filesystem Adapter is NOT the filesystem. The adapter is an execution
adapter that translates Hermes execution contracts into filesystem operations
and translates filesystem results back into Hermes execution results.

  Dispatch contract vs execution (invariant from Sprint 59):
    The Gateway determines which adapter receives the request and whether
    dispatch is valid. The Filesystem Adapter performs execution outside
    the deterministic Hermes kernel. No engine ever writes files directly.

  Every filesystem operation follows this path:
    Execution Gateway → Filesystem Adapter → Filesystem

  Where filesystem-specific logic lives:
    FilesystemAdapter.build_filesystem_request → translates ExecutionRequest → FilesystemRequest
    FilesystemAdapter.execute                  → performs the operation and produces FilesystemExecutionResult
    FilesystemAdapter.resolve_path             → workspace boundary enforcement

Sprint 62 scope:
  Eight operations supported: create_file, overwrite_file, append_file,
  read_file, delete_file, create_directory, delete_directory, exists.
  All paths are workspace-relative. Absolute paths and path traversal
  are rejected at validation time — no operation reaches the filesystem.

All contracts in this module are immutable after construction.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


# ── FilesystemOperation ────────────────────────────────────────────────────────


class FilesystemOperation(enum.Enum):
    """Enumeration of supported filesystem operations.

    Each value is the string that maps to the ExecutionRequest.action_id
    for filesystem requests. The Filesystem Adapter uses the action_id to
    determine which operation to perform.

    CREATE_FILE      → create a new file; fail if the file already exists
    OVERWRITE_FILE   → write content to a file; create if not exists; replace if exists
    MODIFY_FILE      → write content to an existing file; fail if the file does not exist
    APPEND_FILE      → append content to an existing file; fail if not exists
    READ_FILE        → read and return file contents as text
    DELETE_FILE      → delete a file; fail if not exists
    CREATE_DIRECTORY → create a directory and any missing parent directories
    DELETE_DIRECTORY → delete a directory and all its contents (recursive)
    EXISTS           → check whether a path exists; never fails
    """

    CREATE_FILE = "create_file"
    OVERWRITE_FILE = "overwrite_file"
    MODIFY_FILE = "modify_file"
    APPEND_FILE = "append_file"
    READ_FILE = "read_file"
    DELETE_FILE = "delete_file"
    CREATE_DIRECTORY = "create_directory"
    DELETE_DIRECTORY = "delete_directory"
    EXISTS = "exists"


# ── FilesystemTarget ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class FilesystemTarget:
    """Immutable description of what a filesystem operation targets.

    Produced during request translation. Represents the workspace-relative
    path and operation before path resolution occurs.

    path      → workspace-relative path string (as provided in payload)
    operation → the FilesystemOperation to perform

    Immutable after construction.
    """

    path: str
    operation: FilesystemOperation


# ── FilesystemRequest ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class FilesystemRequest:
    """Immutable normalized Hermes-side representation of a filesystem request.

    Produced by FilesystemAdapter.build_filesystem_request() from an
    ExecutionRequest. Represents the canonical request before any filesystem
    operation occurs.

    All paths are workspace-relative at this stage. Absolute resolved paths
    are computed by the adapter at execution time and never stored here.

    Payload key extraction:
      "path"     → target path (workspace-relative)
      "content"  → content to write (for CREATE_FILE, OVERWRITE_FILE, APPEND_FILE)
      "encoding" → text encoding (optional; default "utf-8")

    request_id  → from ExecutionRequest.request_id
    operation_id → from ExecutionRequest.operation_id
    operation   → derived from ExecutionRequest.action_id (FilesystemOperation enum)
    path        → workspace-relative target path (from payload["path"])
    content     → content to write; empty string for read/delete/exists/mkdir
    encoding    → text encoding for read/write operations (default "utf-8")
    metadata    → sorted tuple of additional key-value pairs from the payload

    Immutable after construction.
    """

    request_id: str
    operation_id: str
    operation: FilesystemOperation
    path: str
    content: str
    encoding: str
    metadata: tuple[tuple[str, str], ...]   # sorted (key, value) pairs


# ── FilesystemResult ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class FilesystemResult:
    """Immutable result of a single completed filesystem operation.

    Produced by the Filesystem Adapter after the operation executes
    and before it is wrapped in a FilesystemExecutionResult.

    operation     → the operation that was performed
    path          → workspace-relative path of the target
    content       → file content (for READ_FILE; empty string otherwise)
    exists        → True if the target path exists after the operation
    bytes_written → bytes written (for CREATE_FILE, OVERWRITE_FILE, APPEND_FILE; 0 otherwise)
    bytes_read    → bytes read (for READ_FILE; 0 otherwise)
    metadata      → sorted tuple of adapter-level key-value pairs

    Immutable after construction.
    """

    operation: FilesystemOperation
    path: str
    content: str
    exists: bool
    bytes_written: int
    bytes_read: int
    metadata: tuple[tuple[str, str], ...]   # sorted (key, value) pairs


# ── FilesystemValidationResult ─────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class FilesystemValidationResult:
    """Immutable outcome of adapter-level validation for a filesystem request.

    This is ADAPTER-LEVEL validation only:
      - adapter_type must be ExecutionAdapter.FILESYSTEM
      - action_id must map to a known FilesystemOperation
      - path must be non-empty
      - path must be workspace-relative (no absolute paths, no traversal)
      - content must be present for write operations (empty string is valid)
      - encoding must be non-empty

    The adapter does NOT re-validate:
      - request_id / operation_id → Gateway already validated these
      - operation correctness     → OperationEngine is the authority

    valid=True  → all adapter preconditions met; errors is empty
    valid=False → one or more failures; errors is non-empty

    Immutable after construction.
    """

    valid: bool
    errors: tuple[str, ...]


# ── FilesystemExecutionResult ──────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class FilesystemExecutionResult:
    """Immutable outcome of a complete Filesystem Adapter execution cycle.

    Produced by FilesystemAdapter.execute() and returned to the caller.
    Contains the complete record of translation, validation, and execution.

    Lifecycle states:
      success=True  → filesystem_request populated, filesystem_result populated
      success=False → error describes failure reason
        - filesystem_request=None → failed before translation (validation failure)
        - filesystem_request set  → failed after translation (IO/security failure)
        - filesystem_result=None  → always None when success=False

    request_id          → from the originating ExecutionRequest
    operation_id        → from the originating ExecutionRequest
    operation           → the FilesystemOperation that was attempted
    filesystem_request  → the normalized request (None if pre-translation failure)
    filesystem_result   → the operation result (None on any failure)
    success             → True if the filesystem operation completed successfully
    error               → error description if success=False; None otherwise
    adapter_metadata    → sorted tuple of adapter-level key-value pairs

    Immutable after construction.
    """

    request_id: str
    operation_id: str
    operation: FilesystemOperation | None   # None if action_id was unrecognized
    filesystem_request: FilesystemRequest | None    # None if failed before translation
    filesystem_result: FilesystemResult | None      # None on any failure
    success: bool
    error: str | None
    adapter_metadata: tuple[tuple[str, str], ...]   # sorted (key, value) pairs


# ── Public API ─────────────────────────────────────────────────────────────────


__all__ = [
    "FilesystemExecutionResult",
    "FilesystemOperation",
    "FilesystemRequest",
    "FilesystemResult",
    "FilesystemTarget",
    "FilesystemValidationResult",
]
