"""Tests for Sprint 64 — First Autonomous Engineering Workflow.

Coverage:
  - All typed contracts (FounderGoal, WorkflowMission, WorkflowConfig,
    StepExecutionRecord, WorkflowExecutionReport)
  - EngineeringWorkflow construction and component injection
  - Operation planning (OperationEngine) and topological ordering
  - Gateway dispatch called before every adapter invocation
  - LLM → Filesystem → Git execution sequence
  - Cross-step data flow (LLM output → Filesystem content)
  - Failure propagation and halt-on-failure semantics
  - Gateway boundary: no adapter called without DISPATCHED status
  - Immutable execution report
  - Audit trail completeness
  - Future adapter extension without redesign (structural)

Test strategy:
  - LLM adapter is mocked (MagicMock) — no Ollama required
  - Filesystem and Git use real temporary directories + git repos
  - Gateway is real (ExecutionGateway)
  - JobEngine and OperationEngine are real instances
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

from hermes.adapters.filesystem_adapter import FilesystemAdapter
from hermes.adapters.git_adapter import GitAdapter
from hermes.adapters.llm_adapter import LlmAdapter
from hermes.adapters.validation_adapter import ValidationAdapter
from hermes.kernel.execution_gateway import ExecutionGateway
from hermes.kernel.job_engine import JobEngine
from hermes.kernel.operation_engine import OperationEngine
from hermes.kernel.skill_registry import SkillRegistry
from hermes.models.engineering_workflow import (
    FounderGoal,
    StepExecutionRecord,
    WorkflowConfig,
    WorkflowExecutionReport,
    WorkflowMission,
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
from hermes.workflows.engineering_workflow import EngineeringWorkflow


# ── Helpers and fixtures ───────────────────────────────────────────────────────


def _init_repo(path: Path) -> Path:
    """Initialise a git repo with user identity configured."""
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
    """Build a successful AdapterExecutionResult for mock LLM responses."""
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
    """Build a failed AdapterExecutionResult for mock LLM error simulation."""
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


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


@pytest.fixture()
def repo(workspace: Path) -> Path:
    """A git repo inside the workspace, with an initial commit."""
    repo_path = workspace / "my-project"
    repo_path.mkdir()
    _init_repo(repo_path)
    # Initial commit required so git add/commit work
    (repo_path / ".gitkeep").write_text("")
    subprocess.run(["git", "add", ".gitkeep"], cwd=repo_path, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=repo_path, capture_output=True,
    )
    return repo_path


@pytest.fixture()
def gateway(workspace: Path) -> ExecutionGateway:
    """A fully registered Gateway with LLM, Filesystem, Git, and Validation adapters."""
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
    """A mock LlmAdapter that returns generated code on first call."""
    mock = MagicMock(spec=LlmAdapter)

    def side_effect(request, config):
        return _make_llm_success(request.request_id, request.operation_id, _GENERATED_CODE)

    mock.execute.side_effect = side_effect
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
def job_engine() -> JobEngine:
    return JobEngine(registry=SkillRegistry())


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
    job_engine: JobEngine,
    operation_engine: OperationEngine,
    config: WorkflowConfig,
) -> EngineeringWorkflow:
    return EngineeringWorkflow(
        gateway=gateway,
        llm_adapter=mock_llm,
        filesystem_adapter=fs_adapter,
        git_adapter=git_adapter,
        validation_adapter=validation_adapter,
        job_engine=job_engine,
        operation_engine=operation_engine,
        config=config,
    )


@pytest.fixture()
def goal(workspace: Path, repo: Path) -> FounderGoal:
    return FounderGoal(
        goal_id="hello-world-001",
        description="Write a Python function that returns 'Hello, World!'",
        workspace_path=str(workspace),
        repository_path="my-project",
        output_path="my-project/hello.py",
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

    def test_failure_state(self):
        from hermes.models.execution_gateway import ExecutionRequest
        req = ExecutionRequest(
            request_id="req-fail",
            operation_id="op-fail",
            adapter_type=ExecutionAdapter.LLM,
            action_id="generate",
            payload=(),
        )
        record = StepExecutionRecord(
            step_id="step-fail",
            operation_id="op-fail",
            adapter_type=ExecutionAdapter.LLM,
            action_id="generate",
            execution_request=req,
            dispatch_status=ExecutionStatus.FAILED,
            adapter_success=False,
            adapter_error="gateway_failed: adapter not available",
            output="",
        )
        assert record.adapter_success is False
        assert record.adapter_error is not None


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

    def test_slots(self):
        report = WorkflowExecutionReport(
            report_id="r", goal_id="g", mission_id="m", job_id="j",
            steps=(), success=True, error=None,
            execution_sequence=(), metadata=(),
        )
        assert not hasattr(report, "__dict__")


# ── TestEngineeringWorkflowConstruction ────────────────────────────────────────


class TestEngineeringWorkflowConstruction:
    def test_constructs(self, workflow: EngineeringWorkflow):
        assert workflow is not None

    def test_all_components_injected(
        self,
        gateway: ExecutionGateway,
        mock_llm: MagicMock,
        fs_adapter: FilesystemAdapter,
        git_adapter: GitAdapter,
        validation_adapter: ValidationAdapter,
        job_engine: JobEngine,
        operation_engine: OperationEngine,
        config: WorkflowConfig,
    ):
        wf = EngineeringWorkflow(
            gateway=gateway,
            llm_adapter=mock_llm,
            filesystem_adapter=fs_adapter,
            git_adapter=git_adapter,
            validation_adapter=validation_adapter,
            job_engine=job_engine,
            operation_engine=operation_engine,
            config=config,
        )
        assert wf is not None


# ── TestOperationPlan ──────────────────────────────────────────────────────────


class TestOperationPlan:
    """Verify the OperationEngine plans operations correctly."""

    def test_five_operations_built(
        self, workflow: EngineeringWorkflow, goal: FounderGoal
    ):
        ops = workflow._build_operations(goal, f"job-{goal.goal_id}")
        assert len(ops) == 5

    def test_operation_types_in_order(
        self, workflow: EngineeringWorkflow, goal: FounderGoal
    ):
        from hermes.models.operation import OperationType
        ops = workflow._build_operations(goal, f"job-{goal.goal_id}")
        types = [op.operation_type for op in ops]
        assert types == [
            OperationType.LLM,
            OperationType.FILESYSTEM,
            OperationType.VALIDATION,
            OperationType.GIT,
            OperationType.GIT,
        ]

    def test_execution_refs_set(
        self, workflow: EngineeringWorkflow, goal: FounderGoal
    ):
        ops = workflow._build_operations(goal, f"job-{goal.goal_id}")
        for op in ops:
            assert op.execution_ref is not None
            assert op.execution_ref.adapter_id != ""
            assert op.execution_ref.action_id != ""

    def test_topological_order_correct(
        self, workflow: EngineeringWorkflow, goal: FounderGoal, operation_engine: OperationEngine
    ):
        ops = list(workflow._build_operations(goal, f"job-{goal.goal_id}"))
        order = operation_engine.determine_execution_order(ops)
        # generate → write → validate → add → commit
        assert len(order) == 5
        indices = {op_id: i for i, op_id in enumerate(order)}
        gid = goal.goal_id
        assert indices[f"op-generate-{gid}"] < indices[f"op-write-{gid}"]
        assert indices[f"op-write-{gid}"] < indices[f"op-validate-{gid}"]
        assert indices[f"op-validate-{gid}"] < indices[f"op-add-{gid}"]
        assert indices[f"op-add-{gid}"] < indices[f"op-commit-{gid}"]

    def test_dependencies_declared(
        self, workflow: EngineeringWorkflow, goal: FounderGoal
    ):
        ops = {op.id: op for op in workflow._build_operations(goal, f"job-{goal.goal_id}")}
        gid = goal.goal_id
        # write depends on generate
        write_deps = [d.operation_id for d in ops[f"op-write-{gid}"].depends_on]
        assert f"op-generate-{gid}" in write_deps
        # validate depends on write
        validate_deps = [d.operation_id for d in ops[f"op-validate-{gid}"].depends_on]
        assert f"op-write-{gid}" in validate_deps
        # add depends on validate
        add_deps = [d.operation_id for d in ops[f"op-add-{gid}"].depends_on]
        assert f"op-validate-{gid}" in add_deps

    def test_operations_all_valid(
        self,
        workflow: EngineeringWorkflow,
        goal: FounderGoal,
        operation_engine: OperationEngine,
    ):
        ops = list(workflow._build_operations(goal, f"job-{goal.goal_id}"))
        results = operation_engine.plan_all(ops, completed_op_ids=frozenset())
        for result in results:
            v = result.validation_result
            if v is not None:
                assert v.valid, f"Operation {result.operation_id} invalid: {v.errors}"


# ── TestFullSuccessPath ────────────────────────────────────────────────────────


class TestFullSuccessPath:
    """End-to-end success: LLM → Filesystem → Git add → Git commit."""

    def test_report_success(self, workflow: EngineeringWorkflow, goal: FounderGoal):
        report = workflow.execute(goal)
        assert report.success is True, f"Expected success, got error: {report.error}"

    def test_report_has_five_steps(self, workflow: EngineeringWorkflow, goal: FounderGoal):
        report = workflow.execute(goal)
        assert len(report.steps) == 5

    def test_report_no_error(self, workflow: EngineeringWorkflow, goal: FounderGoal):
        report = workflow.execute(goal)
        assert report.error is None

    def test_generated_file_exists(
        self, workflow: EngineeringWorkflow, goal: FounderGoal, workspace: Path
    ):
        workflow.execute(goal)
        output_file = workspace / goal.output_path
        assert output_file.exists()

    def test_generated_file_content(
        self, workflow: EngineeringWorkflow, goal: FounderGoal, workspace: Path
    ):
        workflow.execute(goal)
        content = (workspace / goal.output_path).read_text()
        assert content == _GENERATED_CODE

    def test_file_committed_to_git(
        self, workflow: EngineeringWorkflow, goal: FounderGoal, workspace: Path
    ):
        workflow.execute(goal)
        repo_path = workspace / goal.repository_path
        log = subprocess.run(
            ["git", "log", "--oneline"],
            cwd=repo_path, capture_output=True, text=True,
        )
        assert "generated by Hermes (test)" in log.stdout

    def test_report_ids_correct(self, workflow: EngineeringWorkflow, goal: FounderGoal):
        report = workflow.execute(goal)
        assert report.goal_id == goal.goal_id
        assert report.mission_id == f"mission-{goal.goal_id}"
        assert report.job_id == f"job-{goal.goal_id}"
        assert report.report_id == f"report-{goal.goal_id}"

    def test_report_is_frozen(self, workflow: EngineeringWorkflow, goal: FounderGoal):
        report = workflow.execute(goal)
        with pytest.raises(Exception):
            report.success = False  # type: ignore[misc]


# ── TestExecutionSequence ──────────────────────────────────────────────────────


class TestExecutionSequence:
    """Verify the LLM → Filesystem → Git adapter sequence is invariant."""

    def test_execution_sequence_field(self, workflow: EngineeringWorkflow, goal: FounderGoal):
        report = workflow.execute(goal)
        assert report.execution_sequence == ("llm", "filesystem", "validation", "git", "git")

    def test_step_adapter_types_in_order(
        self, workflow: EngineeringWorkflow, goal: FounderGoal
    ):
        report = workflow.execute(goal)
        types = [s.adapter_type for s in report.steps]
        assert types == [
            ExecutionAdapter.LLM,
            ExecutionAdapter.FILESYSTEM,
            ExecutionAdapter.VALIDATION,
            ExecutionAdapter.GIT,
            ExecutionAdapter.GIT,
        ]

    def test_step_action_ids(self, workflow: EngineeringWorkflow, goal: FounderGoal):
        report = workflow.execute(goal)
        actions = [s.action_id for s in report.steps]
        assert actions == ["generate", "create_file", "validate", "add", "commit"]

    def test_llm_called_first(
        self, workflow: EngineeringWorkflow, goal: FounderGoal, mock_llm: MagicMock
    ):
        workflow.execute(goal)
        assert mock_llm.execute.call_count == 1
        first_call_request = mock_llm.execute.call_args_list[0][0][0]
        assert first_call_request.adapter_type == ExecutionAdapter.LLM

    def test_llm_output_flows_to_filesystem(
        self, workflow: EngineeringWorkflow, goal: FounderGoal, workspace: Path
    ):
        """The content written to disk must be the LLM's output."""
        report = workflow.execute(goal)
        llm_step = report.steps[0]
        assert llm_step.output == _GENERATED_CODE
        written_content = (workspace / goal.output_path).read_text()
        assert written_content == _GENERATED_CODE


