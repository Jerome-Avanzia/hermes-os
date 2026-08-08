"""EngineeringJobRunner — connects the Workspace REST API to EngineeringCoordinator.

Architecture:
  Workspace REST API
        ↓
  EngineeringJobRunner   (this module)
        ↓
  EngineeringCoordinator (UNCHANGED)
        ↓
  WorkflowExecutionReport (UNCHANGED)
"""

from __future__ import annotations

import dataclasses
import logging
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml

from hermes.adapters.filesystem_adapter import FilesystemAdapter
from hermes.adapters.git_adapter import GitAdapter
from hermes.adapters.llm_adapter import LlmAdapter
from hermes.adapters.validation_adapter import ValidationAdapter
from hermes.kernel.correction_engine import CorrectionEngine
from hermes.kernel.engineering_coordinator import EngineeringCoordinator
from hermes.kernel.engineering_planner import EngineeringPlanner
from hermes.kernel.execution_gateway import ExecutionGateway
from hermes.kernel.operation_engine import OperationEngine
from hermes.kernel.repository_intelligence import RepositoryIntelligence
from hermes.models.engineering_job import EngineeringJob
from hermes.models.engineering_workflow import FounderGoal, WorkflowConfig
from hermes.models.execution_gateway import AdapterRegistration, ExecutionAdapter
from hermes.models.llm_adapter import AdapterConfiguration, LLMProvider
from hermes.providers.ollama_driver import configure_from_env
from hermes.workflows.engineering_workflow import EngineeringWorkflow

logger = logging.getLogger(__name__)


class EngineeringJobNotFoundError(Exception):
    pass


class EngineeringJobStore:
    def __init__(self, workspaces_root: Path = Path("workspaces")) -> None:
        self.workspaces_root = Path(workspaces_root)

    def _jobs_dir(self, workspace_id: str) -> Path:
        return self.workspaces_root / workspace_id / "engineering-jobs"

    def save(self, job: EngineeringJob) -> None:
        j_dir = self._jobs_dir(job.workspace_id)
        j_dir.mkdir(parents=True, exist_ok=True)
        path = j_dir / f"{job.job_id}.yaml"
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(job.to_dict(), f, default_flow_style=False, sort_keys=False)

    def load(self, workspace_id: str, job_id: str) -> EngineeringJob:
        path = self._jobs_dir(workspace_id) / f"{job_id}.yaml"
        if not path.is_file():
            raise EngineeringJobNotFoundError(
                f"Engineering job not found: {job_id} in workspace {workspace_id}"
            )
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return EngineeringJob.from_dict(data)

    def list(self, workspace_id: str) -> list[EngineeringJob]:
        j_dir = self._jobs_dir(workspace_id)
        if not j_dir.is_dir():
            return []
        jobs = []
        for path in sorted(j_dir.iterdir()):
            if path.suffix == ".yaml":
                try:
                    jobs.append(self.load(workspace_id, path.stem))
                except Exception:
                    logger.warning("Failed to load engineering job: %s", path, exc_info=True)
        return jobs


