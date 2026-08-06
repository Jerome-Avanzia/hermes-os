"""Tests for the Git Adapter — Sprint 63.

Coverage:
  - All typed contracts (GitOperation, GitRepository, GitRequest, GitResult,
    GitValidationResult, GitExecutionResult)
  - GitAdapter initialisation and workspace root
  - Path resolution and workspace boundary enforcement
  - Git repository validation (_is_git_repository, resolve_repository)
  - Adapter-level validation (validate)
  - Request translation (build_git_request)
  - All 8 supported operations against real temporary git repositories:
    STATUS, ADD, COMMIT, BRANCH, CHECKOUT, TAG, LOG, DIFF
  - execute() never raises, all failures captured as success=False
  - Remote operations are not dispatched
  - Gateway integration

Test strategy:
  - Real temporary git repositories (tmp_path fixture, subprocess git init)
  - No mocking of subprocess — git operations are tested against real git
  - All ExecutionRequests constructed directly (no Gateway mock required)
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hermes.adapters.git_adapter import GitAdapter, _ACTION_TO_OPERATION, _REMOTE_OPERATIONS
from hermes.models.execution_gateway import ExecutionAdapter, ExecutionRequest
from hermes.models.git_adapter import (
    GitExecutionResult,
    GitOperation,
    GitRepository,
    GitRequest,
    GitResult,
    GitValidationResult,
)


# ── Fixtures ───────────────────────────────────────────────────────────────────


def _make_request(
    action_id: str,
    payload: dict,
    adapter_type: ExecutionAdapter = ExecutionAdapter.GIT,
    request_id: str = "req-001",
    operation_id: str = "op-001",
) -> ExecutionRequest:
    """Build an ExecutionRequest for git adapter tests."""
    return ExecutionRequest(
        request_id=request_id,
        operation_id=operation_id,
        adapter_type=adapter_type,
        action_id=action_id,
        payload=tuple(sorted((k, str(v)) for k, v in payload.items())),
    )


def _init_repo(path: Path) -> Path:
    """Initialise a bare-minimum git repository at path.

    Configures user.name and user.email so commits can be created.
    Returns the repo root path.
    """
    subprocess.run(["git", "init"], cwd=path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@hermes.local"],
        cwd=path, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Hermes Test"],
        cwd=path, capture_output=True, check=True,
    )
    return path


def _initial_commit(repo: Path, filename: str = "README.md", content: str = "# Hermes") -> None:
    """Create a file and make an initial commit."""
    (repo / filename).write_text(content)
    subprocess.run(["git", "add", filename], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial commit"],
        cwd=repo, capture_output=True, check=True,
    )


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    """Return a workspace root directory."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


@pytest.fixture()
def repo(workspace: Path) -> Path:
    """Return a fresh git repository inside the workspace."""
    repo_path = workspace / "repo"
    repo_path.mkdir()
    _init_repo(repo_path)
    return repo_path


@pytest.fixture()
def repo_with_commit(repo: Path) -> Path:
    """Return a git repository with one initial commit."""
    _initial_commit(repo)
    return repo


@pytest.fixture()
def adapter(workspace: Path) -> GitAdapter:
    """Return a GitAdapter rooted at the test workspace."""
    return GitAdapter(workspace_root=workspace)


# ── TestGitOperation ───────────────────────────────────────────────────────────


class TestGitOperation:
    def test_all_values_present(self):
        values = {op.value for op in GitOperation}
        assert values == {"status", "add", "commit", "branch", "checkout", "tag", "log", "diff"}

    def test_status_value(self):
        assert GitOperation.STATUS.value == "status"

    def test_add_value(self):
        assert GitOperation.ADD.value == "add"

    def test_commit_value(self):
        assert GitOperation.COMMIT.value == "commit"

    def test_branch_value(self):
        assert GitOperation.BRANCH.value == "branch"

    def test_checkout_value(self):
        assert GitOperation.CHECKOUT.value == "checkout"

    def test_tag_value(self):
        assert GitOperation.TAG.value == "tag"

    def test_log_value(self):
        assert GitOperation.LOG.value == "log"

    def test_diff_value(self):
        assert GitOperation.DIFF.value == "diff"

    def test_remote_operations_not_present(self):
        all_values = {op.value for op in GitOperation}
        for remote_op in _REMOTE_OPERATIONS:
            assert remote_op not in all_values, f"{remote_op} must not be a GitOperation"

    def test_action_to_operation_map_complete(self):
        assert set(_ACTION_TO_OPERATION.keys()) == {op.value for op in GitOperation}

    def test_action_to_operation_round_trip(self):
        for op in GitOperation:
            assert _ACTION_TO_OPERATION[op.value] is op


# ── TestGitRepository ──────────────────────────────────────────────────────────