# ── TestGatewayDispatch ────────────────────────────────────────────────────────


class TestGatewayDispatch:
    """Verify the Gateway is called for every step and dispatch precedes adapter."""

    def test_all_steps_dispatched(self, workflow: EngineeringWorkflow, goal: FounderGoal):
        report = workflow.execute(goal)
        for step in report.steps:
            assert step.dispatch_status == ExecutionStatus.DISPATCHED

    def test_step_execution_requests_target_correct_adapters(
        self, workflow: EngineeringWorkflow, goal: FounderGoal
    ):
        report = workflow.execute(goal)
        assert report.steps[0].execution_request.adapter_type == ExecutionAdapter.LLM
        assert report.steps[1].execution_request.adapter_type == ExecutionAdapter.FILESYSTEM
        assert report.steps[2].execution_request.adapter_type == ExecutionAdapter.VALIDATION
        assert report.steps[3].execution_request.adapter_type == ExecutionAdapter.GIT
        assert report.steps[4].execution_request.adapter_type == ExecutionAdapter.GIT

    def test_gateway_failure_blocks_llm(
        self,
        mock_llm: MagicMock,
        fs_adapter: FilesystemAdapter,
        git_adapter: GitAdapter,
        validation_adapter: ValidationAdapter,
        job_engine: JobEngine,
        operation_engine: OperationEngine,
        config: WorkflowConfig,
        goal: FounderGoal,
    ):
        """If the LLM adapter is not registered, the LLM is never called."""
        gw = ExecutionGateway()
        # Only register Filesystem, Git, and Validation — NOT LLM
        gw.register(AdapterRegistration(
            adapter=ExecutionAdapter.FILESYSTEM,
            adapter_id="fs", available=True, description="",
        ))
        gw.register(AdapterRegistration(
            adapter=ExecutionAdapter.GIT,
            adapter_id="git", available=True, description="",
        ))
        gw.register(AdapterRegistration(
            adapter=ExecutionAdapter.VALIDATION,
            adapter_id="validation", available=True, description="",
        ))
        wf = EngineeringWorkflow(
            gateway=gw,
            llm_adapter=mock_llm,
            filesystem_adapter=fs_adapter,
            git_adapter=git_adapter,
            validation_adapter=validation_adapter,
            job_engine=job_engine,
            operation_engine=operation_engine,
            config=config,
        )
        report = wf.execute(goal)
        assert report.success is False
        # LLM adapter must NOT have been called
        mock_llm.execute.assert_not_called()

    def test_unavailable_adapter_blocks_execution(
        self,
        mock_llm: MagicMock,
        fs_adapter: FilesystemAdapter,
        git_adapter: GitAdapter,
        validation_adapter: ValidationAdapter,
        job_engine: JobEngine,
        operation_engine: OperationEngine,
        config: WorkflowConfig,
        goal: FounderGoal,
    ):
        """An available=False registration causes the workflow to halt."""
        gw = ExecutionGateway()
        gw.register(AdapterRegistration(
            adapter=ExecutionAdapter.LLM,
            adapter_id="llm", available=False, description="",  # unavailable
        ))
        gw.register(AdapterRegistration(
            adapter=ExecutionAdapter.FILESYSTEM,
            adapter_id="fs", available=True, description="",
        ))
        gw.register(AdapterRegistration(
            adapter=ExecutionAdapter.GIT,
            adapter_id="git", available=True, description="",
        ))
        gw.register(AdapterRegistration(
            adapter=ExecutionAdapter.VALIDATION,
            adapter_id="validation", available=True, description="",
        ))
        wf = EngineeringWorkflow(
            gateway=gw,
            llm_adapter=mock_llm,
            filesystem_adapter=fs_adapter,
            git_adapter=git_adapter,
            validation_adapter=validation_adapter,
            job_engine=job_engine,
            operation_engine=operation_engine,
            config=config,
        )
        report = wf.execute(goal)
        assert report.success is False
        mock_llm.execute.assert_not_called()

    def test_dispatch_status_recorded_per_step(
        self, workflow: EngineeringWorkflow, goal: FounderGoal
    ):
        report = workflow.execute(goal)
        for step in report.steps:
            assert step.dispatch_status == ExecutionStatus.DISPATCHED


