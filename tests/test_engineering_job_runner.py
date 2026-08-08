"""Tests for EngineeringJobRunner — AT-9 unit gate.

Coverage:
- run() transitions: pending → running → completed (success)
- run() transitions: pending → running → failed (coordinator returns success=False)
- run() transitions: pending → running → failed (exception raised)
- run() never raises (exception in run() is swallowed)
- commit_sha and files_changed populated on success
- error populated on failure, commit_sha/files_changed absent
"""

from unittest.mock import MagicMock, patch

import pytest

from hermes.kernel.engineering_job_runner import EngineeringJobRunner, EngineeringJobStore
from hermes.models.engineering_workflow import WorkflowExecutionReport


def _make_report(success: bool, error: str | None = None) -> WorkflowExecutionReport:
    return WorkflowExecutionReport(
        report_id="report-test",
        goal_id="goal-test",
        mission_id="mission-test",
        job_id="job-test",
        steps=(),
        success=success,
        error=error,
        execution_sequence=(),
        metadata=(),
    )


_PATCH_BASE = "hermes.kernel.engineering_job_runner"


def _make_runner(tmp_path):
    store = EngineeringJobStore(workspaces_root=tmp_path)
    runner = EngineeringJobRunner(job_store=store)
    return runner, store


def _common_patches():
    """Return a list of patch context managers for the heavy dependencies."""
    return [
        patch(f"{_PATCH_BASE}.RepositoryIntelligence"),
        patch(f"{_PATCH_BASE}.configure_from_env"),
        patch(f"{_PATCH_BASE}.FilesystemAdapter"),
        patch(f"{_PATCH_BASE}.GitAdapter"),
        patch(f"{_PATCH_BASE}.ValidationAdapter"),
        patch(f"{_PATCH_BASE}.OperationEngine"),
        patch(f"{_PATCH_BASE}.LlmAdapter"),
        patch(f"{_PATCH_BASE}.CorrectionEngine"),
        patch(f"{_PATCH_BASE}.EngineeringWorkflow"),
        patch(f"{_PATCH_BASE}.EngineeringPlanner"),
        patch(f"{_PATCH_BASE}.ExecutionGateway"),
        patch(f"{_PATCH_BASE}.EngineeringCoordinator"),
    ]


def test_run_success_transitions_to_completed(tmp_path):
    runner, store = _make_runner(tmp_path)
    job = runner.create("ws1", "add greeter function", "greeter")

    mock_ri = MagicMock()
    mock_scan = MagicMock()
    mock_scan.build_system = None
    mock_ri.return_value.scan.return_value = mock_scan

    mock_env_cfg = MagicMock()
    mock_env_cfg.base_url = "http://localhost:11434"
    mock_env_cfg.api_key = ""
    mock_capabilities = MagicMock()
    mock_capabilities.default_model = "llama3.2"
    mock_driver = MagicMock()
    mock_configure = MagicMock(return_value=(mock_env_cfg, mock_capabilities, mock_driver))

    mock_coordinator = MagicMock()
    mock_coordinator.return_value.execute.return_value = _make_report(success=True)

    with patch(f"{_PATCH_BASE}.RepositoryIntelligence", mock_ri), \
         patch(f"{_PATCH_BASE}.configure_from_env", mock_configure), \
         patch(f"{_PATCH_BASE}.FilesystemAdapter"), \
         patch(f"{_PATCH_BASE}.GitAdapter"), \
         patch(f"{_PATCH_BASE}.ValidationAdapter"), \
         patch(f"{_PATCH_BASE}.OperationEngine"), \
         patch(f"{_PATCH_BASE}.LlmAdapter"), \
         patch(f"{_PATCH_BASE}.CorrectionEngine"), \
         patch(f"{_PATCH_BASE}.EngineeringWorkflow"), \
         patch(f"{_PATCH_BASE}.EngineeringPlanner"), \
         patch(f"{_PATCH_BASE}.ExecutionGateway"), \
         patch(f"{_PATCH_BASE}.EngineeringCoordinator", mock_coordinator), \
         patch(f"{_PATCH_BASE}._git_rev_parse_head", return_value="a" * 40), \
         patch(f"{_PATCH_BASE}._git_diff_tree_files", return_value=["greeter.py"]):
        runner.run("ws1", str(tmp_path), job.job_id)

    loaded = store.load("ws1", job.job_id)
    assert loaded.status == "completed"
    assert loaded.commit_sha == "a" * 40
    assert loaded.files_changed == ("greeter.py",)
    assert loaded.error is None


