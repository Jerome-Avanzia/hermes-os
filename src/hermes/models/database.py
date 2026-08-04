"""Database model — provider-independent business data source metadata.

Amendment 2: Contains both ``id`` (stable Hermes identity) and
``provider_id`` (native provider ID used only for provider communication).

No business relationship fields — relationships are resolved exclusively
through the Context Graph.
"""

from __future__ import annotations

from dataclasses import dataclass, field


DATABASE_HEALTH_STATES = frozenset({"healthy", "degraded", "unknown"})


def compute_database_health(table_count: int) -> str:
    """Return health state for a database.

    * ``"healthy"``  — has at least one table
    * ``"degraded"`` — zero tables (empty base)
    * ``"unknown"``  — negative count (should not happen)
    """
    if table_count < 0:
        return "unknown"
    if table_count == 0:
        return "degraded"
    return "healthy"


@dataclass(slots=True)
class Database:
    """Provider-independent representation of a business data source.

    Populated by a provider (e.g. NocodbProvider) but carries no
    provider-specific fields beyond ``provider_id``.
    """

    id: str                             # stable Hermes ID (slugified name)
    name: str                           # human display name
    provider_id: str = ""               # native provider ID (e.g. NocoDB base ID)
    provider: str = "nocodb"            # provider name
    table_count: int = 0
    record_count: int = 0               # sum of all table record counts
    health_state: str = "unknown"       # "healthy" | "degraded" | "unknown"