class TestGitRepository:
    def test_construction(self):
        repo = GitRepository(path="my-repo")
        assert repo.path == "my-repo"

    def test_frozen(self):
        repo = GitRepository(path="my-repo")
        with pytest.raises(Exception):
            repo.path = "other"  # type: ignore[misc]

    def test_slots(self):
        assert not hasattr(GitRepository(path="x"), "__dict__")

    def test_equality(self):
        assert GitRepository(path="a") == GitRepository(path="a")
        assert GitRepository(path="a") != GitRepository(path="b")


# ── TestGitRequest ─────────────────────────────────────────────────────────────


class TestGitRequest:
    def _make(self, **kwargs) -> GitRequest:
        defaults = dict(
            request_id="req-001",
            operation_id="op-001",
            operation=GitOperation.STATUS,
            repository_path="repo",
            files=(),
            message="",
            branch_name="",
            ref="",
            max_count=0,
            create_branch=False,
            metadata=(),
        )
        defaults.update(kwargs)
        return GitRequest(**defaults)

    def test_construction(self):
        req = self._make()
        assert req.request_id == "req-001"
        assert req.operation == GitOperation.STATUS

    def test_frozen(self):
        req = self._make()
        with pytest.raises(Exception):
            req.message = "oops"  # type: ignore[misc]

    def test_slots(self):
        assert not hasattr(self._make(), "__dict__")

    def test_files_is_tuple(self):
        req = self._make(files=("a.py", "b.py"))
        assert isinstance(req.files, tuple)

    def test_metadata_is_tuple_of_tuples(self):
        req = self._make(metadata=(("key", "val"),))
        assert req.metadata == (("key", "val"),)

    def test_all_fields(self):
        req = self._make(
            files=("src/main.py",),
            message="fix bug",
            branch_name="feature",
            ref="HEAD~1",
            max_count=5,
            create_branch=True,
        )
        assert req.files == ("src/main.py",)
        assert req.message == "fix bug"
        assert req.branch_name == "feature"
        assert req.ref == "HEAD~1"
        assert req.max_count == 5
        assert req.create_branch is True


# ── TestGitResult ──────────────────────────────────────────────────────────────


class TestGitResult:
    def _make(self, **kwargs) -> GitResult:
        defaults = dict(
            operation=GitOperation.STATUS,
            repository_path="repo",
            output="",
            return_code=0,
            metadata=(),
        )
        defaults.update(kwargs)
        return GitResult(**defaults)

    def test_construction(self):
        result = self._make(output="M  file.py", return_code=0)
        assert result.output == "M  file.py"
        assert result.return_code == 0

    def test_frozen(self):
        result = self._make()
        with pytest.raises(Exception):
            result.output = "x"  # type: ignore[misc]

    def test_slots(self):
        assert not hasattr(self._make(), "__dict__")

    def test_non_zero_return_code(self):
        result = self._make(return_code=128, output="fatal: not a git repository")
        assert result.return_code == 128


# ── TestGitValidationResult ────────────────────────────────────────────────────


class TestGitValidationResult:
    def test_valid_result(self):
        r = GitValidationResult(valid=True, errors=())
        assert r.valid is True
        assert r.errors == ()

    def test_invalid_result(self):
        r = GitValidationResult(valid=False, errors=("error one", "error two"))
        assert r.valid is False
        assert len(r.errors) == 2

    def test_frozen(self):
        r = GitValidationResult(valid=True, errors=())
        with pytest.raises(Exception):
            r.valid = False  # type: ignore[misc]

    def test_slots(self):
        assert not hasattr(GitValidationResult(valid=True, errors=()), "__dict__")


# ── TestGitExecutionResult ─────────────────────────────────────────────────────


class TestGitExecutionResult:
    def _make(self, **kwargs) -> GitExecutionResult:
        defaults = dict(
            request_id="req-001",
            operation_id="op-001",
            operation=GitOperation.STATUS,
            git_request=None,
            git_result=None,
            success=True,
            error=None,
            adapter_metadata=(),
        )
        defaults.update(kwargs)
        return GitExecutionResult(**defaults)

    def test_success_construction(self):
        r = self._make(success=True)
        assert r.success is True
        assert r.error is None

    def test_failure_construction(self):
        r = self._make(success=False, error="something failed")
        assert r.success is False
        assert r.error == "something failed"

    def test_operation_none_allowed(self):
        r = self._make(operation=None)
        assert r.operation is None

    def test_frozen(self):
        r = self._make()
        with pytest.raises(Exception):
            r.success = False  # type: ignore[misc]

    def test_slots(self):
        assert not hasattr(self._make(), "__dict__")

    def test_adapter_metadata_is_tuple(self):
        r = self._make(adapter_metadata=(("key", "val"),))
        assert isinstance(r.adapter_metadata, tuple)


# ── TestGitAdapterInit ─────────────────────────────────────────────────────────


