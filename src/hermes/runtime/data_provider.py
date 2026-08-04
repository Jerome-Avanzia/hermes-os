"""DataProvider — abstract interface for business data sources.

Amendment 1: All data providers implement this generic interface.
Future providers (Airtable, Supabase, PostgreSQL, etc.) plug into
the same NocodbRuntime / data runtime without changing Hermes.
"""

from __future__ import annotations

import abc
from typing import Any


class DataProvider(abc.ABC):
    """Abstract base class for business data providers.

    Each provider translates a native API (NocoDB, Airtable, Supabase)
    into lists of raw dicts that the data runtime converts into
    provider-independent Database and Table objects.
    """

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Short provider name, e.g. 'nocodb', 'airtable', 'supabase'."""

    @property
    @abc.abstractmethod
    def configured(self) -> bool:
        """Whether this provider has the configuration it needs."""

    @abc.abstractmethod
    def health(self) -> dict[str, Any]:
        """Check connectivity and return health status as a raw dict."""

    @abc.abstractmethod
    def list_bases(self) -> list[dict[str, Any]]:
        """List all databases/bases from the provider."""

    @abc.abstractmethod
    def get_base(self, base_id: str) -> dict[str, Any] | None:
        """Get a single database/base by its native provider ID."""

    @abc.abstractmethod
    def list_tables(self, base_id: str) -> list[dict[str, Any]]:
        """List all tables for a given database/base."""

    @abc.abstractmethod
    def get_table(self, table_id: str) -> dict[str, Any] | None:
        """Get a single table by its native provider ID."""
