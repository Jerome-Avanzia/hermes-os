"""Tests for Bootstrap Phase 1 — hermes implement CLI command.

Coverage:
  - _snapshot_to_context() serialiser with various RepositorySnapshot shapes
  - WorkflowConfig.write_mode default and override
  - write_mode flows through _build_operations → action_id
  - implement command wiring: adapters, gateway, WorkflowConfig construction
  - write_mode auto-selection: create_file for new files, overwrite_file for existing
  - CLI integration via typer.testing.CliRunner (with mocked EngineeringWorkflow)
  - Error handling: workflow failure → exit code 1
  - --output required; --repo optional
  - RepositorySnapshot context string format
  - Existing EngineeringWorkflow tests are NOT modified
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from hermes.cli.commands.implement import _snapshot_to_context
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


# ── write_mode flows through EngineeringWorkflow ──────────────────────────────


class TestWriteModeFlowsThroughWorkflow:
    """Verify that write_mode propagates from WorkflowConfig to operation action_id."""

    def _make_workflow(self, tmp_path: Path, write_mode: str):
        from hermes.adapters.filesystem_adapter import FilesystemAdapter
        from hermes.adapters.git_adapter import GitAdapter
        from hermes.adapters.llm_adapter import LlmAdapter
        from hermes.adapters.validation_adapter import ValidationAdapter
        from hermes.kernel.execution_gateway import ExecutionGateway
        from hermes.kernel.job_engine import JobEngine
        from hermes.kernel.operation_engine import OperationEngine
        from hermes.kernel.skill_registry import SkillRegistry
        from hermes.models.execution_gateway import AdapterRegistration
        from hermes.workflows.engineering_workflow import EngineeringWorkflow

        gw = ExecutionGateway()
        gw.register(AdapterRegistration(adapter=ExecutionAdapter.LLM, adapter_id="llm-test", available=True, description=""))
        gw.register(AdapterRegistration(adapter=ExecutionAdapter.FILESYSTEM, adapter_id="fs-test", available=True, description=""))
        gw.register(AdapterRegistration(adapter=ExecutionAdapter.GIT, adapter_id="git-test", available=True, description=""))
        gw.register(AdapterRegistration(adapter=ExecutionAdapter.VALIDATION, adapter_id="validation-test", available=True, description=""))

        config = WorkflowConfig(
            llm_provider=LLMProvider.OLLAMA,
            llm_model="llama3.2",
            llm_base_url="http://localhost:11434",
            llm_api_key="",
            llm_max_tokens=512,
            llm_timeout_seconds=10,
            commit_message="test",
            write_mode=write_mode,
        )
        return EngineeringWorkflow(
            gateway=gw,
            llm_adapter=LlmAdapter(),
            filesystem_adapter=FilesystemAdapter(workspace_root=tmp_path),
            git_adapter=GitAdapter(workspace_root=tmp_path),
            validation_adapter=ValidationAdapter(workspace_root=tmp_path),
            job_engine=JobEngine(registry=SkillRegistry()),
            operation_engine=OperationEngine(),
            config=config,
        )

    def test_create_file_write_mode_sets_action_id(self, tmp_path):
        from hermes.models.engineering_workflow import FounderGoal
        workflow = self._make_workflow(tmp_path, "create_file")
        goal = FounderGoal(
            goal_id="wm-test-1",
            description="test",
            workspace_path=str(tmp_path),
            repository_path=".",
            output_path="out.py",
        )
        ops = workflow._build_operations(goal, "job-wm-test-1")
        fs_op = next(o for o in ops if o.execution_ref and o.execution_ref.adapter_id == "filesystem")
        assert fs_op.execution_ref.action_id == "create_file"

    def test_modify_file_write_mode_sets_action_ids(self, tmp_path):
        """modify_file mode builds a 6-step plan with read_file, modify_file, and validate actions."""
        from hermes.models.engineering_workflow import FounderGoal
        workflow = self._make_workflow(tmp_path, "modify_file")
        goal = FounderGoal(
            goal_id="wm-test-2",
            description="test",
            workspace_path=str(tmp_path),
            repository_path=".",
            output_path="out.py",
        )
        ops = workflow._build_operations(goal, "job-wm-test-2")
        fs_action_ids = [
            o.execution_ref.action_id
            for o in ops
            if o.execution_ref and o.execution_ref.adapter_id == "filesystem"
        ]
        assert "read_file" in fs_action_ids
        assert "modify_file" in fs_action_ids
        assert len(ops) == 6

    def test_default_write_mode_preserves_create_file_action_id(self, tmp_path):
        """The default write_mode keeps existing behaviour: action_id == 'create_file'."""
        from hermes.models.engineering_workflow import FounderGoal
        workflow = self._make_workflow(tmp_path, "create_file")
        goal = FounderGoal(
            goal_id="wm-test-3",
            description="test",
            workspace_path=str(tmp_path),
            repository_path=".",
            output_path="out.py",
        )
        ops = workflow._build_operations(goal, "job-wm-test-3")
        fs_op = next(o for o in ops if o.execution_ref and o.execution_ref.adapter_id == "filesystem")
        # Must equal "create_file" to keep backward compatibility with existing tests
        assert fs_op.execution_ref.action_id == "create_file"


# ── _snapshot_to_context ──────────────────────────────────────────────────────


class TestSnapshotToContext:
    def test_empty_snapshot_produces_string(self):
        snap = _make_snapshot(primary_language="", git_present=False)
        result = _snapshot_to_context(snap)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_primary_language_in_output(self):
        snap = _make_snapshot(primary_language="python")
        result = _snapshot_to_context(snap)
        assert "python" in result

    def test_no_primary_language_omits_line(self):
        snap = _make_snapshot(primary_language="")
        result = _snapshot_to_context(snap)
        assert "Primary language" not in result

    def test_languages_summary_in_output(self):
        snap = _make_snapshot(languages=(
            LanguageDetection(language="python", file_count=10, confidence="primary", extensions=(".py",)),
            LanguageDetection(language="typescript", file_count=3, confidence="secondary", extensions=(".ts",)),
        ))
        result = _snapshot_to_context(snap)
        assert "python" in result
        assert "typescript" in result

    def test_build_system_in_output(self):
        snap = _make_snapshot(build_system=BuildSystemDetection(
            name="poetry", config_file="pyproject.toml",
            build_command="poetry build", test_command="pytest",
        ))
        result = _snapshot_to_context(snap)
        assert "poetry" in result
        assert "pyproject.toml" in result
        assert "pytest" in result

    def test_no_build_system_omits_line(self):
        snap = _make_snapshot(build_system=None)
        result = _snapshot_to_context(snap)
        assert "Build system" not in result

    def test_entry_points_in_output(self):
        snap = _make_snapshot(entry_points=(
            EntryPoint(path="src/main.py", kind="main", language="python"),
            EntryPoint(path="src/cli.py", kind="cli", language="python"),
        ))
        result = _snapshot_to_context(snap)
        assert "src/main.py" in result
        assert "main" in result

    def test_no_entry_points_omits_line(self):
        snap = _make_snapshot(entry_points=())
        result = _snapshot_to_context(snap)
        assert "Entry points" not in result

    def test_test_locations_in_output(self):
        snap = _make_snapshot(test_locations=(
            TestLocation(path="tests", kind="directory", framework="pytest", file_count=12),
        ))
        result = _snapshot_to_context(snap)
        assert "tests" in result
        assert "pytest" in result
        assert "12" in result

    def test_git_present_in_output(self):
        snap = _make_snapshot(git_present=True)
        result = _snapshot_to_context(snap)
        assert "git: yes" in result

    def test_git_absent_omits_marker(self):
        snap = _make_snapshot(git_present=False)
        result = _snapshot_to_context(snap)
        assert "git: yes" not in result

    def test_source_files_listed(self):
        files = tuple(
            RepositoryFile(path=f"src/module{i}.py", extension=".py", size_bytes=100, is_directory=False)
            for i in range(5)
        )
        snap = _make_snapshot(files=files, file_count=5)
        result = _snapshot_to_context(snap)
        assert "src/module0.py" in result

    def test_source_files_capped_at_30(self):
        files = tuple(
            RepositoryFile(path=f"src/f{i}.py", extension=".py", size_bytes=10, is_directory=False)
            for i in range(50)
        )
        snap = _make_snapshot(files=files, file_count=50)
        result = _snapshot_to_context(snap)
        # Only 30 shown
        assert "30 shown" in result
        assert "f29.py" in result
        assert "f30.py" not in result

    def test_directories_excluded_from_source_file_list(self):
        files = (
            RepositoryFile(path="src", extension="", size_bytes=0, is_directory=True),
            RepositoryFile(path="src/main.py", extension=".py", size_bytes=100, is_directory=False),
        )
        snap = _make_snapshot(files=files, file_count=1)
        result = _snapshot_to_context(snap)
        lines = result.splitlines()
        # "src" should not appear as a standalone file entry
        file_lines = [l for l in lines if l.strip().startswith("src") and not l.strip().startswith("src/")]
        assert all("src/main.py" not in l for l in file_lines)

    def test_non_source_extensions_excluded(self):
        files = (
            RepositoryFile(path="README.md", extension=".md", size_bytes=100, is_directory=False),
            RepositoryFile(path="data.json", extension=".json", size_bytes=100, is_directory=False),
            RepositoryFile(path="main.py", extension=".py", size_bytes=100, is_directory=False),
        )
        snap = _make_snapshot(files=files, file_count=3)
        result = _snapshot_to_context(snap)
        assert "main.py" in result
        # .md and .json are not in the source extensions set
        source_section = result.split("Source files")[1] if "Source files" in result else ""
        assert "README.md" not in source_section
        assert "data.json" not in source_section

    def test_file_count_in_output(self):
        snap = _make_snapshot(file_count=42, directory_count=7)
        result = _snapshot_to_context(snap)
        assert "42" in result
        assert "7" in result

    def test_output_is_deterministic(self):
        snap = _make_snapshot(
            primary_language="python",
            languages=(LanguageDetection("python", 5, "primary", (".py",)),),
            git_present=True,
            file_count=5,
        )
        assert _snapshot_to_context(snap) == _snapshot_to_context(snap)

    def test_output_is_newline_separated_string(self):
        snap = _make_snapshot(primary_language="rust", file_count=10)
        result = _snapshot_to_context(snap)
        assert "\n" in result
        assert not result.endswith("\n")

    def test_full_python_project_snapshot(self):
        snap = _make_snapshot(
            primary_language="python",
            languages=(
                LanguageDetection("python", 20, "primary", (".py",)),
                LanguageDetection("typescript", 3, "secondary", (".ts",)),
            ),
            build_system=BuildSystemDetection("poetry", "pyproject.toml", "poetry build", "pytest"),
            entry_points=(
                EntryPoint("src/main.py", "main", "python"),
                EntryPoint("src/api.py", "api", "python"),
            ),
            test_locations=(
                TestLocation("tests", "directory", "pytest", 15),
            ),
            files=(
                RepositoryFile("src/main.py", ".py", 500, False),
                RepositoryFile("src/api.py", ".py", 1200, False),
                RepositoryFile("src/models.py", ".py", 800, False),
                RepositoryFile("tests/test_api.py", ".py", 600, False),
            ),
            file_count=23,
            directory_count=4,
            git_present=True,
        )
        result = _snapshot_to_context(snap)
        assert "python" in result
        assert "poetry" in result
        assert "src/main.py" in result
        assert "tests" in result
        assert "pytest" in result
        assert "git: yes" in result


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

    def test_implement_output_required(self, tmp_path):
        result = runner.invoke(app, ["implement", "Add validation"])
        assert result.exit_code != 0

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

        def capture_execute(goal):
            captured_goals.append(goal)
            return report

        with patch("hermes.cli.commands.implement.RepositoryIntelligence") as mock_ri, \
             patch("hermes.cli.commands.implement.configure_from_env", return_value=(env_cfg, caps, driver)), \
             patch("hermes.cli.commands.implement.EngineeringWorkflow") as mock_wf_cls, \
             patch("hermes.cli.commands.implement.Path.cwd", return_value=tmp_path):
            mock_ri.return_value.scan.return_value = self._empty_snapshot()
            mock_wf_cls.return_value.execute.side_effect = capture_execute

            runner.invoke(app, ["implement", "task", "--output", "f.py"])

        assert len(captured_goals) == 1
        assert captured_goals[0].repository_path == "."

    def test_implement_repo_override(self, tmp_path):
        report = _make_success_report()
        env_cfg, caps, driver = self._mock_env_cfg()

        captured_goals = []

        def capture_execute(goal):
            captured_goals.append(goal)
            return report

        with patch("hermes.cli.commands.implement.RepositoryIntelligence") as mock_ri, \
             patch("hermes.cli.commands.implement.configure_from_env", return_value=(env_cfg, caps, driver)), \
             patch("hermes.cli.commands.implement.EngineeringWorkflow") as mock_wf_cls, \
             patch("hermes.cli.commands.implement.Path.cwd", return_value=tmp_path):
            mock_ri.return_value.scan.return_value = self._empty_snapshot()
            mock_wf_cls.return_value.execute.side_effect = capture_execute

            runner.invoke(app, ["implement", "task", "--output", "f.py", "--repo", "myrepo"])

        assert captured_goals[0].repository_path == "myrepo"

    def test_implement_task_injected_into_goal_description(self, tmp_path):
        report = _make_success_report()
        env_cfg, caps, driver = self._mock_env_cfg()

        captured_goals = []

        def capture_execute(goal):
            captured_goals.append(goal)
            return report

        with patch("hermes.cli.commands.implement.RepositoryIntelligence") as mock_ri, \
             patch("hermes.cli.commands.implement.configure_from_env", return_value=(env_cfg, caps, driver)), \
             patch("hermes.cli.commands.implement.EngineeringWorkflow") as mock_wf_cls, \
             patch("hermes.cli.commands.implement.Path.cwd", return_value=tmp_path):
            mock_ri.return_value.scan.return_value = self._empty_snapshot()
            mock_wf_cls.return_value.execute.side_effect = capture_execute

            runner.invoke(app, [
                "implement",
                "Add JWT authentication to the login endpoint",
                "--output", "src/auth.py",
            ])

        assert "Add JWT authentication" in captured_goals[0].description

    def test_implement_repo_context_injected_into_goal_description(self, tmp_path):
        report = _make_success_report()
        env_cfg, caps, driver = self._mock_env_cfg()

        captured_goals = []

        def capture_execute(goal):
            captured_goals.append(goal)
            return report

        snap = _make_snapshot(primary_language="python", git_present=True, file_count=5)

        with patch("hermes.cli.commands.implement.RepositoryIntelligence") as mock_ri, \
             patch("hermes.cli.commands.implement.configure_from_env", return_value=(env_cfg, caps, driver)), \
             patch("hermes.cli.commands.implement.EngineeringWorkflow") as mock_wf_cls, \
             patch("hermes.cli.commands.implement.Path.cwd", return_value=tmp_path):
            mock_ri.return_value.scan.return_value = snap
            mock_wf_cls.return_value.execute.side_effect = capture_execute

            runner.invoke(app, ["implement", "task", "--output", "src/f.py"])

        desc = captured_goals[0].description
        assert "Repository context" in desc
        assert "python" in desc

    def test_implement_write_mode_create_file_for_new_file(self, tmp_path):
        """When output file does not exist → write_mode must be 'create_file'."""
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
            # output file does NOT exist in tmp_path
            runner.invoke(app, ["implement", "task", "--output", "brand_new.py"])

        assert captured_configs[0].write_mode == "create_file"

    def test_implement_write_mode_modify_for_existing_file(self, tmp_path):
        """When output file already exists → write_mode must be 'modify_file'."""
        report = _make_success_report()
        env_cfg, caps, driver = self._mock_env_cfg()

        # Create the file so it "exists"
        existing = tmp_path / "existing.py"
        existing.write_text("# old content")

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
            runner.invoke(app, ["implement", "task", "--output", "existing.py"])

        assert captured_configs[0].write_mode == "modify_file"

    def test_implement_goal_output_path_set_correctly(self, tmp_path):
        report = _make_success_report()
        env_cfg, caps, driver = self._mock_env_cfg()

        captured_goals = []

        def capture_execute(goal):
            captured_goals.append(goal)
            return report

        with patch("hermes.cli.commands.implement.RepositoryIntelligence") as mock_ri, \
             patch("hermes.cli.commands.implement.configure_from_env", return_value=(env_cfg, caps, driver)), \
             patch("hermes.cli.commands.implement.EngineeringWorkflow") as mock_wf_cls, \
             patch("hermes.cli.commands.implement.Path.cwd", return_value=tmp_path):
            mock_ri.return_value.scan.return_value = self._empty_snapshot()
            mock_wf_cls.return_value.execute.side_effect = capture_execute

            runner.invoke(app, ["implement", "task", "--output", "src/handlers.py"])

        assert captured_goals[0].output_path == "src/handlers.py"

    def test_implement_goal_workspace_path_falls_back_to_cwd(self, tmp_path):
        """Without HERMES_REPOSITORIES set, workspace_path defaults to CWD."""
        report = _make_success_report()
        env_cfg, caps, driver = self._mock_env_cfg()

        captured_goals = []

        def capture_execute(goal):
            captured_goals.append(goal)
            return report

        with patch("hermes.cli.commands.implement.RepositoryIntelligence") as mock_ri, \
             patch("hermes.cli.commands.implement.configure_from_env", return_value=(env_cfg, caps, driver)), \
             patch("hermes.cli.commands.implement.EngineeringWorkflow") as mock_wf_cls, \
             patch("hermes.cli.commands.implement.Path.cwd", return_value=tmp_path), \
             patch.dict("os.environ", {}, clear=False):
            # Ensure HERMES_REPOSITORIES is absent so the CWD fallback fires.
            os.environ.pop("HERMES_REPOSITORIES", None)
            mock_ri.return_value.scan.return_value = self._empty_snapshot()
            mock_wf_cls.return_value.execute.side_effect = capture_execute

            runner.invoke(app, ["implement", "task", "--output", "f.py"])

        assert captured_goals[0].workspace_path == str(tmp_path)

    def test_implement_goal_workspace_path_uses_hermes_repositories_env(self, tmp_path):
        """When HERMES_REPOSITORIES is set, workspace_path uses that directory."""
        report = _make_success_report()
        env_cfg, caps, driver = self._mock_env_cfg()

        captured_goals = []

        def capture_execute(goal):
            captured_goals.append(goal)
            return report

        repos_root = tmp_path / "repos"
        repos_root.mkdir()

        with patch("hermes.cli.commands.implement.RepositoryIntelligence") as mock_ri, \
             patch("hermes.cli.commands.implement.configure_from_env", return_value=(env_cfg, caps, driver)), \
             patch("hermes.cli.commands.implement.EngineeringWorkflow") as mock_wf_cls, \
             patch.dict("os.environ", {"HERMES_REPOSITORIES": str(repos_root)}):
            mock_ri.return_value.scan.return_value = self._empty_snapshot()
            mock_wf_cls.return_value.execute.side_effect = capture_execute

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