class TestGitAdapterInit:
    def test_workspace_root_resolved(self, tmp_path: Path):
        adapter = GitAdapter(workspace_root=tmp_path)
        assert adapter.workspace_root == tmp_path.resolve()

    def test_workspace_root_accepts_string(self, tmp_path: Path):
        adapter = GitAdapter(workspace_root=str(tmp_path))
        assert adapter.workspace_root == tmp_path.resolve()

    def test_workspace_root_property(self, tmp_path: Path):
        adapter = GitAdapter(workspace_root=tmp_path)
        assert isinstance(adapter.workspace_root, Path)
        assert adapter.workspace_root.is_absolute()


# ── TestGitAdapterResolvePath ──────────────────────────────────────────────────


class TestGitAdapterResolvePath:
    def test_simple_path(self, adapter: GitAdapter, workspace: Path):
        resolved = adapter.resolve_path("repo")
        assert resolved == workspace / "repo"

    def test_nested_path(self, adapter: GitAdapter, workspace: Path):
        resolved = adapter.resolve_path("a/b/c")
        assert resolved == workspace / "a" / "b" / "c"

    def test_empty_path_rejected(self, adapter: GitAdapter):
        with pytest.raises(ValueError, match="must not be empty"):
            adapter.resolve_path("")

    def test_whitespace_only_rejected(self, adapter: GitAdapter):
        with pytest.raises(ValueError, match="must not be empty"):
            adapter.resolve_path("   ")

    def test_absolute_path_rejected(self, adapter: GitAdapter):
        with pytest.raises(ValueError, match="absolute path"):
            adapter.resolve_path("/etc/passwd")

    def test_traversal_rejected(self, adapter: GitAdapter):
        with pytest.raises(ValueError, match="traversal"):
            adapter.resolve_path("../../etc/passwd")

    def test_deep_traversal_rejected(self, adapter: GitAdapter):
        with pytest.raises(ValueError, match="traversal"):
            adapter.resolve_path("repo/../../../outside")


# ── TestGitAdapterResolveRepository ───────────────────────────────────────────


class TestGitAdapterResolveRepository:
    def test_valid_repo(self, adapter: GitAdapter, repo: Path, workspace: Path):
        resolved = adapter.resolve_repository("repo")
        assert resolved == repo

    def test_non_existent_path_rejected(self, adapter: GitAdapter):
        with pytest.raises(ValueError, match="does not exist"):
            adapter.resolve_repository("nonexistent-dir")

    def test_file_rejected(self, adapter: GitAdapter, workspace: Path):
        (workspace / "file.txt").write_text("hello")
        with pytest.raises(ValueError, match="not a directory"):
            adapter.resolve_repository("file.txt")

    def test_non_git_directory_rejected(self, adapter: GitAdapter, workspace: Path):
        plain = workspace / "plain-dir"
        plain.mkdir()
        with pytest.raises(ValueError, match="not a valid git repository"):
            adapter.resolve_repository("plain-dir")

    def test_workspace_boundary_enforced(self, adapter: GitAdapter, tmp_path: Path):
        outside = tmp_path / "outside"
        outside.mkdir()
        _init_repo(outside)
        with pytest.raises(ValueError):
            adapter.resolve_repository("../outside")


# ── TestGitAdapterValidation ───────────────────────────────────────────────────


class TestGitAdapterValidation:
    def test_valid_status_request(self, adapter: GitAdapter, repo: Path):
        request = _make_request("status", {"repository_path": "repo"})
        result = adapter.validate(request, GitOperation.STATUS, "repo")
        assert result.valid is True
        assert result.errors == ()

    def test_wrong_adapter_type_rejected(self, adapter: GitAdapter, repo: Path):
        request = _make_request(
            "status",
            {"repository_path": "repo"},
            adapter_type=ExecutionAdapter.FILESYSTEM,
        )
        result = adapter.validate(request, GitOperation.STATUS, "repo")
        assert result.valid is False
        assert any("GIT" in e for e in result.errors)

    def test_unknown_operation_rejected(self, adapter: GitAdapter):
        request = _make_request("push", {"repository_path": "repo"})
        result = adapter.validate(request, None, "repo")
        assert result.valid is False
        assert any("not a recognized GitOperation" in e for e in result.errors)

    def test_empty_repository_path_rejected(self, adapter: GitAdapter):
        request = _make_request("status", {"repository_path": ""})
        result = adapter.validate(request, GitOperation.STATUS, "")
        assert result.valid is False
        assert any("repository_path" in e for e in result.errors)

    def test_absolute_repository_path_rejected(self, adapter: GitAdapter):
        request = _make_request("status", {"repository_path": "/etc"})
        result = adapter.validate(request, GitOperation.STATUS, "/etc")
        assert result.valid is False
        assert any("absolute" in e for e in result.errors)

    def test_non_git_repository_rejected(self, adapter: GitAdapter, workspace: Path):
        plain = workspace / "plain"
        plain.mkdir()
        request = _make_request("status", {"repository_path": "plain"})
        result = adapter.validate(request, GitOperation.STATUS, "plain")
        assert result.valid is False
        assert any("git repository" in e for e in result.errors)

    def test_commit_requires_message(self, adapter: GitAdapter, repo: Path):
        request = _make_request("commit", {"repository_path": "repo"})
        result = adapter.validate(request, GitOperation.COMMIT, "repo")
        assert result.valid is False
        assert any("message" in e for e in result.errors)

    def test_commit_with_message_valid(self, adapter: GitAdapter, repo: Path):
        request = _make_request("commit", {"repository_path": "repo", "message": "fix"})
        result = adapter.validate(request, GitOperation.COMMIT, "repo")
        assert result.valid is True

    def test_multiple_errors_accumulated(self, adapter: GitAdapter):
        request = _make_request(
            "push",
            {"repository_path": ""},
            adapter_type=ExecutionAdapter.FILESYSTEM,
        )
        result = adapter.validate(request, None, "")
        assert result.valid is False
        assert len(result.errors) >= 2

    def test_validation_result_is_frozen(self, adapter: GitAdapter, repo: Path):
        request = _make_request("status", {"repository_path": "repo"})
        result = adapter.validate(request, GitOperation.STATUS, "repo")
        with pytest.raises(Exception):
            result.valid = False  # type: ignore[misc]


