"""HostProvider — reads host-level information from the operating system.

Implements the InfrastructureProvider interface (Amendment 1).
No external dependencies. Always configured.
"""

from __future__ import annotations

import logging
import platform
import time
from typing import Any

from hermes.runtime.infrastructure_provider import InfrastructureProvider, ProviderHealth

logger = logging.getLogger(__name__)


class HostProvider(InfrastructureProvider):
    """Read-only provider for host OS information."""

    @property
    def name(self) -> str:
        return "host"

    @property
    def configured(self) -> bool:
        return True

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider_name=self.name,
            configured=True,
            reachable=True,
            detail={
                "hostname": platform.node(),
                "platform": platform.platform(),
                "python_version": platform.python_version(),
            },
        )

    def collect(self) -> list[dict[str, Any]]:
        """Return a single host-info dict.

        The host provider does not produce Service objects directly;
        the InfrastructureRuntime uses this for the health endpoint only.
        """
        return [{
            "hostname": platform.node(),
            "platform": platform.platform(),
            "boot_time": time.time(),
        }]
