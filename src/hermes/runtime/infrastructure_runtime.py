"""InfrastructureRuntime — orchestrates infrastructure providers into Service models.

Read-only runtime that discovers live infrastructure state through
provider abstractions (Amendment 1).

Amendment 2: Service IDs come from ``hermes.service`` container labels.
Amendment 3: Repository mapping uses ``hermes.repository`` labels only.
Amendment 5: Computes resource_state on each Service.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from hermes.models.service import Service, compute_resource_state
from hermes.runtime.infrastructure_provider import InfrastructureProvider, ProviderHealth

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class InfraHealthStatus:
    """Aggregated health across all infrastructure providers."""

    configured: bool = False
    providers: list[ProviderHealth] = field(default_factory=list)
    last_refresh: str = ""


class InfrastructureRuntime:
    """High-level runtime that produces Service objects from infrastructure providers."""

    def __init__(self, providers: list[InfrastructureProvider]) -> None:
        self._providers = providers

    @property
    def configured(self) -> bool:
        return any(p.configured for p in self._providers)

    def health(self) -> InfraHealthStatus:
        """Return aggregated health status from all providers."""
        provider_statuses = [p.health() for p in self._providers]
        return InfraHealthStatus(
            configured=self.configured,
            providers=provider_statuses,
            last_refresh=datetime.now(timezone.utc).isoformat(),
        )

    def list_services(self) -> list[Service]:
        """Collect data from all providers and merge into Service models."""
        # Collect Docker containers (primary source of services)
        docker_items: list[dict[str, Any]] = []
        traefik_items: list[dict[str, Any]] = []

        for provider in self._providers:
            if not provider.configured:
                continue
            items = provider.collect()
            if provider.name == "docker":
                docker_items.extend(items)
            elif provider.name == "traefik":
                traefik_items.extend(items)
            # host provider data is used for health only

        # Build services from Docker containers
        services: list[Service] = []
        for container in docker_items:
            svc = self._container_to_service(container)
            if svc is not None:
                services.append(svc)

        # Enrich with Traefik routing data
        if traefik_items:
            self._enrich_with_traefik(services, traefik_items)

        return services

    def get_service(self, service_id: str) -> Service | None:
        """Return a single service by ID, or None."""
        for svc in self.list_services():
            if svc.id == service_id:
                return svc
        return None

    @staticmethod
    def _container_to_service(container: dict[str, Any]) -> Service | None:
        """Convert a raw Docker container dict to a Service.

        Amendment 2: Only containers with ``hermes.service`` label are
        converted. The label value becomes the service ID.
        """
        labels = container.get("Labels") or {}
        service_id = labels.get("hermes.service", "")
        if not service_id:
            return None

        # Parse image and tag
        image_full = container.get("Image", "")
        image, image_tag = _parse_image(image_full)

        # Container name (Docker prefixes with /)
        names = container.get("Names") or []
        container_name = names[0].lstrip("/") if names else ""

        # Status
        state = container.get("State", "unknown")
        status = state if state in ("running", "stopped", "restarting", "paused") else "stopped"

        # Health (from Status string or labels)
        health = "none"
        status_str = container.get("Status", "")
        if "(healthy)" in status_str:
            health = "healthy"
        elif "(unhealthy)" in status_str:
            health = "unhealthy"
        elif "(health: starting)" in status_str:
            health = "starting"

        # Uptime
        uptime = _extract_uptime(status_str)

        # Ports
        ports = _format_ports(container.get("Ports") or [])

        # Started at (from Created timestamp)
        created = container.get("Created", 0)
        started_at = ""
        if isinstance(created, (int, float)) and created > 0:
            started_at = datetime.fromtimestamp(created, tz=timezone.utc).isoformat()

        # Amendment 3: repository_ref from hermes.repository label only
        repository_ref = labels.get("hermes.repository", "")

        # Resource metrics (from container stats if available)
        cpu_percent = 0.0
        memory_usage = 0
        memory_limit = 0

        resource_state = compute_resource_state(cpu_percent, memory_usage, memory_limit)

        return Service(
            id=service_id,
            name=labels.get("hermes.name", service_id),
            type="container",
            status=status,
            health=health,
            image=image,
            image_tag=image_tag,
            repository_ref=repository_ref,
            container_name=container_name,
            uptime=uptime,
            started_at=started_at,
            cpu_percent=cpu_percent,
            memory_usage=memory_usage,
            memory_limit=memory_limit,
            resource_state=resource_state,
            ports=ports,
            labels=labels,
        )

    @staticmethod
    def _enrich_with_traefik(
        services: list[Service],
        routers: list[dict[str, Any]],
    ) -> None:
        """Enrich services with URL information from Traefik routers."""
        # Build a map of traefik service name → router rules
        service_urls: dict[str, list[str]] = {}
        for router in routers:
            svc_name = router.get("service", "")
            rule = router.get("rule", "")
            # Extract Host(`...`) patterns from Traefik rules
            hosts = re.findall(r"Host\(`([^`]+)`\)", rule)
            tls = router.get("tls") is not None
            for host in hosts:
                scheme = "https" if tls else "http"
                url = f"{scheme}://{host}"
                service_urls.setdefault(svc_name, []).append(url)

        # Match by service ID or container name
        for svc in services:
            urls: list[str] = []
            # Traefik service names often match container/service names
            for key in (svc.id, svc.container_name, f"{svc.container_name}@docker"):
                if key in service_urls:
                    urls.extend(service_urls[key])
            if urls:
                svc.urls = list(dict.fromkeys(urls))  # deduplicate, preserve order


def _parse_image(image_full: str) -> tuple[str, str]:
    """Split 'registry/image:tag' into (image, tag)."""
    # Remove registry prefix if present
    parts = image_full.split("/")
    name_tag = parts[-1] if parts else image_full

    if ":" in name_tag:
        image, tag = name_tag.rsplit(":", 1)
        # Handle sha256 digests
        if tag.startswith("sha256:"):
            return image, tag[:19]  # truncate digest
        return image, tag
    return name_tag, "latest"


def _extract_uptime(status_str: str) -> str:
    """Extract human-readable uptime from Docker status string.

    e.g. 'Up 3 days' → '3 days', 'Up 2 hours (healthy)' → '2 hours'
    """
    if not status_str.startswith("Up "):
        return ""
    # Remove 'Up ' prefix and any parenthetical suffix
    uptime = status_str[3:]
    paren = uptime.find("(")
    if paren != -1:
        uptime = uptime[:paren].strip()
    return uptime


def _format_ports(ports: list[dict[str, Any]]) -> list[str]:
    """Format Docker port mappings into human-readable strings."""
    result: list[str] = []
    for p in ports:
        private = p.get("PrivatePort", 0)
        public = p.get("PublicPort", 0)
        proto = p.get("Type", "tcp")
        if public:
            result.append(f"{public}:{private}/{proto}")
        else:
            result.append(f"{private}/{proto}")
    return result
