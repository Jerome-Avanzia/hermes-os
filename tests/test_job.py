from datetime import datetime, timezone

import pytest
import yaml

from hermes.kernel.job_id import generate_job_id
from hermes.kernel.job_store import JobNotFoundError, JobStore
from hermes.models import Job


def _make_job(workspace_id="TEST", **kwargs):
    now = datetime.now(timezone.utc)
    defaults = {
        "id": "JOB-20260801-001",
        "workspace_id": workspace_id,
        "operation_id": "OP-20260801-001",
        "status": "completed",
        "started_at": now,
        "finished_at": now,
        "completed_steps": ["Python"],
        "generated_output": "Generated output text",
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(kwargs)
    return Job(**defaults)


# -- Model tests -----------------------------------------------------------


def test_job_has_required_fields():
    job = _make_job()
    assert job.id == "JOB-20260801-001"
    assert job.workspace_id == "TEST"
    assert job.operation_id == "OP-20260801-001"
    assert job.status == "completed"
    assert isinstance(job.started_at, datetime)
    assert isinstance(job.finished_at, datetime)
    assert job.completed_steps == ["Python"]
    assert job.generated_output == "Generated output text"
    assert isinstance(job.created_at, datetime)
    assert isinstance(job.updated_at, datetime)


def test_job_default_extra_fields_is_empty():
    job = _make_job()
    assert job.extra_fields == {}


def test_job_generated_output_can_be_none():
    job = _make_job(generated_output=None)
    assert job.generated_output is None


# -- ID generator tests ----------------------------------------------------


def test_generate_job_id_first_of_day(tmp_path):
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    job_id = generate_job_id(jobs_dir)
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    assert job_id == f"JOB-{today}-001"


def test_generate_job_id_increments_sequence(tmp_path):
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    (jobs_dir / f"JOB-{today}-001.yaml").touch()
    (jobs_dir / f"JOB-{today}-002.yaml").touch()
    job_id = generate_job_id(jobs_dir)
    assert job_id == f"JOB-{today}-003"


def test_generate_job_id_nonexistent_directory(tmp_path):
    jobs_dir = tmp_path / "does-not-exist"
    job_id = generate_job_id(jobs_dir)
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    assert job_id == f"JOB-{today}-001"


def test_generate_job_id_ignores_non_yaml(tmp_path):
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    (jobs_dir / f"JOB-{today}-001.txt").touch()
    job_id = generate_job_id(jobs_dir)
    assert job_id == f"JOB-{today}-001"


# -- Store tests -----------------------------------------------------------


def test_save_creates_yaml_file(tmp_path):
    store = JobStore(workspaces_root=tmp_path)
    job = _make_job()
    store.save(job)
    path = tmp_path / "TEST" / "jobs" / "JOB-20260801-001.yaml"
    assert path.is_file()


def test_save_creates_jobs_directory(tmp_path):
    store = JobStore(workspaces_root=tmp_path)
    job = _make_job()
    store.save(job)
    assert (tmp_path / "TEST" / "jobs").is_dir()


def test_load_returns_saved_job(tmp_path):
    store = JobStore(workspaces_root=tmp_path)
    job = _make_job()
    store.save(job)
    loaded = store.load("TEST", "JOB-20260801-001")
    assert loaded.id == job.id
    assert loaded.workspace_id == job.workspace_id
    assert loaded.operation_id == job.operation_id
    assert loaded.status == job.status
    assert loaded.completed_steps == job.completed_steps
    assert loaded.generated_output == job.generated_output


def test_load_nonexistent_raises(tmp_path):
    store = JobStore(workspaces_root=tmp_path)
    with pytest.raises(JobNotFoundError):
        store.load("TEST", "JOB-99999999-999")


def test_list_returns_all_jobs(tmp_path):
    store = JobStore(workspaces_root=tmp_path)
    job1 = _make_job(id="JOB-20260801-001")
    job2 = _make_job(id="JOB-20260801-002")
    store.save(job1)
    store.save(job2)
    jobs = store.list("TEST")
    assert len(jobs) == 2


def test_list_empty_workspace_returns_empty(tmp_path):
    store = JobStore(workspaces_root=tmp_path)
    assert store.list("NONEXISTENT") == []


def test_list_by_operation_filters_correctly(tmp_path):
    store = JobStore(workspaces_root=tmp_path)
    job1 = _make_job(id="JOB-20260801-001", operation_id="OP-20260801-001")
    job2 = _make_job(id="JOB-20260801-002", operation_id="OP-20260801-002")
    store.save(job1)
    store.save(job2)
    filtered = store.list_by_operation("TEST", "OP-20260801-001")
    assert len(filtered) == 1
    assert filtered[0].operation_id == "OP-20260801-001"


def test_list_by_operation_returns_empty_when_none_match(tmp_path):
    store = JobStore(workspaces_root=tmp_path)
    job = _make_job()
    store.save(job)
    assert store.list_by_operation("TEST", "OP-NONEXISTENT") == []


def test_save_includes_version(tmp_path):
    store = JobStore(workspaces_root=tmp_path)
    job = _make_job()
    store.save(job)
    path = tmp_path / "TEST" / "jobs" / "JOB-20260801-001.yaml"
    with open(path) as f:
        data = yaml.safe_load(f)
    assert data["version"] == 1


def test_save_preserves_unknown_fields(tmp_path):
    store = JobStore(workspaces_root=tmp_path)
    job = _make_job(extra_fields={"future_field": "future_value"})
    store.save(job)
    loaded = store.load("TEST", "JOB-20260801-001")
    assert loaded.extra_fields["future_field"] == "future_value"


def test_round_trip_preserves_unknown_fields_after_modification(tmp_path):
    store = JobStore(workspaces_root=tmp_path)
    now = datetime.now(timezone.utc)
    jobs_dir = tmp_path / "TEST" / "jobs"
    jobs_dir.mkdir(parents=True)
    data = {
        "version": 1,
        "id": "JOB-20260801-001",
        "workspace_id": "TEST",
        "operation_id": "OP-20260801-001",
        "status": "completed",
        "started_at": now.isoformat(),
        "finished_at": now.isoformat(),
        "completed_steps": [],
        "generated_output": None,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "newer_hermes_field": {"nested": True},
    }
    with open(jobs_dir / "JOB-20260801-001.yaml", "w") as f:
        yaml.safe_dump(data, f)

    job = store.load("TEST", "JOB-20260801-001")
    job.status = "failed"
    store.save(job)

    reloaded = store.load("TEST", "JOB-20260801-001")
    assert reloaded.status == "failed"
    assert reloaded.extra_fields["newer_hermes_field"] == {"nested": True}


def test_list_sorted_by_filename(tmp_path):
    store = JobStore(workspaces_root=tmp_path)
    job2 = _make_job(id="JOB-20260801-002")
    job1 = _make_job(id="JOB-20260801-001")
    store.save(job2)
    store.save(job1)
    jobs = store.list("TEST")
    assert jobs[0].id == "JOB-20260801-001"
    assert jobs[1].id == "JOB-20260801-002"


# -- Business acceptance tests ---------------------------------------------


def test_job_round_trip_in_workspace(tmp_path):
    store = JobStore(workspaces_root=tmp_path)
    job_id = generate_job_id(store.jobs_dir("ACME"))
    now = datetime.now(timezone.utc)
    job = Job(
        id=job_id,
        workspace_id="ACME",
        operation_id="OP-20260801-001",
        status="completed",
        started_at=now,
        finished_at=now,
        completed_steps=["context_assembly", "execution"],
        generated_output="Result text",
        created_at=now,
        updated_at=now,
    )
    store.save(job)

    jobs = store.list("ACME")
    assert len(jobs) == 1
    assert jobs[0].id == job_id

    loaded = store.load("ACME", job_id)
    assert loaded.operation_id == "OP-20260801-001"
    assert loaded.completed_steps == ["context_assembly", "execution"]


def test_job_linked_to_operation(tmp_path):
    from hermes.kernel.operation_store import OperationStore
    from hermes.models import Operation

    op_store = OperationStore(workspaces_root=tmp_path)
    job_store = JobStore(workspaces_root=tmp_path)
    now = datetime.now(timezone.utc)

    op = Operation(
        id="OP-20260801-001", workspace_id="ACME", request="Do work",
        status="created", created_at=now, updated_at=now,
    )
    op_store.save(op)

    job = _make_job(workspace_id="ACME", operation_id=op.id)
    job_store.save(job)

    linked = job_store.list_by_operation("ACME", op.id)
    assert len(linked) == 1
    assert linked[0].operation_id == op.id


def test_multiple_jobs_per_operation(tmp_path):
    store = JobStore(workspaces_root=tmp_path)
    job1 = _make_job(id="JOB-20260801-001", operation_id="OP-20260801-001")
    job2 = _make_job(id="JOB-20260801-002", operation_id="OP-20260801-001")
    job3 = _make_job(id="JOB-20260801-003", operation_id="OP-20260801-002")
    store.save(job1)
    store.save(job2)
    store.save(job3)

    linked = store.list_by_operation("TEST", "OP-20260801-001")
    assert len(linked) == 2