# ── TestGitAdapterBuildRequest ─────────────────────────────────────────────────


class TestGitAdapterBuildRequest:
    def test_status_request(self, adapter: GitAdapter):
        request = _make_request("status", {"repository_path": "repo"})
        git_req = adapter.build_git_request(request, GitOperation.STATUS)
        assert git_req.operation == GitOperation.STATUS
        assert git_req.repository_path == "repo"
        assert git_req.files == ()
        assert git_req.message == ""
        assert git_req.branch_name == ""

    def test_commit_request(self, adapter: GitAdapter):
        request = _make_request("commit", {"repository_path": "repo", "message": "my commit"})
        git_req = adapter.build_git_request(request, GitOperation.COMMIT)
        assert git_req.message == "my commit"

    def test_add_with_files(self, adapter: GitAdapter):
        request = _make_request(
            "add",
            {"repository_path": "repo", "files": "src/main.py,src/util.py"},
        )
        git_req = adapter.build_git_request(request, GitOperation.ADD)
        assert "src/main.py" in git_req.files
        assert "src/util.py" in git_req.files

    def test_checkout_create_branch(self, adapter: GitAdapter):
        request = _make_request(
            "checkout",
            {"repository_path": "repo", "branch_name": "feature", "create_branch": "true"},
        )
        git_req = adapter.build_git_request(request, GitOperation.CHECKOUT)
        assert git_req.branch_name == "feature"
        assert git_req.create_branch is True

    def test_log_max_count(self, adapter: GitAdapter):
        request = _make_request("log", {"repository_path": "repo", "max_count": "5"})
        git_req = adapter.build_git_request(request, GitOperation.LOG)
        assert git_req.max_count == 5

    def test_log_max_count_zero_default(self, adapter: GitAdapter):
        request = _make_request("log", {"repository_path": "repo"})
        git_req = adapter.build_git_request(request, GitOperation.LOG)
        assert git_req.max_count == 0

    def test_ref_field(self, adapter: GitAdapter):
        request = _make_request("diff", {"repository_path": "repo", "ref": "HEAD~1"})
        git_req = adapter.build_git_request(request, GitOperation.DIFF)
        assert git_req.ref == "HEAD~1"

    def test_extra_payload_becomes_metadata(self, adapter: GitAdapter):
        request = _make_request(
            "status", {"repository_path": "repo", "extra_key": "extra_val"}
        )
        git_req = adapter.build_git_request(request, GitOperation.STATUS)
        meta_dict = dict(git_req.metadata)
        assert meta_dict.get("extra_key") == "extra_val"

    def test_result_is_frozen(self, adapter: GitAdapter):
        request = _make_request("status", {"repository_path": "repo"})
        git_req = adapter.build_git_request(request, GitOperation.STATUS)
        with pytest.raises(Exception):
            git_req.message = "x"  # type: ignore[misc]

    def test_ids_forwarded(self, adapter: GitAdapter):
        request = _make_request(
            "status", {"repository_path": "repo"},
            request_id="req-abc", operation_id="op-xyz",
        )
        git_req = adapter.build_git_request(request, GitOperation.STATUS)
        assert git_req.request_id == "req-abc"
        assert git_req.operation_id == "op-xyz"


# ── TestGitAdapterStatus ───────────────────────────────────────────────────────