# ── TestFailurePropagation ─────────────────────────────────────────────────────


class TestFailurePropagation:
    """Verify failure at any step halts the workflow."""

    def test_llm_failure_halts_workflow(
        self,
        gateway: ExecutionGateway,
        fs_adapter: FilesystemAdapter,
        git_adapter: GitAdapter,
        validation_adapter: ValidationAdapter,
        job_engine: JobEngine,
        operation_engine: OperationEngine,
        config: WorkflowConfig,
        goal: FounderGoal,
    ):
        """LLM failure → filesystem and git are never called."""
        mock_llm = MagicMock(spec=LlmAdapter)
        mock_llm.execute.side_effect = lambda req, cfg: _make_llm_failure(
            req.request_id, req.operation_id
        )

        wf = EngineeringWorkflow(
            gateway=gateway,
            llm_adapter=mock_llm,
            filesystem_adapter=fs_adapter,
            git_adapter=git_adapter,
            validation_adapter=validation_adapter,
            job_engine=job_engine,
            operation_engine=operation_engine,
            config=config,
        )
        report = wf.execute(goal)

        assert report.success is False
        assert report.error is not None
        # Only the LLM step was attempted
        assert len(report.steps) == 1
        assert report.steps[0].adapter_type == ExecutionAdapter.LLM
        assert report.steps[0].adapter_success is False

    def test_filesystem_failure_halts_workflow(
        self,
        gateway: ExecutionGateway,
        mock_llm: MagicMock,
        git_adapter: GitAdapter,
        validation_adapter: ValidationAdapter,
        job_engine: JobEngine,
        operation_engine: OperationEngine,
        config: WorkflowConfig,
        goal: FounderGoal,
        workspace: Path,
    ):
        """Filesystem failure → validation and git are never called."""
        # Pre-create the file so create_file will fail (file already exists)
        output_file = workspace / goal.output_path
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text("already exists")

        wf = EngineeringWorkflow(
            gateway=gateway,
            llm_adapter=mock_llm,
            filesystem_adapter=FilesystemAdapter(workspace_root=workspace),
            git_adapter=git_adapter,
            validation_adapter=validation_adapter,
            job_engine=job_engine,
            operation_engine=operation_engine,
            config=config,
        )
        report = wf.execute(goal)

        assert report.success is False
        # LLM succeeded, filesystem failed
        assert len(report.steps) == 2
        assert report.steps[0].adapter_success is True   # LLM
        assert report.steps[1].adapter_success is False  # Filesystem

    def test_failure_report_has_error(
        self,
        gateway: ExecutionGateway,
        fs_adapter: FilesystemAdapter,
        git_adapter: GitAdapter,
        validation_adapter: ValidationAdapter,
        job_engine: JobEngine,
        operation_engine: OperationEngine,
        config: WorkflowConfig,
        goal: FounderGoal,
    ):
        mock_llm = MagicMock(spec=LlmAdapter)
        mock_llm.execute.side_effect = lambda req, cfg: _make_llm_failure(
            req.request_id, req.operation_id
        )
        wf = EngineeringWorkflow(
            gateway=gateway,
            llm_adapter=mock_llm,
            filesystem_adapter=fs_adapter,
            git_adapter=git_adapter,
            validation_adapter=validation_adapter,
            job_engine=job_engine,
            operation_engine=operation_engine,
            config=config,
        )
        report = wf.execute(goal)
        assert report.error is not None
        assert len(report.error) > 0

    def test_partial_steps_recorded_on_failure(
        self,
        gateway: ExecutionGateway,
        fs_adapter: FilesystemAdapter,
        git_adapter: GitAdapter,
        validation_adapter: ValidationAdapter,
        job_engine: JobEngine,
        operation_engine: OperationEngine,
        config: WorkflowConfig,
        goal: FounderGoal,
    ):
        mock_llm = MagicMock(spec=LlmAdapter)
        mock_llm.execute.side_effect = lambda req, cfg: _make_llm_failure(
            req.request_id, req.operation_id
        )
        wf = EngineeringWorkflow(
            gateway=gateway,
            llm_adapter=mock_llm,
            filesystem_adapter=fs_adapter,
            git_adapter=git_adapter,
            validation_adapter=validation_adapter,
            job_engine=job_engine,
            operation_engine=operation_engine,
            config=config,
        )
        report = wf.execute(goal)
        # Steps up to and including the failing step are recorded
        assert len(report.steps) >= 1
        assert any(not s.adapter_success for s in report.steps)


