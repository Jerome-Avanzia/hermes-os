"""SOP — Standard Operating Procedure reference document.

SOP IDs are filesystem-derived: ``{skill_id}/{filename-stem}``.
If a file is renamed on disk, its ID changes.  This is an explicit
design decision — the filesystem is the source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass


# Valid SOP statuses.
SOP_STATUSES = frozenset({"draft", "active", "deprecated"})

# Descriptive SOP categories.
SOP_CATEGORIES = frozenset({
    "Operational",
    "Technical",
    "Business",
    "Marketing",
    "Sales",
})


@dataclass(slots=True)
class SOP:
    id: str               # e.g. "copywriting/content-review"
    title: str            # extracted from first H1, or filename stem
    skill_id: str         # owning skill id
    filename: str         # e.g. "content-review.md"
    content: str          # raw markdown body
    description: str = "" # first paragraph after H1
    version: str = ""     # from optional YAML frontmatter
    status: str = "active"
    owner: str | None = None      # inherited from skill manifest if not set
    category: str | None = None   # descriptive only