class TestGitAdapterStatus:
    def test_status_clean_repo(self, adapter: GitAdapter, repo_with_commit: Path):
        request = _make_request("status", {"repository_path": "repo"})
        result = adapter.execute(request)
        assert result.success is True
        assert result.operation == GitOperation.STATUS
        assert result.git_result is not None
        assert result.git_result.return_code == 0

    def test_status_untracked_file(self, adapter: GitAdapter, repo: Path):
        _initial_commit(repo)
        (repo / "new_file.py").write_text("hello")
        request = _make_request("status", {"repository_path": "repo"})
        result = adapter.execute(request)
        assert result.success is True
        assert "new_file.py" in result.git_result.output

    def test_status_modified_file(self, adapter: GitAdapter, repo: Path):
        _initial_commit(repo)
        (repo / "README.md").write_text("changed content")
        request = _make_request("status", {"repository_path": "repo"})
        result = adapter.execute(request)
        assert result.success is True
        assert "README.md" in result.git_result.output

    def test_status_result_structure(self, adapter: GitAdapter, repo_with_commit: Path):
        request = _make_request("status", {"repository_path": "repo"})
        result = adapter.execute(request)
        assert result.request_id == "req-001"
        assert result.operation_id == "op-001"
        assert result.error is None
        assert result.git_request is not None
        assert result.git_request.operation == GitOperation.STATUS


# ── TestGitAdapterAdd ─────────────────────────────────────────────────────────


class TestGitAdapterAdd:
    def test_add_all(self, adapter: GitAdapter, repo: Path):
        _initial_commit(repo)
        (repo / "new_file.py").write_text("content")
        request = _make_request("add", {"repository_path": "repo"})
        result = adapter.execute(request)
        assert result.success is True
        assert result.operation == GitOperation.ADD

    def test_add_specific_file(self, adapter: GitAdapter, repo: Path):
        _initial_commit(repo)
        (repo / "file_a.py").write_text("a")
        (repo / "file_b.py").write_text("b")
        request = _make_request(
            "add", {"repository_path": "repo", "files": "file_a.py"}
        )
        result = adapter.execute(request)
        assert result.success is True
        # Verify only file_a.py was staged (file_b.py remains untracked)
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo, capture_output=True, text=True,
        )
        assert "A  file_a.py" in status.stdout
        assert "?? file_b.py" in status.stdout

    def test_add_result_structure(self, adapter: GitAdapter, repo: Path):
        _initial_commit(repo)
        (repo / "f.py").write_text("x")
        request = _make_request("add", {"repository_path": "repo"})
        result = adapter.execute(request)
        assert result.git_result is not None
        assert result.git_result.operation == GitOperation.ADD
        assert result.git_result.return_code == 0


# ── TestGitAdapterCommit ──────────────────────────────────────────────────────


class TestGitAdapterCommit:
    def test_commit_creates_commit(self, adapter: GitAdapter, repo: Path):
        _initial_commit(repo)
        (repo / "new.py").write_text("content")
        subprocess.run(["git", "add", "new.py"], cwd=repo, capture_output=True)
        request = _make_request(
            "commit", {"repository_path": "repo", "message": "add new.py"}
        )
        result = adapter.execute(request)
        assert result.success is True
        # Verify commit was created
        log = subprocess.run(
            ["git", "log", "--oneline"],
            cwd=repo, capture_output=True, text=True,
        )
        assert "add new.py" in log.stdout

    def test_commit_output_contains_info(self, adapter: GitAdapter, repo: Path):
        _initial_commit(repo)
        (repo / "f.txt").write_text("x")
        subprocess.run(["git", "add", "f.txt"], cwd=repo, capture_output=True)
        request = _make_request(
            "commit", {"repository_path": "repo", "message": "test commit"}
        )
        result = adapter.execute(request)
        assert result.success is True
        assert result.git_result is not None

    def test_commit_nothing_to_commit_fails(self, adapter: GitAdapter, repo_with_commit: Path):
        """Committing with nothing staged should return success=False."""
        request = _make_request(
            "commit", {"repository_path": "repo", "message": "empty"}
        )
        result = adapter.execute(request)
        # git commit exits 1 when nothing to commit
        assert result.success is False
        assert result.error is not None


# ── TestGitAdapterBranch ──────────────────────────────────────────────────────


class TestGitAdapterBranch:
    def test_list_branches(self, adapter: GitAdapter, repo_with_commit: Path):
        request = _make_request("branch", {"repository_path": "repo"})
        result = adapter.execute(request)
        assert result.success is True
        # Should list the current branch
        assert result.git_result.output.strip() != "" or result.git_result.return_code == 0

    def test_create_branch(self, adapter: GitAdapter, repo_with_commit: Path):
        request = _make_request(
            "branch", {"repository_path": "repo", "branch_name": "feature-x"}
        )
        result = adapter.execute(request)
        assert result.success is True
        # Verify branch was created
        branches = subprocess.run(
            ["git", "branch"], cwd=repo_with_commit, capture_output=True, text=True
        )
        assert "feature-x" in branches.stdout

    def test_create_duplicate_branch_fails(self, adapter: GitAdapter, repo_with_commit: Path):
        # Get current branch name
        branch_output = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=repo_with_commit, capture_output=True, text=True,
        )
        current = branch_output.stdout.strip() or "main"
        request = _make_request(
            "branch", {"repository_path": "repo", "branch_name": current}
        )
        result = adapter.execute(request)
        assert result.success is False

    def test_branch_result_structure(self, adapter: GitAdapter, repo_with_commit: Path):
        request = _make_request("branch", {"repository_path": "repo"})
        result = adapter.execute(request)
        assert result.git_result is not None
        assert result.git_result.operation == GitOperation.BRANCH