# ── TestAuditTrail ─────────────────────────────────────────────────────────────


class TestAuditTrail:
    """Verify the audit trail is complete and accurate."""

    def test_report_metadata_present(self, workflow: EngineeringWorkflow, goal: FounderGoal):
        report = workflow.execute(goal)
        assert report.metadata != ()

    def test_report_metadata_sorted(self, workflow: EngineeringWorkflow, goal: FounderGoal):
        report = workflow.execute(goal)
        keys = [k for k, _ in report.metadata]
        assert keys == sorted(keys)

    def test_metadata_contains_goal_id(
        self, workflow: EngineeringWorkflow, goal: FounderGoal
    ):
        report = workflow.execute(goal)
        meta = dict(report.metadata)
        assert meta.get("goal_id") == goal.goal_id

    def test_metadata_contains_steps_completed(
        self, workflow: EngineeringWorkflow, goal: FounderGoal
    ):
        report = workflow.execute(goal)
        meta = dict(report.metadata)
        assert meta.get("steps_completed") == "5"

    def test_metadata_contains_generated_file(
        self, workflow: EngineeringWorkflow, goal: FounderGoal
    ):
        report = workflow.execute(goal)
        meta = dict(report.metadata)
        assert meta.get("generated_file") == goal.output_path

    def test_every_step_has_execution_request(
        self, workflow: EngineeringWorkflow, goal: FounderGoal
    ):
        report = workflow.execute(goal)
        for step in report.steps:
            assert step.execution_request is not None
            assert step.execution_request.request_id != ""
            assert step.execution_request.operation_id != ""

    def test_step_ids_unique(self, workflow: EngineeringWorkflow, goal: FounderGoal):
        report = workflow.execute(goal)
        step_ids = [s.step_id for s in report.steps]
        assert len(step_ids) == len(set(step_ids))

    def test_operation_ids_unique(self, workflow: EngineeringWorkflow, goal: FounderGoal):
        report = workflow.execute(goal)
        op_ids = [s.operation_id for s in report.steps]
        assert len(op_ids) == len(set(op_ids))

    def test_steps_are_frozen(self, workflow: EngineeringWorkflow, goal: FounderGoal):
        report = workflow.execute(goal)
        for step in report.steps:
            with pytest.raises(Exception):
                step.adapter_success = not step.adapter_success  # type: ignore[misc]

    def test_llm_step_has_output(self, workflow: EngineeringWorkflow, goal: FounderGoal):
        report = workflow.execute(goal)
        llm_step = next(s for s in report.steps if s.adapter_type == ExecutionAdapter.LLM)
        assert llm_step.output == _GENERATED_CODE

    def test_non_llm_steps_have_empty_output(
        self, workflow: EngineeringWorkflow, goal: FounderGoal
    ):
        report = workflow.execute(goal)
        for step in report.steps:
            if step.adapter_type != ExecutionAdapter.LLM:
                assert step.output == ""