class EngineeringJobRunner:
    def __init__(self, job_store: EngineeringJobStore) -> None:
        self._store = job_store

    def create(self, workspace_id: str, task: str, repo: str) -> EngineeringJob:
        job_id = str(uuid.uuid4())
        job = EngineeringJob(
            job_id=job_id,
            workspace_id=workspace_id,
            task=task,
            repo=repo,
            status="pending",
            dispatched_at=datetime.now(timezone.utc).isoformat(),
            completed_at=None,
            commit_sha=None,
            files_changed=None,
            error=None,
        )
        self._store.save(job)
        return job

    def run(self, workspace_id: str, workspace_path: str, job_id: str) -> None:
        """Blocking execution. Never raises."""
        try:
            job = self._store.load(workspace_id, job_id)
        except Exception as exc:
            logger.error("EngineeringJobRunner: failed to load job %s: %s", job_id, exc)
            return

        job_running = dataclasses.replace(job, status="running")
        self._store.save(job_running)

        final_job: EngineeringJob
        try:
            scan = RepositoryIntelligence(Path(workspace_path)).scan(job.repo)
            env_cfg, capabilities, driver = configure_from_env()

            test_command = (
                scan.build_system.test_command.strip()
                if scan.build_system and scan.build_system.test_command.strip()
                else ""
            )

            config = WorkflowConfig(
                llm_provider=LLMProvider.OLLAMA,
                llm_model=capabilities.default_model,
                llm_base_url=env_cfg.base_url,
                llm_api_key=env_cfg.api_key,
                llm_max_tokens=4096,
                llm_timeout_seconds=120,
                commit_message=f"feat: {job.task[:72]}",
                test_command=test_command,
            )

            goal_id = str(uuid.uuid4())[:8]
            goal = FounderGoal(
                goal_id=goal_id,
                description=job.task,
                workspace_path=workspace_path,
                repository_path=job.repo,
                output_path="",
            )

            gateway = ExecutionGateway()
            gateway.register(AdapterRegistration(
                adapter=ExecutionAdapter.LLM,
                adapter_id="llm-ollama",
                available=True,
                description="Ollama LLM provider (local or cloud)",
            ))
            gateway.register(AdapterRegistration(
                adapter=ExecutionAdapter.FILESYSTEM,
                adapter_id="filesystem-workspace",
                available=True,
                description="Workspace filesystem adapter",
            ))
            gateway.register(AdapterRegistration(
                adapter=ExecutionAdapter.GIT,
                adapter_id="git-workspace",
                available=True,
                description="Workspace git adapter",
            ))
            gateway.register(AdapterRegistration(
                adapter=ExecutionAdapter.VALIDATION,
                adapter_id="validation-workspace",
                available=True,
                description="Workspace validation adapter (syntax gate)",
            ))

            workspace_root = Path(workspace_path)

            llm_adapter = LlmAdapter()
            llm_adapter.register_provider(LLMProvider.OLLAMA, capabilities, driver=driver)

            fs_adapter = FilesystemAdapter(workspace_root=workspace_root)
            git_adapter = GitAdapter(workspace_root=workspace_root)
            validation_adapter = ValidationAdapter(workspace_root=workspace_root)
            op_engine = OperationEngine()

            llm_config = AdapterConfiguration(
                provider=LLMProvider.OLLAMA,
                model=capabilities.default_model,
                base_url=env_cfg.base_url,
                api_key=env_cfg.api_key,
                max_tokens=4096,
                timeout_seconds=120,
                temperature=0.0,
            )

            correction_engine = CorrectionEngine(
                gateway=gateway,
                llm_adapter=llm_adapter,
                filesystem_adapter=fs_adapter,
                git_adapter=git_adapter,
                validation_adapter=validation_adapter,
                operation_engine=op_engine,
                config=config,
            )

            workflow = EngineeringWorkflow(
                gateway=gateway,
                git_adapter=git_adapter,
                operation_engine=op_engine,
                config=config,
                correction_engine=correction_engine,
            )

            planner = EngineeringPlanner(
                gateway=gateway,
                llm_adapter=llm_adapter,
                llm_config=llm_config,
                operation_engine=op_engine,
            )

            coordinator = EngineeringCoordinator(
                planner=planner,
                workflow=workflow,
            )

            report = coordinator.execute(goal)

            if report.success:
                repo_path = Path(workspace_path) / job.repo
                commit_sha = _git_rev_parse_head(repo_path)
                files_changed = _git_diff_tree_files(repo_path)
                final_job = dataclasses.replace(
                    job_running,
                    status="completed",
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    commit_sha=commit_sha,
                    files_changed=tuple(files_changed),
                )
            else:
                final_job = dataclasses.replace(
                    job_running,
                    status="failed",
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    error=report.error or "pipeline_failed",
                )
        except Exception as exc:
            final_job = dataclasses.replace(
                job_running,
                status="failed",
                completed_at=datetime.now(timezone.utc).isoformat(),
                error=str(exc),
            )

        self._store.save(final_job)


def _git_rev_parse_head(repo_path: Path) -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        cwd=repo_path,
    )
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


def _git_diff_tree_files(repo_path: Path) -> list[str]:
    result = subprocess.run(
        ["git", "diff-tree", "--no-commit-id", "-r", "--name-only", "HEAD"],
        capture_output=True,
        text=True,
        cwd=repo_path,
    )
    if result.returncode != 0:
        return []
    return [f for f in result.stdout.strip().splitlines() if f]