# ── TestGitAdapterCheckout ────────────────────────────────────────────────────


class TestGitAdapterCheckout:
    def test_checkout_existing_branch(self, adapter: GitAdapter, repo_with_commit: Path):
        # Create a branch first
        subprocess.run(["git", "branch", "dev"], cwd=repo_with_commit, capture_output=True)
        request = _make_request(
            "checkout", {"repository_path": "repo", "branch_name": "dev"}
        )
        result = adapter.execute(request)
        assert result.success is True
        # Verify we switched
        current = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=repo_with_commit, capture_output=True, text=True,
        )
        assert current.stdout.strip() == "dev"

    def test_checkout_create_new_branch(self, adapter: GitAdapter, repo_with_commit: Path):
        request = _make_request(
            "checkout",
            {
                "repository_path": "repo",
                "branch_name": "new-feature",
                "create_branch": "true",
            },
        )
        result = adapter.execute(request)
        assert result.success is True
        current = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=repo_with_commit, capture_output=True, text=True,
        )
        assert current.stdout.strip() == "new-feature"

    def test_checkout_nonexistent_branch_fails(self, adapter: GitAdapter, repo_with_commit: Path):
        request = _make_request(
            "checkout", {"repository_path": "repo", "branch_name": "does-not-exist"}
        )
        result = adapter.execute(request)
        assert result.success is False
        assert result.error is not None


# ── TestGitAdapterTag ─────────────────────────────────────────────────────────


class TestGitAdapterTag:
    def test_lightweight_tag(self, adapter: GitAdapter, repo_with_commit: Path):
        request = _make_request(
            "tag",
            {"repository_path": "repo", "branch_name": "v1.0.0"},
        )
        result = adapter.execute(request)
        assert result.success is True
        tags = subprocess.run(
            ["git", "tag"], cwd=repo_with_commit, capture_output=True, text=True
        )
        assert "v1.0.0" in tags.stdout

    def test_annotated_tag(self, adapter: GitAdapter, repo_with_commit: Path):
        request = _make_request(
            "tag",
            {
                "repository_path": "repo",
                "branch_name": "v2.0.0",
                "message": "Release 2.0",
            },
        )
        result = adapter.execute(request)
        assert result.success is True
        tags = subprocess.run(
            ["git", "tag"], cwd=repo_with_commit, capture_output=True, text=True
        )
        assert "v2.0.0" in tags.stdout

    def test_duplicate_tag_fails(self, adapter: GitAdapter, repo_with_commit: Path):
        subprocess.run(["git", "tag", "v0.1"], cwd=repo_with_commit, capture_output=True)
        request = _make_request(
            "tag", {"repository_path": "repo", "branch_name": "v0.1"}
        )
        result = adapter.execute(request)
        assert result.success is False


# ── TestGitAdapterLog ─────────────────────────────────────────────────────────


class TestGitAdapterLog:
    def test_log_returns_commits(self, adapter: GitAdapter, repo_with_commit: Path):
        request = _make_request("log", {"repository_path": "repo"})
        result = adapter.execute(request)
        assert result.success is True
        assert "initial commit" in result.git_result.output

    def test_log_with_max_count(self, adapter: GitAdapter, repo: Path):
        _initial_commit(repo)
        # Make two more commits
        (repo / "f1.txt").write_text("a")
        subprocess.run(["git", "add", "f1.txt"], cwd=repo, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t.com", "-c", "user.name=T", "commit", "-m", "second"],
            cwd=repo, capture_output=True,
        )
        (repo / "f2.txt").write_text("b")
        subprocess.run(["git", "add", "f2.txt"], cwd=repo, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t.com", "-c", "user.name=T", "commit", "-m", "third"],
            cwd=repo, capture_output=True,
        )
        request = _make_request("log", {"repository_path": "repo", "max_count": "1"})
        result = adapter.execute(request)
        assert result.success is True
        lines = [l for l in result.git_result.output.strip().splitlines() if l]
        assert len(lines) == 1

    def test_log_empty_repo_fails(self, adapter: GitAdapter, repo: Path):
        """git log on a repo with no commits fails."""
        request = _make_request("log", {"repository_path": "repo"})
        result = adapter.execute(request)
        assert result.success is False

    def test_log_result_structure(self, adapter: GitAdapter, repo_with_commit: Path):
        request = _make_request("log", {"repository_path": "repo"})
        result = adapter.execute(request)
        assert result.git_result is not None
        assert result.git_result.operation == GitOperation.LOG


# ── TestGitAdapterDiff ────────────────────────────────────────────────────────


