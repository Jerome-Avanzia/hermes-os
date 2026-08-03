"""N8nRuntime — translates n8n API data into Workflow models.

Wraps N8nProvider and converts raw n8n JSON into provider-independent
Workflow dataclass instances.

Amendment 1: Generates stable Hermes IDs from workflow names.
             Preserves provider_id for provider communication.
Amendment 3: N8nHealthStatus includes last_sync and refresh_duration_ms.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from hermes.models.workflow import Workflow, compute_attention_state
from hermes.runtime.n8n_provider import N8nProvider

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class N8nHealthStatus:
    """Health status for the n8n integration.

    Amendment 3: Includes last_sync and refresh_duration_ms.
    """

    configured: bool = False
    authenticated: bool = False
    reachable: bool = False
    api_version: str = ""
    workflow_count: int = 0
    active_count: int = 0
    failed_recently: int = 0
    last_sync: str = ""
    refresh_duration_ms: int = 0


class N8nRuntime:
    """High-level runtime that produces Workflow objects from n8n."""

    def __init__(self, provider: N8nProvider) -> None:
        self._provider = provider

    @property
    def configured(self) -> bool:
        return self._provider.configured

    def health(self) -> N8nHealthStatus:
        """Return health status with sync metadata (Amendment 3)."""
        start = time.monotonic()
        raw = self._provider.health()
        duration_ms = int((time.monotonic() - start) * 1000)

        status = N8nHealthStatus(
            configured=raw.get("configured", False),
            authenticated=raw.get("authenticated", False),
            reachable=raw.get("reachable", False),
            workflow_count=raw.get("workflow_count", 0),
            active_count=raw.get("active_count", 0),
            last_sync=datetime.now(timezone.utc).isoformat(),
            refresh_duration_ms=duration_ms,
        )

        # Count recently failed workflows
        if status.reachable and status.authenticated:
            try:
                workflows = self._provider.list_workflows()
                failed = 0
                for w in workflows:
                    wf_id = str(w.get("id", ""))
                    execs = self._provider.list_executions(wf_id, limit=1)
                    if execs and not execs[0].get("finished"):
                        continue
                    if execs and execs[0].get("status") == "error":
                        failed += 1
                status.failed_recently = failed
            except Exception:
                pass

        return status

    def list_workflows(self) -> list[Workflow]:
        """Fetch all workflows and convert to Workflow models."""
        if not self.configured:
            return []
        raw_workflows = self._provider.list_workflows()
        result: list[Workflow] = []
        for raw in raw_workflows:
            wf = self._to_model(raw)
            result.append(wf)
        return result

    def get_workflow(self, workflow_id: str) -> Workflow | None:
        """Fetch a single workflow by Hermes ID."""
        if not self.configured:
            return None
        # Must search through all workflows since we map by Hermes ID
        for wf in self.list_workflows():
            if wf.id == workflow_id:
                return wf
        return None

    def _to_model(self, raw: dict) -> Workflow:
        """Convert a raw n8n workflow dict into a Workflow model.

        Amendment 1: Generates stable Hermes ID from workflow name.
        """
        provider_id = str(raw.get("id", ""))
        name = raw.get("name", "")
        hermes_id = _slugify(name) if name else provider_id

        active = bool(raw.get("active", False))

        # Extract tags
        tags: list[str] = []
        raw_tags = raw.get("tags", [])
        if isinstance(raw_tags, list):
            for t in raw_tags:
                if isinstance(t, dict):
                    tags.append(t.get("name", ""))
                elif isinstance(t, str):
                    tags.append(t)

        # Extract trigger types from nodes
        trigger_types = _extract_trigger_types(raw.get("nodes", []))

        # Node count
        nodes = raw.get("nodes", [])
        node_count = len(nodes) if isinstance(nodes, list) else 0

        # Timestamps
        created_at = raw.get("createdAt", "")
        updated_at = raw.get("updatedAt", "")

        # Execution stats — populated from provider if available
        stats = raw.get("statisticsItems", [])
        execution_count = 0
        last_execution = ""
        last_success = ""
        last_failure = ""

        if isinstance(stats, list):
            for stat in stats:
                if stat.get("name") == "production_success":
                    execution_count += stat.get("count", 0)
                elif stat.get("name") == "production_error":
                    execution_count += stat.get("count", 0)

        # Use executionData if available (from single workflow fetch)
        exec_data = raw.get("executionData", {})
        if isinstance(exec_data, dict):
            last_execution = exec_data.get("lastExecution", "")

        # Compute status
        status = "active" if active else "inactive"

        # Compute attention state
        attention_state = compute_attention_state(active, last_failure, last_success)

        return Workflow(
            id=hermes_id,
            name=name,
            provider_id=provider_id,
            active=active,
            tags=tags,
            trigger_types=trigger_types,
            node_count=node_count,
            execution_count=execution_count,
            last_execution=last_execution,
            last_success=last_success,
            last_failure=last_failure,
            status=status,
            attention_state=attention_state,
            created_at=created_at,
            updated_at=updated_at,
        )


def _slugify(name: str) -> str:
    """Convert a workflow name to a stable Hermes slug.

    'Process Etsy Orders' → 'process-etsy-orders'
    """
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug or "unnamed"


def _extract_trigger_types(nodes: list) -> list[str]:
    """Extract trigger type names from n8n node definitions."""
    if not isinstance(nodes, list):
        return []
    trigger_types: list[str] = []
    seen: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_type = node.get("type", "")
        # n8n trigger nodes typically contain "Trigger" in the type
        if "trigger" in node_type.lower() or "webhook" in node_type.lower():
            # Extract a clean trigger type
            trigger = _clean_trigger_type(node_type)
            if trigger and trigger not in seen:
                seen.add(trigger)
                trigger_types.append(trigger)
    return trigger_types


def _clean_trigger_type(node_type: str) -> str:
    """Clean an n8n node type into a human-readable trigger type.

    'n8n-nodes-base.webhookTrigger' → 'webhook'
    'n8n-nodes-base.cronTrigger' → 'cron'
    'n8n-nodes-base.manualTrigger' → 'manual'
    """
    # Extract the last part after the dot
    parts = node_type.split(".")
    name = parts[-1] if parts else node_type
    # Remove 'Trigger' suffix and lowercase
    name = re.sub(r"[Tt]rigger$", "", name).lower()
    return name or "trigger"
