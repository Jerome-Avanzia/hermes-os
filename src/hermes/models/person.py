"""Person model — organizational member with stable identity."""

from __future__ import annotations

from dataclasses import dataclass, field

PERSON_STATUSES = frozenset({"active", "inactive"})


@dataclass(slots=True)
class Person:
    """A real organizational member identified by a stable personal ID.

    IDs represent people, not roles (e.g. ``jerome-cornet``, not ``founder``).
    Roles belong in *title*.  Ownership of departments, capabilities, and
    operations is resolved via ``id`` and ``owner_aliases``.
    """

    id: str
    name: str
    title: str
    department_ids: list[str] = field(default_factory=list)
    email: str | None = None
    status: str = "active"
    responsibilities: list[str] = field(default_factory=list)
    owner_aliases: list[str] = field(default_factory=list)
