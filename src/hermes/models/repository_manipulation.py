"""Repository Manipulation — typed contracts for deterministic repository change planning.

Architecture position:
  Repository Intelligence → Repository Manipulation → Filesystem Adapter

Repository Manipulation is NOT execution. It is NOT planning in the LLM sense.
It is the authoritative layer that validates, composes, and describes safe
repository changes as immutable contracts.

  What this layer produces:
    RepositoryManipulationPlan — an ordered, validated, conflict-free (or
    conflict-annotated) description of what changes should be applied to a
    repository. The plan is handed to the Filesystem Adapter for execution.

  What this layer does NOT do:
    - Write files
    - Execute Git
    - Call Docker
    - Invoke LLMs
    - Dispatch through the Execution Gateway

  Invariant: every contract in this module is immutable after construction.

All contracts use frozen=True and slots=True.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


# ── RepositoryOperationKind ───────────────────────────────────────────────────


class RepositoryOperationKind(enum.Enum):
    """Enumeration of supported repository manipulation operations.

    CREATE_FILE      → create a new file with given content
    MODIFY_FILE      → replace the content of an existing file
    DELETE_FILE      → remove a file from the repository
    RENAME_FILE      → rename a file within the same directory
    MOVE_FILE        → move a file to a different directory
    CREATE_DIRECTORY → create a directory (and any missing parents)
    DELETE_DIRECTORY → remove a directory (recursive flag controls safety)
    """

    CREATE_FILE = "create_file"
    MODIFY_FILE = "modify_file"
    DELETE_FILE = "delete_file"
    RENAME_FILE = "rename_file"
    MOVE_FILE = "move_file"
    CREATE_DIRECTORY = "create_directory"
    DELETE_DIRECTORY = "delete_directory"


# ── ConflictKind ──────────────────────────────────────────────────────────────


class ConflictKind(enum.Enum):
    """Enumeration of reasons a planned operation cannot be applied safely.

    PATH_ALREADY_EXISTS      → create_file target already exists (allow_overwrite=False)
    PATH_NOT_FOUND           → modify/delete/rename/move source does not exist
    DESTINATION_ALREADY_EXISTS → rename/move destination already exists
    DESTINATION_PARENT_MISSING → destination parent directory does not exist and
                                  will not be created by a preceding operation
    PATH_TRAVERSAL           → path resolves outside the workspace boundary
    INVALID_PATH             → path is empty, whitespace-only, or otherwise malformed
    DIRECTORY_NOT_EMPTY      → delete_directory target is not empty and recursive=False
    MISSING_DESTINATION      → rename/move operation has no destination specified
    """

    PATH_ALREADY_EXISTS = "path_already_exists"
    PATH_NOT_FOUND = "path_not_found"
    DESTINATION_ALREADY_EXISTS = "destination_already_exists"
    DESTINATION_PARENT_MISSING = "destination_parent_missing"
    PATH_TRAVERSAL = "path_traversal"
    INVALID_PATH = "invalid_path"
    DIRECTORY_NOT_EMPTY = "directory_not_empty"
    MISSING_DESTINATION = "missing_destination"


# ── RepositoryOperation ───────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RepositoryOperation:
    """Immutable description of a single intended repository change.

    All paths are repository-relative (never absolute, never containing '..').
    The engine validates paths against the workspace boundary before accepting
    any operation into a plan.

    operation_id  → caller-supplied identifier; used to correlate operations
                    with changes and conflicts in the resulting plan
    kind          → which operation to perform (RepositoryOperationKind)
    path          → repository-relative source path
    destination   → repository-relative destination path (rename/move only)
    content       → file content as a string (create_file/modify_file only)
    encoding      → encoding to declare for the content (default: "utf-8")
    allow_overwrite → if True, create_file overwrites an existing file
    recursive     → if True, delete_directory removes non-empty directories
    """

    operation_id: str
    kind: RepositoryOperationKind
    path: str
    destination: str | None = None
    content: str | None = None
    encoding: str = "utf-8"
    allow_overwrite: bool = False
    recursive: bool = False


# ── RepositoryChange ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RepositoryChange:
    """Immutable description of the filesystem effect a valid operation will produce.

    Produced for every operation that passes conflict detection. A change
    describes what WILL happen when the Filesystem Adapter executes the plan —
    without making it happen.

    operation_id  → links back to the RepositoryOperation that produced this change
    kind          → the operation kind
    path          → the source path affected
    destination   → the destination path (rename/move only; None otherwise)
    creates_path  → True if the operation will create a new path
    removes_path  → True if the operation will remove an existing path
    modifies_path → True if the operation will modify existing content in-place
    """

    operation_id: str
    kind: RepositoryOperationKind
    path: str
    destination: str | None
    creates_path: bool
    removes_path: bool
    modifies_path: bool


# ── RepositoryConflict ────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RepositoryConflict:
    """Immutable description of a conflict that prevents an operation from being applied.

    A conflict is not an error — it is a first-class output of the planning
    engine. Plans with conflicts are not invalid per se; they are not safe to
    execute. Callers decide what to do with conflicted plans.

    operation_id → links back to the RepositoryOperation that triggered this conflict
    kind         → the ConflictKind describing why the operation cannot be applied
    path         → the path involved in the conflict
    detail       → human-readable description of the specific conflict
    """

    operation_id: str
    kind: ConflictKind
    path: str
    detail: str


# ── RepositoryManipulationPlan ────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RepositoryManipulationPlan:
    """Immutable, ordered plan describing safe repository changes.

    The plan is the primary output of the RepositoryManipulation engine. It
    contains all input operations, the computed changes for valid operations,
    and any conflicts detected for invalid operations.

    A plan with conflict_count == 0 is safe to hand to the Filesystem Adapter.
    A plan with conflict_count > 0 must not be executed until resolved.

    plan_id          → deterministic identifier derived from repository_path and operations
    repository_path  → path to the repository root (as provided to the engine)
    planned_at       → ISO-8601 UTC timestamp of when the plan was produced
    operations       → the input operations, in the order they were provided
    changes          → one RepositoryChange per conflict-free operation (same order)
    conflicts        → one RepositoryConflict per invalid operation (same order)
    valid            → True iff conflict_count == 0
    operation_count  → len(operations)
    conflict_count   → len(conflicts)
    """

    plan_id: str
    repository_path: str
    planned_at: str
    operations: tuple[RepositoryOperation, ...]
    changes: tuple[RepositoryChange, ...]
    conflicts: tuple[RepositoryConflict, ...]
    valid: bool
    operation_count: int
    conflict_count: int


# ── RepositoryManipulationResult ──────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RepositoryManipulationResult:
    """Lightweight summary of a planning or validation run.

    Returned by RepositoryManipulation.validate() for callers that only need
    to know whether a set of operations is safe to apply, without needing the
    full plan detail.

    plan_id         → the plan_id that would be generated for this set of operations
    repository_path → the repository path that was validated
    valid           → True iff no conflicts were detected
    operation_count → total number of operations evaluated
    conflict_count  → number of operations that produced conflicts
    conflicts       → the full set of conflicts (for reporting and resolution)
    """

    plan_id: str
    repository_path: str
    valid: bool
    operation_count: int
    conflict_count: int
    conflicts: tuple[RepositoryConflict, ...]
