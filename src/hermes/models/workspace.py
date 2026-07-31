from dataclasses import dataclass, field

from hermes.models.organization import Organization


@dataclass(slots=True)
class Workspace:
    project_id: str
    path: str
    name: str = ""
    description: str = ""
    mission: str = ""
    profiles: list[str] = field(default_factory=list)
    organization: Organization | None = None