def test_run_failure_from_report(tmp_path):
    runner, store = _make_runner(tmp_path)
    job = runner.create("ws1", "add greeter function", "greeter")

    mock_ri = MagicMock()
    mock_scan = MagicMock()
    mock_scan.build_system = None
    mock_ri.return_value.scan.return_value = mock_scan

    mock_env_cfg = MagicMock()
    mock_env_cfg.base_url = "http://localhost:11434"
    mock_env_cfg.api_key = ""
    mock_capabilities = MagicMock()
    mock_capabilities.default_model = "llama3.2"
    mock_driver = MagicMock()
    mock_configure = MagicMock(return_value=(mock_env_cfg, mock_capabilities, mock_driver))

    mock_coordinator = MagicMock()
    mock_coordinator.return_value.execute.return_value = _make_report(success=False, error="pipeline_failed")

    with patch(f"{_PATCH_BASE}.RepositoryIntelligence", mock_ri), \
         patch(f"{_PATCH_BASE}.configure_from_env", mock_configure), \
         patch(f"{_PATCH_BASE}.FilesystemAdapter"), \
         patch(f"{_PATCH_BASE}.GitAdapter"), \
         patch(f"{_PATCH_BASE}.ValidationAdapter"), \
         patch(f"{_PATCH_BASE}.OperationEngine"), \
         patch(f"{_PATCH_BASE}.LlmAdapter"), \
         patch(f"{_PATCH_BASE}.CorrectionEngine"), \
         patch(f"{_PATCH_BASE}.EngineeringWorkflow"), \
         patch(f"{_PATCH_BASE}.EngineeringPlanner"), \
         patch(f"{_PATCH_BASE}.ExecutionGateway"), \
         patch(f"{_PATCH_BASE}.EngineeringCoordinator", mock_coordinator):
        runner.run("ws1", str(tmp_path), job.job_id)

    loaded = store.load("ws1", job.job_id)
    assert loaded.status == "failed"
    assert loaded.error
    assert loaded.commit_sha is None


def test_run_failure_from_exception(tmp_path):
    runner, store = _make_runner(tmp_path)
    job = runner.create("ws1", "add greeter function", "greeter")

    mock_ri = MagicMock()
    mock_ri.return_value.scan.side_effect = RuntimeError("boom")

    with patch(f"{_PATCH_BASE}.RepositoryIntelligence", mock_ri), \
         patch(f"{_PATCH_BASE}.configure_from_env"):
        runner.run("ws1", str(tmp_path), job.job_id)

    loaded = store.load("ws1", job.job_id)
    assert loaded.status == "failed"
    assert loaded.error == "boom"
    assert loaded.commit_sha is None


def test_run_never_raises(tmp_path):
    runner, store = _make_runner(tmp_path)
    job = runner.create("ws1", "add greeter function", "greeter")

    mock_coordinator = MagicMock()
    mock_coordinator.return_value.execute.side_effect = RuntimeError("unexpected")

    mock_ri = MagicMock()
    mock_scan = MagicMock()
    mock_scan.build_system = None
    mock_ri.return_value.scan.return_value = mock_scan

    mock_env_cfg = MagicMock()
    mock_env_cfg.base_url = "http://localhost:11434"
    mock_env_cfg.api_key = ""
    mock_capabilities = MagicMock()
    mock_capabilities.default_model = "llama3.2"
    mock_driver = MagicMock()
    mock_configure = MagicMock(return_value=(mock_env_cfg, mock_capabilities, mock_driver))

    # Must not raise
    with patch(f"{_PATCH_BASE}.RepositoryIntelligence", mock_ri), \
         patch(f"{_PATCH_BASE}.configure_from_env", mock_configure), \
         patch(f"{_PATCH_BASE}.FilesystemAdapter"), \
         patch(f"{_PATCH_BASE}.GitAdapter"), \
         patch(f"{_PATCH_BASE}.ValidationAdapter"), \
         patch(f"{_PATCH_BASE}.OperationEngine"), \
         patch(f"{_PATCH_BASE}.LlmAdapter"), \
         patch(f"{_PATCH_BASE}.CorrectionEngine"), \
         patch(f"{_PATCH_BASE}.EngineeringWorkflow"), \
         patch(f"{_PATCH_BASE}.EngineeringPlanner"), \
         patch(f"{_PATCH_BASE}.ExecutionGateway"), \
         patch(f"{_PATCH_BASE}.EngineeringCoordinator", mock_coordinator):
        runner.run("ws1", str(tmp_path), job.job_id)  # must not raise