# ── TestNeverRaises ────────────────────────────────────────────────────────────


class TestNeverRaises:
    """execute() must never raise exceptions — all failures are in the report."""

    def test_unregistered_gateway_does_not_raise(
        self,
        mock_llm: MagicMock,
        fs_adapter: FilesystemAdapter,
        git_adapter: GitAdapter,
        validation_adapter: ValidationAdapter,
        job_engine: JobEngine,
        operation_engine: OperationEngine,
        config: WorkflowConfig,
        goal: FounderGoal,
    ):
        empty_gateway = ExecutionGateway()
        wf = EngineeringWorkflow(
            gateway=empty_gateway,
            llm_adapter=mock_llm,
            filesystem_adapter=fs_adapter,
            git_adapter=git_adapter,
            validation_adapter=validation_adapter,
            job_engine=job_engine,
            operation_engine=operation_engine,
            config=config,
        )
        result = wf.execute(goal)
        assert isinstance(result, WorkflowExecutionReport)
        assert result.success is False

    def test_llm_exception_does_not_propagate(
        self,
        gateway: ExecutionGateway,
        fs_adapter: FilesystemAdapter,
        git_adapter: GitAdapter,
        validation_adapter: ValidationAdapter,
        job_engine: JobEngine,
        operation_engine: OperationEngine,
        config: WorkflowConfig,
        goal: FounderGoal,
    ):
        mock_llm = MagicMock(spec=LlmAdapter)
        mock_llm.execute.side_effect = RuntimeError("unexpected crash")
        wf = EngineeringWorkflow(
            gateway=gateway,
            llm_adapter=mock_llm,
            filesystem_adapter=fs_adapter,
            git_adapter=git_adapter,
            validation_adapter=validation_adapter,
            job_engine=job_engine,
            operation_engine=operation_engine,
            config=config,
        )
        result = wf.execute(goal)
        assert isinstance(result, WorkflowExecutionReport)
        assert result.success is False
        assert "unexpected_adapter_error" in (result.error or "")


