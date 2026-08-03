"""Service model — provider-independent infrastructure service metadata.

Amendment 5: Includes computed resource_state (normal, elevated, critical)
while preserving raw CPU and memory metrics.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# Resource-state thresholds (CPU percent, memory percent).
_CPU_ELEVATED = 70.0
_CPU_CRITICAL = 90.0
_MEM_ELEVATED = 70.0
_MEM_CRITICAL = 90.0

VALID_RESOURCE_STATES = frozenset({"normal", "elevated", "critical"})


def compute_resource_state(
    cpu_percent: float,
    memory_usage: int,
    memory_limit: int,
) -> str:
    """Return an executive-friendly resource state.

    * ``"critical"`` — CPU ≥ 90 % **or** memory ≥ 90 %
    * ``"elevated"`` — CPU ≥ 70 % **or** memory ≥ 70 %
    * ``"normal"``   — everything below thresholds
    """
    mem_pct = (memory_usage / memory_limit * 100.0) if memory_limit > 0 else 0.0

    if cpu_percent >= _CPU_CRITICAL or mem_pct >= _MEM_CRITICAL:
        return "critical"
    if cpu_percent >= _CPU_ELEVATED or mem_pct >= _MEM_ELEVATED:
        return "elevated"
    return "normal"


@dataclass(slots=True)
class Service:
    """Provider-independent representation of a running infrastructure service.

    Populated by an InfrastructureProvider (e.g. DockerProvider) but carries
    no provider-specific fields.

    Amendment 2: ``id`` comes from the ``hermes.service`` container label.
    Amendment 3: ``repository_ref`` comes from the ``hermes.repository`` label.
    Amendment 5: ``resource_state`` is computed from CPU / memory metrics.
    """

    id: str                             # from hermes.service label
    name: str                           # human display name
    type: str                           # "container" | "system"
    status: str                         # "running" | "stopped" | "restarting" | "paused"
    health: str                         # "healthy" | "unhealthy" | "starting" | "none"
    image: str = ""                     # e.g. "hermes-os"
    image_tag: str = ""                 # e.g. "v1.9.0"
    repository_ref: str = ""            # from hermes.repository label
    container_name: str = ""
    uptime: str = ""                    # human-readable, e.g. "3d 4h"
    started_at: str = ""                # ISO-8601
    cpu_percent: float = 0.0
    memory_usage: int = 0               # bytes
    memory_limit: int = 0               # bytes
    resource_state: str = "normal"      # "normal" | "elevated" | "critical"
    ports: list[str] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)
    urls: list[str] = field(default_factory=list)