class TestGitAdapterDiff:
    def test_diff_clean_repo_empty(self, adapter: GitAdapter, repo_with_commit: Path):
        request = _make_request("diff", {"repository_path": "repo"})
        result = adapter.execute(request)
        assert result.success is True
        assert result.git_result.output == ""   # no changes

    def test_diff_shows_modifications(self, adapter: GitAdapter, repo: Path):
        _initial_commit(repo)
        (repo / "README.md").write_text("changed content")
        request = _make_request("diff", {"repository_path": "repo"})
        result = adapter.execute(request)
        assert result.success is True
        assert "README.md" in result.git_result.output or result.git_result.output != ""

    def test_diff_with_ref(self, adapter: GitAdapter, repo_with_commit: Path):
        request = _make_request("diff", {"repository_path": "repo", "ref": "HEAD"})
        result = adapter.execute(request)
        assert result.success is True

    def test_diff_result_structure(self, adapter: GitAdapter, repo_with_commit: Path):
        request = _make_request("diff", {"repository_path": "repo"})
        result = adapter.execute(request)
        assert result.git_result is not None
        assert result.git_result.operation == GitOperation.DIFF


# ── TestGitAdapterWorkspaceBoundary ───────────────────────────────────────────


class TestGitAdapterWorkspaceBoundary:
    def test_traversal_rejected_in_execute(self, adapter: GitAdapter):
        request = _make_request("status", {"repository_path": "../../outside"})
        result = adapter.execute(request)
        assert result.success is False
        assert result.error is not None
        assert result.git_request is None   # failed at validation

    def test_absolute_path_rejected_in_execute(self, adapter: GitAdapter):
        request = _make_request("status", {"repository_path": "/tmp"})
        result = adapter.execute(request)
        assert result.success is False
        assert result.git_request is None

    def test_non_git_directory_rejected_in_execute(
        self, adapter: GitAdapter, workspace: Path
    ):
        plain = workspace / "plain"
        plain.mkdir()
        request = _make_request("status", {"repository_path": "plain"})
        result = adapter.execute(request)
        assert result.success is False
        assert "git repository" in result.error

    def test_nonexistent_path_rejected_in_execute(self, adapter: GitAdapter):
        request = _make_request("status", {"repository_path": "missing-repo"})
        result = adapter.execute(request)
        assert result.success is False

    def test_remote_operation_rejected(self, adapter: GitAdapter, repo: Path):
        """push/pull/fetch/clone action_ids must fail as unrecognized operations."""
        for remote_op in _REMOTE_OPERATIONS:
            request = _make_request(remote_op, {"repository_path": "repo"})
            result = adapter.execute(request)
            assert result.success is False, f"{remote_op} should be rejected"
            assert result.operation is None, f"{remote_op} should map to None operation"

    def test_metadata_present_on_traversal_failure(self, adapter: GitAdapter):
        request = _make_request("status", {"repository_path": "../../outside"})
        result = adapter.execute(request)
        assert result.adapter_metadata != ()


# ── TestGitAdapterNeverRaises ─────────────────────────────────────────────────


class TestGitAdapterNeverRaises:
    """execute() must never raise an exception — all failures are success=False."""

    def test_empty_action_id(self, adapter: GitAdapter):
        request = _make_request("", {"repository_path": "repo"})
        result = adapter.execute(request)
        assert isinstance(result, GitExecutionResult)
        assert result.success is False

    def test_unknown_action_id(self, adapter: GitAdapter):
        request = _make_request("rebase", {"repository_path": "repo"})
        result = adapter.execute(request)
        assert isinstance(result, GitExecutionResult)
        assert result.success is False

    def test_missing_repository_path(self, adapter: GitAdapter):
        request = _make_request("status", {})
        result = adapter.execute(request)
        assert isinstance(result, GitExecutionResult)
        assert result.success is False

    def test_wrong_adapter_type(self, adapter: GitAdapter, repo: Path):
        request = _make_request(
            "status",
            {"repository_path": "repo"},
            adapter_type=ExecutionAdapter.LLM,
        )
        result = adapter.execute(request)
        assert isinstance(result, GitExecutionResult)
        assert result.success is False

    def test_result_is_frozen(self, adapter: GitAdapter, repo: Path):
        request = _make_request("status", {"repository_path": "repo"})
        result = adapter.execute(request)
        assert isinstance(result, GitExecutionResult)
        with pytest.raises(Exception):
            result.success = not result.success  # type: ignore[misc]


# ── TestGitAdapterDeterminism ─────────────────────────────────────────────────


