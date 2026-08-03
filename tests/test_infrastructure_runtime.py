"""Tests for Infrastructure Runtime — Sprint 42.

Covers: Service model, InfrastructureProvider interface, DockerProvider,
TraefikProvider, HostProvider, InfrastructureRuntime, Gateway endpoints,
and Context Graph integration.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from hermes.models.service import Service, compute_resource_state, VALID_RESOURCE_STATES
from hermes.runtime.infrastructure_provider import InfrastructureProvider, ProviderHealth
from hermes.runtime.docker_provider import DockerProvider
from hermes.runtime.traefik_provider import TraefikProvider
from hermes.runtime.host_provider import HostProvider
from hermes.runtime.infrastructure_runtime import (
    InfraHealthStatus,
    InfrastructureRuntime,
    _parse_image,
    _extract_uptime,
    _format_ports,
)


# ── Service model tests ─────────────────────────────────────────────────────


def test_service_defaults():
    svc = Service(id="web", name="Web", type="container", status="running", health="healthy")
    assert svc.id == "web"
    assert svc.type == "container"
    assert svc.image == ""
    assert svc.image_tag == ""
    assert svc.repository_ref == ""
    assert svc.container_name == ""
    assert svc.cpu_percent == 0.0
    assert svc.memory_usage == 0
    assert svc.memory_limit == 0
    assert svc.resource_state == "normal"
    assert svc.ports == []
    assert svc.labels == {}
    assert svc.urls == []


def test_service_all_fields():
    svc = Service(
        id="hermes-gateway",
        name="Hermes Gateway",
        type="container",
        status="running",
        health="healthy",
        image="hermes-os",
        image_tag="v1.9.0",
        repository_ref="hermes-os",
        container_name="hermes-gateway",
        uptime="3d 4h",
        started_at="2026-07-31T08:00:00Z",
        cpu_percent=12.5,
        memory_usage=134217728,
        memory_limit=536870912,
        resource_state="normal",
        ports=["8000:8000/tcp"],
        labels={"hermes.service": "hermes-gateway"},
        urls=["https://hermes.avanzia.com"],
    )
    assert svc.image_tag == "v1.9.0"
    assert svc.repository_ref == "hermes-os"
    assert svc.cpu_percent == 12.5
    assert len(svc.ports) == 1
    assert len(svc.urls) == 1


# ── compute_resource_state tests ─────────────────────────────────────────────


def test_resource_state_normal():
    assert compute_resource_state(10.0, 100, 1000) == "normal"


def test_resource_state_elevated_cpu():
    assert compute_resource_state(75.0, 100, 1000) == "elevated"


def test_resource_state_elevated_memory():
    assert compute_resource_state(10.0, 750, 1000) == "elevated"


def test_resource_state_critical_cpu():
    assert compute_resource_state(95.0, 100, 1000) == "critical"


def test_resource_state_critical_memory():
    assert compute_resource_state(10.0, 950, 1000) == "critical"


def test_resource_state_zero_memory_limit():
    assert compute_resource_state(50.0, 100, 0) == "normal"


def test_resource_state_boundary_elevated():
    assert compute_resource_state(70.0, 0, 1000) == "elevated"


def test_resource_state_boundary_critical():
    assert compute_resource_state(90.0, 0, 1000) == "critical"


def test_valid_resource_states():
    assert VALID_RESOURCE_STATES == {"normal", "elevated", "critical"}


# ── InfrastructureProvider interface tests ───────────────────────────────────


def test_provider_is_abstract():
    with pytest.raises(TypeError):
        InfrastructureProvider()


def test_provider_health_defaults():
    health = ProviderHealth(provider_name="test")
    assert not health.configured
    assert not health.reachable
    assert health.detail == {}


def test_provider_health_detail_none_init():
    """ProviderHealth.__post_init__ converts None to {}."""
    health = ProviderHealth(provider_name="test", detail=None)
    assert health.detail == {}


# ── DockerProvider tests ────────────────────────────────────────────────────


def test_docker_configured_with_host():
    provider = DockerProvider(host="unix:///var/run/docker.sock")
    assert provider.configured
    assert provider.name == "docker"


def test_docker_not_configured_empty():
    provider = DockerProvider(host="")
    assert not provider.configured


def test_docker_health_unconfigured():
    provider = DockerProvider(host="")
    health = provider.health()
    assert not health.configured
    assert not health.reachable


def test_docker_health_reachable():
    provider = DockerProvider(host="unix:///var/run/docker.sock")
    mock_data = {"Containers": 5, "Images": 10, "ServerVersion": "24.0.7"}
    with patch.object(provider, "_get", return_value=mock_data):
        health = provider.health()
    assert health.configured
    assert health.reachable
    assert health.detail["containers_count"] == 5
    assert health.detail["server_version"] == "24.0.7"


def test_docker_health_unreachable():
    provider = DockerProvider(host="unix:///var/run/docker.sock")
    with patch.object(provider, "_get", side_effect=ConnectionError("refused")):
        health = provider.health()
    assert health.configured
    assert not health.reachable


def test_docker_collect_unconfigured():
    provider = DockerProvider(host="")
    assert provider.collect() == []


def test_docker_collect_filters_by_label():
    """Only containers with hermes.service label are returned (Amendment 2)."""
    provider = DockerProvider(host="unix:///var/run/docker.sock")
    containers = [
        {"Names": ["/hermes-gw"], "Labels": {"hermes.service": "hermes-gateway"}},
        {"Names": ["/postgres"], "Labels": {}},  # no hermes.service → filtered out
        {"Names": ["/redis"], "Labels": {"some": "label"}},  # no hermes.service → filtered out
    ]
    with patch.object(provider, "_get", return_value=containers):
        result = provider.collect()
    assert len(result) == 1
    assert result[0]["Labels"]["hermes.service"] == "hermes-gateway"


def test_docker_collect_handles_error():
    provider = DockerProvider(host="unix:///var/run/docker.sock")
    with patch.object(provider, "_get", side_effect=ConnectionError("fail")):
        assert provider.collect() == []


# ── TraefikProvider tests ───────────────────────────────────────────────────


def test_traefik_configured():
    provider = TraefikProvider(api_url="http://localhost:8080")
    assert provider.configured
    assert provider.name == "traefik"


def test_traefik_not_configured():
    provider = TraefikProvider(api_url="")
    assert not provider.configured


def test_traefik_health_unconfigured():
    provider = TraefikProvider(api_url="")
    health = provider.health()
    assert not health.configured
    assert not health.reachable


def test_traefik_health_reachable():
    provider = TraefikProvider(api_url="http://localhost:8080")
    mock_data = {"http": {"routers": {"total": 3}, "services": {"total": 2}}}
    with patch.object(provider, "_get", return_value=mock_data):
        health = provider.health()
    assert health.configured
    assert health.reachable
    assert health.detail["routers_count"] == 3


def test_traefik_health_unreachable():
    provider = TraefikProvider(api_url="http://localhost:8080")
    with patch.object(provider, "_get", side_effect=ConnectionError("refused")):
        health = provider.health()
    assert health.configured
    assert not health.reachable


def test_traefik_collect_unconfigured():
    provider = TraefikProvider(api_url="")
    assert provider.collect() == []


def test_traefik_collect_returns_routers():
    provider = TraefikProvider(api_url="http://localhost:8080")
    routers = [{"rule": "Host(`hermes.avanzia.com`)", "service": "hermes-gateway@docker"}]
    with patch.object(provider, "_get", return_value=routers):
        result = provider.collect()
    assert len(result) == 1


def test_traefik_collect_handles_error():
    provider = TraefikProvider(api_url="http://localhost:8080")
    with patch.object(provider, "_get", side_effect=ConnectionError("fail")):
        assert provider.collect() == []


# ── HostProvider tests ──────────────────────────────────────────────────────


def test_host_always_configured():
    provider = HostProvider()
    assert provider.configured
    assert provider.name == "host"


def test_host_health():
    provider = HostProvider()
    health = provider.health()
    assert health.configured
    assert health.reachable
    assert "hostname" in health.detail
    assert "platform" in health.detail


def test_host_collect():
    provider = HostProvider()
    items = provider.collect()
    assert len(items) == 1
    assert "hostname" in items[0]


# ── Helper function tests ───────────────────────────────────────────────────


def test_parse_image_with_tag():
    assert _parse_image("hermes-os:v1.9.0") == ("hermes-os", "v1.9.0")


def test_parse_image_no_tag():
    assert _parse_image("hermes-os") == ("hermes-os", "latest")


def test_parse_image_with_registry():
    assert _parse_image("ghcr.io/avanzia/hermes-os:v2.0") == ("hermes-os", "v2.0")


def test_parse_image_empty():
    assert _parse_image("") == ("", "latest")


def test_extract_uptime_running():
    assert _extract_uptime("Up 3 days") == "3 days"


def test_extract_uptime_with_health():
    assert _extract_uptime("Up 2 hours (healthy)") == "2 hours"


def test_extract_uptime_not_up():
    assert _extract_uptime("Exited (0) 5 minutes ago") == ""


def test_format_ports_with_public():
    ports = [{"PrivatePort": 8000, "PublicPort": 8000, "Type": "tcp"}]
    assert _format_ports(ports) == ["8000:8000/tcp"]


def test_format_ports_private_only():
    ports = [{"PrivatePort": 3306, "PublicPort": 0, "Type": "tcp"}]
    assert _format_ports(ports) == ["3306/tcp"]


def test_format_ports_empty():
    assert _format_ports([]) == []


# ── InfrastructureRuntime tests ──────────────────────────────────────────────


def _mock_docker(containers=None):
    provider = MagicMock(spec=DockerProvider)
    provider.name = "docker"
    provider.configured = True
    provider.collect.return_value = containers or []
    provider.health.return_value = ProviderHealth(
        provider_name="docker", configured=True, reachable=True,
        detail={"containers_count": len(containers or [])},
    )
    return provider


def _mock_traefik(routers=None):
    provider = MagicMock(spec=TraefikProvider)
    provider.name = "traefik"
    provider.configured = bool(routers is not None)
    provider.collect.return_value = routers or []
    provider.health.return_value = ProviderHealth(
        provider_name="traefik", configured=bool(routers is not None), reachable=True,
    )
    return provider


def _mock_host():
    provider = MagicMock(spec=HostProvider)
    provider.name = "host"
    provider.configured = True
    provider.collect.return_value = [{"hostname": "prod-1"}]
    provider.health.return_value = ProviderHealth(
        provider_name="host", configured=True, reachable=True,
        detail={"hostname": "prod-1"},
    )
    return provider


def _sample_container(
    service_id="hermes-gateway",
    name="hermes-gateway",
    image="hermes-os:v1.9.0",
    state="running",
    status="Up 3 days (healthy)",
    repository="hermes-os",
    extra_labels=None,
):
    labels = {"hermes.service": service_id}
    if repository:
        labels["hermes.repository"] = repository
    if extra_labels:
        labels.update(extra_labels)
    return {
        "Names": [f"/{name}"],
        "Image": image,
        "State": state,
        "Status": status,
        "Created": 1722412800,
        "Labels": labels,
        "Ports": [{"PrivatePort": 8000, "PublicPort": 8000, "Type": "tcp"}],
    }


def test_runtime_unconfigured():
    docker = _mock_docker()
    docker.configured = False
    traefik = _mock_traefik()
    traefik.configured = False
    host = MagicMock(spec=HostProvider)
    host.name = "host"
    host.configured = True  # host is always configured
    host.collect.return_value = []
    host.health.return_value = ProviderHealth(provider_name="host", configured=True, reachable=True)

    runtime = InfrastructureRuntime(providers=[docker, traefik, host])
    # configured is True because host is always configured
    assert runtime.configured
    services = runtime.list_services()
    assert services == []


def test_runtime_configured():
    runtime = InfrastructureRuntime(providers=[_mock_docker([]), _mock_host()])
    assert runtime.configured


def test_runtime_list_services():
    container = _sample_container()
    docker = _mock_docker([container])
    runtime = InfrastructureRuntime(providers=[docker, _mock_host()])

    services = runtime.list_services()
    assert len(services) == 1
    svc = services[0]
    assert isinstance(svc, Service)
    assert svc.id == "hermes-gateway"
    assert svc.status == "running"
    assert svc.health == "healthy"
    assert svc.image == "hermes-os"
    assert svc.image_tag == "v1.9.0"
    assert svc.repository_ref == "hermes-os"
    assert svc.uptime == "3 days"
    assert svc.resource_state == "normal"


def test_runtime_get_service():
    container = _sample_container()
    docker = _mock_docker([container])
    runtime = InfrastructureRuntime(providers=[docker, _mock_host()])

    svc = runtime.get_service("hermes-gateway")
    assert svc is not None
    assert svc.id == "hermes-gateway"


def test_runtime_get_service_not_found():
    docker = _mock_docker([_sample_container()])
    runtime = InfrastructureRuntime(providers=[docker, _mock_host()])
    assert runtime.get_service("nonexistent") is None


def test_runtime_health():
    docker = _mock_docker([_sample_container()])
    traefik = _mock_traefik([])
    host = _mock_host()
    runtime = InfrastructureRuntime(providers=[docker, traefik, host])

    health = runtime.health()
    assert isinstance(health, InfraHealthStatus)
    assert health.configured
    assert len(health.providers) == 3
    assert health.last_refresh != ""
    assert health.providers[0].provider_name == "docker"
    assert health.providers[0].reachable


def test_runtime_service_id_from_label():
    """Amendment 2: Service ID must come from hermes.service label."""
    container = _sample_container(service_id="my-custom-id", name="different-container-name")
    docker = _mock_docker([container])
    runtime = InfrastructureRuntime(providers=[docker, _mock_host()])

    services = runtime.list_services()
    assert len(services) == 1
    assert services[0].id == "my-custom-id"
    assert services[0].container_name == "different-container-name"


def test_runtime_no_service_without_label():
    """Amendment 2: Containers without hermes.service label are ignored."""
    container = {
        "Names": ["/postgres"],
        "Image": "postgres:15",
        "State": "running",
        "Status": "Up 5 days",
        "Created": 1722412800,
        "Labels": {},
        "Ports": [],
    }
    # This shouldn't even get to the runtime because DockerProvider filters it,
    # but testing the runtime converter as well.
    svc = InfrastructureRuntime._container_to_service(container)
    assert svc is None


def test_runtime_repository_ref_from_label():
    """Amendment 3: Repository mapping from hermes.repository label only."""
    container = _sample_container(repository="hermes-os")
    docker = _mock_docker([container])
    runtime = InfrastructureRuntime(providers=[docker, _mock_host()])

    svc = runtime.list_services()[0]
    assert svc.repository_ref == "hermes-os"


def test_runtime_no_repository_ref_without_label():
    """Amendment 3: No repository_ref without explicit label."""
    container = _sample_container(repository="")
    docker = _mock_docker([container])
    runtime = InfrastructureRuntime(providers=[docker, _mock_host()])

    svc = runtime.list_services()[0]
    assert svc.repository_ref == ""


def test_runtime_merges_traefik_urls():
    container = _sample_container()
    docker = _mock_docker([container])
    traefik = _mock_traefik([
        {
            "rule": "Host(`hermes.avanzia.com`)",
            "service": "hermes-gateway",
            "tls": {},
        },
    ])
    runtime = InfrastructureRuntime(providers=[docker, traefik, _mock_host()])

    services = runtime.list_services()
    assert len(services) == 1
    assert "https://hermes.avanzia.com" in services[0].urls


def test_runtime_traefik_no_tls():
    container = _sample_container()
    docker = _mock_docker([container])
    traefik = _mock_traefik([
        {
            "rule": "Host(`api.local`)",
            "service": "hermes-gateway",
        },
    ])
    runtime = InfrastructureRuntime(providers=[docker, traefik, _mock_host()])

    services = runtime.list_services()
    assert "http://api.local" in services[0].urls


def test_runtime_unhealthy_container():
    container = _sample_container(
        status="Up 1 hour (unhealthy)",
        state="running",
    )
    docker = _mock_docker([container])
    runtime = InfrastructureRuntime(providers=[docker, _mock_host()])

    svc = runtime.list_services()[0]
    assert svc.health == "unhealthy"
    assert svc.status == "running"


def test_runtime_stopped_container():
    container = _sample_container(
        status="Exited (0) 5 minutes ago",
        state="stopped",
    )
    docker = _mock_docker([container])
    runtime = InfrastructureRuntime(providers=[docker, _mock_host()])

    svc = runtime.list_services()[0]
    assert svc.status == "stopped"
    assert svc.health == "none"
    assert svc.uptime == ""


def test_runtime_resource_state_computed():
    """Amendment 5: resource_state is computed from CPU/memory."""
    svc = Service(
        id="test", name="test", type="container",
        status="running", health="healthy",
        cpu_percent=95.0, memory_usage=900, memory_limit=1000,
        resource_state=compute_resource_state(95.0, 900, 1000),
    )
    assert svc.resource_state == "critical"


def test_runtime_multiple_services():
    containers = [
        _sample_container(service_id="gateway", name="gw", image="gw:v1"),
        _sample_container(service_id="api", name="api", image="api:v2", repository="api-service"),
    ]
    docker = _mock_docker(containers)
    runtime = InfrastructureRuntime(providers=[docker, _mock_host()])

    services = runtime.list_services()
    assert len(services) == 2
    ids = {s.id for s in services}
    assert ids == {"gateway", "api"}


# ── Gateway endpoint tests ──────────────────────────────────────────────────


@pytest.fixture
def test_client():
    """Create a FastAPI test client with mocked infrastructure."""
    from fastapi.testclient import TestClient
    from hermes.gateway.app import app, _hermes_service

    # Mock the infrastructure runtime on the service
    mock_runtime = MagicMock()
    mock_runtime.configured = True
    mock_runtime.list_services.return_value = [
        Service(
            id="hermes-gateway", name="Hermes Gateway",
            type="container", status="running", health="healthy",
            image="hermes-os", image_tag="v1.9.0",
            repository_ref="hermes-os", resource_state="normal",
        ),
    ]
    mock_runtime.get_service.return_value = Service(
        id="hermes-gateway", name="Hermes Gateway",
        type="container", status="running", health="healthy",
        image="hermes-os", image_tag="v1.9.0",
        repository_ref="hermes-os", resource_state="normal",
    )
    mock_runtime.health.return_value = InfraHealthStatus(
        configured=True,
        providers=[
            ProviderHealth(provider_name="docker", configured=True, reachable=True),
        ],
        last_refresh="2026-08-03T12:00:00Z",
    )

    _hermes_service._infrastructure_runtime = mock_runtime

    client = TestClient(app, raise_server_exceptions=False)
    yield client

    # Cleanup
    _hermes_service._infrastructure_runtime = None


def test_get_services_endpoint(test_client):
    resp = test_client.get("/v1/services")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["id"] == "hermes-gateway"
    assert data[0]["resource_state"] == "normal"


def test_get_service_detail_endpoint(test_client):
    resp = test_client.get("/v1/services/hermes-gateway")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "hermes-gateway"
    assert data["health"] == "healthy"


def test_get_service_not_found(test_client):
    from hermes.gateway.app import _hermes_service
    _hermes_service._infrastructure_runtime.get_service.return_value = None

    resp = test_client.get("/v1/services/nonexistent")
    assert resp.status_code == 404
    assert "not found" in resp.json()["error"].lower()


def test_infrastructure_health_endpoint(test_client):
    resp = test_client.get("/health/infrastructure")
    assert resp.status_code == 200
    data = resp.json()
    assert data["configured"] is True
    assert len(data["providers"]) == 1
    assert data["providers"][0]["name"] == "docker"


# ── Context Graph integration tests ─────────────────────────────────────────


def test_service_in_supported_types():
    from hermes.context.context_graph import SUPPORTED_TYPES
    assert "service" in SUPPORTED_TYPES


def test_services_in_all_relation_keys():
    from hermes.context.context_graph import ALL_RELATION_KEYS
    assert "services" in ALL_RELATION_KEYS


def test_context_graph_service_resolver():
    """Service resolves with related repositories and capabilities."""
    from hermes.context.context_graph import ContextGraph, GraphData
    from hermes.models.capability import Capability
    from hermes.models.code_repository import CodeRepository

    # Create minimal registries
    goal_reg = MagicMock()
    goal_reg.list.return_value = []
    people_reg = MagicMock()
    people_reg.list.return_value = []
    dept_reg = MagicMock()
    dept_reg.list.return_value = []
    sop_reg = MagicMock()
    sop_reg.list.return_value = []

    cap = Capability(
        id="web-hosting", name="Web Hosting", version="1.0",
        provides=["hosting"], keywords=["web"],
        repository_refs=["hermes-os"],
    )
    cap_reg = MagicMock()
    cap_reg.list.return_value = [cap]

    graph = ContextGraph(
        goal_registry=goal_reg,
        people_registry=people_reg,
        department_registry=dept_reg,
        capability_registry=cap_reg,
        sop_registry=sop_reg,
    )

    svc = Service(
        id="hermes-gateway", name="Hermes Gateway",
        type="container", status="running", health="healthy",
        repository_ref="hermes-os", resource_state="normal",
    )
    repo = CodeRepository(id="hermes-os", name="avanzia/hermes-os", provider="github")

    data = GraphData(
        workspace_id="test",
        repositories=[repo],
        services=[svc],
    )

    result = graph.resolve("service", "hermes-gateway", data)
    assert result is not None
    assert result["object_type"] == "service"
    assert result["object_id"] == "hermes-gateway"
    assert result["object_summary"]["id"] == "hermes-gateway"
    assert result["object_summary"]["resource_state"] == "normal"
    assert len(result["repositories"]) == 1
    assert result["repositories"][0]["id"] == "hermes-os"
    assert len(result["capabilities"]) == 1
    assert result["capabilities"][0]["id"] == "web-hosting"


def test_context_graph_service_not_found():
    from hermes.context.context_graph import ContextGraph, GraphData

    goal_reg = MagicMock()
    goal_reg.list.return_value = []
    people_reg = MagicMock()
    people_reg.list.return_value = []
    dept_reg = MagicMock()
    dept_reg.list.return_value = []
    cap_reg = MagicMock()
    cap_reg.list.return_value = []
    sop_reg = MagicMock()
    sop_reg.list.return_value = []

    graph = ContextGraph(goal_reg, people_reg, dept_reg, cap_reg, sop_reg)
    data = GraphData(workspace_id="test")
    result = graph.resolve("service", "nonexistent", data)
    assert result is None


def test_context_graph_repository_to_services():
    """Repository includes related services in context."""
    from hermes.context.context_graph import ContextGraph, GraphData
    from hermes.models.code_repository import CodeRepository

    goal_reg = MagicMock()
    goal_reg.list.return_value = []
    people_reg = MagicMock()
    people_reg.list.return_value = []
    dept_reg = MagicMock()
    dept_reg.list.return_value = []
    cap_reg = MagicMock()
    cap_reg.list.return_value = []
    sop_reg = MagicMock()
    sop_reg.list.return_value = []

    graph = ContextGraph(goal_reg, people_reg, dept_reg, cap_reg, sop_reg)

    repo = CodeRepository(id="hermes-os", name="avanzia/hermes-os", provider="github")
    svc = Service(
        id="hermes-gateway", name="Hermes Gateway",
        type="container", status="running", health="healthy",
        repository_ref="hermes-os", resource_state="normal",
    )

    data = GraphData(workspace_id="test", repositories=[repo], services=[svc])
    result = graph.resolve("repository", "hermes-os", data)
    assert result is not None
    assert len(result["services"]) == 1
    assert result["services"][0]["id"] == "hermes-gateway"


def test_context_graph_capability_to_services():
    """Capability includes related services via repository_refs."""
    from hermes.context.context_graph import ContextGraph, GraphData
    from hermes.models.capability import Capability

    goal_reg = MagicMock()
    goal_reg.list.return_value = []
    people_reg = MagicMock()
    people_reg.list.return_value = []
    dept_reg = MagicMock()
    dept_reg.list.return_value = []
    sop_reg = MagicMock()
    sop_reg.list.return_value = []

    cap = Capability(
        id="web-hosting", name="Web Hosting", version="1.0",
        provides=["hosting"], keywords=["web"],
        repository_refs=["hermes-os"],
    )
    cap_reg = MagicMock()
    cap_reg.list.return_value = [cap]

    graph = ContextGraph(goal_reg, people_reg, dept_reg, cap_reg, sop_reg)

    svc = Service(
        id="hermes-gateway", name="Hermes Gateway",
        type="container", status="running", health="healthy",
        repository_ref="hermes-os", resource_state="normal",
    )

    data = GraphData(workspace_id="test", services=[svc])
    result = graph.resolve("capability", "web-hosting", data)
    assert result is not None
    assert len(result["services"]) == 1
    assert result["services"][0]["id"] == "hermes-gateway"
