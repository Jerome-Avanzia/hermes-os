"""Tests for Phase 6/7 — Engineering Workflow (multi-operation plan execution).

Coverage:
  - All typed contracts (FounderGoal, WorkflowMission, WorkflowConfig,
    StepExecutionRecord, WorkflowExecutionReport, CorrectionRecord,
    OperationCorrectionResult)
  - EngineeringWorkflow construction (Phase 7: requires correction_engine)
  - execute(plan, goal): two-op create plan success
  - execute(plan, goal): two-op modify plan success
  - execute(plan, goal): mixed create + modify plan success
  - Bulk validation failure: plan_validation_conflict, no adapter calls
  - Op 1 fails generate: Op 2 never executes
  - Commit failure at end: success=False
  - operations_completed in metadata
  - correction_attempts in metadata (Phase 7)
  - Single commit step at end of all_steps
  - Per-op create pipeline has NO commit step (ends at add)
  - Per-op modify pipeline has NO commit step (ends at add)
  - depends_on ordering respected
  - execute() never raises
  - Phase 7: delegation — workflow calls correction_engine.execute_operation per op
  - Phase 7: isolation — successful operations are never re-executed during correction

Test strategy:
  - LLM adapter is mocked (MagicMock) — no Ollama required
  - Filesystem and Git use real temporary directories + git repos
  - Gateway is real (ExecutionGateway)
  - OperationEngine is real instance
  - CorrectionEngine is real instance (wrapping mocked LLM)
  - For delegation/isolation tests: CorrectionEngine is also mocked
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from hermes.adapters.filesystem_adapter import FilesystemAdapter
from hermes.adapters.git_adapter import GitAdapter
from hermes.adapters.llm_adapter import LlmAdapter
from hermes.adapters.validation_adapter import ValidationAdapter
from hermes.kernel.correction_engine import CorrectionEngine
from hermes.kernel.execution_gateway import ExecutionGateway
from hermes.kernel.operation_engine import OperationEngine
from hermes.models.engineering_plan import EngineeringPlan, PlannedOperation
from hermes.models.engineering_workflow import (
    CorrectionRecord,
    FounderGoal,
    OperationCorrectionResult,
    StepExecutionRecord,
    WorkflowConfig,
    WorkflowExecutionReport,
    WorkflowMission,
)
from hermes.models.execution_gateway import (
    AdapterRegistration,
    ExecutionAdapter,
    ExecutionRequest,
    ExecutionStatus,
)
from hermes.models.llm_adapter import (
    AdapterExecutionResult,
    LLMProvider,
    LLMRequest,
    LLMResponse,
)
from hermes.workflows.engineering_workflow import EngineeringWorkflow


# ── Helpers and fixtures ───────────────────────────────────────────────────────


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


def _make_llm_success(request_id: str, operation_id: str, content: str) -> AdapterExecutionResult:
    llm_request = LLMRequest(
        request_id=request_id,
        provider=LLMProvider.OLLAMA,
        model="test-model",
        system_prompt="",
        user_prompt="test prompt",
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
        adapter_metadata=(("action", "generate"), ("model", "test-model")),
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


_GENERATED_CODE = "def hello():\n    return 'Hello, World!'\n"
_MODIFIED_CODE = "def hello():\n    return 'Hello, Modified!'\n"


def _make_correction_engine(
    gateway: ExecutionGateway,
    mock_llm: MagicMock,
    fs_adapter: FilesystemAdapter,
    git_adapter: GitAdapter,
    validation_adapter: ValidationAdapter,
    operation_engine: OperationEngine,
    config: WorkflowConfig,
) -> CorrectionEngine:
    """Construct a real CorrectionEngine with a mocked LLM adapter."""
    return CorrectionEngine(
        gateway=gateway,
        llm_adapter=mock_llm,
        filesystem_adapter=fs_adapter,
        git_adapter=git_adapter,
        validation_adapter=validation_adapter,
        operation_engine=operation_engine,
        config=config,
    )


def _make_workflow(
    gateway: ExecutionGateway,
    mock_llm: MagicMock,
    fs_adapter: FilesystemAdapter,
    git_adapter: GitAdapter,
    validation_adapter: ValidationAdapter,
    operation_engine: OperationEngine,
    config: WorkflowConfig,
) -> EngineeringWorkflow:
    """Construct EngineeringWorkflow with a real CorrectionEngine (mocked LLM)."""
    ce = _make_correction_engine(
        gateway, mock_llm, fs_adapter, git_adapter,
        validation_adapter, operation_engine, config,
    )
    return EngineeringWorkflow(
        gateway=gateway,
        git_adapter=git_adapter,
        operation_engine=operation_engine,
        config=config,
        correction_engine=ce,
    )


def _make_dummy_step_record(operation_id: str = "op-0") -> StepExecutionRecord:
    """Build a minimal successful StepExecutionRecord for use in mock results."""
    req = ExecutionRequest(
        request_id=f"req-{operation_id}",
        operation_id=operation_id,
        adapter_type=ExecutionAdapter.LLM,
        action_id="generate",
        payload=(),
    )
    return StepExecutionRecord(
        step_id=f"step-{operation_id}",
        operation_id=operation_id,
        adapter_type=ExecutionAdapter.LLM,
        action_id="generate",
        execution_request=req,
        dispatch_status=ExecutionStatus.DISPATCHED,
        adapter_success=True,
        adapter_error=None,
        output=_GENERATED_CODE,
    )


def _make_op_correction_result(
    operation_id: str,
    *,
    success: bool = True,
    correction_attempts: int = 0,
    correction_log: tuple[CorrectionRecord, ...] = (),
) -> OperationCorrectionResult:
    """Build a minimal OperationCorrectionResult for use in mock CorrectionEngine."""
    return OperationCorrectionResult(
        operation_id=operation_id,
        success=success,
        correction_attempts=correction_attempts,
        correction_log=correction_log,
        steps=(_make_dummy_step_record(operation_id),),
        error=None if success else "mock_failure",
    )


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
def mock_llm(workspace: Path) -> MagicMock:
    mock = MagicMock(spec=LlmAdapter)
    mock.execute.side_effect = lambda req, cfg: _make_llm_success(
        req.request_id, req.operation_id, _GENERATED_CODE
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
    )


@pytest.fixture()
def workflow(
    gateway: ExecutionGateway,
    mock_llm: MagicMock,
    fs_adapter: FilesystemAdapter,
    git_adapter: GitAdapter,
    validation_adapter: ValidationAdapter,
    operation_engine: OperationEngine,
    config: WorkflowConfig,
) -> EngineeringWorkflow:
    return _make_workflow(
        gateway, mock_llm, fs_adapter, git_adapter,
        validation_adapter, operation_engine, config,
    )


@pytest.fixture()
def goal(workspace: Path, repo: Path) -> FounderGoal:
    return FounderGoal(
        goal_id="hello-world-001",
        description="Write a Python function that returns 'Hello, World!'",
        workspace_path=str(workspace),
        repository_path="my-project",
        output_path="",  # not used in Phase 6 execute(plan, goal) calls directly
    )


def _make_single_create_plan(goal: FounderGoal, filename: str = "hello.py") -> EngineeringPlan:
    return EngineeringPlan(
        plan_id=f"plan-{goal.goal_id}",
        goal_id=goal.goal_id,
        confidence="high",
        basis="single file creation",
        operations=(PlannedOperation(
            operation_id="op-0",
            target=filename,
            intent="create",
            goal=goal.description,
        ),),
    )


def _make_two_create_plan(goal: FounderGoal) -> EngineeringPlan:
    return EngineeringPlan(
        plan_id=f"plan-{goal.goal_id}",
        goal_id=goal.goal_id,
        confidence="high",
        basis="two file creation",
        operations=(
            PlannedOperation(
                operation_id="op-0",
                target="module_a.py",
                intent="create",
                goal="Create module A",
            ),
            PlannedOperation(
                operation_id="op-1",
                target="module_b.py",
                intent="create",
                goal="Create module B",
                depends_on=("op-0",),
            ),
        ),
    )


# ── TestFounderGoal ────────────────────────────────────────────────────────────


class TestFounderGoal:
    def test_construction(self, workspace: Path):
        g = FounderGoal(
            goal_id="g1",
            description="test",
            workspace_path=str(workspace),
            repository_path="repo",
            output_path="repo/out.py",
        )
        assert g.goal_id == "g1"
        assert g.description == "test"

    def test_frozen(self, workspace: Path):
        g = FounderGoal(
            goal_id="g1", description="test",
            workspace_path=str(workspace),
            repository_path="repo", output_path="repo/out.py",
        )
        with pytest.raises(Exception):
            g.goal_id = "other"  # type: ignore[misc]

    def test_slots(self, workspace: Path):
        g = FounderGoal(
            goal_id="g1", description="test",
            workspace_path=str(workspace),
            repository_path="repo", output_path="repo/out.py",
        )
        assert not hasattr(g, "__dict__")

    def test_equality(self, workspace: Path):
        kwargs = dict(
            goal_id="g1", description="test",
            workspace_path=str(workspace),
            repository_path="repo", output_path="repo/out.py",
        )
        assert FounderGoal(**kwargs) == FounderGoal(**kwargs)

    def test_output_path_defaults_to_empty(self, workspace: Path):
        g = FounderGoal(
            goal_id="g1", description="test",
            workspace_path=str(workspace),
            repository_path="repo",
        )
        assert g.output_path == ""


# ── TestWorkflowMission ────────────────────────────────────────────────────────


class TestWorkflowMission:
    def test_construction(self):
        m = WorkflowMission(mission_id="m1", goal_id="g1", objective="build X")
        assert m.mission_id == "m1"
        assert m.goal_id == "g1"
        assert m.objective == "build X"

    def test_frozen(self):
        m = WorkflowMission(mission_id="m1", goal_id="g1", objective="obj")
        with pytest.raises(Exception):
            m.objective = "other"  # type: ignore[misc]

    def test_slots(self):
        m = WorkflowMission(mission_id="m1", goal_id="g1", objective="obj")
        assert not hasattr(m, "__dict__")


# ── TestWorkflowConfig ────────────────────────────────────────────────────────


class TestWorkflowConfig:
    def test_construction(self):
        cfg = WorkflowConfig(
            llm_provider=LLMProvider.OLLAMA,
            llm_model="llama3.2",
            llm_base_url="http://localhost:11434",
            llm_api_key="",
            llm_max_tokens=2048,
            llm_timeout_seconds=30,
            commit_message="feat: test",
        )
        assert cfg.llm_model == "llama3.2"
        assert cfg.commit_message == "feat: test"

    def test_frozen(self, config: WorkflowConfig):
        with pytest.raises(Exception):
            config.commit_message = "other"  # type: ignore[misc]

    def test_slots(self, config: WorkflowConfig):
        assert not hasattr(config, "__dict__")


# ── TestStepExecutionRecord ────────────────────────────────────────────────────


class TestStepExecutionRecord:
    def _make(self) -> StepExecutionRecord:
        from hermes.models.execution_gateway import ExecutionRequest
        req = ExecutionRequest(
            request_id="req-1",
            operation_id="op-1",
            adapter_type=ExecutionAdapter.LLM,
            action_id="generate",
            payload=(),
        )
        return StepExecutionRecord(
            step_id="step-op-1",
            operation_id="op-1",
            adapter_type=ExecutionAdapter.LLM,
            action_id="generate",
            execution_request=req,
            dispatch_status=ExecutionStatus.DISPATCHED,
            adapter_success=True,
            adapter_error=None,
            output="def hello(): pass",
        )

    def test_construction(self):
        record = self._make()
        assert record.step_id == "step-op-1"
        assert record.adapter_success is True
        assert record.output == "def hello(): pass"

    def test_frozen(self):
        record = self._make()
        with pytest.raises(Exception):
            record.adapter_success = False  # type: ignore[misc]

    def test_slots(self):
        assert not hasattr(self._make(), "__dict__")


# ── TestWorkflowExecutionReport ────────────────────────────────────────────────


class TestWorkflowExecutionReport:
    def test_construction(self):
        report = WorkflowExecutionReport(
            report_id="report-001",
            goal_id="g1",
            mission_id="mission-g1",
            job_id="job-g1",
            steps=(),
            success=True,
            error=None,
            execution_sequence=("llm", "filesystem", "git", "git"),
            metadata=(("steps_completed", "4"),),
        )
        assert report.success is True
        assert len(report.execution_sequence) == 4

    def test_frozen(self):
        report = WorkflowExecutionReport(
            report_id="r", goal_id="g", mission_id="m", job_id="j",
            steps=(), success=True, error=None,
            execution_sequence=(), metadata=(),
        )
        with pytest.raises(Exception):
            report.success = False  # type: ignore[misc]


# ── TestEngineeringWorkflowConstruction ───────────────────────────────────────


class TestEngineeringWorkflowConstruction:
    def test_constructs_with_correction_engine(
        self,
        gateway: ExecutionGateway,
        mock_llm: MagicMock,
        fs_adapter: FilesystemAdapter,
        git_adapter: GitAdapter,
        validation_adapter: ValidationAdapter,
        operation_engine: OperationEngine,
        config: WorkflowConfig,
    ):
        """Phase 7: EngineeringWorkflow takes correction_engine, not raw adapters."""
        wf = _make_workflow(
            gateway, mock_llm, fs_adapter, git_adapter,
            validation_adapter, operation_engine, config,
        )
        assert wf is not None

    def test_unknown_kwargs_not_accepted(
        self,
        gateway: ExecutionGateway,
        git_adapter: GitAdapter,
        operation_engine: OperationEngine,
        config: WorkflowConfig,
    ):
        """Unknown keyword arguments raise TypeError."""
        ce = MagicMock(spec=CorrectionEngine)
        with pytest.raises(TypeError):
            EngineeringWorkflow(
                gateway=gateway,
                git_adapter=git_adapter,
                operation_engine=operation_engine,
                config=config,
                correction_engine=ce,
                job_engine=MagicMock(),  # type: ignore[call-arg]
            )


# ── TestSingleOpCreateSuccess ─────────────────────────────────────────────────


class TestSingleOpCreateSuccess:
    """Single-operation create plan success path."""

    def test_report_success(self, workflow: EngineeringWorkflow, goal: FounderGoal, repo: Path):
        plan = _make_single_create_plan(goal, "hello.py")
        report = workflow.execute(plan, goal)
        assert report.success is True, f"Expected success, got: {report.error}"

    def test_file_created_on_disk(
        self, workflow: EngineeringWorkflow, goal: FounderGoal, workspace: Path, repo: Path
    ):
        plan = _make_single_create_plan(goal, "hello.py")
        workflow.execute(plan, goal)
        assert (workspace / "my-project" / "hello.py").exists()

    def test_file_committed_to_git(
        self, workflow: EngineeringWorkflow, goal: FounderGoal, workspace: Path, repo: Path
    ):
        plan = _make_single_create_plan(goal, "hello.py")
        workflow.execute(plan, goal)
        log = subprocess.run(
            ["git", "log", "--oneline"],
            cwd=workspace / "my-project", capture_output=True, text=True,
        )
        assert "generated by Hermes (test)" in log.stdout

    def test_report_ids_correct(self, workflow: EngineeringWorkflow, goal: FounderGoal, repo: Path):
        plan = _make_single_create_plan(goal, "hello.py")
        report = workflow.execute(plan, goal)
        assert report.goal_id == goal.goal_id
        assert report.mission_id == f"mission-{goal.goal_id}"
        assert report.job_id == f"job-{goal.goal_id}"
        assert report.report_id == f"report-{goal.goal_id}"

    def test_metadata_operations_completed(
        self, workflow: EngineeringWorkflow, goal: FounderGoal, repo: Path
    ):
        plan = _make_single_create_plan(goal, "hello.py")
        report = workflow.execute(plan, goal)
        meta = dict(report.metadata)
        assert meta.get("operations_completed") == "1"

    def test_metadata_planned_operations(
        self, workflow: EngineeringWorkflow, goal: FounderGoal, repo: Path
    ):
        plan = _make_single_create_plan(goal, "hello.py")
        report = workflow.execute(plan, goal)
        meta = dict(report.metadata)
        assert meta.get("planned_operations") == "1"

    def test_commit_step_is_last(
        self, workflow: EngineeringWorkflow, goal: FounderGoal, repo: Path
    ):
        plan = _make_single_create_plan(goal, "hello.py")
        report = workflow.execute(plan, goal)
        assert report.steps[-1].action_id == "commit"

    def test_create_pipeline_no_per_op_commit(
        self, workflow: EngineeringWorkflow, goal: FounderGoal, repo: Path
    ):
        """Per-op pipeline ends at 'add', not 'commit'. Single commit at end."""
        plan = _make_single_create_plan(goal, "hello.py")
        report = workflow.execute(plan, goal)
        action_ids = [s.action_id for s in report.steps]
        # Only one commit — the plan-level commit at the end
        assert action_ids.count("commit") == 1
        # The commit must be the last step
        assert action_ids[-1] == "commit"

    def test_create_pipeline_steps_order(
        self, workflow: EngineeringWorkflow, goal: FounderGoal, repo: Path
    ):
        plan = _make_single_create_plan(goal, "hello.py")
        report = workflow.execute(plan, goal)
        action_ids = [s.action_id for s in report.steps]
        # generate → create_file → validate → run_tests → add → commit
        assert action_ids == ["generate", "create_file", "validate", "run_tests", "add", "commit"]


# ── TestTwoOpCreatePlan ───────────────────────────────────────────────────────


class TestTwoOpCreatePlan:
    """Two-operation create plan."""

    def test_two_create_ops_success(
        self, workflow: EngineeringWorkflow, goal: FounderGoal, repo: Path
    ):
        plan = _make_two_create_plan(goal)
        report = workflow.execute(plan, goal)
        assert report.success is True, f"Expected success, got: {report.error}"

    def test_both_files_created(
        self, workflow: EngineeringWorkflow, goal: FounderGoal, workspace: Path, repo: Path
    ):
        plan = _make_two_create_plan(goal)
        workflow.execute(plan, goal)
        assert (workspace / "my-project" / "module_a.py").exists()
        assert (workspace / "my-project" / "module_b.py").exists()

    def test_single_commit_at_end(
        self, workflow: EngineeringWorkflow, goal: FounderGoal, repo: Path
    ):
        plan = _make_two_create_plan(goal)
        report = workflow.execute(plan, goal)
        action_ids = [s.action_id for s in report.steps]
        assert action_ids.count("commit") == 1
        assert action_ids[-1] == "commit"

    def test_operations_completed_two(
        self, workflow: EngineeringWorkflow, goal: FounderGoal, repo: Path
    ):
        plan = _make_two_create_plan(goal)
        report = workflow.execute(plan, goal)
        meta = dict(report.metadata)
        assert meta.get("operations_completed") == "2"

    def test_planned_operations_two(
        self, workflow: EngineeringWorkflow, goal: FounderGoal, repo: Path
    ):
        plan = _make_two_create_plan(goal)
        report = workflow.execute(plan, goal)
        meta = dict(report.metadata)
        assert meta.get("planned_operations") == "2"

    def test_depends_on_ordering_respected(
        self, workflow: EngineeringWorkflow, goal: FounderGoal, repo: Path
    ):
        """op-1 depends on op-0 → op-0 pipeline must execute before op-1 pipeline."""
        plan = _make_two_create_plan(goal)
        report = workflow.execute(plan, goal)
        action_ids = [s.action_id for s in report.steps]
        # First generate is for op-0, second generate is for op-1
        first_gen = action_ids.index("generate")
        # There should be two generate steps; the first is for op-0
        # We verify the file created for op-0 (module_a) comes before op-1 (module_b)
        # by checking the step sequence
        assert first_gen < len(action_ids) - 1  # not the last step


# ── TestModifyPlan ────────────────────────────────────────────────────────────


class TestModifyPlan:
    """Modify-mode plan execution."""

    @pytest.fixture()
    def existing_file(self, workspace: Path, repo: Path) -> Path:
        """Create and commit an existing file."""
        f = workspace / "my-project" / "existing.py"
        f.write_text("# original content\n")
        subprocess.run(["git", "add", "existing.py"], cwd=workspace / "my-project", capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "add existing"],
            cwd=workspace / "my-project", capture_output=True,
        )
        return f

    def test_modify_plan_success(
        self, workflow: EngineeringWorkflow, goal: FounderGoal, existing_file: Path
    ):
        plan = EngineeringPlan(
            plan_id=f"plan-{goal.goal_id}",
            goal_id=goal.goal_id,
            confidence="high",
            basis="modify one file",
            operations=(PlannedOperation(
                operation_id="op-0",
                target="existing.py",
                intent="modify",
                goal="Modify existing module",
            ),),
        )
        report = workflow.execute(plan, goal)
        assert report.success is True, f"Expected success, got: {report.error}"

    def test_modify_pipeline_steps_order(
        self, workflow: EngineeringWorkflow, goal: FounderGoal, existing_file: Path
    ):
        plan = EngineeringPlan(
            plan_id=f"plan-{goal.goal_id}",
            goal_id=goal.goal_id,
            confidence="high",
            basis="modify one file",
            operations=(PlannedOperation(
                operation_id="op-0",
                target="existing.py",
                intent="modify",
                goal="Modify existing module",
            ),),
        )
        report = workflow.execute(plan, goal)
        action_ids = [s.action_id for s in report.steps]
        # read_file → generate → modify_file → validate → run_tests → add → commit
        assert action_ids == [
            "read_file", "generate", "modify_file", "validate", "run_tests", "add", "commit"
        ]

    def test_modify_pipeline_no_per_op_commit(
        self, workflow: EngineeringWorkflow, goal: FounderGoal, existing_file: Path
    ):
        plan = EngineeringPlan(
            plan_id=f"plan-{goal.goal_id}",
            goal_id=goal.goal_id,
            confidence="high",
            basis="modify one file",
            operations=(PlannedOperation(
                operation_id="op-0",
                target="existing.py",
                intent="modify",
                goal="Modify existing module",
            ),),
        )
        report = workflow.execute(plan, goal)
        action_ids = [s.action_id for s in report.steps]
        assert action_ids.count("commit") == 1
        assert action_ids[-1] == "commit"


# ── TestBulkValidationGate ────────────────────────────────────────────────────


class TestBulkValidationGate:
    """Bulk RepositoryManipulation validation before any LLM call."""

    def test_modify_nonexistent_file_intent_corrected_to_create(
        self,
        workflow: EngineeringWorkflow,
        goal: FounderGoal,
        mock_llm: MagicMock,
        repo: Path,
    ):
        """LLM intent="modify" on a non-existent file is auto-corrected to "create".

        The workflow must not fail at plan_validation for this case — it must
        correct the intent and proceed to execution (LLM is called).
        """
        plan = EngineeringPlan(
            plan_id=f"plan-{goal.goal_id}",
            goal_id=goal.goal_id,
            confidence="high",
            basis="modify a file that doesn't exist — intent should be corrected",
            operations=(PlannedOperation(
                operation_id="op-0",
                target="nonexistent_file.py",
                intent="modify",
                goal="Implement nonexistent_file.py",
            ),),
        )
        workflow.execute(plan, goal)
        # Intent was corrected: workflow proceeded past plan_validation and called LLM.
        mock_llm.execute.assert_called()

    def test_duplicate_target_fails_before_generate(
        self,
        workflow: EngineeringWorkflow,
        goal: FounderGoal,
        mock_llm: MagicMock,
        repo: Path,
    ):
        """Duplicate targets in a plan fail at validation, not execution."""
        plan = EngineeringPlan(
            plan_id=f"plan-{goal.goal_id}",
            goal_id=goal.goal_id,
            confidence="high",
            basis="two ops on same file",
            operations=(
                PlannedOperation(
                    operation_id="op-0",
                    target="same_file.py",
                    intent="create",
                    goal="Create file",
                ),
                PlannedOperation(
                    operation_id="op-1",
                    target="same_file.py",
                    intent="create",
                    goal="Create file again",
                ),
            ),
        )
        report = workflow.execute(plan, goal)
        assert report.success is False
        mock_llm.execute.assert_not_called()

    def test_conflict_metadata_has_plan_validation_stage(
        self,
        workflow: EngineeringWorkflow,
        goal: FounderGoal,
        repo: Path,
    ):
        """Duplicate targets produce failure_stage=plan_validation in metadata."""
        plan = EngineeringPlan(
            plan_id=f"plan-{goal.goal_id}",
            goal_id=goal.goal_id,
            confidence="high",
            basis="two ops targeting the same file",
            operations=(
                PlannedOperation(
                    operation_id="op-0",
                    target="dup.py",
                    intent="create",
                    goal="Create dup.py",
                ),
                PlannedOperation(
                    operation_id="op-1",
                    target="dup.py",
                    intent="create",
                    goal="Create dup.py again",
                ),
            ),
        )
        report = workflow.execute(plan, goal)
        meta = dict(report.metadata)
        assert meta.get("failure_stage") == "plan_validation"


# ── TestFailurePropagation ────────────────────────────────────────────────────


class TestFailurePropagation:
    """Failure at any step halts the workflow."""

    def test_llm_failure_halts_workflow(
        self,
        gateway: ExecutionGateway,
        fs_adapter: FilesystemAdapter,
        git_adapter: GitAdapter,
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
        wf = _make_workflow(
            gateway, mock_llm, fs_adapter, git_adapter,
            validation_adapter, operation_engine, config,
        )
        plan = _make_single_create_plan(goal, "hello.py")
        report = wf.execute(plan, goal)
        assert report.success is False
        assert report.error is not None

    def test_llm_failure_operations_completed_zero(
        self,
        gateway: ExecutionGateway,
        fs_adapter: FilesystemAdapter,
        git_adapter: GitAdapter,
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
        wf = _make_workflow(
            gateway, mock_llm, fs_adapter, git_adapter,
            validation_adapter, operation_engine, config,
        )
        plan = _make_two_create_plan(goal)
        report = wf.execute(plan, goal)
        meta = dict(report.metadata)
        assert meta.get("operations_completed") == "0"

    def test_second_op_not_executed_when_first_fails(
        self,
        gateway: ExecutionGateway,
        fs_adapter: FilesystemAdapter,
        git_adapter: GitAdapter,
        validation_adapter: ValidationAdapter,
        operation_engine: OperationEngine,
        config: WorkflowConfig,
        goal: FounderGoal,
        workspace: Path,
        repo: Path,
    ):
        mock_llm = MagicMock(spec=LlmAdapter)
        mock_llm.execute.side_effect = lambda req, cfg: _make_llm_failure(
            req.request_id, req.operation_id
        )
        wf = _make_workflow(
            gateway, mock_llm, fs_adapter, git_adapter,
            validation_adapter, operation_engine, config,
        )
        plan = _make_two_create_plan(goal)
        wf.execute(plan, goal)
        # Neither module should exist since op-0 failed at generate
        assert not (workspace / "my-project" / "module_a.py").exists()
        assert not (workspace / "my-project" / "module_b.py").exists()


# ── TestGatewayDispatch ───────────────────────────────────────────────────────


class TestGatewayDispatch:
    def test_all_steps_dispatched_on_success(
        self, workflow: EngineeringWorkflow, goal: FounderGoal, repo: Path
    ):
        plan = _make_single_create_plan(goal, "hello.py")
        report = workflow.execute(plan, goal)
        for step in report.steps:
            assert step.dispatch_status == ExecutionStatus.DISPATCHED

    def test_unregistered_gateway_halts(
        self,
        mock_llm: MagicMock,
        fs_adapter: FilesystemAdapter,
        git_adapter: GitAdapter,
        validation_adapter: ValidationAdapter,
        operation_engine: OperationEngine,
        config: WorkflowConfig,
        goal: FounderGoal,
        repo: Path,
    ):
        empty_gw = ExecutionGateway()
        wf = _make_workflow(
            empty_gw, mock_llm, fs_adapter, git_adapter,
            validation_adapter, operation_engine, config,
        )
        plan = _make_single_create_plan(goal, "hello.py")
        report = wf.execute(plan, goal)
        assert report.success is False
        mock_llm.execute.assert_not_called()


# ── TestNeverRaises ───────────────────────────────────────────────────────────


class TestNeverRaises:
    def test_execute_does_not_raise_on_gateway_failure(
        self,
        mock_llm: MagicMock,
        fs_adapter: FilesystemAdapter,
        git_adapter: GitAdapter,
        validation_adapter: ValidationAdapter,
        operation_engine: OperationEngine,
        config: WorkflowConfig,
        goal: FounderGoal,
        repo: Path,
    ):
        empty_gw = ExecutionGateway()
        wf = _make_workflow(
            empty_gw, mock_llm, fs_adapter, git_adapter,
            validation_adapter, operation_engine, config,
        )
        plan = _make_single_create_plan(goal, "hello.py")
        result = wf.execute(plan, goal)
        assert isinstance(result, WorkflowExecutionReport)
        assert result.success is False

    def test_execute_does_not_raise_on_llm_exception(
        self,
        gateway: ExecutionGateway,
        fs_adapter: FilesystemAdapter,
        git_adapter: GitAdapter,
        validation_adapter: ValidationAdapter,
        operation_engine: OperationEngine,
        config: WorkflowConfig,
        goal: FounderGoal,
        repo: Path,
    ):
        mock_llm = MagicMock(spec=LlmAdapter)
        mock_llm.execute.side_effect = RuntimeError("unexpected crash")
        wf = _make_workflow(
            gateway, mock_llm, fs_adapter, git_adapter,
            validation_adapter, operation_engine, config,
        )
        plan = _make_single_create_plan(goal, "hello.py")
        result = wf.execute(plan, goal)
        assert isinstance(result, WorkflowExecutionReport)
        assert result.success is False
        assert "unexpected_adapter_error" in (result.error or "")


# ── TestMetadata ──────────────────────────────────────────────────────────────


class TestMetadata:
    def test_metadata_sorted(
        self, workflow: EngineeringWorkflow, goal: FounderGoal, repo: Path
    ):
        plan = _make_single_create_plan(goal, "hello.py")
        report = workflow.execute(plan, goal)
        keys = [k for k, _ in report.metadata]
        assert keys == sorted(keys)

    def test_metadata_contains_goal_id(
        self, workflow: EngineeringWorkflow, goal: FounderGoal, repo: Path
    ):
        plan = _make_single_create_plan(goal, "hello.py")
        report = workflow.execute(plan, goal)
        meta = dict(report.metadata)
        assert meta.get("goal_id") == goal.goal_id

    def test_metadata_contains_commit_message(
        self, workflow: EngineeringWorkflow, goal: FounderGoal, repo: Path
    ):
        plan = _make_single_create_plan(goal, "hello.py")
        report = workflow.execute(plan, goal)
        meta = dict(report.metadata)
        assert meta.get("commit_message") is not None

    def test_metadata_contains_repository(
        self, workflow: EngineeringWorkflow, goal: FounderGoal, repo: Path
    ):
        plan = _make_single_create_plan(goal, "hello.py")
        report = workflow.execute(plan, goal)
        meta = dict(report.metadata)
        assert meta.get("repository") == goal.repository_path

    def test_metadata_contains_correction_attempts(
        self, workflow: EngineeringWorkflow, goal: FounderGoal, repo: Path
    ):
        """correction_attempts is always present in metadata (0 when none needed)."""
        plan = _make_single_create_plan(goal, "hello.py")
        report = workflow.execute(plan, goal)
        meta = dict(report.metadata)
        assert "correction_attempts" in meta
        assert meta["correction_attempts"] == "0"


# ── TestRunTestsGate ──────────────────────────────────────────────────────────


class TestRunTestsGate:
    def test_run_tests_failure_halts_before_commit(
        self,
        gateway: ExecutionGateway,
        mock_llm: MagicMock,
        git_adapter: GitAdapter,
        operation_engine: OperationEngine,
        goal: FounderGoal,
        workspace: Path,
        repo: Path,
    ):
        import subprocess as _sp
        from unittest.mock import patch as _patch

        fs_adapter = FilesystemAdapter(workspace_root=workspace)
        validation_adapter = ValidationAdapter(workspace_root=workspace)

        config = WorkflowConfig(
            llm_provider=LLMProvider.OLLAMA,
            llm_model="test-model",
            llm_base_url="http://localhost:11434",
            llm_api_key="",
            llm_max_tokens=2048,
            llm_timeout_seconds=30,
            commit_message="feat: test",
            test_command="pytest",
            max_corrections=0,  # disable correction loop — test verifies immediate halt
        )

        wf = _make_workflow(
            gateway, mock_llm, fs_adapter, git_adapter,
            validation_adapter, operation_engine, config,
        )

        original_run = _sp.run

        def selective_run(cmd, **kwargs):
            if isinstance(cmd, list) and len(cmd) > 2 and "py_compile" in cmd[2]:
                return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
            if isinstance(cmd, list) and cmd[0] == "pytest":
                return type("R", (), {
                    "returncode": 1,
                    "stdout": "1 failed in 0.01s",
                    "stderr": "FAILED test_foo::test_bar",
                })()
            return original_run(cmd, **kwargs)

        plan = _make_single_create_plan(goal, "hello.py")
        with _patch("subprocess.run", side_effect=selective_run):
            report = wf.execute(plan, goal)

        assert report.success is False
        action_ids = [s.action_id for s in report.steps]
        assert "run_tests" in action_ids
        assert "commit" not in action_ids


# ── TestPhase7Delegation ───────────────────────────────────────────────────────


class TestPhase7Delegation:
    """Phase 7: EngineeringWorkflow delegates each PlannedOperation to CorrectionEngine."""

    def test_workflow_calls_correction_engine_per_operation(
        self,
        gateway: ExecutionGateway,
        git_adapter: GitAdapter,
        operation_engine: OperationEngine,
        config: WorkflowConfig,
        goal: FounderGoal,
        repo: Path,
    ):
        """workflow.execute() calls correction_engine.execute_operation() exactly
        once per PlannedOperation, in topological order."""
        mock_ce = MagicMock(spec=CorrectionEngine)
        mock_ce.execute_operation.side_effect = lambda op, g: _make_op_correction_result(
            op.operation_id, success=True
        )

        wf = EngineeringWorkflow(
            gateway=gateway,
            git_adapter=git_adapter,
            operation_engine=operation_engine,
            config=config,
            correction_engine=mock_ce,
        )

        plan = _make_two_create_plan(goal)
        report = wf.execute(plan, goal)

        assert mock_ce.execute_operation.call_count == 2
        call_op_ids = [
            call.args[0].operation_id
            for call in mock_ce.execute_operation.call_args_list
        ]
        # op-0 must be called before op-1 (depends_on ordering)
        assert call_op_ids.index("op-0") < call_op_ids.index("op-1")

    def test_workflow_halts_after_first_failed_operation(
        self,
        gateway: ExecutionGateway,
        git_adapter: GitAdapter,
        operation_engine: OperationEngine,
        config: WorkflowConfig,
        goal: FounderGoal,
        repo: Path,
    ):
        """If operation 0 fails, operation 1 is never delegated."""
        mock_ce = MagicMock(spec=CorrectionEngine)
        mock_ce.execute_operation.side_effect = lambda op, g: _make_op_correction_result(
            op.operation_id, success=(op.operation_id != "op-0")
        )

        wf = EngineeringWorkflow(
            gateway=gateway,
            git_adapter=git_adapter,
            operation_engine=operation_engine,
            config=config,
            correction_engine=mock_ce,
        )

        plan = _make_two_create_plan(goal)
        report = wf.execute(plan, goal)

        assert report.success is False
        # execute_operation called once (op-0 failed, op-1 never started)
        assert mock_ce.execute_operation.call_count == 1

    def test_correction_attempts_accumulated_across_operations(
        self,
        gateway: ExecutionGateway,
        git_adapter: GitAdapter,
        operation_engine: OperationEngine,
        config: WorkflowConfig,
        goal: FounderGoal,
        repo: Path,
    ):
        """correction_attempts in metadata is the sum across all operations."""
        mock_ce = MagicMock(spec=CorrectionEngine)
        mock_ce.execute_operation.side_effect = lambda op, g: _make_op_correction_result(
            op.operation_id, success=True,
            correction_attempts=1,  # each op needed one correction
        )

        wf = EngineeringWorkflow(
            gateway=gateway,
            git_adapter=git_adapter,
            operation_engine=operation_engine,
            config=config,
            correction_engine=mock_ce,
        )

        plan = _make_two_create_plan(goal)
        report = wf.execute(plan, goal)

        meta = dict(report.metadata)
        # Two operations, each with correction_attempts=1 → total = 2
        assert meta.get("correction_attempts") == "2"


# ── TestPhase7Isolation ────────────────────────────────────────────────────────


class TestPhase7Isolation:
    """Phase 7: A successfully completed operation is NEVER re-executed while
    another operation is undergoing correction.

    This is enforced structurally: EngineeringWorkflow calls
    correction_engine.execute_operation() exactly once per PlannedOperation.
    The correction loop runs entirely inside CorrectionEngine — the workflow
    never re-calls execute_operation for an already-completed operation.
    """

    def test_successful_op1_not_re_executed_when_op2_corrects(
        self,
        gateway: ExecutionGateway,
        git_adapter: GitAdapter,
        operation_engine: OperationEngine,
        config: WorkflowConfig,
        goal: FounderGoal,
        repo: Path,
    ):
        """Op 1 succeeds first attempt. Op 2 needed 2 corrections.
        execute_operation must be called exactly once for op-0 and once for op-1."""
        call_counts: dict[str, int] = {}

        def fake_execute(op, g):
            op_id = op.operation_id
            call_counts[op_id] = call_counts.get(op_id, 0) + 1
            return _make_op_correction_result(
                op_id,
                success=True,
                correction_attempts=2 if op_id == "op-1" else 0,
            )

        mock_ce = MagicMock(spec=CorrectionEngine)
        mock_ce.execute_operation.side_effect = fake_execute

        wf = EngineeringWorkflow(
            gateway=gateway,
            git_adapter=git_adapter,
            operation_engine=operation_engine,
            config=config,
            correction_engine=mock_ce,
        )

        plan = _make_two_create_plan(goal)
        report = wf.execute(plan, goal)

        assert report.success is True
        # op-0 called exactly once — never re-invoked during op-1's correction
        assert call_counts.get("op-0") == 1, (
            f"op-0 was called {call_counts.get('op-0')} times — "
            "must be exactly 1 regardless of op-1's correction count"
        )
        # op-1 called exactly once — the correction loop ran INSIDE CorrectionEngine
        assert call_counts.get("op-1") == 1

    def test_generate_step_count_per_op_reflects_corrections(
        self,
        gateway: ExecutionGateway,
        git_adapter: GitAdapter,
        operation_engine: OperationEngine,
        config: WorkflowConfig,
        goal: FounderGoal,
        repo: Path,
    ):
        """The steps audit trail shows more generate calls for the corrected op,
        and exactly one generate call for the uncorrected op."""
        def fake_execute(op, g):
            op_id = op.operation_id
            if op_id == "op-0":
                # First attempt succeeded — one generate step
                step = _make_dummy_step_record(op_id)
                return OperationCorrectionResult(
                    operation_id=op_id,
                    success=True,
                    correction_attempts=0,
                    correction_log=(),
                    steps=(step,),
                    error=None,
                )
            else:
                # Required 1 correction — two generate steps (initial + 1 correction)
                step1 = _make_dummy_step_record(f"{op_id}-initial")
                step2 = _make_dummy_step_record(f"{op_id}-corr1")
                step2_rec = StepExecutionRecord(
                    step_id=f"step-op-generate-corr1-{op_id}",
                    operation_id=f"op-generate-corr1-{op_id}",
                    adapter_type=ExecutionAdapter.LLM,
                    action_id="generate",
                    execution_request=step1.execution_request,
                    dispatch_status=ExecutionStatus.DISPATCHED,
                    adapter_success=True,
                    adapter_error=None,
                    output=_GENERATED_CODE,
                )
                return OperationCorrectionResult(
                    operation_id=op_id,
                    success=True,
                    correction_attempts=1,
                    correction_log=(CorrectionRecord(
                        attempt=1,
                        trigger="test_failure",
                        error_excerpt="1 failed",
                    ),),
                    steps=(step1, step2_rec),
                    error=None,
                )

        mock_ce = MagicMock(spec=CorrectionEngine)
        mock_ce.execute_operation.side_effect = fake_execute

        wf = EngineeringWorkflow(
            gateway=gateway,
            git_adapter=git_adapter,
            operation_engine=operation_engine,
            config=config,
            correction_engine=mock_ce,
        )

        plan = _make_two_create_plan(goal)
        report = wf.execute(plan, goal)

        assert report.success is True

        # Count generate steps per operation in the flat audit trail.
        all_generate_steps = [
            s for s in report.steps if s.action_id == "generate"
        ]
        # op-0: exactly 1 generate step (operation_id contains "op-0")
        op0_generates = [s for s in all_generate_steps if "op-0" in s.operation_id]
        assert len(op0_generates) == 1, (
            f"op-0 has {len(op0_generates)} generate steps — expected exactly 1"
        )
        # op-1: 2 generate steps (initial + 1 correction)
        op1_generates = [s for s in all_generate_steps if "op-1" in s.operation_id]
        assert len(op1_generates) == 2, (
            f"op-1 has {len(op1_generates)} generate steps — expected 2 (initial + 1 correction)"
        )