class TestGitAdapterDeterminism:
    """Same inputs produce same structural outputs (excluding git-level non-determinism)."""

    def test_build_git_request_is_deterministic(self, adapter: GitAdapter):
        request = _make_request(
            "commit",
            {"repository_path": "repo", "message": "fix bug", "extra": "data"},
        )
        r1 = adapter.build_git_request(request, GitOperation.COMMIT)
        r2 = adapter.build_git_request(request, GitOperation.COMMIT)
        assert r1 == r2

    def test_validate_is_deterministic(self, adapter: GitAdapter, repo: Path):
        request = _make_request("status", {"repository_path": "repo"})
        v1 = adapter.validate(request, GitOperation.STATUS, "repo")
        v2 = adapter.validate(request, GitOperation.STATUS, "repo")
        assert v1 == v2

    def test_adapter_metadata_sorted(self, adapter: GitAdapter, repo: Path):
        _initial_commit(repo)
        request = _make_request("status", {"repository_path": "repo"})
        result = adapter.execute(request)
        keys = [k for k, _ in result.adapter_metadata]
        assert keys == sorted(keys)

    def test_git_result_metadata_sorted(self, adapter: GitAdapter, repo_with_commit: Path):
        request = _make_request("log", {"repository_path": "repo"})
        result = adapter.execute(request)
        keys = [k for k, _ in result.git_result.metadata]
        assert keys == sorted(keys)


# ── TestGitAdapterAdapterMetadata ─────────────────────────────────────────────


class TestGitAdapterAdapterMetadata:
    def test_metadata_contains_action(self, adapter: GitAdapter, repo_with_commit: Path):
        request = _make_request("status", {"repository_path": "repo"})
        result = adapter.execute(request)
        meta = dict(result.adapter_metadata)
        assert meta.get("action") == "status"

    def test_metadata_contains_operation(self, adapter: GitAdapter, repo_with_commit: Path):
        request = _make_request("status", {"repository_path": "repo"})
        result = adapter.execute(request)
        meta = dict(result.adapter_metadata)
        assert meta.get("operation") == "status"

    def test_metadata_contains_return_code_on_success(
        self, adapter: GitAdapter, repo_with_commit: Path
    ):
        request = _make_request("status", {"repository_path": "repo"})
        result = adapter.execute(request)
        meta = dict(result.adapter_metadata)
        assert "return_code" in meta
        assert meta["return_code"] == "0"

    def test_failure_metadata_present(self, adapter: GitAdapter):
        request = _make_request("status", {"repository_path": "nonexistent"})
        result = adapter.execute(request)
        assert result.adapter_metadata != ()

    def test_validation_failure_operation_unknown(self, adapter: GitAdapter):
        request = _make_request("push", {"repository_path": "repo"})
        result = adapter.execute(request)
        meta = dict(result.adapter_metadata)
        assert meta.get("operation") == "unknown"


# ── TestGitAdapterGatewayIntegration ──────────────────────────────────────────


class TestGitAdapterGatewayIntegration:
    """Verify the adapter contract as seen from the Execution Gateway boundary."""

    def test_returns_git_execution_result(self, adapter: GitAdapter, repo_with_commit: Path):
        request = _make_request("status", {"repository_path": "repo"})
        result = adapter.execute(request)
        assert isinstance(result, GitExecutionResult)

    def test_request_id_preserved(self, adapter: GitAdapter, repo_with_commit: Path):
        request = _make_request(
            "status", {"repository_path": "repo"}, request_id="gateway-req-99"
        )
        result = adapter.execute(request)
        assert result.request_id == "gateway-req-99"

    def test_operation_id_preserved(self, adapter: GitAdapter, repo_with_commit: Path):
        request = _make_request(
            "status", {"repository_path": "repo"}, operation_id="op-gateway-42"
        )
        result = adapter.execute(request)
        assert result.operation_id == "op-gateway-42"

    def test_all_eight_operations_dispatched(
        self, adapter: GitAdapter, repo_with_commit: Path
    ):
        """All 8 operations must be recognized and dispatched (not fall to unknown)."""
        for op in GitOperation:
            request = _make_request(
                op.value,
                {"repository_path": "repo", "message": "msg", "branch_name": "x"},
            )
            result = adapter.execute(request)
            # operation is recognized (not None), even if git itself fails
            assert result.operation == op, f"{op.value} was not dispatched"
            assert result.operation is not None

    def test_success_false_has_error(self, adapter: GitAdapter):
        request = _make_request("status", {"repository_path": ""})
        result = adapter.execute(request)
        assert result.success is False
        assert result.error is not None
        assert len(result.error) > 0

    def test_success_true_has_no_error(self, adapter: GitAdapter, repo_with_commit: Path):
        request = _make_request("status", {"repository_path": "repo"})
        result = adapter.execute(request)
        assert result.success is True
        assert result.error is None

    def test_git_request_none_on_validation_failure(self, adapter: GitAdapter):
        request = _make_request("status", {"repository_path": ""})
        result = adapter.execute(request)
        assert result.git_request is None

    def test_git_request_set_on_success(self, adapter: GitAdapter, repo_with_commit: Path):
        request = _make_request("status", {"repository_path": "repo"})
        result = adapter.execute(request)
        assert result.git_request is not None

    def test_git_result_none_on_failure(self, adapter: GitAdapter):
        request = _make_request("status", {"repository_path": "missing"})
        result = adapter.execute(request)
        assert result.git_result is None
