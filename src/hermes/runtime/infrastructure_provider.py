"""InfrastructureProvider — abstract interface for infrastructure data sources.

Amendment 1: All infrastructure providers implement this generic interface.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ProviderHealth:
    """Health status returned by any infrastructure provider."""

    provider_name: str = ""
    configured: bool = False
    reachable: bool = False
    detail: dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.detail is None:
            self.detail = {}


class InfrastructureProvider(abc.ABC):
    """Abstract base class for infrastructure data providers.

    Each provider translates a native API (Docker, Traefik, host OS)
    into a list of raw dicts that the InfrastructureRuntime converts
    into provider-independent Service objects.
    """

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Short provider name, e.g. 'docker', 'traefik', 'host'."""

    @property
    @abc.abstractmethod
    def configured(self) -> bool:
        """Whether this provider has the configuration it needs."""

    @abc.abstractmethod
    def health(self) -> ProviderHealth:
        """Check connectivity and return health status."""

    @abc.abstractmethod
    def collect(self) -> list[dict[str, Any]]:
        """Collect raw data items from the provider.

        Returns a list of dicts. The InfrastructureRuntime is responsible
        for translating these into Service model instances.
        """
