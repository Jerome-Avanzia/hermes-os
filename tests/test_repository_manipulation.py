"""Tests for the Repository Manipulation engine.

Coverage:
  - Typed contracts (frozen, slots, immutability)
  - RepositoryManipulation init and resolve_path
  - plan() and validate() interface contracts
  - Every operation kind: create_file, modify_file, delete_file,
    rename_file, move_file, create_directory, delete_directory
  - Conflict detection: every ConflictKind variant
  - Intra-plan dependency resolution (simulated state carries forward)
  - Workspace boundary enforcement
  - Path traversal rejection
  - Determinism: identical inputs → identical plan_id and changes
  - No filesystem writes during plan/validate
  - No subprocess or network access
  - RepositoryManipulationPlan and RepositoryManipulationResult contracts
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from hermes.kernel.repository_manipulation import (
    RepositoryManipulation,
    _make_plan_id,
    _normalise_path,
    _parent_dir,
)
from hermes.models.repository_manipulation import (
    ConflictKind,
    RepositoryChange,
    RepositoryConflict,
    RepositoryManipulationPlan,
    RepositoryManipulationResult,
    RepositoryOperation,
    RepositoryOperationKind,
)


# ── Fixtures / helpers ────────────────────────────────────────────────────────


def _op(
    operation_id: str,
    kind: RepositoryOperationKind,
    path: str,
    *,
    destination: str | None = None,
    content: str | None = None,
    allow_overwrite: bool = False,
    recursive: bool = False,
) -> RepositoryOperation:
    return RepositoryOperation(
        operation_id=operation_id,
        kind=kind,
        path=path,
        destination=destination,
        content=content,
        allow_overwrite=allow_overwrite,
        recursive=recursive,
    )


def _write(path: Path, content: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


CF = RepositoryOperationKind.CREATE_FILE
MF = RepositoryOperationKind.MODIFY_FILE
DF = RepositoryOperationKind.DELETE_FILE
RF = RepositoryOperationKind.RENAME_FILE
MV = RepositoryOperationKind.MOVE_FILE
CD = RepositoryOperationKind.CREATE_DIRECTORY
DD = RepositoryOperationKind.DELETE_DIRECTORY


# ── Contract tests ────────────────────────────────────────────────────────────


class TestContracts:
    def test_repository_operation_frozen(self):
        op = _op("op1", CF, "main.py")
        with pytest.raises((AttributeError, TypeError)):
            op.path = "other.py"  # type: ignore[misc]

    def test_repository_change_frozen(self):
        ch = RepositoryChange(
            operation_id="op1", kind=CF, path="main.py",
            destination=None, creates_path=True, removes_path=False, modifies_path=False,
        )
        with pytest.raises((AttributeError, TypeError)):
            ch.creates_path = False  # type: ignore[misc]

    def test_repository_conflict_frozen(self):
        cf = RepositoryConflict(
            operation_id="op1", kind=ConflictKind.PATH_NOT_FOUND,
            path="main.py", detail="not found",
        )
        with pytest.raises((AttributeError, TypeError)):
            cf.detail = "changed"  # type: ignore[misc]

    def test_manipulation_plan_frozen(self):
        plan = RepositoryManipulationPlan(
            plan_id="x", repository_path=".", planned_at="2026-01-01T00:00:00+00:00",
            operations=(), changes=(), conflicts=(),
            valid=True, operation_count=0, conflict_count=0,
        )
        with pytest.raises((AttributeError, TypeError)):
            plan.valid = False  # type: ignore[misc]

    def test_manipulation_result_frozen(self):
        result = RepositoryManipulationResult(
            plan_id="x", repository_path=".", valid=True,
            operation_count=0, conflict_count=0, conflicts=(),
        )
        with pytest.raises((AttributeError, TypeError)):
            result.valid = False  # type: ignore[misc]

    def test_repository_operation_has_slots(self):
        op = _op("op1", CF, "a.py")
        assert hasattr(type(op), "__slots__")

    def test_manipulation_plan_has_slots(self):
        plan = RepositoryManipulationPlan(
            plan_id="x", repository_path=".", planned_at="2026-01-01T00:00:00+00:00",
            operations=(), changes=(), conflicts=(),
            valid=True, operation_count=0, conflict_count=0,
        )
        assert hasattr(type(plan), "__slots__")

    def test_manipulation_result_has_slots(self):
        result = RepositoryManipulationResult(
            plan_id="x", repository_path=".", valid=True,
            operation_count=0, conflict_count=0, conflicts=(),
        )
        assert hasattr(type(result), "__slots__")

    def test_plan_operations_is_tuple(self, tmp_path):
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [])
        assert isinstance(plan.operations, tuple)

    def test_plan_changes_is_tuple(self, tmp_path):
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [])
        assert isinstance(plan.changes, tuple)

    def test_plan_conflicts_is_tuple(self, tmp_path):
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [])
        assert isinstance(plan.conflicts, tuple)

    def test_result_conflicts_is_tuple(self, tmp_path):
        engine = RepositoryManipulation(tmp_path)
        result = engine.validate("repo", [])
        assert isinstance(result.conflicts, tuple)

    def test_operation_default_encoding(self):
        op = _op("op1", CF, "a.py")
        assert op.encoding == "utf-8"

    def test_operation_default_allow_overwrite(self):
        op = _op("op1", CF, "a.py")
        assert op.allow_overwrite is False

    def test_operation_default_recursive(self):
        op = _op("op1", DD, "dir")
        assert op.recursive is False

    def test_conflict_kind_values_are_strings(self):
        for member in ConflictKind:
            assert isinstance(member.value, str)

    def test_operation_kind_values_are_strings(self):
        for member in RepositoryOperationKind:
            assert isinstance(member.value, str)


# ── Engine init ───────────────────────────────────────────────────────────────


class TestInit:
    def test_accepts_string_workspace_root(self, tmp_path):
        engine = RepositoryManipulation(str(tmp_path))
        assert engine._workspace_root == tmp_path.resolve()

    def test_accepts_path_workspace_root(self, tmp_path):
        engine = RepositoryManipulation(tmp_path)
        assert engine._workspace_root == tmp_path.resolve()

    def test_resolves_symlinks(self, tmp_path):
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real)
        engine = RepositoryManipulation(link)
        assert engine._workspace_root == real.resolve()


# ── resolve_path ──────────────────────────────────────────────────────────────


class TestResolvePath:
    def test_resolves_relative_path(self, tmp_path):
        engine = RepositoryManipulation(tmp_path)
        result = engine.resolve_path("my-repo")
        assert result == (tmp_path / "my-repo").resolve()

    def test_resolves_nested_path(self, tmp_path):
        engine = RepositoryManipulation(tmp_path)
        result = engine.resolve_path("a/b/c")
        assert result == (tmp_path / "a" / "b" / "c").resolve()

    def test_raises_on_path_traversal(self, tmp_path):
        engine = RepositoryManipulation(tmp_path)
        with pytest.raises(ValueError, match="escapes workspace root"):
            engine.resolve_path("../outside")

    def test_raises_on_absolute_outside(self, tmp_path):
        engine = RepositoryManipulation(tmp_path)
        with pytest.raises(ValueError, match="escapes workspace root"):
            engine.resolve_path("/etc/passwd")

    def test_dot_resolves_to_workspace_root(self, tmp_path):
        engine = RepositoryManipulation(tmp_path)
        assert engine.resolve_path(".") == tmp_path.resolve()


# ── plan() interface ──────────────────────────────────────────────────────────


class TestPlanInterface:
    def test_returns_manipulation_plan(self, tmp_path):
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [])
        assert isinstance(plan, RepositoryManipulationPlan)

    def test_empty_operations_valid_plan(self, tmp_path):
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [])
        assert plan.valid is True
        assert plan.operation_count == 0
        assert plan.conflict_count == 0

    def test_repository_path_preserved(self, tmp_path):
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("my-repo", [])
        assert plan.repository_path == "my-repo"

    def test_plan_id_override(self, tmp_path):
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [], plan_id="custom-id")
        assert plan.plan_id == "custom-id"

    def test_planned_at_override(self, tmp_path):
        engine = RepositoryManipulation(tmp_path)
        ts = "2026-01-01T00:00:00+00:00"
        plan = engine.plan("repo", [], planned_at=ts)
        assert plan.planned_at == ts

    def test_planned_at_is_iso8601(self, tmp_path):
        from datetime import datetime
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [])
        dt = datetime.fromisoformat(plan.planned_at)
        assert dt.tzinfo is not None

    def test_operation_count_matches_input(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "a.py")
        engine = RepositoryManipulation(tmp_path)
        ops = [_op("op1", MF, "a.py"), _op("op2", MF, "a.py")]
        plan = engine.plan("repo", ops)
        assert plan.operation_count == 2

    def test_operations_preserved_in_plan(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "a.py")
        engine = RepositoryManipulation(tmp_path)
        op = _op("op1", MF, "a.py")
        plan = engine.plan("repo", [op])
        assert plan.operations == (op,)

    def test_never_raises_on_missing_repo(self, tmp_path):
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("nonexistent-repo", [_op("op1", MF, "a.py")])
        # Should return a plan with a conflict (file not found) — not raise
        assert isinstance(plan, RepositoryManipulationPlan)


# ── validate() interface ──────────────────────────────────────────────────────


class TestValidateInterface:
    def test_returns_manipulation_result(self, tmp_path):
        engine = RepositoryManipulation(tmp_path)
        result = engine.validate("repo", [])
        assert isinstance(result, RepositoryManipulationResult)

    def test_valid_for_empty_operations(self, tmp_path):
        engine = RepositoryManipulation(tmp_path)
        result = engine.validate("repo", [])
        assert result.valid is True

    def test_plan_id_matches_plan(self, tmp_path):
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [], planned_at="2026-01-01T00:00:00+00:00")
        result = engine.validate("repo", [])
        assert result.plan_id == plan.plan_id

    def test_conflicts_match_plan(self, tmp_path):
        engine = RepositoryManipulation(tmp_path)
        op = _op("op1", MF, "missing.py")
        plan = engine.plan("repo", [op])
        result = engine.validate("repo", [op])
        assert result.conflicts == plan.conflicts

    def test_operation_count_matches(self, tmp_path):
        engine = RepositoryManipulation(tmp_path)
        ops = [_op("op1", MF, "a.py")]
        result = engine.validate("repo", ops)
        assert result.operation_count == 1


# ── CREATE_FILE ───────────────────────────────────────────────────────────────


class TestCreateFile:
    def test_create_file_valid_when_not_exists(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", CF, "new.py", content="print(1)")])
        assert plan.valid is True
        assert len(plan.changes) == 1

    def test_create_file_change_creates_path(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", CF, "new.py")])
        ch = plan.changes[0]
        assert ch.creates_path is True
        assert ch.removes_path is False
        assert ch.modifies_path is False

    def test_create_file_conflict_when_exists(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "existing.py")
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", CF, "existing.py")])
        assert plan.valid is False
        assert plan.conflicts[0].kind == ConflictKind.PATH_ALREADY_EXISTS

    def test_create_file_allow_overwrite_resolves_conflict(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "existing.py")
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", CF, "existing.py", allow_overwrite=True)])
        assert plan.valid is True

    def test_create_file_overwrite_change_modifies_path(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "existing.py")
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", CF, "existing.py", allow_overwrite=True)])
        ch = plan.changes[0]
        assert ch.modifies_path is True
        assert ch.creates_path is False

    def test_create_file_conflict_missing_parent(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", CF, "nonexistent-dir/new.py")])
        assert plan.valid is False
        assert plan.conflicts[0].kind == ConflictKind.DESTINATION_PARENT_MISSING

    def test_create_file_after_create_directory_valid(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        engine = RepositoryManipulation(tmp_path)
        ops = [
            _op("op1", CD, "newdir"),
            _op("op2", CF, "newdir/new.py"),
        ]
        plan = engine.plan("repo", ops)
        assert plan.valid is True
        assert len(plan.changes) == 2

    def test_create_file_path_recorded_in_change(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", CF, "src/main.py")])
        # Parent missing conflict — but path is recorded
        assert plan.conflicts[0].path == "src/main.py"

    def test_create_file_in_root_no_parent_check(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", CF, "root.py")])
        assert plan.valid is True

    def test_create_file_does_not_write_filesystem(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        engine = RepositoryManipulation(tmp_path)
        engine.plan("repo", [_op("op1", CF, "new.py", content="hello")])
        assert not (repo / "new.py").exists()


# ── MODIFY_FILE ───────────────────────────────────────────────────────────────


class TestModifyFile:
    def test_modify_file_valid_when_exists(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "main.py", "# old")
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", MF, "main.py", content="# new")])
        assert plan.valid is True

    def test_modify_file_change_modifies_path(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "main.py")
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", MF, "main.py")])
        ch = plan.changes[0]
        assert ch.modifies_path is True
        assert ch.creates_path is False
        assert ch.removes_path is False

    def test_modify_file_conflict_when_not_exists(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", MF, "missing.py")])
        assert plan.valid is False
        assert plan.conflicts[0].kind == ConflictKind.PATH_NOT_FOUND

    def test_modify_file_after_create_file_valid(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        engine = RepositoryManipulation(tmp_path)
        ops = [
            _op("op1", CF, "new.py", content="v1"),
            _op("op2", MF, "new.py", content="v2"),
        ]
        plan = engine.plan("repo", ops)
        assert plan.valid is True
        assert len(plan.changes) == 2

    def test_modify_file_after_delete_file_conflict(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "target.py")
        engine = RepositoryManipulation(tmp_path)
        ops = [
            _op("op1", DF, "target.py"),
            _op("op2", MF, "target.py"),
        ]
        plan = engine.plan("repo", ops)
        assert plan.conflict_count == 1
        assert plan.conflicts[0].operation_id == "op2"
        assert plan.conflicts[0].kind == ConflictKind.PATH_NOT_FOUND

    def test_modify_file_does_not_write_filesystem(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "main.py", "original")
        engine = RepositoryManipulation(tmp_path)
        engine.plan("repo", [_op("op1", MF, "main.py", content="modified")])
        assert (repo / "main.py").read_text() == "original"


# ── DELETE_FILE ───────────────────────────────────────────────────────────────


class TestDeleteFile:
    def test_delete_file_valid_when_exists(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "to_delete.py")
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", DF, "to_delete.py")])
        assert plan.valid is True

    def test_delete_file_change_removes_path(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "to_delete.py")
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", DF, "to_delete.py")])
        ch = plan.changes[0]
        assert ch.removes_path is True
        assert ch.creates_path is False
        assert ch.modifies_path is False

    def test_delete_file_conflict_when_not_exists(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", DF, "missing.py")])
        assert plan.valid is False
        assert plan.conflicts[0].kind == ConflictKind.PATH_NOT_FOUND

    def test_delete_file_removes_from_simulated_state(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "target.py")
        engine = RepositoryManipulation(tmp_path)
        ops = [
            _op("op1", DF, "target.py"),
            _op("op2", CF, "target.py"),  # should now succeed (fresh create)
        ]
        plan = engine.plan("repo", ops)
        assert plan.valid is True
        assert plan.changes[1].creates_path is True

    def test_delete_file_does_not_delete_filesystem(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "target.py", "keep me")
        engine = RepositoryManipulation(tmp_path)
        engine.plan("repo", [_op("op1", DF, "target.py")])
        assert (repo / "target.py").exists()


# ── RENAME_FILE ───────────────────────────────────────────────────────────────


class TestRenameFile:
    def test_rename_file_valid(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "old.py")
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", RF, "old.py", destination="new.py")])
        assert plan.valid is True

    def test_rename_file_change_creates_and_removes(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "old.py")
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", RF, "old.py", destination="new.py")])
        ch = plan.changes[0]
        assert ch.creates_path is True
        assert ch.removes_path is True
        assert ch.destination == "new.py"

    def test_rename_file_conflict_source_not_found(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", RF, "missing.py", destination="new.py")])
        assert plan.conflicts[0].kind == ConflictKind.PATH_NOT_FOUND

    def test_rename_file_conflict_dest_already_exists(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "source.py")
        _write(repo / "dest.py")
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", RF, "source.py", destination="dest.py")])
        assert plan.conflicts[0].kind == ConflictKind.DESTINATION_ALREADY_EXISTS

    def test_rename_file_conflict_no_destination(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "source.py")
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", RF, "source.py")])
        assert plan.conflicts[0].kind == ConflictKind.MISSING_DESTINATION

    def test_rename_file_conflict_dest_parent_missing(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "source.py")
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", RF, "source.py", destination="newdir/dest.py")])
        assert plan.conflicts[0].kind == ConflictKind.DESTINATION_PARENT_MISSING

    def test_rename_file_updates_simulated_state(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "old.py")
        engine = RepositoryManipulation(tmp_path)
        # After rename old→new, modifying new should succeed
        ops = [
            _op("op1", RF, "old.py", destination="new.py"),
            _op("op2", MF, "new.py", content="updated"),
        ]
        plan = engine.plan("repo", ops)
        assert plan.valid is True

    def test_rename_file_source_no_longer_in_sim_state(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "old.py")
        engine = RepositoryManipulation(tmp_path)
        # After rename old→new, modifying old should fail
        ops = [
            _op("op1", RF, "old.py", destination="new.py"),
            _op("op2", MF, "old.py"),
        ]
        plan = engine.plan("repo", ops)
        assert plan.conflict_count == 1
        assert plan.conflicts[0].operation_id == "op2"

    def test_rename_dest_traversal_rejected(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "source.py")
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", RF, "source.py", destination="../../evil.py")])
        assert plan.conflicts[0].kind == ConflictKind.PATH_TRAVERSAL


# ── MOVE_FILE ─────────────────────────────────────────────────────────────────


class TestMoveFile:
    def test_move_file_valid(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "src" / "main.py")
        (repo / "lib").mkdir()
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", MV, "src/main.py", destination="lib/main.py")])
        assert plan.valid is True

    def test_move_file_change_has_destination(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "main.py")
        (repo / "src").mkdir()
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", MV, "main.py", destination="src/main.py")])
        assert plan.changes[0].destination == "src/main.py"

    def test_move_file_conflict_dest_already_exists(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "a.py")
        _write(repo / "b.py")
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", MV, "a.py", destination="b.py")])
        assert plan.conflicts[0].kind == ConflictKind.DESTINATION_ALREADY_EXISTS

    def test_move_file_conflict_no_destination(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "a.py")
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", MV, "a.py")])
        assert plan.conflicts[0].kind == ConflictKind.MISSING_DESTINATION

    def test_move_after_create_directory_valid(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "main.py")
        engine = RepositoryManipulation(tmp_path)
        ops = [
            _op("op1", CD, "dest"),
            _op("op2", MV, "main.py", destination="dest/main.py"),
        ]
        plan = engine.plan("repo", ops)
        assert plan.valid is True


# ── CREATE_DIRECTORY ──────────────────────────────────────────────────────────


class TestCreateDirectory:
    def test_create_directory_valid(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", CD, "newdir")])
        assert plan.valid is True

    def test_create_directory_change_creates_path(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", CD, "newdir")])
        ch = plan.changes[0]
        assert ch.creates_path is True
        assert ch.removes_path is False
        assert ch.modifies_path is False

    def test_create_directory_conflict_already_exists(self, tmp_path):
        repo = tmp_path / "repo"
        (repo / "existing").mkdir(parents=True)
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", CD, "existing")])
        assert plan.valid is False
        assert plan.conflicts[0].kind == ConflictKind.PATH_ALREADY_EXISTS

    def test_create_directory_registers_in_simulated_state(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        engine = RepositoryManipulation(tmp_path)
        ops = [
            _op("op1", CD, "src"),
            _op("op2", CF, "src/main.py"),
        ]
        plan = engine.plan("repo", ops)
        assert plan.valid is True

    def test_create_directory_nested_registers_parents(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        engine = RepositoryManipulation(tmp_path)
        ops = [
            _op("op1", CD, "a/b/c"),
            _op("op2", CF, "a/main.py"),  # parent 'a' should exist from op1
        ]
        plan = engine.plan("repo", ops)
        assert plan.valid is True

    def test_create_directory_does_not_create_filesystem(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        engine = RepositoryManipulation(tmp_path)
        engine.plan("repo", [_op("op1", CD, "newdir")])
        assert not (repo / "newdir").exists()

    def test_double_create_directory_conflict(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        engine = RepositoryManipulation(tmp_path)
        ops = [
            _op("op1", CD, "src"),
            _op("op2", CD, "src"),
        ]
        plan = engine.plan("repo", ops)
        assert plan.conflict_count == 1
        assert plan.conflicts[0].operation_id == "op2"


# ── DELETE_DIRECTORY ──────────────────────────────────────────────────────────


class TestDeleteDirectory:
    def test_delete_empty_directory_valid(self, tmp_path):
        repo = tmp_path / "repo"
        (repo / "emptydir").mkdir(parents=True)
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", DD, "emptydir")])
        assert plan.valid is True

    def test_delete_directory_change_removes_path(self, tmp_path):
        repo = tmp_path / "repo"
        (repo / "mydir").mkdir(parents=True)
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", DD, "mydir")])
        ch = plan.changes[0]
        assert ch.removes_path is True
        assert ch.creates_path is False

    def test_delete_directory_conflict_not_found(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", DD, "missing")])
        assert plan.conflicts[0].kind == ConflictKind.PATH_NOT_FOUND

    def test_delete_nonempty_directory_without_recursive_conflict(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "mydir" / "file.py")
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", DD, "mydir")])
        assert plan.valid is False
        assert plan.conflicts[0].kind == ConflictKind.DIRECTORY_NOT_EMPTY

    def test_delete_nonempty_directory_with_recursive_valid(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "mydir" / "file.py")
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", DD, "mydir", recursive=True)])
        assert plan.valid is True

    def test_delete_directory_removes_from_simulated_state(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "mydir" / "file.py")
        engine = RepositoryManipulation(tmp_path)
        ops = [
            _op("op1", DD, "mydir", recursive=True),
            _op("op2", CD, "mydir"),  # should succeed — dir was deleted
        ]
        plan = engine.plan("repo", ops)
        assert plan.valid is True

    def test_delete_directory_clears_child_files_from_sim_state(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "mydir" / "file.py")
        engine = RepositoryManipulation(tmp_path)
        ops = [
            _op("op1", DD, "mydir", recursive=True),
            _op("op2", MF, "mydir/file.py"),  # should conflict — file was deleted
        ]
        plan = engine.plan("repo", ops)
        assert plan.conflict_count == 1
        assert plan.conflicts[0].operation_id == "op2"

    def test_delete_directory_does_not_delete_filesystem(self, tmp_path):
        repo = tmp_path / "repo"
        (repo / "mydir").mkdir(parents=True)
        engine = RepositoryManipulation(tmp_path)
        engine.plan("repo", [_op("op1", DD, "mydir")])
        assert (repo / "mydir").exists()


# ── Path traversal rejection ──────────────────────────────────────────────────


class TestPathTraversal:
    def test_dotdot_path_rejected(self, tmp_path):
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", CF, "../../etc/passwd")])
        assert plan.conflicts[0].kind == ConflictKind.PATH_TRAVERSAL

    def test_absolute_path_normalised_safely(self, tmp_path):
        # /etc/passwd strips leading slash → treated as repo-relative "etc/passwd".
        # It does not escape the workspace; it conflicts because parent "etc" is missing.
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", CF, "/etc/passwd")])
        assert plan.valid is False
        assert plan.conflicts[0].kind in (
            ConflictKind.PATH_TRAVERSAL,
            ConflictKind.INVALID_PATH,
            ConflictKind.DESTINATION_PARENT_MISSING,
        )

    def test_embedded_traversal_rejected(self, tmp_path):
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", CF, "a/b/../../../../../../etc/passwd")])
        assert plan.conflicts[0].kind == ConflictKind.PATH_TRAVERSAL

    def test_traversal_conflict_recorded_not_raised(self, tmp_path):
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", CF, "../escape")])
        assert isinstance(plan, RepositoryManipulationPlan)
        assert plan.conflict_count == 1

    def test_safe_path_within_workspace_accepted(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", CF, "a/b/c.py")])
        # Conflict is parent-missing, NOT traversal
        assert plan.conflicts[0].kind != ConflictKind.PATH_TRAVERSAL


# ── Invalid path rejection ────────────────────────────────────────────────────


class TestInvalidPath:
    def test_empty_path_rejected(self, tmp_path):
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", CF, "")])
        assert plan.conflicts[0].kind == ConflictKind.INVALID_PATH

    def test_whitespace_only_path_rejected(self, tmp_path):
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", CF, "   ")])
        assert plan.conflicts[0].kind == ConflictKind.INVALID_PATH

    def test_slash_only_path_rejected(self, tmp_path):
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", CF, "/")])
        assert plan.conflicts[0].kind in (
            ConflictKind.INVALID_PATH, ConflictKind.PATH_TRAVERSAL
        )

    def test_rename_empty_destination_rejected(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "source.py")
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", RF, "source.py", destination="")])
        assert plan.conflicts[0].kind == ConflictKind.MISSING_DESTINATION


# ── Conflict detection completeness ──────────────────────────────────────────


class TestConflictKindCoverage:
    """Verify every ConflictKind can be produced by the engine."""

    def test_path_already_exists_produced(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "f.py")
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", CF, "f.py")])
        assert any(c.kind == ConflictKind.PATH_ALREADY_EXISTS for c in plan.conflicts)

    def test_path_not_found_produced(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", MF, "missing.py")])
        assert any(c.kind == ConflictKind.PATH_NOT_FOUND for c in plan.conflicts)

    def test_destination_already_exists_produced(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "a.py")
        _write(repo / "b.py")
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", RF, "a.py", destination="b.py")])
        assert any(c.kind == ConflictKind.DESTINATION_ALREADY_EXISTS for c in plan.conflicts)

    def test_destination_parent_missing_produced(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", CF, "no-dir/f.py")])
        assert any(c.kind == ConflictKind.DESTINATION_PARENT_MISSING for c in plan.conflicts)

    def test_path_traversal_produced(self, tmp_path):
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", CF, "../../escape")])
        assert any(c.kind == ConflictKind.PATH_TRAVERSAL for c in plan.conflicts)

    def test_invalid_path_produced(self, tmp_path):
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", CF, "")])
        assert any(c.kind == ConflictKind.INVALID_PATH for c in plan.conflicts)

    def test_directory_not_empty_produced(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "dir" / "file.py")
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", DD, "dir")])
        assert any(c.kind == ConflictKind.DIRECTORY_NOT_EMPTY for c in plan.conflicts)

    def test_missing_destination_produced(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "source.py")
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [_op("op1", RF, "source.py")])
        assert any(c.kind == ConflictKind.MISSING_DESTINATION for c in plan.conflicts)


# ── Intra-plan simulated state ────────────────────────────────────────────────


class TestSimulatedState:
    def test_create_then_modify_valid(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [
            _op("op1", CF, "f.py"),
            _op("op2", MF, "f.py", content="new"),
        ])
        assert plan.valid is True

    def test_create_then_delete_valid(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [
            _op("op1", CF, "f.py"),
            _op("op2", DF, "f.py"),
        ])
        assert plan.valid is True

    def test_delete_then_create_valid(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "f.py")
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [
            _op("op1", DF, "f.py"),
            _op("op2", CF, "f.py"),
        ])
        assert plan.valid is True

    def test_modify_deleted_file_conflict(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "f.py")
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [
            _op("op1", DF, "f.py"),
            _op("op2", MF, "f.py"),
        ])
        assert plan.conflict_count == 1
        assert plan.conflicts[0].kind == ConflictKind.PATH_NOT_FOUND

    def test_create_dir_then_create_file_valid(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [
            _op("op1", CD, "src"),
            _op("op2", CF, "src/main.py"),
        ])
        assert plan.valid is True

    def test_rename_then_modify_dest_valid(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "old.py")
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [
            _op("op1", RF, "old.py", destination="new.py"),
            _op("op2", MF, "new.py"),
        ])
        assert plan.valid is True

    def test_multiple_independent_creates_valid(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        engine = RepositoryManipulation(tmp_path)
        plan = engine.plan("repo", [
            _op("op1", CF, "a.py"),
            _op("op2", CF, "b.py"),
            _op("op3", CF, "c.py"),
        ])
        assert plan.valid is True
        assert len(plan.changes) == 3

    def test_conflict_does_not_pollute_simulated_state(self, tmp_path):
        """A conflicted operation must not update simulated state."""
        repo = tmp_path / "repo"
        repo.mkdir()
        engine = RepositoryManipulation(tmp_path)
        ops = [
            _op("op1", MF, "nonexistent.py"),  # conflict — file missing
            _op("op2", CF, "nonexistent.py"),   # should succeed — op1 didn't create it
        ]
        plan = engine.plan("repo", ops)
        assert plan.conflict_count == 1
        assert plan.changes[0].operation_id == "op2"

    def test_partial_plan_changes_and_conflicts(self, tmp_path):
        """Mixed valid/invalid operations produce both changes and conflicts."""
        repo = tmp_path / "repo"
        _write(repo / "exists.py")
        engine = RepositoryManipulation(tmp_path)
        ops = [
            _op("op1", MF, "exists.py"),      # valid
            _op("op2", MF, "missing.py"),     # conflict
            _op("op3", DF, "exists.py"),      # valid
        ]
        plan = engine.plan("repo", ops)
        assert plan.operation_count == 3
        assert len(plan.changes) == 2
        assert plan.conflict_count == 1
        assert plan.conflicts[0].operation_id == "op2"


# ── Determinism ───────────────────────────────────────────────────────────────


class TestDeterminism:
    def test_same_inputs_same_plan_id(self, tmp_path):
        ops = [_op("op1", CF, "main.py")]
        id1 = _make_plan_id("repo", ops)
        id2 = _make_plan_id("repo", ops)
        assert id1 == id2

    def test_different_ops_different_plan_id(self, tmp_path):
        id1 = _make_plan_id("repo", [_op("op1", CF, "a.py")])
        id2 = _make_plan_id("repo", [_op("op1", CF, "b.py")])
        assert id1 != id2

    def test_different_repo_path_different_plan_id(self):
        ops = [_op("op1", CF, "main.py")]
        id1 = _make_plan_id("repo-a", ops)
        id2 = _make_plan_id("repo-b", ops)
        assert id1 != id2

    def test_plan_id_is_16_hex_chars(self):
        sid = _make_plan_id("repo", [])
        assert len(sid) == 16
        int(sid, 16)

    def test_scan_twice_same_changes(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "main.py")
        engine = RepositoryManipulation(tmp_path)
        ts = "2026-01-01T00:00:00+00:00"
        ops = [_op("op1", MF, "main.py")]
        plan1 = engine.plan("repo", ops, planned_at=ts)
        plan2 = engine.plan("repo", ops, planned_at=ts)
        assert plan1.changes == plan2.changes
        assert plan1.conflicts == plan2.conflicts

    def test_scan_twice_same_plan_id(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "main.py")
        engine = RepositoryManipulation(tmp_path)
        ops = [_op("op1", MF, "main.py")]
        plan1 = engine.plan("repo", ops)
        plan2 = engine.plan("repo", ops)
        assert plan1.plan_id == plan2.plan_id


# ── No filesystem writes ──────────────────────────────────────────────────────


class TestNoFilesystemWrites:
    def test_plan_does_not_create_files(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        before = set(p.name for p in repo.iterdir())
        engine = RepositoryManipulation(tmp_path)
        engine.plan("repo", [
            _op("op1", CF, "new_file.py", content="hello"),
            _op("op2", CD, "new_dir"),
        ])
        after = set(p.name for p in repo.iterdir())
        assert before == after

    def test_plan_does_not_delete_files(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "keep_me.py", "important")
        engine = RepositoryManipulation(tmp_path)
        engine.plan("repo", [_op("op1", DF, "keep_me.py")])
        assert (repo / "keep_me.py").exists()

    def test_plan_does_not_modify_files(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "main.py", "original content")
        engine = RepositoryManipulation(tmp_path)
        engine.plan("repo", [_op("op1", MF, "main.py", content="changed")])
        assert (repo / "main.py").read_text() == "original content"

    def test_validate_does_not_write(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        before = list(repo.iterdir())
        engine = RepositoryManipulation(tmp_path)
        engine.validate("repo", [_op("op1", CF, "test.py")])
        assert list(repo.iterdir()) == before

    def test_no_subprocess_called(self, tmp_path):
        engine = RepositoryManipulation(tmp_path)
        with patch("subprocess.run") as mock_run, \
             patch("subprocess.Popen") as mock_popen:
            engine.plan("repo", [_op("op1", CF, "f.py")])
            mock_run.assert_not_called()
            mock_popen.assert_not_called()


# ── Module-level helper tests ─────────────────────────────────────────────────


class TestHelpers:
    def test_normalise_path_strips_whitespace(self):
        assert _normalise_path("  main.py  ") == "main.py"

    def test_normalise_path_strips_leading_slash(self):
        assert _normalise_path("/main.py") == "main.py"

    def test_normalise_path_forward_slashes(self):
        result = _normalise_path("src\\main.py")
        assert "\\" not in result

    def test_normalise_path_empty_returns_none(self):
        assert _normalise_path("") is None

    def test_normalise_path_whitespace_returns_none(self):
        assert _normalise_path("   ") is None

    def test_normalise_path_slash_only_returns_none(self):
        assert _normalise_path("/") is None

    def test_normalise_path_nested(self):
        assert _normalise_path("src/app/main.py") == "src/app/main.py"

    def test_parent_dir_root_level(self):
        assert _parent_dir("main.py") == ""

    def test_parent_dir_nested(self):
        assert _parent_dir("src/main.py") == "src"

    def test_parent_dir_deeply_nested(self):
        assert _parent_dir("a/b/c/main.py") == "a/b/c"

    def test_make_plan_id_length(self):
        assert len(_make_plan_id("repo", [])) == 16

    def test_make_plan_id_hex(self):
        int(_make_plan_id("repo", []), 16)

    def test_make_plan_id_deterministic(self):
        ops = [_op("op1", CF, "a.py")]
        assert _make_plan_id("repo", ops) == _make_plan_id("repo", ops)
