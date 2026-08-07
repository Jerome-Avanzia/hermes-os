"""Tests for Phase 7 — CorrectionEngine (single-operation executor with correction loop).

Coverage:
  - First-attempt success: create mode (correction_attempts=0, no correction_log)
  - First-attempt success: modify mode (correction_attempts=0, read_file step present)
  - Validate failure → one correction cycle → success (correction_attempts=1)
  - Test failure → one correction cycle → success (correction_attempts=1)
  - Max corrections exceeded → success=False, error="repair_limit_exceeded"
  - Non-correctable failure (LLM generate fails) → correction_attempts=0
  - Non-correctable failure (gateway dispatch fails) → correction_attempts=0
  - Two consecutive corrections before success (correction_attempts=2)
  - max_corrections=0 disables correction loop (immediate failure, not repair_limit_exceeded)
  - Step IDs use PlannedOperation.operation_id prefix (not goal_id)

Test strategy:
  - LLM adapter is mocked (MagicMock) — no Ollama required
  - Filesystem and Git use real temporary directories + git repos
  - Gateway is real (ExecutionGateway)
  - ValidationAdapter mocked via subprocess.run patch for test-gate tests
  - OperationEngine and all other adapters are real instances
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hermes.adapters.filesystem_adapter import FilesystemAdapter
from hermes.adapters.git_adapter import GitAdapter
from hermes.adapters.llm_adapter import LlmAdapter
from hermes.adapters.validation_adapter import ValidationAdapter
from hermes.kernel.correction_engine import CorrectionEngine
from hermes.kernel.execution_gateway import ExecutionGateway
from hermes.kernel.operation_engine import OperationEngine
from hermes.models.engineering_plan import PlannedOperation
from hermes.models.engineering_workflow import (
    CorrectionRecord,
    FounderGoal,
    OperationCorrectionResult,
    WorkflowConfig,
)
from hermes.models.execution_gateway import (
    AdapterRegistration,
    ExecutionAdapter,
    ExecutionStatus,
)
from hermes.models.llm_adapter import (
    AdapterExecutionResult,
    LLMProvider,
    LLMRequest,
    LLMResponse,
)

# ── Shared constants ────────────────────────────────────────────────────────────

_VALID_PYTHON = "def hello():\n    return 'Hello, World!'\n"
_CORRECTED_PYTHON = "def hello():\n    return 'Corrected!'\n"


# ── Helpers ─────────────────────────────────────────────────────────────────────


def _init_repo(path: Path) -> Path:
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


def _make_llm_success(
    request_id: str, operation_id: str, content: str = _VALID_PYTHON
) -> AdapterExecutionResult:
    llm_request = LLMRequest(
        request_id=request_id,
        provider=LLMProvider.OLLAMA,
        model="test-model",
        system_prompt="",
        user_prompt="test",
        max_tokens=2048,
        temperature=0.0,
        streaming=False,
        structured_output_schema="",
        metadata=(),
    )
    llm_response = LLMResponse(
        request_id=request_id,
        provider=LLMProvider.OLLAMA,
        model="test-model",
        content=content,
        input_tokens=10,
        output_tokens=20,
        total_tokens=30,
        streaming_used=False,
        structured_output_used=False,
        finish_reason="stop",
        metadata=(),
    )
    return AdapterExecutionResult(
        request_id=request_id,
        operation_id=operation_id,
        provider=LLMProvider.OLLAMA,
        llm_request=llm_request,
        llm_response=llm_response,
        success=True,
        error=None,
        adapter_metadata=(("action", "generate"),),
    )


def _make_llm_failure(request_id: str, operation_id: str) -> AdapterExecutionResult:
    return AdapterExecutionResult(
        request_id=request_id,
        operation_id=operation_id,
        provider=LLMProvider.OLLAMA,
        llm_request=None,
        llm_response=None,
        success=False,
        error="connection_refused: ollama not available",
        adapter_metadata=(("action", "generate"),),
    )


# ── Fixtures ────────────────────────────────────────────────────────────────────


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


@pytest.fixture()
def repo(workspace: Path) -> Path:
    repo_path = workspace / "my-project"
    repo_path.mkdir()
    _init_repo(repo_path)
    (repo_path / ".gitkeep").write_text("")
    subprocess.run(["git", "add", ".gitkeep"], cwd=repo_path, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=repo_path, capture_output=True,
    )
    return repo_path


@pytest.fixture()
def gateway(workspace: Path) -> ExecutionGateway:
    gw = ExecutionGateway()
    gw.register(AdapterRegistration(
        adapter=ExecutionAdapter.LLM,
        adapter_id="llm-test",
        available=True,
        description="Mock LLM adapter",
    ))
    gw.register(AdapterRegistration(
        adapter=ExecutionAdapter.FILESYSTEM,
        adapter_id="filesystem-test",
        available=True,
        description="Test filesystem adapter",
    ))
    gw.register(AdapterRegistration(
        adapter=ExecutionAdapter.GIT,
        adapter_id="git-test",
        available=True,
        description="Test git adapter",
    ))
    gw.register(AdapterRegistration(
        adapter=ExecutionAdapter.VALIDATION,
        adapter_id="validation-test",
        available=True,
        description="Test validation adapter",
    ))
    return gw


@pytest.fixture()
def mock_llm() -> MagicMock:
    mock = MagicMock(spec=LlmAdapter)
    mock.execute.side_effect = lambda req, cfg: _make_llm_success(
        req.request_id, req.operation_id
    )
    return mock


@pytest.fixture()
def fs_adapter(workspace: Path) -> FilesystemAdapter:
    return FilesystemAdapter(workspace_root=workspace)


@pytest.fixture()
def git_adapter(workspace: Path) -> GitAdapter:
    return GitAdapter(workspace_root=workspace)


@pytest.fixture()
def validation_adapter(workspace: Path) -> ValidationAdapter:
    return ValidationAdapter(workspace_root=workspace)


@pytest.fixture()
def operation_engine() -> OperationEngine:
    return OperationEngine()


@pytest.fixture()
def config() -> WorkflowConfig:
    return WorkflowConfig(
        llm_provider=LLMProvider.OLLAMA,
        llm_model="test-model",
        llm_base_url="http://localhost:11434",
        llm_api_key="",
        llm_max_tokens=2048,
        llm_timeout_seconds=30,
        commit_message="feat: generated by Hermes (test)",
        max_corrections=3,
    )


@pytest.fixture()
def engine(
    gateway: ExecutionGateway,
    mock_llm: MagicMock,
    fs_adapter: FilesystemAdapter,
    git_adapter: GitAdapter,
    validation_adapter: ValidationAdapter,
    operation_engine: OperationEngine,
    config: WorkflowConfig,
) -> CorrectionEngine:
    return CorrectionEngine(
        gateway=gateway,
        llm_adapter=mock_llm,
        filesystem_adapter=fs_adapter,
        git_adapter=git_adapter,
        validation_adapter=validation_adapter,
        operation_engine=operation_engine,
        config=config,
    )


@pytest.fixture()
def goal(workspace: Path, repo: Path) -> FounderGoal:
    return FounderGoal(
        goal_id="test-goal-001",
        description="Write a Python function that returns Hello, World!",
        workspace_path=str(workspace),
        repository_path="my-project",
        output_path="",
    )


def _create_op(op_id: str = "op-alpha", target: str = "hello.py") -> PlannedOperation:
    return PlannedOperation(
        operation_id=op_id,
        target=target,
        intent="create",
        goal="Write a hello function.",
    )


def _modify_op(op_id: str = "op-alpha", target: str = "existing.py") -> PlannedOperation:
    return PlannedOperation(
        operation_id=op_id,
        target=target,
        intent="modify",
        goal="Modify the existing function.",
    )


# ── Tests ───────────────────────────────────────────────────────────────────────


class TestFirstAttemptSuccess:
    """No correction needed — first attempt clears all gates."""

    def test_create_mode_success(
        self, engine: CorrectionEngine, goal: FounderGoal, repo: Path
    ):
        result = engine.execute_operation(_create_op(), goal)
        assert result.success is True
        assert result.correction_attempts == 0
        assert result.correction_log == ()

    def test_create_mode_file_created(
        self, engine: CorrectionEngine, goal: FounderGoal, workspace: Path, repo: Path
    ):
        engine.execute_operation(_create_op(), goal)
        assert (workspace / "my-project" / "hello.py").exists()

    def test_create_mode_file_staged(
        self, engine: CorrectionEngine, goal: FounderGoal, workspace: Path, repo: Path
    ):
        engine.execute_operation(_create_op(), goal)
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=workspace / "my-project",
            capture_output=True, text=True,
        )
        # File staged (A) but not committed — CorrectionEngine does not commit
        assert "hello.py" in status.stdout

    def test_create_mode_steps_include_add_not_commit(
        self, engine: CorrectionEngine, goal: FounderGoal, repo: Path
    ):
        result = engine.execute_operation(_create_op(), goal)
        action_ids = [s.action_id for s in result.steps]
        assert "add" in action_ids
        assert "commit" not in action_ids

    def test_create_mode_pipeline_order(
        self, engine: CorrectionEngine, goal: FounderGoal, repo: Path
    ):
        result = engine.execute_operation(_create_op(), goal)
        action_ids = [s.action_id for s in result.steps]
        assert action_ids == ["generate", "create_file", "validate", "run_tests", "add"]

    def test_modify_mode_success(
        self, engine: CorrectionEngine, goal: FounderGoal, workspace: Path, repo: Path
    ):
        # Create and commit a file to modify
        f = workspace / "my-project" / "existing.py"
        f.write_text("# original\n")
        subprocess.run(["git", "add", "existing.py"], cwd=workspace / "my-project", capture_output=True)
        subprocess.run(["git", "commit", "-m", "add file"], cwd=workspace / "my-project", capture_output=True)

        result = engine.execute_operation(_modify_op(), goal)
        assert result.success is True
        assert result.correction_attempts == 0

    def test_modify_mode_pipeline_order(
        self, engine: CorrectionEngine, goal: FounderGoal, workspace: Path, repo: Path
    ):
        f = workspace / "my-project" / "existing.py"
        f.write_text("# original\n")
        subprocess.run(["git", "add", "existing.py"], cwd=workspace / "my-project", capture_output=True)
        subprocess.run(["git", "commit", "-m", "add file"], cwd=workspace / "my-project", capture_output=True)

        result = engine.execute_operation(_modify_op(), goal)
        action_ids = [s.action_id for s in result.steps]
        assert action_ids == [
            "read_file", "generate", "modify_file", "validate", "run_tests", "add"
        ]


class TestCorrectionOnValidateFailure:
    """Validate fails on first attempt; correction cycle succeeds."""

    def test_correction_attempts_is_one(
        self,
        gateway: ExecutionGateway,
        git_adapter: GitAdapter,
        fs_adapter: FilesystemAdapter,
        validation_adapter: ValidationAdapter,
        operation_engine: OperationEngine,
        config: WorkflowConfig,
        goal: FounderGoal,
        repo: Path,
        workspace: Path,
    ):
        call_count = {"n": 0}

        def llm_side_effect(req, cfg):
            call_count["n"] += 1
            # First generate call: produce invalid Python (triggers validate failure)
            if call_count["n"] == 1:
                return _make_llm_success(req.request_id, req.operation_id, "def bad syntax!!!")
            # Second call (correction): produce valid Python
            return _make_llm_success(req.request_id, req.operation_id, _VALID_PYTHON)

        mock_llm = MagicMock(spec=LlmAdapter)
        mock_llm.execute.side_effect = llm_side_effect

        engine = CorrectionEngine(
            gateway=gateway,
            llm_adapter=mock_llm,
            filesystem_adapter=fs_adapter,
            git_adapter=git_adapter,
            validation_adapter=validation_adapter,
            operation_engine=operation_engine,
            config=config,
        )
        result = engine.execute_operation(_create_op(), goal)
        assert result.success is True
        assert result.correction_attempts == 1

    def test_correction_log_has_one_record(
        self,
        gateway: ExecutionGateway,
        git_adapter: GitAdapter,
        fs_adapter: FilesystemAdapter,
        validation_adapter: ValidationAdapter,
        operation_engine: OperationEngine,
        config: WorkflowConfig,
        goal: FounderGoal,
        repo: Path,
        workspace: Path,
    ):
        call_count = {"n": 0}

        def llm_side_effect(req, cfg):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return _make_llm_success(req.request_id, req.operation_id, "def bad!!!")
            return _make_llm_success(req.request_id, req.operation_id, _VALID_PYTHON)

        mock_llm = MagicMock(spec=LlmAdapter)
        mock_llm.execute.side_effect = llm_side_effect

        engine = CorrectionEngine(
            gateway=gateway,
            llm_adapter=mock_llm,
            filesystem_adapter=fs_adapter,
            git_adapter=git_adapter,
            validation_adapter=validation_adapter,
            operation_engine=operation_engine,
            config=config,
        )
        result = engine.execute_operation(_create_op(), goal)
        assert len(result.correction_log) == 1
        assert result.correction_log[0].attempt == 1
        assert result.correction_log[0].trigger == "validation_failure"
        assert result.correction_log[0].error_excerpt  # non-empty

    def test_correction_record_is_lightweight(
        self,
        gateway: ExecutionGateway,
        git_adapter: GitAdapter,
        fs_adapter: FilesystemAdapter,
        validation_adapter: ValidationAdapter,
        operation_engine: OperationEngine,
        config: WorkflowConfig,
        goal: FounderGoal,
        repo: Path,
        workspace: Path,
    ):
        """CorrectionRecord is lightweight — stores only attempt, trigger,
        error_excerpt. No source_code, current_code, or steps fields."""
        call_count = {"n": 0}

        def llm_side_effect(req, cfg):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return _make_llm_success(req.request_id, req.operation_id, "def bad!!!")
            return _make_llm_success(req.request_id, req.operation_id, _VALID_PYTHON)

        mock_llm = MagicMock(spec=LlmAdapter)
        mock_llm.execute.side_effect = llm_side_effect

        engine = CorrectionEngine(
            gateway=gateway,
            llm_adapter=mock_llm,
            filesystem_adapter=fs_adapter,
            git_adapter=git_adapter,
            validation_adapter=validation_adapter,
            operation_engine=operation_engine,
            config=config,
        )
        result = engine.execute_operation(_create_op(), goal)
        assert len(result.correction_log) == 1
        record = result.correction_log[0]
        # CorrectionRecord must have exactly these three lightweight fields
        assert hasattr(record, "attempt")
        assert hasattr(record, "trigger")
        assert hasattr(record, "error_excerpt")
        # Must NOT carry full source code or step records
        assert not hasattr(record, "source_code")
        assert not hasattr(record, "current_code")
        assert not hasattr(record, "generated_code")
        assert not hasattr(record, "steps")


class TestCorrectionOnTestFailure:
    """Test gate fails on first attempt; correction cycle succeeds."""

    def test_test_failure_triggers_correction(
        self,
        gateway: ExecutionGateway,
        git_adapter: GitAdapter,
        fs_adapter: FilesystemAdapter,
        validation_adapter: ValidationAdapter,
        operation_engine: OperationEngine,
        goal: FounderGoal,
        repo: Path,
        workspace: Path,
    ):
        """First attempt passes validate but fails run_tests; correction succeeds."""
        config = WorkflowConfig(
            llm_provider=LLMProvider.OLLAMA,
            llm_model="test-model",
            llm_base_url="http://localhost:11434",
            llm_api_key="",
            llm_max_tokens=2048,
            llm_timeout_seconds=30,
            commit_message="feat: test",
            test_command="pytest",
            max_corrections=3,
        )

        call_count = {"n": 0}

        def llm_side_effect(req, cfg):
            call_count["n"] += 1
            return _make_llm_success(req.request_id, req.operation_id, _VALID_PYTHON)

        mock_llm = MagicMock(spec=LlmAdapter)
        mock_llm.execute.side_effect = llm_side_effect

        engine = CorrectionEngine(
            gateway=gateway,
            llm_adapter=mock_llm,
            filesystem_adapter=fs_adapter,
            git_adapter=git_adapter,
            validation_adapter=validation_adapter,
            operation_engine=operation_engine,
            config=config,
        )

        original_run = subprocess.run

        fail_once = {"done": False}

        def selective_run(cmd, **kwargs):
            if isinstance(cmd, list) and len(cmd) > 0 and cmd[0] == "pytest":
                if not fail_once["done"]:
                    fail_once["done"] = True
                    return type("R", (), {
                        "returncode": 1,
                        "stdout": "1 failed in 0.01s",
                        "stderr": "FAILED test_hello::test_basic",
                    })()
                return type("R", (), {
                    "returncode": 0,
                    "stdout": "1 passed in 0.01s",
                    "stderr": "",
                })()
            return original_run(cmd, **kwargs)

        with patch("subprocess.run", side_effect=selective_run):
            result = engine.execute_operation(_create_op(), goal)

        assert result.success is True
        assert result.correction_attempts == 1
        assert len(result.correction_log) == 1
        assert result.correction_log[0].trigger == "test_failure"
        assert "1 failed" in result.correction_log[0].error_excerpt


class TestMaxCorrectionsExceeded:
    """Correction limit exhausted → repair_limit_exceeded."""

    def test_returns_repair_limit_exceeded(
        self,
        gateway: ExecutionGateway,
        git_adapter: GitAdapter,
        fs_adapter: FilesystemAdapter,
        validation_adapter: ValidationAdapter,
        operation_engine: OperationEngine,
        goal: FounderGoal,
        repo: Path,
    ):
        config = WorkflowConfig(
            llm_provider=LLMProvider.OLLAMA,
            llm_model="test-model",
            llm_base_url="http://localhost:11434",
            llm_api_key="",
            llm_max_tokens=2048,
            llm_timeout_seconds=30,
            commit_message="feat: test",
            max_corrections=2,
        )

        mock_llm = MagicMock(spec=LlmAdapter)
        # Always return invalid Python so validate always fails
        mock_llm.execute.side_effect = lambda req, cfg: _make_llm_success(
            req.request_id, req.operation_id, "def bad syntax!!!"
        )

        engine = CorrectionEngine(
            gateway=gateway,
            llm_adapter=mock_llm,
            filesystem_adapter=fs_adapter,
            git_adapter=git_adapter,
            validation_adapter=validation_adapter,
            operation_engine=operation_engine,
            config=config,
        )
        result = engine.execute_operation(_create_op(), goal)
        assert result.success is False
        assert result.error == "repair_limit_exceeded"

    def test_correction_attempts_equals_max(
        self,
        gateway: ExecutionGateway,
        git_adapter: GitAdapter,
        fs_adapter: FilesystemAdapter,
        validation_adapter: ValidationAdapter,
        operation_engine: OperationEngine,
        goal: FounderGoal,
        repo: Path,
    ):
        config = WorkflowConfig(
            llm_provider=LLMProvider.OLLAMA,
            llm_model="test-model",
            llm_base_url="http://localhost:11434",
            llm_api_key="",
            llm_max_tokens=2048,
            llm_timeout_seconds=30,
            commit_message="feat: test",
            max_corrections=2,
        )

        mock_llm = MagicMock(spec=LlmAdapter)
        mock_llm.execute.side_effect = lambda req, cfg: _make_llm_success(
            req.request_id, req.operation_id, "def bad syntax!!!"
        )

        engine = CorrectionEngine(
            gateway=gateway,
            llm_adapter=mock_llm,
            filesystem_adapter=fs_adapter,
            git_adapter=git_adapter,
            validation_adapter=validation_adapter,
            operation_engine=operation_engine,
            config=config,
        )
        result = engine.execute_operation(_create_op(), goal)
        assert result.correction_attempts == 2
        assert len(result.correction_log) == 2

    def test_no_commit_after_exhaustion(
        self,
        gateway: ExecutionGateway,
        git_adapter: GitAdapter,
        fs_adapter: FilesystemAdapter,
        validation_adapter: ValidationAdapter,
        operation_engine: OperationEngine,
        goal: FounderGoal,
        workspace: Path,
        repo: Path,
    ):
        config = WorkflowConfig(
            llm_provider=LLMProvider.OLLAMA,
            llm_model="test-model",
            llm_base_url="http://localhost:11434",
            llm_api_key="",
            llm_max_tokens=2048,
            llm_timeout_seconds=30,
            commit_message="feat: test",
            max_corrections=1,
        )
        mock_llm = MagicMock(spec=LlmAdapter)
        mock_llm.execute.side_effect = lambda req, cfg: _make_llm_success(
            req.request_id, req.operation_id, "def bad syntax!!!"
        )
        engine = CorrectionEngine(
            gateway=gateway,
            llm_adapter=mock_llm,
            filesystem_adapter=fs_adapter,
            git_adapter=git_adapter,
            validation_adapter=validation_adapter,
            operation_engine=operation_engine,
            config=config,
        )
        result = engine.execute_operation(_create_op(), goal)
        assert result.success is False
        # No git add or commit in steps
        action_ids = [s.action_id for s in result.steps]
        assert "add" not in action_ids
        assert "commit" not in action_ids


class TestNonCorrectableFailure:
    """Failures outside validate/run_tests are not corrected (correction_attempts=0)."""

    def test_llm_generate_failure_not_corrected(
        self,
        gateway: ExecutionGateway,
        git_adapter: GitAdapter,
        fs_adapter: FilesystemAdapter,
        validation_adapter: ValidationAdapter,
        operation_engine: OperationEngine,
        config: WorkflowConfig,
        goal: FounderGoal,
        repo: Path,
    ):
        mock_llm = MagicMock(spec=LlmAdapter)
        mock_llm.execute.side_effect = lambda req, cfg: _make_llm_failure(
            req.request_id, req.operation_id
        )
        engine = CorrectionEngine(
            gateway=gateway,
            llm_adapter=mock_llm,
            filesystem_adapter=fs_adapter,
            git_adapter=git_adapter,
            validation_adapter=validation_adapter,
            operation_engine=operation_engine,
            config=config,
        )
        result = engine.execute_operation(_create_op(), goal)
        assert result.success is False
        assert result.correction_attempts == 0
        assert result.correction_log == ()

    def test_gateway_dispatch_failure_not_corrected(
        self,
        mock_llm: MagicMock,
        git_adapter: GitAdapter,
        fs_adapter: FilesystemAdapter,
        validation_adapter: ValidationAdapter,
        operation_engine: OperationEngine,
        config: WorkflowConfig,
        goal: FounderGoal,
        repo: Path,
    ):
        empty_gw = ExecutionGateway()  # no adapters registered
        engine = CorrectionEngine(
            gateway=empty_gw,
            llm_adapter=mock_llm,
            filesystem_adapter=fs_adapter,
            git_adapter=git_adapter,
            validation_adapter=validation_adapter,
            operation_engine=operation_engine,
            config=config,
        )
        result = engine.execute_operation(_create_op(), goal)
        assert result.success is False
        assert result.correction_attempts == 0
        mock_llm.execute.assert_not_called()

    def test_never_raises(
        self,
        gateway: ExecutionGateway,
        git_adapter: GitAdapter,
        fs_adapter: FilesystemAdapter,
        validation_adapter: ValidationAdapter,
        operation_engine: OperationEngine,
        config: WorkflowConfig,
        goal: FounderGoal,
        repo: Path,
    ):
        mock_llm = MagicMock(spec=LlmAdapter)
        mock_llm.execute.side_effect = RuntimeError("unexpected crash")
        engine = CorrectionEngine(
            gateway=gateway,
            llm_adapter=mock_llm,
            filesystem_adapter=fs_adapter,
            git_adapter=git_adapter,
            validation_adapter=validation_adapter,
            operation_engine=operation_engine,
            config=config,
        )
        result = engine.execute_operation(_create_op(), goal)
        assert isinstance(result, OperationCorrectionResult)
        assert result.success is False


class TestMultipleCorrections:
    """Two consecutive correction cycles before success."""

    def test_two_corrections_before_success(
        self,
        gateway: ExecutionGateway,
        git_adapter: GitAdapter,
        fs_adapter: FilesystemAdapter,
        validation_adapter: ValidationAdapter,
        operation_engine: OperationEngine,
        goal: FounderGoal,
        repo: Path,
    ):
        config = WorkflowConfig(
            llm_provider=LLMProvider.OLLAMA,
            llm_model="test-model",
            llm_base_url="http://localhost:11434",
            llm_api_key="",
            llm_max_tokens=2048,
            llm_timeout_seconds=30,
            commit_message="feat: test",
            max_corrections=3,
        )

        call_count = {"n": 0}

        def llm_side_effect(req, cfg):
            call_count["n"] += 1
            # Initial generate (1) and first correction (2): invalid Python
            # Second correction (3): valid Python
            if call_count["n"] <= 2:
                return _make_llm_success(req.request_id, req.operation_id, "def bad!!!")
            return _make_llm_success(req.request_id, req.operation_id, _VALID_PYTHON)

        mock_llm = MagicMock(spec=LlmAdapter)
        mock_llm.execute.side_effect = llm_side_effect

        engine = CorrectionEngine(
            gateway=gateway,
            llm_adapter=mock_llm,
            filesystem_adapter=fs_adapter,
            git_adapter=git_adapter,
            validation_adapter=validation_adapter,
            operation_engine=operation_engine,
            config=config,
        )
        result = engine.execute_operation(_create_op(), goal)
        assert result.success is True
        assert result.correction_attempts == 2
        assert len(result.correction_log) == 2
        assert result.correction_log[0].attempt == 1
        assert result.correction_log[1].attempt == 2


class TestMaxCorrectionsZero:
    """max_corrections=0 disables the correction loop entirely."""

    def test_validate_failure_halts_immediately(
        self,
        gateway: ExecutionGateway,
        git_adapter: GitAdapter,
        fs_adapter: FilesystemAdapter,
        validation_adapter: ValidationAdapter,
        operation_engine: OperationEngine,
        goal: FounderGoal,
        repo: Path,
    ):
        config = WorkflowConfig(
            llm_provider=LLMProvider.OLLAMA,
            llm_model="test-model",
            llm_base_url="http://localhost:11434",
            llm_api_key="",
            llm_max_tokens=2048,
            llm_timeout_seconds=30,
            commit_message="feat: test",
            max_corrections=0,
        )
        mock_llm = MagicMock(spec=LlmAdapter)
        mock_llm.execute.side_effect = lambda req, cfg: _make_llm_success(
            req.request_id, req.operation_id, "def bad syntax!!!"
        )
        engine = CorrectionEngine(
            gateway=gateway,
            llm_adapter=mock_llm,
            filesystem_adapter=fs_adapter,
            git_adapter=git_adapter,
            validation_adapter=validation_adapter,
            operation_engine=operation_engine,
            config=config,
        )
        result = engine.execute_operation(_create_op(), goal)
        assert result.success is False
        # Should be the validate failure error, NOT "repair_limit_exceeded"
        assert result.error != "repair_limit_exceeded"
        assert result.correction_attempts == 0
        assert result.correction_log == ()


class TestStepIdNamespacing:
    """Step IDs use PlannedOperation.operation_id prefix, not goal_id."""

    def test_step_ids_contain_operation_id(
        self, engine: CorrectionEngine, goal: FounderGoal, repo: Path
    ):
        op = _create_op(op_id="op-my-unique-op", target="output.py")
        result = engine.execute_operation(op, goal)
        for step in result.steps:
            # Every step's operation_id must reference our specific planned op id
            assert "op-my-unique-op" in step.operation_id, (
                f"Step {step.step_id!r} has operation_id {step.operation_id!r} "
                "which does not contain the planned operation_id"
            )

    def test_two_operations_have_distinct_step_ids(
        self,
        gateway: ExecutionGateway,
        git_adapter: GitAdapter,
        fs_adapter: FilesystemAdapter,
        validation_adapter: ValidationAdapter,
        operation_engine: OperationEngine,
        config: WorkflowConfig,
        goal: FounderGoal,
        workspace: Path,
        repo: Path,
        mock_llm: MagicMock,
    ):
        """Steps from different planned operations must have distinct IDs."""
        engine = CorrectionEngine(
            gateway=gateway,
            llm_adapter=mock_llm,
            filesystem_adapter=fs_adapter,
            git_adapter=git_adapter,
            validation_adapter=validation_adapter,
            operation_engine=operation_engine,
            config=config,
        )
        op_a = _create_op(op_id="op-alpha", target="alpha.py")
        op_b = _create_op(op_id="op-beta", target="beta.py")

        result_a = engine.execute_operation(op_a, goal)
        result_b = engine.execute_operation(op_b, goal)

        step_ids_a = {s.step_id for s in result_a.steps}
        step_ids_b = {s.step_id for s in result_b.steps}

        assert step_ids_a.isdisjoint(step_ids_b), (
            f"Operations share step IDs: {step_ids_a & step_ids_b}"
        )
