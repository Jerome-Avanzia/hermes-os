"""DockerProvider — reads container state from the Docker Engine API.

Implements the InfrastructureProvider interface (Amendment 1).
Uses urllib over Unix socket or TCP — no ``docker`` SDK dependency.

Amendment 2: Only containers with a ``hermes.service`` label are returned.
Amendment 3: Repository mapping uses ``hermes.repository`` label only.
"""

from __future__ import annotations

import http.client
import json
import logging
import socket
from typing import Any
from urllib.error import URLError
from urllib.parse import urlparse

from hermes.runtime.infrastructure_provider import InfrastructureProvider, ProviderHealth

logger = logging.getLogger(__name__)


class _UnixHTTPConnection(http.client.HTTPConnection):
    """HTTPConnection subclass that connects via a Unix socket."""

    def __init__(self, socket_path: str, timeout: int = 10) -> None:
        super().__init__("localhost", timeout=timeout)
        self._socket_path = socket_path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._socket_path)


class DockerProvider(InfrastructureProvider):
    """Read-only provider for the Docker Engine API."""

    def __init__(self, host: str = "unix:///var/run/docker.sock") -> None:
        self._host = host
        self._parsed = urlparse(host) if host else None

    @property
    def name(self) -> str:
        return "docker"

    @property
    def configured(self) -> bool:
        return bool(self._host)

    def health(self) -> ProviderHealth:
        status = ProviderHealth(provider_name=self.name, configured=self.configured)
        if not self.configured:
            return status
        try:
            data = self._get("/info")
            status.reachable = True
            status.detail = {
                "containers_count": data.get("Containers", 0),
                "images_count": data.get("Images", 0),
                "server_version": data.get("ServerVersion", ""),
            }
        except Exception as exc:
            logger.debug("Docker health check failed: %s", exc)
            status.reachable = False
        return status

    def collect(self) -> list[dict[str, Any]]:
        """Return raw container dicts for containers with a hermes.service label."""
        if not self.configured:
            return []
        try:
            containers = self._get("/containers/json?all=true")
        except Exception as exc:
            logger.warning("Docker collect failed: %s", exc)
            return []

        result: list[dict[str, Any]] = []
        for c in containers:
            labels = c.get("Labels") or {}
            if "hermes.service" not in labels:
                continue
            result.append(c)
        return result

    def _get(self, path: str) -> Any:
        """Make a GET request to the Docker Engine API."""
        parsed = self._parsed
        if not parsed:
            raise URLError("Docker host not configured")

        if parsed.scheme == "unix":
            socket_path = parsed.path
            conn = _UnixHTTPConnection(socket_path)
        elif parsed.scheme in ("http", "tcp"):
            host = parsed.hostname or "localhost"
            port = parsed.port or 2375
            conn = http.client.HTTPConnection(host, port, timeout=10)
        else:
            raise URLError(f"Unsupported Docker host scheme: {parsed.scheme}")

        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            body = resp.read().decode("utf-8")
            if resp.status >= 400:
                raise URLError(f"Docker API error {resp.status}: {body[:200]}")
            return json.loads(body)
        finally:
            conn.close()
