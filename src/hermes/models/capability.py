from __future__ import annotations

from dataclasses import dataclass, field


# Valid capability statuses.
CAPABILITY_STATUSES = frozenset({"draft", "active", "experimental", "deprecated"})


@dataclass(slots=True)
class Capability:
    id: str
    name: str
    version: str
    provides: list[str]
    keywords: list[str]
    description: str = ""
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    sop_ref: str | None = None       # legacy — prefer sop_refs
    sop_refs: list[str] = field(default_factory=list)
    status: str = "active"
    skill_id: str = ""
    owner: str | None = None
    department_id: str = ""
    depends_on: list[str] = field(default_factory=list)
    repository_refs: list[str] = field(default_factory=list)
    workflow_refs: list[str] = field(default_factory=list)
