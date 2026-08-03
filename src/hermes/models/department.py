"""Department — organizational ownership unit.

Departments define ownership. Capabilities belong to Departments.
SOPs belong to Capabilities. No execution, no staffing, no permissions.
"""

from __future__ import annotations

from dataclasses import dataclass, field


DEPARTMENT_STATUSES = frozenset({"active", "inactive"})


@dataclass(slots=True)
class Department:
    id: str
    name: str
    description: str = ""
    mission: str = ""
    owner: str | None = None
    status: str = "active"
    tags: list[str] = field(default_factory=list)
