"""Tests for Bootstrap Phase 6 — hermes implement CLI command.

Coverage:
  - _snapshot_to_context() serialiser with various RepositorySnapshot shapes
  - WorkflowConfig.write_mode default and override
  - implement command wiring: adapters, gateway, WorkflowConfig construction
  - CLI integration via typer.testing.CliRunner (with mocked EngineeringCoordinator)
  - Error handling: coordinator failure → exit code 1
  - --output optional; --repo optional
  - RepositorySnapshot context string format
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from hermes.cli.main import app
from hermes.models.engineering_workflow import WorkflowConfig
from hermes.models.execution_gateway import ExecutionAdapter, ExecutionStatus
from hermes.models.llm_adapter import LLMProvider
from hermes.models.repository_intelligence import (
    BuildSystemDetection,
    EntryPoint,
    LanguageDetection,
    RepositoryFile,
    RepositorySnapshot,
    TestLocation,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

runner = CliRunner()


def _make_snapshot(
    *,
    primary_language: str = "python",
    languages: tuple[LanguageDetection, ...] = (),
    build_system: BuildSystemDetection | None = None,
    entry_points: tuple[EntryPoint, ...] = (),
    test_locations: tuple[TestLocation, ...] = (),
    files: tuple[RepositoryFile, ...] = (),
    file_count: int = 0,
    directory_count: int = 0,
    git_present: bool = True,
) -> RepositorySnapshot:
    return RepositorySnapshot(
        snapshot_id="test-id",
        repository_path=".",
        scanned_at="2026-01-01T00:00:00+00:00",
        primary_language=primary_language,
        languages=languages,
        build_system=build_system,
        entry_points=entry_points,
        test_locations=test_locations,
        config_files=(),
        documentation=(),
        files=files,
        file_count=file_count,
        directory_count=directory_count,
        total_size_bytes=0,
        git_present=git_present,
        metadata=(),
    )


def _make_success_report():
    """Return a mock WorkflowExecutionReport representing full success."""
    step1 = MagicMock()
    step1.adapter_success = True
    step1.adapter_type = ExecutionAdapter.LLM
    step1.action_id = "generate"

    step2 = MagicMock()
    step2.adapter_success = True
    step2.adapter_type = ExecutionAdapter.FILESYSTEM
    step2.action_id = "overwrite_file"

    step3 = MagicMock()
    step3.adapter_success = True
    step3.adapter_type = ExecutionAdapter.GIT
    step3.action_id = "add"

    step4 = MagicMock()
    step4.adapter_success = True
    step4.adapter_type = ExecutionAdapter.GIT
    step4.action_id = "commit"

    report = MagicMock()
    report.success = True
    report.error = None
    report.steps = [step1, step2, step3, step4]
    report.metadata = [
        ("commit_message", "feat: add validation"),
        ("generated_file", "src/api.py"),
        ("goal_id", "abc123"),
        ("repository", "."),
        ("steps_completed", "4"),
    ]
    return report


def _make_failure_report(error: str = "llm_error: connection refused"):
    report = MagicMock()
    report.success = False
    report.error = error
    report.steps = []
    report.metadata = [("failure_stage", "generate"), ("steps_completed", "0")]
    return report


# ── WorkflowConfig.write_mode ─────────────────────────────────────────────────


class TestWorkflowConfigWriteMode:
    def test_default_write_mode_is_create_file(self):
        config = WorkflowConfig(
            llm_provider=LLMProvider.OLLAMA,
            llm_model="llama3.2",
            llm_base_url="http://localhost:11434",
            llm_api_key="",
            llm_max_tokens=4096,
            llm_timeout_seconds=60,
            commit_message="feat: test",
        )
        assert config.write_mode == "create_file"

    def test_write_mode_can_be_set_to_overwrite(self):
        config = WorkflowConfig(
            llm_provider=LLMProvider.OLLAMA,
            llm_model="llama3.2",
            llm_base_url="http://localhost:11434",
            llm_api_key="",
            llm_max_tokens=4096,
            llm_timeout_seconds=60,
            commit_message="feat: test",
            write_mode="overwrite_file",
        )
        assert config.write_mode == "overwrite_file"

    def test_write_mode_is_frozen(self):
        config = WorkflowConfig(
            llm_provider=LLMProvider.OLLAMA,
            llm_model="llama3.2",
            llm_base_url="http://localhost:11434",
            llm_api_key="",
            llm_max_tokens=4096,
            llm_timeout_seconds=60,
            commit_message="feat: test",
        )
        with pytest.raises((AttributeError, TypeError)):
            config.write_mode = "overwrite_file"  # type: ignore[misc]

    def test_write_mode_does_not_break_existing_config_construction(self):
        """All 7 original fields still work without write_mode."""
        config = WorkflowConfig(
            llm_provider=LLMProvider.OLLAMA,
            llm_model="llama3.2",
            llm_base_url="http://localhost:11434",
            llm_api_key="sk-test",
            llm_max_tokens=2048,
            llm_timeout_seconds=30,
            commit_message="chore: update",
        )
        assert config.llm_model == "llama3.2"
        assert config.commit_message == "chore: update"
        assert config.write_mode == "create_file"

    def test_default_test_command_is_empty_string(self):
        config = WorkflowConfig(
            llm_provider=LLMProvider.OLLAMA,
            llm_model="llama3.2",
            llm_base_url="http://localhost:11434",
            llm_api_key="",
            llm_max_tokens=4096,
            llm_timeout_seconds=60,
            commit_message="feat: test",
        )
        assert config.test_command == ""

    def test_test_command_can_be_set(self):
        config = WorkflowConfig(
            llm_provider=LLMProvider.OLLAMA,
            llm_model="llama3.2",
            llm_base_url="http://localhost:11434",
            llm_api_key="",
            llm_max_tokens=4096,
            llm_timeout_seconds=60,
            commit_message="feat: test",
            test_command="pytest -x",
        )
        assert config.test_command == "pytest -x"


# ── write_mode in WorkflowConfig ──────────────────────────────────────────────


class TestWorkflowConfigWriteModeField:
    """Verify that write_mode field is still present in WorkflowConfig (backward compat)."""

    def test_write_mode_field_present(self):
        config = WorkflowConfig(
            llm_provider=LLMProvider.OLLAMA,
            llm_model="llama3.2",
            llm_base_url="http://localhost:11434",
            llm_api_key="",
            llm_max_tokens=512,
            llm_timeout_seconds=10,
            commit_message="test",
        )
        # Field should exist even if not used in Phase 6 execution path
        assert hasattr(config, "write_mode")

    def test_write_mode_can_be_set(self):
        config = WorkflowConfig(
            llm_provider=LLMProvider.OLLAMA,
            llm_model="llama3.2",
            llm_base_url="http://localhost:11434",
            llm_api_key="",
            llm_max_tokens=512,
            llm_timeout_seconds=10,
            commit_message="test",
            write_mode="modify_file",
        )
        assert config.write_mode == "modify_file"


# ── CLI integration ───────────────────────────────────────────────────────────


class TestImplementCLI:
    """Integration tests for 'hermes implement' via Typer test runner.

    EngineeringWorkflow.execute is mocked so no real HTTP or git calls occur.
    RepositoryIntelligence.scan and configure_from_env are also mocked.
    """

    def _mock_env_cfg(self):
        from hermes.providers.ollama_driver import (
            OLLAMA_LOCAL_CAPABILITIES,
            OLLAMA_LOCAL_DRIVER,
            OllamaEnvConfig,
            OllamaMode,
        )
        env_cfg = OllamaEnvConfig(
            mode=OllamaMode.LOCAL,
            base_url="http://localhost:11434",
            api_key="",
        )
        return env_cfg, OLLAMA_LOCAL_CAPABILITIES, OLLAMA_LOCAL_DRIVER

    def _empty_snapshot(self) -> RepositorySnapshot:
        return _make_snapshot(file_count=0, directory_count=0, git_present=False)

    def test_implement_succeeds_exits_zero(self, tmp_path):
        report = _make_success_report()
        env_cfg, caps, driver = self._mock_env_cfg()

        with patch("hermes.cli.commands.implement.RepositoryIntelligence") as mock_ri, \
             patch("hermes.cli.commands.implement.configure_from_env", return_value=(env_cfg, caps, driver)), \
             patch("hermes.cli.commands.implement.EngineeringWorkflow") as mock_wf_cls, \
             patch("hermes.cli.commands.implement.Path.cwd", return_value=tmp_path):
            mock_ri.return_value.scan.return_value = self._empty_snapshot()
            mock_wf_cls.return_value.execute.return_value = report

            result = runner.invoke(app, [
                "implement",
                "Add validation to the API",
                "--output", "src/api.py",
            ])

        assert result.exit_code == 0, result.output

    def test_implement_failure_exits_one(self, tmp_path):
        report = _make_failure_report()
        env_cfg, caps, driver = self._mock_env_cfg()

        with patch("hermes.cli.commands.implement.RepositoryIntelligence") as mock_ri, \
             patch("hermes.cli.commands.implement.configure_from_env", return_value=(env_cfg, caps, driver)), \
             patch("hermes.cli.commands.implement.EngineeringWorkflow") as mock_wf_cls, \
             patch("hermes.cli.commands.implement.Path.cwd", return_value=tmp_path):
            mock_ri.return_value.scan.return_value = self._empty_snapshot()
            mock_wf_cls.return_value.execute.return_value = report

            result = runner.invoke(app, [
                "implement",
                "Add validation",
                "--output", "src/api.py",
            ])

        assert result.exit_code == 1

    def test_implement_output_optional_triggers_autonomous_mode(self, tmp_path):
        """When --output is omitted, implement runs in autonomous mode (goal.output_path='')."""
        report = _make_success_report()
        env_cfg, caps, driver = self._mock_env_cfg()

        captured_goals = []

        def capture_coordinator_execute(goal, snapshot):
            captured_goals.append(goal)
            return report

        with patch("hermes.cli.commands.implement.RepositoryIntelligence") as mock_ri, \
             patch("hermes.cli.commands.implement.configure_from_env", return_value=(env_cfg, caps, driver)), \
             patch("hermes.cli.commands.implement.EngineeringCoordinator") as mock_coord_cls, \
             patch("hermes.cli.commands.implement.Path.cwd", return_value=tmp_path):
            mock_ri.return_value.scan.return_value = self._empty_snapshot()
            mock_coord_cls.return_value.execute.side_effect = capture_coordinator_execute

            result = runner.invoke(app, ["implement", "Add validation"])

        # Must succeed at CLI level (no longer a required argument error)
        assert result.exit_code == 0, result.output
        # Goal must have empty output_path → autonomous mode
        assert len(captured_goals) == 1
        assert captured_goals[0].output_path == ""

    def test_implement_autonomous_mode_prints_autonomous_label(self, tmp_path):
        """When --output is omitted, stdout must show '(autonomous)' not the path."""
        report = _make_success_report()
        env_cfg, caps, driver = self._mock_env_cfg()

        with patch("hermes.cli.commands.implement.RepositoryIntelligence") as mock_ri, \
             patch("hermes.cli.commands.implement.configure_from_env", return_value=(env_cfg, caps, driver)), \
             patch("hermes.cli.commands.implement.EngineeringCoordinator") as mock_coord_cls, \
             patch("hermes.cli.commands.implement.Path.cwd", return_value=tmp_path):
            mock_ri.return_value.scan.return_value = self._empty_snapshot()
            mock_coord_cls.return_value.execute.return_value = report

            result = runner.invoke(app, ["implement", "Add validation"])

        assert "(autonomous)" in result.output

    def test_implement_prints_task_and_output(self, tmp_path):
        report = _make_success_report()
        env_cfg, caps, driver = self._mock_env_cfg()

        with patch("hermes.cli.commands.implement.RepositoryIntelligence") as mock_ri, \
             patch("hermes.cli.commands.implement.configure_from_env", return_value=(env_cfg, caps, driver)), \
             patch("hermes.cli.commands.implement.EngineeringWorkflow") as mock_wf_cls, \
             patch("hermes.cli.commands.implement.Path.cwd", return_value=tmp_path):
            mock_ri.return_value.scan.return_value = self._empty_snapshot()
            mock_wf_cls.return_value.execute.return_value = report

            result = runner.invoke(app, [
                "implement",
                "Add rate limiting to the API",
                "--output", "src/api.py",
            ])

        assert "Add rate limiting to the API" in result.output
        assert "src/api.py" in result.output

    def test_implement_prints_success_status(self, tmp_path):
        report = _make_success_report()
        env_cfg, caps, driver = self._mock_env_cfg()

        with patch("hermes.cli.commands.implement.RepositoryIntelligence") as mock_ri, \
             patch("hermes.cli.commands.implement.configure_from_env", return_value=(env_cfg, caps, driver)), \
             patch("hermes.cli.commands.implement.EngineeringWorkflow") as mock_wf_cls, \
             patch("hermes.cli.commands.implement.Path.cwd", return_value=tmp_path):
            mock_ri.return_value.scan.return_value = self._empty_snapshot()
            mock_wf_cls.return_value.execute.return_value = report

            result = runner.invoke(app, [
                "implement", "task", "--output", "src/f.py",
            ])

        assert "SUCCESS" in result.output

    def test_implement_prints_failure_error(self, tmp_path):
        report = _make_failure_report("connection refused to localhost:11434")
        env_cfg, caps, driver = self._mock_env_cfg()

        with patch("hermes.cli.commands.implement.RepositoryIntelligence") as mock_ri, \
             patch("hermes.cli.commands.implement.configure_from_env", return_value=(env_cfg, caps, driver)), \
             patch("hermes.cli.commands.implement.EngineeringWorkflow") as mock_wf_cls, \
             patch("hermes.cli.commands.implement.Path.cwd", return_value=tmp_path):
            mock_ri.return_value.scan.return_value = self._empty_snapshot()
            mock_wf_cls.return_value.execute.return_value = report

            result = runner.invoke(app, [
                "implement", "task", "--output", "src/f.py",
            ])

        assert "connection refused" in result.output

    def test_implement_repo_defaults_to_dot(self, tmp_path):
        report = _make_success_report()
        env_cfg, caps, driver = self._mock_env_cfg()

        captured_goals = []

        def capture_coordinator_execute(goal, snapshot):
            captured_goals.append(goal)
            return report

        with patch("hermes.cli.commands.implement.RepositoryIntelligence") as mock_ri, \
             patch("hermes.cli.commands.implement.configure_from_env", return_value=(env_cfg, caps, driver)), \
             patch("hermes.cli.commands.implement.EngineeringCoordinator") as mock_coord_cls, \
             patch("hermes.cli.commands.implement.Path.cwd", return_value=tmp_path):
            mock_ri.return_value.scan.return_value = self._empty_snapshot()
            mock_coord_cls.return_value.execute.side_effect = capture_coordinator_execute

            runner.invoke(app, ["implement", "task", "--output", "f.py"])

        assert len(captured_goals) == 1
        assert captured_goals[0].repository_path == "."

    def test_implement_repo_override(self, tmp_path):
        report = _make_success_report()
        env_cfg, caps, driver = self._mock_env_cfg()

        captured_goals = []

        def capture_coordinator_execute(goal, snapshot):
            captured_goals.append(goal)
            return report

        with patch("hermes.cli.commands.implement.RepositoryIntelligence") as mock_ri, \
             patch("hermes.cli.commands.implement.configure_from_env", return_value=(env_cfg, caps, driver)), \
             patch("hermes.cli.commands.implement.EngineeringCoordinator") as mock_coord_cls, \
             patch("hermes.cli.commands.implement.Path.cwd", return_value=tmp_path):
            mock_ri.return_value.scan.return_value = self._empty_snapshot()
            mock_coord_cls.return_value.execute.side_effect = capture_coordinator_execute

            runner.invoke(app, ["implement", "task", "--output", "f.py", "--repo", "myrepo"])

        assert captured_goals[0].repository_path == "myrepo"

    def test_implement_task_injected_into_goal_description(self, tmp_path):
        report = _make_success_report()
        env_cfg, caps, driver = self._mock_env_cfg()

        captured_goals = []

        def capture_coordinator_execute(goal, snapshot):
            captured_goals.append(goal)
            return report

        with patch("hermes.cli.commands.implement.RepositoryIntelligence") as mock_ri, \
             patch("hermes.cli.commands.implement.configure_from_env", return_value=(env_cfg, caps, driver)), \
             patch("hermes.cli.commands.implement.EngineeringCoordinator") as mock_coord_cls, \
             patch("hermes.cli.commands.implement.Path.cwd", return_value=tmp_path):
            mock_ri.return_value.scan.return_value = self._empty_snapshot()
            mock_coord_cls.return_value.execute.side_effect = capture_coordinator_execute

            runner.invoke(app, [
                "implement",
                "Add JWT authentication to the login endpoint",
                "--output", "src/auth.py",
            ])

        assert "Add JWT authentication" in captured_goals[0].description

    def test_implement_goal_description_is_task_only(self, tmp_path):
        """goal.description must be the task string only; repository context
        flows via the RepositorySnapshot parameter, not via description injection."""
        report = _make_success_report()
        env_cfg, caps, driver = self._mock_env_cfg()

        captured_goals = []

        def capture_coordinator_execute(goal, snapshot):
            captured_goals.append(goal)
            return report

        snap = _make_snapshot(primary_language="python", git_present=True, file_count=5)

        with patch("hermes.cli.commands.implement.RepositoryIntelligence") as mock_ri, \
             patch("hermes.cli.commands.implement.configure_from_env", return_value=(env_cfg, caps, driver)), \
             patch("hermes.cli.commands.implement.EngineeringCoordinator") as mock_coord_cls, \
             patch("hermes.cli.commands.implement.Path.cwd", return_value=tmp_path):
            mock_ri.return_value.scan.return_value = snap
            mock_coord_cls.return_value.execute.side_effect = capture_coordinator_execute

            runner.invoke(app, ["implement", "Add fallback handler", "--output", "src/f.py"])

        desc = captured_goals[0].description
        assert "Add fallback handler" in desc
        assert "Repository context" not in desc

    def test_implement_snapshot_passed_to_coordinator(self, tmp_path):
        """The RepositorySnapshot from scan() must be passed to coordinator.execute()."""
        report = _make_success_report()
        env_cfg, caps, driver = self._mock_env_cfg()

        captured_snapshots = []

        def capture_coordinator_execute(goal, snapshot):
            captured_snapshots.append(snapshot)
            return report

        snap = _make_snapshot(file_count=7)

        with patch("hermes.cli.commands.implement.RepositoryIntelligence") as mock_ri, \
             patch("hermes.cli.commands.implement.configure_from_env", return_value=(env_cfg, caps, driver)), \
             patch("hermes.cli.commands.implement.EngineeringCoordinator") as mock_coord_cls, \
             patch("hermes.cli.commands.implement.Path.cwd", return_value=tmp_path):
            mock_ri.return_value.scan.return_value = snap
            mock_coord_cls.return_value.execute.side_effect = capture_coordinator_execute

            runner.invoke(app, ["implement", "Add something", "--output", "src/f.py"])

        assert len(captured_snapshots) == 1
        assert captured_snapshots[0] is snap

    def test_implement_write_mode_defaults_to_create_file(self, tmp_path):
        """Phase 6: implement.py no longer derives write_mode from file existence.
        WorkflowConfig always uses default write_mode='create_file'; intent is
        determined by EngineeringCoordinator from PlannedOperation.intent.
        """
        report = _make_success_report()
        env_cfg, caps, driver = self._mock_env_cfg()

        captured_configs = []

        def capture_init(**kwargs):
            captured_configs.append(kwargs.get("config"))
            m = MagicMock()
            m.execute.return_value = report
            return m

        with patch("hermes.cli.commands.implement.RepositoryIntelligence") as mock_ri, \
             patch("hermes.cli.commands.implement.configure_from_env", return_value=(env_cfg, caps, driver)), \
             patch("hermes.cli.commands.implement.EngineeringWorkflow", side_effect=capture_init), \
             patch("hermes.cli.commands.implement.Path.cwd", return_value=tmp_path):
            mock_ri.return_value.scan.return_value = self._empty_snapshot()
            runner.invoke(app, ["implement", "task", "--output", "brand_new.py"])

        # Default write_mode is always 'create_file'; coordinator determines intent
        assert captured_configs[0].write_mode == "create_file"

    def test_implement_goal_output_path_set_correctly(self, tmp_path):
        report = _make_success_report()
        env_cfg, caps, driver = self._mock_env_cfg()

        captured_goals = []

        def capture_coordinator_execute(goal, snapshot):
            captured_goals.append(goal)
            return report

        with patch("hermes.cli.commands.implement.RepositoryIntelligence") as mock_ri, \
             patch("hermes.cli.commands.implement.configure_from_env", return_value=(env_cfg, caps, driver)), \
             patch("hermes.cli.commands.implement.EngineeringCoordinator") as mock_coord_cls, \
             patch("hermes.cli.commands.implement.Path.cwd", return_value=tmp_path):
            mock_ri.return_value.scan.return_value = self._empty_snapshot()
            mock_coord_cls.return_value.execute.side_effect = capture_coordinator_execute

            runner.invoke(app, ["implement", "task", "--output", "src/handlers.py"])

        assert captured_goals[0].output_path == "src/handlers.py"

    def test_implement_goal_workspace_path_falls_back_to_cwd(self, tmp_path):
        """Without HERMES_REPOSITORIES set, workspace_path defaults to CWD."""
        report = _make_success_report()
        env_cfg, caps, driver = self._mock_env_cfg()

        captured_goals = []

        def capture_coordinator_execute(goal, snapshot):
            captured_goals.append(goal)
            return report

        with patch("hermes.cli.commands.implement.RepositoryIntelligence") as mock_ri, \
             patch("hermes.cli.commands.implement.configure_from_env", return_value=(env_cfg, caps, driver)), \
             patch("hermes.cli.commands.implement.EngineeringCoordinator") as mock_coord_cls, \
             patch("hermes.cli.commands.implement.Path.cwd", return_value=tmp_path), \
             patch.dict("os.environ", {}, clear=False):
            # Ensure HERMES_REPOSITORIES is absent so the CWD fallback fires.
            os.environ.pop("HERMES_REPOSITORIES", None)
            mock_ri.return_value.scan.return_value = self._empty_snapshot()
            mock_coord_cls.return_value.execute.side_effect = capture_coordinator_execute

            runner.invoke(app, ["implement", "task", "--output", "f.py"])

        assert captured_goals[0].workspace_path == str(tmp_path)

    def test_implement_goal_workspace_path_uses_hermes_repositories_env(self, tmp_path):
        """When HERMES_REPOSITORIES is set, workspace_path uses that directory."""
        report = _make_success_report()
        env_cfg, caps, driver = self._mock_env_cfg()

        captured_goals = []

        def capture_coordinator_execute(goal, snapshot):
            captured_goals.append(goal)
            return report

        repos_root = tmp_path / "repos"
        repos_root.mkdir()

        with patch("hermes.cli.commands.implement.RepositoryIntelligence") as mock_ri, \
             patch("hermes.cli.commands.implement.configure_from_env", return_value=(env_cfg, caps, driver)), \
             patch("hermes.cli.commands.implement.EngineeringCoordinator") as mock_coord_cls, \
             patch.dict("os.environ", {"HERMES_REPOSITORIES": str(repos_root)}):
            mock_ri.return_value.scan.return_value = self._empty_snapshot()
            mock_coord_cls.return_value.execute.side_effect = capture_coordinator_execute

            runner.invoke(app, ["implement", "task", "--output", "f.py"])

        assert captured_goals[0].workspace_path == str(repos_root)

    def test_implement_commit_message_derived_from_task(self, tmp_path):
        report = _make_success_report()
        env_cfg, caps, driver = self._mock_env_cfg()

        captured_configs = []

        def capture_init(**kwargs):
            captured_configs.append(kwargs.get("config"))
            m = MagicMock()
            m.execute.return_value = report
            return m

        with patch("hermes.cli.commands.implement.RepositoryIntelligence") as mock_ri, \
             patch("hermes.cli.commands.implement.configure_from_env", return_value=(env_cfg, caps, driver)), \
             patch("hermes.cli.commands.implement.EngineeringWorkflow", side_effect=capture_init), \
             patch("hermes.cli.commands.implement.Path.cwd", return_value=tmp_path):
            mock_ri.return_value.scan.return_value = self._empty_snapshot()
            runner.invoke(app, ["implement", "Add logging to scheduler", "--output", "f.py"])

        assert "Add logging to scheduler" in captured_configs[0].commit_message

    def test_implement_llm_provider_is_ollama(self, tmp_path):
        report = _make_success_report()
        env_cfg, caps, driver = self._mock_env_cfg()

        captured_configs = []

        def capture_init(**kwargs):
            captured_configs.append(kwargs.get("config"))
            m = MagicMock()
            m.execute.return_value = report
            return m

        with patch("hermes.cli.commands.implement.RepositoryIntelligence") as mock_ri, \
             patch("hermes.cli.commands.implement.configure_from_env", return_value=(env_cfg, caps, driver)), \
             patch("hermes.cli.commands.implement.EngineeringWorkflow", side_effect=capture_init), \
             patch("hermes.cli.commands.implement.Path.cwd", return_value=tmp_path):
            mock_ri.return_value.scan.return_value = self._empty_snapshot()
            runner.invoke(app, ["implement", "task", "--output", "f.py"])

        assert captured_configs[0].llm_provider == LLMProvider.OLLAMA

    def test_implement_gateway_registers_four_adapters(self, tmp_path):
        report = _make_success_report()
        env_cfg, caps, driver = self._mock_env_cfg()

        captured_gateways = []

        def capture_init(**kwargs):
            captured_gateways.append(kwargs.get("gateway"))
            m = MagicMock()
            m.execute.return_value = report
            return m

        with patch("hermes.cli.commands.implement.RepositoryIntelligence") as mock_ri, \
             patch("hermes.cli.commands.implement.configure_from_env", return_value=(env_cfg, caps, driver)), \
             patch("hermes.cli.commands.implement.EngineeringWorkflow", side_effect=capture_init), \
             patch("hermes.cli.commands.implement.Path.cwd", return_value=tmp_path):
            mock_ri.return_value.scan.return_value = self._empty_snapshot()
            runner.invoke(app, ["implement", "task", "--output", "f.py"])

        gw = captured_gateways[0]
        assert gw.resolve(ExecutionAdapter.LLM) is not None
        assert gw.resolve(ExecutionAdapter.FILESYSTEM) is not None
        assert gw.resolve(ExecutionAdapter.GIT) is not None
        assert gw.resolve(ExecutionAdapter.VALIDATION) is not None

    def test_implement_ri_scan_called_with_repo(self, tmp_path):
        report = _make_success_report()
        env_cfg, caps, driver = self._mock_env_cfg()

        with patch("hermes.cli.commands.implement.RepositoryIntelligence") as mock_ri_cls, \
             patch("hermes.cli.commands.implement.configure_from_env", return_value=(env_cfg, caps, driver)), \
             patch("hermes.cli.commands.implement.EngineeringWorkflow") as mock_wf_cls, \
             patch("hermes.cli.commands.implement.Path.cwd", return_value=tmp_path):
            mock_ri_cls.return_value.scan.return_value = self._empty_snapshot()
            mock_wf_cls.return_value.execute.return_value = report

            runner.invoke(app, ["implement", "task", "--output", "f.py", "--repo", "myrepo"])

        mock_ri_cls.return_value.scan.assert_called_once_with("myrepo")

    def test_implement_configure_from_env_called(self, tmp_path):
        report = _make_success_report()
        env_cfg, caps, driver = self._mock_env_cfg()

        with patch("hermes.cli.commands.implement.RepositoryIntelligence") as mock_ri, \
             patch("hermes.cli.commands.implement.configure_from_env", return_value=(env_cfg, caps, driver)) as mock_cfg, \
             patch("hermes.cli.commands.implement.EngineeringWorkflow") as mock_wf_cls, \
             patch("hermes.cli.commands.implement.Path.cwd", return_value=tmp_path):
            mock_ri.return_value.scan.return_value = self._empty_snapshot()
            mock_wf_cls.return_value.execute.return_value = report

            runner.invoke(app, ["implement", "task", "--output", "f.py"])

        mock_cfg.assert_called_once()


# ── CLI is registered in main app ─────────────────────────────────────────────


class TestCLIRegistration:
    def test_implement_command_exists_in_app(self):
        result = runner.invoke(app, ["implement", "--help"])
        assert result.exit_code == 0
        assert "implement" in result.output.lower() or "task" in result.output.lower()

    def test_implement_help_mentions_output(self):
        result = runner.invoke(app, ["implement", "--help"])
        assert "--output" in result.output or "-o" in result.output

    def test_implement_help_mentions_repo(self):
        result = runner.invoke(app, ["implement", "--help"])
        assert "--repo" in result.output or "-r" in result.output

    def test_hermes_help_lists_implement(self):
        result = runner.invoke(app, ["--help"])
        assert "implement" in result.output


# ── Total execution time ──────────────────────────────────────────────────────


class TestTotalExecutionTime:
    """Total execution time appears in CLI output on success and failure."""

    def _mock_env_cfg(self):
        from hermes.providers.ollama_driver import (
            OLLAMA_LOCAL_CAPABILITIES,
            OLLAMA_LOCAL_DRIVER,
            OllamaEnvConfig,
            OllamaMode,
        )
        return (
            OllamaEnvConfig(mode=OllamaMode.LOCAL, base_url="http://localhost:11434", api_key=""),
            OLLAMA_LOCAL_CAPABILITIES,
            OLLAMA_LOCAL_DRIVER,
        )

    def _empty_snapshot(self) -> RepositorySnapshot:
        return _make_snapshot(file_count=0, directory_count=0, git_present=False)

    def test_total_execution_time_shown_on_success(self, tmp_path):
        report = _make_success_report()
        env_cfg, caps, driver = self._mock_env_cfg()

        with patch("hermes.cli.commands.implement.RepositoryIntelligence") as mock_ri, \
             patch("hermes.cli.commands.implement.configure_from_env", return_value=(env_cfg, caps, driver)), \
             patch("hermes.cli.commands.implement.EngineeringWorkflow") as mock_wf_cls, \
             patch("hermes.cli.commands.implement.Path.cwd", return_value=tmp_path):
            mock_ri.return_value.scan.return_value = self._empty_snapshot()
            mock_wf_cls.return_value.execute.return_value = report

            result = runner.invoke(app, ["implement", "task", "--output", "f.py"])

        assert "Total execution time" in result.output

    def test_total_execution_time_shown_on_failure(self, tmp_path):
        report = _make_failure_report()
        env_cfg, caps, driver = self._mock_env_cfg()

        with patch("hermes.cli.commands.implement.RepositoryIntelligence") as mock_ri, \
             patch("hermes.cli.commands.implement.configure_from_env", return_value=(env_cfg, caps, driver)), \
             patch("hermes.cli.commands.implement.EngineeringWorkflow") as mock_wf_cls, \
             patch("hermes.cli.commands.implement.Path.cwd", return_value=tmp_path):
            mock_ri.return_value.scan.return_value = self._empty_snapshot()
            mock_wf_cls.return_value.execute.return_value = report

            result = runner.invoke(app, ["implement", "task", "--output", "f.py"])

        assert "Total execution time" in result.output

    def test_total_execution_time_format_matches_pattern(self, tmp_path):
        """Format must be X.Xs (one decimal place, seconds suffix)."""
        report = _make_success_report()
        env_cfg, caps, driver = self._mock_env_cfg()

        with patch("hermes.cli.commands.implement.RepositoryIntelligence") as mock_ri, \
             patch("hermes.cli.commands.implement.configure_from_env", return_value=(env_cfg, caps, driver)), \
             patch("hermes.cli.commands.implement.EngineeringWorkflow") as mock_wf_cls, \
             patch("hermes.cli.commands.implement.Path.cwd", return_value=tmp_path):
            mock_ri.return_value.scan.return_value = self._empty_snapshot()
            mock_wf_cls.return_value.execute.return_value = report

            result = runner.invoke(app, ["implement", "task", "--output", "f.py"])

        assert re.search(r"Total execution time: \d+\.\d+s", result.output)


# ── TestTestCommandExtraction ─────────────────────────────────────────────────


class TestTestCommandExtraction:
    """Verify test_command is extracted from BuildSystemDetection and passed to WorkflowConfig."""

    def _mock_env_cfg(self):
        from hermes.providers.ollama_driver import (
            OLLAMA_LOCAL_CAPABILITIES,
            OLLAMA_LOCAL_DRIVER,
            OllamaEnvConfig,
            OllamaMode,
        )
        return (
            OllamaEnvConfig(mode=OllamaMode.LOCAL, base_url="http://localhost:11434", api_key=""),
            OLLAMA_LOCAL_CAPABILITIES,
            OLLAMA_LOCAL_DRIVER,
        )

    def test_test_command_from_build_system(self, tmp_path):
        """When BuildSystemDetection has a test_command, it flows to WorkflowConfig."""
        from unittest.mock import patch, MagicMock
        report = _make_success_report()
        env_cfg, caps, driver = self._mock_env_cfg()

        snap = _make_snapshot(build_system=BuildSystemDetection(
            name="poetry",
            config_file="pyproject.toml",
            build_command="poetry build",
            test_command="pytest --tb=short",
        ))

        captured_configs = []

        def capture_init(**kwargs):
            captured_configs.append(kwargs.get("config"))
            m = MagicMock()
            m.execute.return_value = report
            return m

        with patch("hermes.cli.commands.implement.RepositoryIntelligence") as mock_ri, \
             patch("hermes.cli.commands.implement.configure_from_env", return_value=(env_cfg, caps, driver)), \
             patch("hermes.cli.commands.implement.EngineeringWorkflow", side_effect=capture_init), \
             patch("hermes.cli.commands.implement.Path.cwd", return_value=tmp_path):
            mock_ri.return_value.scan.return_value = snap
            runner.invoke(app, ["implement", "task", "--output", "f.py"])

        assert captured_configs[0].test_command == "pytest --tb=short"

    def test_no_build_system_gives_empty_test_command(self, tmp_path):
        """When build_system is None, test_command must be empty string (not 'pytest')."""
        from unittest.mock import patch, MagicMock
        report = _make_success_report()
        env_cfg, caps, driver = self._mock_env_cfg()

        snap = _make_snapshot(build_system=None)
        captured_configs = []

        def capture_init(**kwargs):
            captured_configs.append(kwargs.get("config"))
            m = MagicMock()
            m.execute.return_value = report
            return m

        with patch("hermes.cli.commands.implement.RepositoryIntelligence") as mock_ri, \
             patch("hermes.cli.commands.implement.configure_from_env", return_value=(env_cfg, caps, driver)), \
             patch("hermes.cli.commands.implement.EngineeringWorkflow", side_effect=capture_init), \
             patch("hermes.cli.commands.implement.Path.cwd", return_value=tmp_path):
            mock_ri.return_value.scan.return_value = snap
            runner.invoke(app, ["implement", "task", "--output", "f.py"])

        assert captured_configs[0].test_command == ""

    def test_empty_build_system_test_command_gives_empty(self, tmp_path):
        """When build_system.test_command is empty, test_command must be empty (no fallback)."""
        from unittest.mock import patch, MagicMock
        report = _make_success_report()
        env_cfg, caps, driver = self._mock_env_cfg()

        snap = _make_snapshot(build_system=BuildSystemDetection(
            name="custom",
            config_file="Makefile",
            build_command="make build",
            test_command="",
        ))
        captured_configs = []

        def capture_init(**kwargs):
            captured_configs.append(kwargs.get("config"))
            m = MagicMock()
            m.execute.return_value = report
            return m

        with patch("hermes.cli.commands.implement.RepositoryIntelligence") as mock_ri, \
             patch("hermes.cli.commands.implement.configure_from_env", return_value=(env_cfg, caps, driver)), \
             patch("hermes.cli.commands.implement.EngineeringWorkflow", side_effect=capture_init), \
             patch("hermes.cli.commands.implement.Path.cwd", return_value=tmp_path):
            mock_ri.return_value.scan.return_value = snap
            runner.invoke(app, ["implement", "task", "--output", "f.py"])

        assert captured_configs[0].test_command == ""
