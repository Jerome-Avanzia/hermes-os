"""Table model — provider-independent business data table metadata.

Amendment 2: Contains both ``id`` (stable Hermes identity) and
``provider_id`` (native provider ID used only for provider communication).

Amendment 3: ``compute_table_attention()`` derives state from observable
metadata (empty tables, missing columns, no primary key).

Amendment 5: ``ColumnSummary`` lightweight model instead of plain strings.

No business relationship fields — relationships are resolved exclusively
through the Context Graph.
"""

from __future__ import annotations

from dataclasses import dataclass, field


TABLE_ATTENTION_STATES = frozenset({"ok", "warning", "critical"})


def compute_table_attention(
    record_count: int,
    column_count: int,
    primary_key: str,
) -> str:
    """Return attention state derived from observable metadata.

    Amendment 3:
    * ``"critical"`` — no columns (invalid/broken table structure)
    * ``"warning"``  — empty table (zero records) or no primary key
    * ``"ok"``       — has records, columns, and a primary key
    """
    if column_count == 0:
        return "critical"
    if record_count == 0 or not primary_key:
        return "warning"
    return "ok"


@dataclass(slots=True)
class ColumnSummary:
    """Lightweight column metadata — Amendment 5.

    Read-only summary of a table column. Avoids plain strings
    so the model can carry type/nullable/primary without breaking changes.
    """

    name: str
    type: str = ""                      # e.g. "text", "number", "date"
    nullable: bool = True
    primary: bool = False


@dataclass(slots=True)
class Table:
    """Provider-independent representation of a business data table.

    Populated by a provider (e.g. NocodbProvider) but carries no
    provider-specific fields beyond ``provider_id``.
    """

    id: str                             # stable Hermes ID ("db-slug--table-slug")
    name: str                           # human display name
    provider_id: str = ""               # native provider ID (e.g. NocoDB table ID)
    database_id: str = ""               # Hermes Database ID (parent link)
    record_count: int = 0
    column_count: int = 0
    columns: list[ColumnSummary] = field(default_factory=list)
    primary_key: str = ""               # name of primary key column
    last_updated: str = ""              # ISO-8601
    attention_state: str = "ok"         # "ok" | "warning" | "critical"