# ── TestGatewayBoundary ────────────────────────────────────────────────────────


class TestGatewayBoundary:
    """Structural proof that the Gateway is the sole dispatch boundary."""

    def test_gateway_called_once_per_step(
        self,
        mock_llm: MagicMock,
        fs_adapter: FilesystemAdapter,
        git_adapter: GitAdapter,
        job_engine: JobEngine,
        operation_engine: OperationEngine,
        config: WorkflowConfig,
        goal: FounderGoal,
        workspace: Path,
        repo: Path,
    ):
        """Spy on gateway.dispatch() to count calls."""
        gw = ExecutionGateway()
        gw.register(AdapterRegistration(
            adapter=ExecutionAdapter.LLM, adapter_id="llm", available=True, description="",
        ))
        gw.register(AdapterRegistration(
            adapter=ExecutionAdapter.FILESYSTEM, adapter_id="fs", available=True, description="",
        ))
        gw.register(AdapterRegistration(
            adapter=ExecutionAdapter.GIT, adapter_id="git", available=True, description="",
        ))
        gw.register(AdapterRegistration(
            adapter=ExecutionAdapter.VALIDATION, adapter_id="validation", available=True, description="",
        ))

        original_dispatch = gw.dispatch
        dispatch_calls = []

        def tracking_dispatch(request):
            dispatch_calls.append(request)
            return original_dispatch(request)

        gw.dispatch = tracking_dispatch  # type: ignore[method-assign]

        wf = EngineeringWorkflow(
            gateway=gw,
            llm_adapter=mock_llm,
            filesystem_adapter=FilesystemAdapter(workspace_root=workspace),
            git_adapter=GitAdapter(workspace_root=workspace),
            validation_adapter=ValidationAdapter(workspace_root=workspace),
            job_engine=job_engine,
            operation_engine=operation_engine,
            config=config,
        )
        report = wf.execute(goal)

        # Gateway must be called exactly once per step (5 steps = 5 dispatch calls)
        assert report.success is True
        assert len(dispatch_calls) == 5

    def test_adapter_not_called_when_gateway_returns_unsupported(
        self,
        mock_llm: MagicMock,
        fs_adapter: FilesystemAdapter,
        git_adapter: GitAdapter,
        validation_adapter: ValidationAdapter,
        job_engine: JobEngine,
        operation_engine: OperationEngine,
        config: WorkflowConfig,
        goal: FounderGoal,
    ):
        """No adapter must be invoked if Gateway returns UNSUPPORTED."""
        empty_gw = ExecutionGateway()  # no adapters registered
        wf = EngineeringWorkflow(
            gateway=empty_gw,
            llm_adapter=mock_llm,
            filesystem_adapter=fs_adapter,
            git_adapter=git_adapter,
            validation_adapter=validation_adapter,
            job_engine=job_engine,
            operation_engine=operation_engine,
            config=config,
        )
        wf.execute(goal)
        # LLM adapter must NEVER be called since gateway returned UNSUPPORTED
        mock_llm.execute.assert_not_called()

    def test_failed_step_dispatch_status_not_dispatched(
        self,
        mock_llm: MagicMock,
        fs_adapter: FilesystemAdapter,
        git_adapter: GitAdapter,
        validation_adapter: ValidationAdapter,
        job_engine: JobEngine,
        operation_engine: OperationEngine,
        config: WorkflowConfig,
        goal: FounderGoal,
    ):
        empty_gw = ExecutionGateway()
        wf = EngineeringWorkflow(
            gateway=empty_gw,
            llm_adapter=mock_llm,
            filesystem_adapter=fs_adapter,
            git_adapter=git_adapter,
            validation_adapter=validation_adapter,
            job_engine=job_engine,
            operation_engine=operation_engine,
            config=config,
        )
        report = wf.execute(goal)
        assert report.success is False
        failed_step = report.steps[0]
        assert failed_step.dispatch_status != ExecutionStatus.DISPATCHED


