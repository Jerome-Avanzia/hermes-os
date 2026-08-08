"""Gateway tests for AT-9 engineering job endpoints.

Coverage:
- POST returns 202 with job_id
- GET returns job by ID
- GET list includes the dispatched job
- GET unknown job_id returns 404
- POST/GET unknown workspace_id returns 404
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from hermes.gateway import app as app_module
from hermes.kernel.engineering_job_runner import EngineeringJobStore
from hermes.models.engineering_job import EngineeringJob


def _make_job(workspace_id: str, task: str, repo: str, store: EngineeringJobStore) -> EngineeringJob:
    job = EngineeringJob(
        job_id=str(uuid.uuid4()),
        workspace_id=workspace_id,
        task=task,
        repo=repo,
        status="pending",
        dispatched_at="2026-08-08T00:00:00+00:00",
        completed_at=None,
        commit_sha=None,
        files_changed=None,
        error=None,
    )
    store.save(job)
    return job


@pytest.fixture
def client(tmp_path, monkeypatch):
    store = EngineeringJobStore(workspaces_root=tmp_path)
    runner = MagicMock()
    runner._store = store
    runner.create.side_effect = lambda ws, task, repo: _make_job(ws, task, repo, store)
    monkeypatch.setattr(app_module._hermes_service, "engineering_job_runner", runner)
    monkeypatch.setattr(app_module._hermes_service, "validate_workspace", lambda ws: None)
    mock_workspace = MagicMock()
    mock_workspace.workspace.path = str(tmp_path)
    monkeypatch.setattr(app_module._workspace_engine, "resolve", lambda ws: mock_workspace)
    return TestClient(app_module.app)


def test_post_returns_202_with_job_id(client):
    with patch("asyncio.create_task"):
        resp = client.post(
            "/v1/workspaces/ws1/engineering/jobs",
            json={"task": "add greeter function", "repo": "greeter"},
        )
    assert resp.status_code == 202
    body = resp.json()
    assert "job_id" in body
    assert body["status"] == "pending"
    assert "dispatched_at" in body


def test_get_returns_job_by_id(tmp_path, monkeypatch):
    store = EngineeringJobStore(workspaces_root=tmp_path)
    runner = MagicMock()
    runner._store = store
    runner.create.side_effect = lambda ws, task, repo: _make_job(ws, task, repo, store)
    monkeypatch.setattr(app_module._hermes_service, "engineering_job_runner", runner)
    monkeypatch.setattr(app_module._hermes_service, "validate_workspace", lambda ws: None)
    mock_workspace = MagicMock()
    mock_workspace.workspace.path = str(tmp_path)
    monkeypatch.setattr(app_module._workspace_engine, "resolve", lambda ws: mock_workspace)
    client = TestClient(app_module.app)

    with patch("asyncio.create_task"):
        post_resp = client.post(
            "/v1/workspaces/ws1/engineering/jobs",
            json={"task": "add greeter function", "repo": "greeter"},
        )
    job_id = post_resp.json()["job_id"]

    get_resp = client.get(f"/v1/workspaces/ws1/engineering/jobs/{job_id}")
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert body["job_id"] == job_id
    assert body["status"] == "pending"


def test_get_list_includes_dispatched_job(tmp_path, monkeypatch):
    store = EngineeringJobStore(workspaces_root=tmp_path)
    runner = MagicMock()
    runner._store = store
    runner.create.side_effect = lambda ws, task, repo: _make_job(ws, task, repo, store)
    monkeypatch.setattr(app_module._hermes_service, "engineering_job_runner", runner)
    monkeypatch.setattr(app_module._hermes_service, "validate_workspace", lambda ws: None)
    mock_workspace = MagicMock()
    mock_workspace.workspace.path = str(tmp_path)
    monkeypatch.setattr(app_module._workspace_engine, "resolve", lambda ws: mock_workspace)
    client = TestClient(app_module.app)

    with patch("asyncio.create_task"):
        post_resp = client.post(
            "/v1/workspaces/ws1/engineering/jobs",
            json={"task": "add greeter function", "repo": "greeter"},
        )
    job_id = post_resp.json()["job_id"]

    list_resp = client.get("/v1/workspaces/ws1/engineering/jobs")
    assert list_resp.status_code == 200
    job_ids = [j["job_id"] for j in list_resp.json()]
    assert job_id in job_ids


def test_get_unknown_job_id_returns_404(client):
    resp = client.get("/v1/workspaces/ws1/engineering/jobs/nonexistent-job-id")
    assert resp.status_code == 404


def test_unknown_workspace_returns_404(tmp_path, monkeypatch):
    """With real validate_workspace (not patched), unknown workspace → 404."""
    store = EngineeringJobStore(workspaces_root=tmp_path)
    runner = MagicMock()
    runner._store = store
    monkeypatch.setattr(app_module._hermes_service, "engineering_job_runner", runner)
    # Do NOT patch validate_workspace — let it raise WorkspaceNotFoundError
    client = TestClient(app_module.app)

    resp = client.post(
        "/v1/workspaces/nonexistent-workspace/engineering/jobs",
        json={"task": "anything", "repo": "repo"},
    )
    assert resp.status_code == 404
