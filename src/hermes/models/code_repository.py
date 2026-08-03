"""CodeRepository model — provider-independent code repository metadata."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class CodeRepository:
    """Provider-independent representation of a code repository.

    Populated by a provider (e.g. GitHubProvider) but carries no
    provider-specific fields.  The ``provider`` field records
    which provider sourced the data (e.g. "github").
    """

    id: str                         # slug, e.g. "hermes-os"
    name: str                       # display name
    provider: str                   # "github", "gitlab", etc.
    url: str = ""                   # web URL
    description: str = ""
    default_branch: str = "main"
    language: str = ""
    visibility: str = "private"     # "public" | "private"
    archived: bool = False
    open_issues_count: int = 0
    open_prs_count: int = 0
    last_push_at: str = ""          # ISO-8601 or ""
    topics: list[str] = field(default_factory=list)
