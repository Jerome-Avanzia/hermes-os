"""Repository Manipulation — deterministic engine for planning safe repository changes.

Architecture position:
  Repository Intelligence → Repository Manipulation → Filesystem Adapter

The RepositoryManipulation engine accepts an ordered list of RepositoryOperation
values, validates each against the workspace boundary and the simulated
repository state (real filesystem + effect of all preceding operations), and
returns an immutable RepositoryManipulationPlan.

  What this engine does:
    - Resolves all paths to absolute workspace-safe locations
    - Validates each operation in sequence, maintaining a simulated state that
      accounts for the effects of all preceding operations in the plan
    - Detects every category of conflict (path traversal, missing paths,
      existing paths, empty directory violations, missing destinations)
    - Produces an immutable plan with changes for valid operations and
      conflicts for invalid operations
    - Never raises — all errors are captured as conflicts

  What this engine does NOT do:
    - Write files
    - Delete files
    - Execute shell commands
    - Call Git, Docker, or any network service
    - Invoke LLMs
    - Dispatch through the Execution Gateway

Architecture invariant:
  The plan produced by this engine is the ONLY input the Filesystem Adapter
  needs to execute the change set. The Filesystem Adapter must not re-validate
  workspace boundaries — the plan already contains only safe, validated paths.

Usage:
  engine = RepositoryManipulation(workspace_root="/workspace")
  plan = engine.plan("my-repo", [op1, op2, op3])
  if plan.valid:
      # hand plan.changes to the Filesystem Adapter
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path

from hermes.models.repository_manipulation import (
    ConflictKind,
    RepositoryChange,
    RepositoryConflict,
    RepositoryManipulationPlan,
    RepositoryManipulationResult,
    RepositoryOperation,
    RepositoryOperationKind,
)


class RepositoryManipulation:
    """Deterministic engine that plans safe repository changes.

    The engine validates each operation in the order provided, simulating the
    cumulative effect of preceding operations so that intra-plan dependencies
    are correctly resolved (e.g. create_directory followed by create_file
    inside that directory is valid; the reverse order is a conflict).

    Usage:
        engine = RepositoryManipulation(workspace_root="/workspace")
        plan   = engine.plan("my-repo", operations)
        result = engine.validate("my-repo", operations)   # lightweight check
    """

    def __init__(self, workspace_root: str | Path) -> None:
        self._workspace_root = Path(workspace_root).resolve()

    # ── Public interface ──────────────────────────────────────────────────────

    def resolve_path(self, relative_path: str) -> Path:
        """Resolve a workspace-relative path, enforcing workspace boundary.

        Raises ValueError if the resolved path escapes workspace_root.
        """
        resolved = (self._workspace_root / relative_path).resolve()
        try:
            resolved.relative_to(self._workspace_root)
        except ValueError:
            raise ValueError(
                f"Path {relative_path!r} escapes workspace root {self._workspace_root}"
            )
        return resolved

    def plan(
        self,
        repository_path: str,
        operations: list[RepositoryOperation],
        *,
        plan_id: str | None = None,
        planned_at: str | None = None,
    ) -> RepositoryManipulationPlan:
        """Validate operations and return an immutable RepositoryManipulationPlan.

        Args:
            repository_path: Repository root, relative to workspace_root.
            operations:      Ordered list of intended changes to validate.
            plan_id:         Override the deterministic plan identifier.
            planned_at:      Override the ISO-8601 UTC timestamp.

        Returns:
            RepositoryManipulationPlan with changes for valid operations and
            conflicts for invalid operations. Never raises.
        """
        pid = plan_id or _make_plan_id(repository_path, operations)
        ts = planned_at or datetime.now(timezone.utc).isoformat(timespec="seconds")

        # Resolve repository root — fall back to absolute path if outside workspace
        try:
            repo_root = self.resolve_path(repository_path)
        except ValueError:
            repo_root = Path(repository_path).resolve()

        # Build initial simulated state from the real filesystem
        sim_files, sim_dirs = self._scan_initial_state(repo_root)

        changes: list[RepositoryChange] = []
        conflicts: list[RepositoryConflict] = []

        # ── Pre-validation: duplicate target detection ─────────────────────────
        # Flag operations of the SAME kind targeting the same path.
        # Two CREATE_FILE or two MODIFY_FILE ops on the same path indicate a
        # planner error. Sequential ops of DIFFERENT kinds (e.g. MODIFY then
        # DELETE) are valid and handled by the simulation loop below.
        seen_path_kinds: dict[tuple[str, str], str] = {}  # (path_norm, kind) → first op_id
        duplicate_conflicts: list[RepositoryConflict] = []
        duplicate_op_ids: set[str] = set()
        for op in operations:
            path_norm = _normalise_path(op.path)
            if path_norm is None:
                continue  # invalid path conflicts handled in simulation loop below
            key = (path_norm, op.kind.value)
            if key in seen_path_kinds:
                first_op_id = seen_path_kinds[key]
                duplicate_conflicts.append(RepositoryConflict(
                    operation_id=op.operation_id,
                    kind=ConflictKind.PATH_ALREADY_EXISTS,
                    path=path_norm,
                    detail=f"duplicate_target: {path_norm} already targeted by {first_op_id}",
                ))
                duplicate_op_ids.add(op.operation_id)
            else:
                seen_path_kinds[key] = op.operation_id

        for op in operations:
            if op.operation_id in duplicate_op_ids:
                continue  # already captured as a duplicate conflict; skip simulation
            change, conflict = self._process_operation(op, repo_root, sim_files, sim_dirs)
            if change is not None:
                changes.append(change)
            if conflict is not None:
                conflicts.append(conflict)

        all_conflicts = duplicate_conflicts + conflicts
        return RepositoryManipulationPlan(
            plan_id=pid,
            repository_path=repository_path,
            planned_at=ts,
            operations=tuple(operations),
            changes=tuple(changes),
            conflicts=tuple(all_conflicts),
            valid=len(all_conflicts) == 0,
            operation_count=len(operations),
            conflict_count=len(all_conflicts),
        )

    def validate(
        self,
        repository_path: str,
        operations: list[RepositoryOperation],
    ) -> RepositoryManipulationResult:
        """Validate operations and return a lightweight RepositoryManipulationResult.

        Equivalent to plan() but returns only the summary fields needed to
        determine whether the operation set is safe to apply.
        """
        full_plan = self.plan(repository_path, operations)
        return RepositoryManipulationResult(
            plan_id=full_plan.plan_id,
            repository_path=full_plan.repository_path,
            valid=full_plan.valid,
            operation_count=full_plan.operation_count,
            conflict_count=full_plan.conflict_count,
            conflicts=full_plan.conflicts,
        )

    # ── Filesystem state scan ─────────────────────────────────────────────────

    def _scan_initial_state(
        self, repo_root: Path
    ) -> tuple[set[str], set[str]]:
        """Scan the real filesystem and return (existing_files, existing_dirs).

        Both sets contain repo-relative forward-slash paths.
        Returns empty sets if repo_root does not exist.
        """
        if not repo_root.is_dir():
            return set(), set()

        files: set[str] = set()
        dirs: set[str] = set()

        for dirpath_str, dirnames, filenames in os.walk(str(repo_root)):
            dp = Path(dirpath_str)
            try:
                rel = dp.relative_to(repo_root)
            except ValueError:
                continue

            rel_str = _to_forward_slash(str(rel))
            if rel_str and rel_str != ".":
                dirs.add(rel_str)

            for filename in filenames:
                file_rel = _to_forward_slash(str(rel / filename)) if rel_str and rel_str != "." else filename
                files.add(file_rel)

        return files, dirs

    # ── Operation validation ──────────────────────────────────────────────────

    def _process_operation(
        self,
        op: RepositoryOperation,
        repo_root: Path,
        sim_files: set[str],
        sim_dirs: set[str],
    ) -> tuple[RepositoryChange | None, RepositoryConflict | None]:
        """Validate one operation against the simulated repository state.

        Updates sim_files and sim_dirs in-place to reflect this operation's
        effect, so subsequent operations in the same plan see accurate state.

        Returns (change, None) for valid operations or (None, conflict) for
        invalid operations. Never raises.
        """
        # ── Path validation ───────────────────────────────────────────────────
        path_norm = _normalise_path(op.path)
        if path_norm is None:
            return None, RepositoryConflict(
                operation_id=op.operation_id,
                kind=ConflictKind.INVALID_PATH,
                path=op.path,
                detail=f"Path {op.path!r} is empty or malformed",
            )

        if not self._is_safe_path(path_norm, repo_root):
            return None, RepositoryConflict(
                operation_id=op.operation_id,
                kind=ConflictKind.PATH_TRAVERSAL,
                path=path_norm,
                detail=f"Path {path_norm!r} resolves outside the workspace boundary",
            )

        kind = op.kind

        # ── CREATE_FILE ───────────────────────────────────────────────────────
        if kind == RepositoryOperationKind.CREATE_FILE:
            existed_before = path_norm in sim_files

            if existed_before and not op.allow_overwrite:
                return None, RepositoryConflict(
                    operation_id=op.operation_id,
                    kind=ConflictKind.PATH_ALREADY_EXISTS,
                    path=path_norm,
                    detail=f"File {path_norm!r} already exists; set allow_overwrite=True to replace it",
                )

            parent = _parent_dir(path_norm)
            if parent and parent not in sim_dirs:
                return None, RepositoryConflict(
                    operation_id=op.operation_id,
                    kind=ConflictKind.DESTINATION_PARENT_MISSING,
                    path=path_norm,
                    detail=f"Parent directory {parent!r} does not exist",
                )

            sim_files.add(path_norm)
            return RepositoryChange(
                operation_id=op.operation_id,
                kind=kind,
                path=path_norm,
                destination=None,
                creates_path=not existed_before,
                removes_path=False,
                modifies_path=existed_before,
            ), None

        # ── MODIFY_FILE ───────────────────────────────────────────────────────
        if kind == RepositoryOperationKind.MODIFY_FILE:
            if path_norm not in sim_files:
                return None, RepositoryConflict(
                    operation_id=op.operation_id,
                    kind=ConflictKind.PATH_NOT_FOUND,
                    path=path_norm,
                    detail=f"File {path_norm!r} does not exist",
                )

            # sim_files unchanged — file still exists after modification
            return RepositoryChange(
                operation_id=op.operation_id,
                kind=kind,
                path=path_norm,
                destination=None,
                creates_path=False,
                removes_path=False,
                modifies_path=True,
            ), None

        # ── DELETE_FILE ───────────────────────────────────────────────────────
        if kind == RepositoryOperationKind.DELETE_FILE:
            if path_norm not in sim_files:
                return None, RepositoryConflict(
                    operation_id=op.operation_id,
                    kind=ConflictKind.PATH_NOT_FOUND,
                    path=path_norm,
                    detail=f"File {path_norm!r} does not exist",
                )

            sim_files.discard(path_norm)
            return RepositoryChange(
                operation_id=op.operation_id,
                kind=kind,
                path=path_norm,
                destination=None,
                creates_path=False,
                removes_path=True,
                modifies_path=False,
            ), None

        # ── RENAME_FILE / MOVE_FILE ───────────────────────────────────────────
        if kind in (RepositoryOperationKind.RENAME_FILE, RepositoryOperationKind.MOVE_FILE):
            if not op.destination or not op.destination.strip():
                return None, RepositoryConflict(
                    operation_id=op.operation_id,
                    kind=ConflictKind.MISSING_DESTINATION,
                    path=path_norm,
                    detail=f"Operation {kind.value!r} requires a destination path",
                )

            dest_norm = _normalise_path(op.destination)
            if dest_norm is None:
                return None, RepositoryConflict(
                    operation_id=op.operation_id,
                    kind=ConflictKind.INVALID_PATH,
                    path=op.destination,
                    detail=f"Destination path {op.destination!r} is empty or malformed",
                )

            if not self._is_safe_path(dest_norm, repo_root):
                return None, RepositoryConflict(
                    operation_id=op.operation_id,
                    kind=ConflictKind.PATH_TRAVERSAL,
                    path=dest_norm,
                    detail=f"Destination {dest_norm!r} resolves outside the workspace boundary",
                )

            if path_norm not in sim_files:
                return None, RepositoryConflict(
                    operation_id=op.operation_id,
                    kind=ConflictKind.PATH_NOT_FOUND,
                    path=path_norm,
                    detail=f"Source file {path_norm!r} does not exist",
                )

            if dest_norm in sim_files:
                return None, RepositoryConflict(
                    operation_id=op.operation_id,
                    kind=ConflictKind.DESTINATION_ALREADY_EXISTS,
                    path=dest_norm,
                    detail=f"Destination {dest_norm!r} already exists",
                )

            dest_parent = _parent_dir(dest_norm)
            if dest_parent and dest_parent not in sim_dirs:
                return None, RepositoryConflict(
                    operation_id=op.operation_id,
                    kind=ConflictKind.DESTINATION_PARENT_MISSING,
                    path=dest_norm,
                    detail=f"Destination parent directory {dest_parent!r} does not exist",
                )

            sim_files.discard(path_norm)
            sim_files.add(dest_norm)
            return RepositoryChange(
                operation_id=op.operation_id,
                kind=kind,
                path=path_norm,
                destination=dest_norm,
                creates_path=True,
                removes_path=True,
                modifies_path=False,
            ), None

        # ── CREATE_DIRECTORY ──────────────────────────────────────────────────
        if kind == RepositoryOperationKind.CREATE_DIRECTORY:
            if path_norm in sim_dirs:
                return None, RepositoryConflict(
                    operation_id=op.operation_id,
                    kind=ConflictKind.PATH_ALREADY_EXISTS,
                    path=path_norm,
                    detail=f"Directory {path_norm!r} already exists",
                )

            # Register all implied parent directories in simulated state
            _add_dir_with_parents(path_norm, sim_dirs)
            return RepositoryChange(
                operation_id=op.operation_id,
                kind=kind,
                path=path_norm,
                destination=None,
                creates_path=True,
                removes_path=False,
                modifies_path=False,
            ), None

        # ── DELETE_DIRECTORY ──────────────────────────────────────────────────
        if kind == RepositoryOperationKind.DELETE_DIRECTORY:
            if path_norm not in sim_dirs:
                return None, RepositoryConflict(
                    operation_id=op.operation_id,
                    kind=ConflictKind.PATH_NOT_FOUND,
                    path=path_norm,
                    detail=f"Directory {path_norm!r} does not exist",
                )

            prefix = path_norm + "/"
            has_contents = any(
                f.startswith(prefix) for f in sim_files
            ) or any(
                d.startswith(prefix) for d in sim_dirs
            )

            if has_contents and not op.recursive:
                return None, RepositoryConflict(
                    operation_id=op.operation_id,
                    kind=ConflictKind.DIRECTORY_NOT_EMPTY,
                    path=path_norm,
                    detail=f"Directory {path_norm!r} is not empty; set recursive=True to delete recursively",
                )

            # Remove the directory and all its contents from simulated state
            sim_dirs.discard(path_norm)
            sim_dirs -= {d for d in sim_dirs if d.startswith(prefix)}
            sim_files -= {f for f in sim_files if f.startswith(prefix)}

            return RepositoryChange(
                operation_id=op.operation_id,
                kind=kind,
                path=path_norm,
                destination=None,
                creates_path=False,
                removes_path=True,
                modifies_path=False,
            ), None

        # ── Unknown operation kind (defensive) ────────────────────────────────
        return None, RepositoryConflict(
            operation_id=op.operation_id,
            kind=ConflictKind.INVALID_PATH,
            path=path_norm,
            detail=f"Unknown operation kind: {kind!r}",
        )

    # ── Path helpers ──────────────────────────────────────────────────────────

    def _is_safe_path(self, path_norm: str, repo_root: Path) -> bool:
        """Return True if path_norm resolves within workspace_root."""
        try:
            resolved = (repo_root / path_norm).resolve()
            resolved.relative_to(self._workspace_root)
            return True
        except ValueError:
            return False


# ── Module-level helpers ──────────────────────────────────────────────────────


def _normalise_path(raw: str) -> str | None:
    """Normalise a user-supplied path to a repo-relative forward-slash string.

    Returns None if the path is empty, whitespace-only, or reduces to nothing
    after stripping leading/trailing slashes and whitespace.
    """
    if not raw or not raw.strip():
        return None
    normalised = raw.strip().replace("\\", "/").strip("/")
    return normalised if normalised else None


def _parent_dir(path_norm: str) -> str:
    """Return the parent directory of a normalised path, or '' if at root level."""
    parent = _to_forward_slash(str(Path(path_norm).parent))
    return "" if parent == "." else parent


def _to_forward_slash(path: str) -> str:
    """Normalise OS path separators to forward slashes."""
    return path.replace("\\", "/")


def _add_dir_with_parents(dir_path: str, sim_dirs: set[str]) -> None:
    """Add dir_path and all its parent directories to sim_dirs."""
    parts = dir_path.split("/")
    for i in range(1, len(parts) + 1):
        sim_dirs.add("/".join(parts[:i]))


def _make_plan_id(repository_path: str, operations: list[RepositoryOperation]) -> str:
    """Return a 16-character deterministic hex identifier for this plan."""
    parts = [repository_path]
    for op in operations:
        parts.append(
            f"{op.operation_id}:{op.kind.value}:{op.path}"
            f":{op.destination or ''}:{op.allow_overwrite}:{op.recursive}"
        )
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


# ── Public API ────────────────────────────────────────────────────────────────

__all__ = ["RepositoryManipulation"]
