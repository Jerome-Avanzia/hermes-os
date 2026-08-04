"""NocodbRuntime — translates data provider output into Database/Table models.

Wraps a DataProvider (Amendment 1) and converts raw JSON into
provider-independent Database and Table dataclass instances.

Amendment 2: Generates stable Hermes IDs from names.
             Preserves provider_id for provider communication.
Amendment 3: compute_table_attention derives state from metadata.
Amendment 5: Columns are ColumnSummary instances.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from hermes.models.database import Database, compute_database_health
from hermes.models.table import (
    ColumnSummary,
    Table,
    compute_table_attention,
)
from hermes.runtime.data_provider import DataProvider

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class NocodbHealthStatus:
    """Health status for the NocoDB integration."""

    configured: bool = False
    authenticated: bool = False
    reachable: bool = False
    database_count: int = 0
    table_count: int = 0
    record_count: int = 0
    last_sync: str = ""
    refresh_duration_ms: int = 0


class NocodbRuntime:
    """High-level runtime that produces Database/Table objects from a data provider."""

    def __init__(self, provider: DataProvider) -> None:
        self._provider = provider

    @property
    def configured(self) -> bool:
        return self._provider.configured

    def health(self) -> NocodbHealthStatus:
        """Return health status with sync metadata."""
        start = time.monotonic()
        raw = self._provider.health()
        duration_ms = int((time.monotonic() - start) * 1000)

        status = NocodbHealthStatus(
            configured=raw.get("configured", False),
            authenticated=raw.get("authenticated", False),
            reachable=raw.get("reachable", False),
            database_count=raw.get("database_count", 0),
            last_sync=datetime.now(timezone.utc).isoformat(),
            refresh_duration_ms=duration_ms,
        )

        # Count tables and records across all databases
        if status.reachable and status.authenticated:
            try:
                databases = self.list_databases()
                status.database_count = len(databases)
                status.table_count = sum(db.table_count for db in databases)
                status.record_count = sum(db.record_count for db in databases)
            except Exception:
                pass

        return status

    def list_databases(self) -> list[Database]:
        """Fetch all databases and convert to Database models."""
        if not self.configured:
            return []
        raw_bases = self._provider.list_bases()
        result: list[Database] = []
        for raw in raw_bases:
            db = self._base_to_database(raw)
            result.append(db)
        return result

    def get_database(self, database_id: str) -> Database | None:
        """Fetch a single database by Hermes ID."""
        if not self.configured:
            return None
        for db in self.list_databases():
            if db.id == database_id:
                return db
        return None

    def list_tables(self) -> list[Table]:
        """Fetch all tables across all databases."""
        if not self.configured:
            return []
        result: list[Table] = []
        for db in self.list_databases():
            tables = self.list_tables_for_database(db)
            result.extend(tables)
        return result

    def list_tables_for_database(self, db: Database) -> list[Table]:
        """Fetch tables for a specific database."""
        if not self.configured:
            return []
        raw_tables = self._provider.list_tables(db.provider_id)
        result: list[Table] = []
        for raw in raw_tables:
            tbl = self._raw_to_table(raw, db)
            result.append(tbl)
        return result

    def get_table(self, table_id: str) -> Table | None:
        """Fetch a single table by Hermes ID."""
        if not self.configured:
            return None
        for tbl in self.list_tables():
            if tbl.id == table_id:
                return tbl
        return None

    def _base_to_database(self, raw: dict) -> Database:
        """Convert a raw provider base dict into a Database model."""
        provider_id = str(raw.get("id", ""))
        name = raw.get("title", "") or raw.get("name", "")
        hermes_id = _slugify(name) if name else provider_id

        # Fetch tables to get counts
        raw_tables = self._provider.list_tables(provider_id)
        table_count = len(raw_tables) if isinstance(raw_tables, list) else 0
        record_count = 0
        for t in raw_tables:
            meta = t.get("meta", {})
            if isinstance(meta, dict):
                record_count += meta.get("rows", 0)

        health_state = compute_database_health(table_count)

        return Database(
            id=hermes_id,
            name=name,
            provider_id=provider_id,
            provider=self._provider.name,
            table_count=table_count,
            record_count=record_count,
            health_state=health_state,
        )

    def _raw_to_table(self, raw: dict, db: Database) -> Table:
        """Convert a raw provider table dict into a Table model."""
        provider_id = str(raw.get("id", ""))
        name = raw.get("title", "") or raw.get("name", "")
        table_slug = _slugify(name) if name else provider_id
        hermes_id = f"{db.id}--{table_slug}"

        # Columns (Amendment 5: ColumnSummary)
        raw_columns = raw.get("columns", [])
        columns: list[ColumnSummary] = []
        primary_key = ""
        if isinstance(raw_columns, list):
            for col in raw_columns:
                if not isinstance(col, dict):
                    continue
                col_name = col.get("title", "") or col.get("column_name", "")
                col_type = col.get("uidt", "") or col.get("dt", "")
                nullable = bool(col.get("rqd") is not True)
                is_primary = bool(col.get("pv", False) or col.get("pk", False))
                columns.append(ColumnSummary(
                    name=col_name,
                    type=col_type,
                    nullable=nullable,
                    primary=is_primary,
                ))
                if is_primary and not primary_key:
                    primary_key = col_name

        column_count = len(columns)

        # Record count
        meta = raw.get("meta", {})
        record_count = 0
        if isinstance(meta, dict):
            record_count = meta.get("rows", 0)

        # Timestamps
        last_updated = raw.get("updated_at", "") or raw.get("updatedAt", "")

        # Attention state (Amendment 3)
        attention_state = compute_table_attention(record_count, column_count, primary_key)

        return Table(
            id=hermes_id,
            name=name,
            provider_id=provider_id,
            database_id=db.id,
            record_count=record_count,
            column_count=column_count,
            columns=columns,
            primary_key=primary_key,
            last_updated=last_updated,
            attention_state=attention_state,
        )


def _slugify(name: str) -> str:
    """Convert a name to a stable Hermes slug.

    'My Database' → 'my-database'
    """
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug or "unnamed"