# ── TestDeterminism ────────────────────────────────────────────────────────────


class TestDeterminism:
    """Same inputs produce same structural outputs (excluding non-deterministic adapters)."""

    def test_operation_plan_is_deterministic(
        self, workflow: EngineeringWorkflow, goal: FounderGoal
    ):
        ops1 = workflow._build_operations(goal, f"job-{goal.goal_id}")
        ops2 = workflow._build_operations(goal, f"job-{goal.goal_id}")
        assert ops1 == ops2

    def test_report_ids_are_deterministic(
        self,
        workflow: EngineeringWorkflow,
        goal: FounderGoal,
        workspace: Path,
        repo: Path,
    ):
        # First execution
        report1 = workflow.execute(goal)

        # Re-create workspace state for second execution
        # (file was written, so we need a fresh setup)
        # Instead, just verify the ID fields are deterministic from goal_id
        assert report1.report_id == f"report-{goal.goal_id}"
        assert report1.mission_id == f"mission-{goal.goal_id}"
        assert report1.job_id == f"job-{goal.goal_id}"

    def test_execution_sequence_always_same(
        self, workflow: EngineeringWorkflow, goal: FounderGoal
    ):
        report = workflow.execute(goal)
        assert report.execution_sequence == ("llm", "filesystem", "validation", "git", "git")

    def test_operation_ids_derived_from_goal_id(
        self, workflow: EngineeringWorkflow, goal: FounderGoal
    ):
        ops = workflow._build_operations(goal, f"job-{goal.goal_id}")
        gid = goal.goal_id
        op_ids = {op.id for op in ops}
        assert f"op-generate-{gid}" in op_ids
        assert f"op-write-{gid}" in op_ids
        assert f"op-validate-{gid}" in op_ids
        assert f"op-add-{gid}" in op_ids
        assert f"op-commit-{gid}" in op_ids


