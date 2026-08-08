"""hermes job run — Founder Runtime entry point.

Every execution:
  1. Resolves the named capability and its SOPs.
  2. Generates a persistent Job ID (JOB-YYYYMMDD-NNN).
  3. Builds workspace context: all org facets + profile + knowledge.
  4. Executes through the Conductor (profile-aware system prompt).
  5. Streams output to stdout.
  6. Persists a completed Job record to workspaces/{workspace}/jobs/.

Architecture:
    hermes job run --workspace W --profile P --capability C
         ↓
    CapabilityRegistry.get(C)              → Capability + sop_refs
    SOPRegistry.get(sop_ref)              → SOP content (the procedure)
         ↓
    generate_job_id(jobs_dir)             → JOB-YYYYMMDD-NNN
    JobStore.save(job, status=running)
         ↓
    ContextEngine.build_conversation(W,P) → Context (12 org facets + profile)
    Conductor.stream_chat_with_context    → stdout (streaming)
         ↓
    JobStore.save(job, status=completed)  → persistent record
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone

import typer

from hermes.conductor import Conductor
from hermes.kernel.capability_registry import CapabilityRegistry
from hermes.kernel.job_id import generate_job_id
from hermes.kernel.job_store import JobStore
from hermes.kernel.profile_loader import ProfileLoader
from hermes.kernel.sop_registry import SOPRegistry
from hermes.kernel.workspace_engine import WorkspaceNotFoundError
from hermes.models.job import Job
from hermes.providers.ollama_provider import ChatMessage, OllamaProvider
from hermes.runtime.context_engine import ContextEngine


def run(
    workspace: str = typer.Option(
        ..., "--workspace", "-w", help="Workspace ID, e.g. 'AVANZIA'"
    ),
    profile: str = typer.Option(
        ..., "--profile", "-p", help="Profile ID, e.g. 'brand-strategist'"
    ),
    capability: str = typer.Option(
        ..., "--capability", "-c",
        help="Namespaced capability ID, e.g. 'brand.identity-review'"
    ),
) -> None:
    """Execute a Workspace job using a named Profile and Capability."""

    # -- 1. Resolve capability -------------------------------------------------
    cap_registry = CapabilityRegistry()
    cap = cap_registry.get(capability)
    if cap is None:
        typer.echo(f"Error: capability '{capability}' not found.", err=True)
        raise typer.Exit(code=1)

    sop_registry = SOPRegistry()
    sops = [sop for ref in cap.sop_refs if (sop := sop_registry.get(ref)) is not None]
    if not sops:
        typer.echo(
            f"Error: no SOPs found for capability '{capability}' "
            f"(refs: {cap.sop_refs or 'none'}).",
            err=True,
        )
        raise typer.Exit(code=1)

    # -- 2. Create Job record immediately -------------------------------------
    job_store = JobStore()
    job_id = generate_job_id(job_store.jobs_dir(workspace))
    now = datetime.now(timezone.utc)

    job = Job(
        id=job_id,
        workspace_id=workspace,
        operation_id=f"cli-{job_id}",
        status="running",
        started_at=now,
        finished_at=now,
        completed_steps=[capability],
        generated_output=None,
        created_at=now,
        updated_at=now,
    )
    job_store.save(job)

    # -- 3. Build context (workspace + profile + knowledge) -------------------
    profile_loader = ProfileLoader()
    try:
        context = ContextEngine(profile_loader=profile_loader).build_conversation(
            workspace_id=workspace,
            profile_id=profile,
            query=capability,
        )
    except WorkspaceNotFoundError as error:
        _fail_job(job_store, job, now)
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error

    # -- 4. Build conductor ---------------------------------------------------
    conductor = Conductor(
        provider=OllamaProvider.from_env(),
        profile_loader=profile_loader,
    )

    # -- 5. Compose instruction from SOP(s) -----------------------------------
    sop_content = "\n\n---\n\n".join(sop.content for sop in sops)
    messages = [
        ChatMessage(
            role="user",
            content=f"Execute the following procedure:\n\n{sop_content}",
        )
    ]

    # -- 6. Header ------------------------------------------------------------
    typer.echo(f"Job:        {job_id}")
    typer.echo(f"Workspace:  {workspace}")
    typer.echo(f"Profile:    {profile}")
    typer.echo(f"Capability: {capability}")
    typer.echo(f"SOP(s):     {', '.join(sop.id for sop in sops)}")
    typer.echo(f"Model:      {conductor._provider._model}")
    typer.echo("")

    # -- 7. Stream and collect output -----------------------------------------
    output_chunks: list[str] = []
    try:
        for chunk in conductor.stream_chat_with_context(messages, context):
            sys.stdout.write(chunk)
            sys.stdout.flush()
            output_chunks.append(chunk)
        sys.stdout.write("\n")
    except Exception as exc:
        _fail_job(job_store, job, now)
        typer.echo(f"\nError: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    # -- 8. Persist completed record ------------------------------------------
    finished = datetime.now(timezone.utc)
    job_store.save(Job(
        id=job_id,
        workspace_id=workspace,
        operation_id=f"cli-{job_id}",
        status="completed",
        started_at=now,
        finished_at=finished,
        completed_steps=[capability],
        generated_output="".join(output_chunks),
        created_at=now,
        updated_at=finished,
    ))

    typer.echo(f"\nJob {job_id} completed.")


def _fail_job(job_store: JobStore, job: Job, started_at: datetime) -> None:
    now = datetime.now(timezone.utc)
    job_store.save(Job(
        id=job.id,
        workspace_id=job.workspace_id,
        operation_id=job.operation_id,
        status="failed",
        started_at=started_at,
        finished_at=now,
        completed_steps=job.completed_steps,
        generated_output=None,
        created_at=job.created_at,
        updated_at=now,
    ))