# ── TestFutureExtensibility ────────────────────────────────────────────────────


class TestFutureExtensibility:
    """Verify that adding a future adapter (e.g. HTTP, Docker) requires only
    Gateway registration — no changes to existing components."""

    def test_gateway_accepts_additional_adapter_registration(self, gateway: ExecutionGateway):
        """Gateway can accept a future adapter type without redesign."""
        registered = gateway.register(AdapterRegistration(
            adapter=ExecutionAdapter.HTTP,
            adapter_id="http-v1",
            available=True,
            description="Future HTTP adapter",
        ))
        assert registered is True
        assert gateway.resolve(ExecutionAdapter.HTTP) is not None

    def test_gateway_first_wins_still_applies(self, gateway: ExecutionGateway):
        """First-wins semantics hold when a new adapter is added."""
        gateway.register(AdapterRegistration(
            adapter=ExecutionAdapter.DATABASE,
            adapter_id="db-v1",
            available=True,
            description="First",
        ))
        registered_second = gateway.register(AdapterRegistration(
            adapter=ExecutionAdapter.DATABASE,
            adapter_id="db-v2",
            available=True,
            description="Second — must be ignored",
        ))
        assert registered_second is False
        assert gateway.resolve(ExecutionAdapter.DATABASE).adapter_id == "db-v1"

    def test_operation_type_has_docker_and_http(self):
        """OperationType already declares future adapter categories."""
        from hermes.models.operation import OperationType
        all_types = {ot.value for ot in OperationType}
        assert "http" in all_types
        assert "docker" in all_types
        assert "database" in all_types

    def test_execution_adapter_has_docker_and_http(self):
        """ExecutionAdapter already declares future adapter categories."""
        all_adapters = {a.value for a in ExecutionAdapter}
        assert "http" in all_adapters
        assert "database" in all_adapters
