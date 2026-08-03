"""Workflow model — provider-independent automation workflow metadata.

Amendment 1: Contains both ``id`` (stable Hermes identity) and
``provider_id`` (native n8n ID used only for provider communication).

Amendment 2: No business relationship fields — relationships are
resolved exclusively through the Context Graph.
"""

from __future__ import annotations

from dataclasses import dataclass, field


WORKFLOW_STATUSES = frozenset({"active", "inactive", "error", "unknown"})
ATTENTION_STATES = frozenset({"ok", "warning", "critical"})


def compute_attention_state(
    active: bool,
    last_failure: str,
    last_success: str,
) -> str:
    """Return an executive-friendly attention state.

    * ``"critical"`` — active workflow whose most recent execution failed
      (last_failure is set and is more recent than last_success)
    * ``"warning"``  — active workflow with any failure recorded but last
      execution succeeded
    * ``"ok"``       — inactive, no failures, or never executed
    """
    if not active:
        return "ok"
    if not last_failure:
        return "ok"
    # Both timestamps present — compare lexicographically (ISO-8601)
    if last_success and last_success >= last_failure:
        return "warning"
    # last_failure is more recent than last_success (or no success at all)
    return "critical"


@dataclass(slots=True)
class Workflow:
    """Provider-independent representation of an automation workflow.

    Populated by a provider (e.g. N8nProvider) but carries no
    provider-specific fields beyond ``provider_id``.

    Amendment 1: ``id`` is the stable Hermes identity.
                 ``provider_id`` is the native n8n workflow ID.
    Amendment 2: No capability_refs, repository_refs, operation_refs,
                 department_refs, or people_refs. Business relationships
                 are resolved through the Context Graph only.
    """

    id: str                             # stable Hermes ID, e.g. "process-etsy-orders"
    name: str                           # human display name
    provider_id: str = ""               # native n8n ID, e.g. "6Vh7kP..."
    active: bool = False
    tags: list[str] = field(default_factory=list)
    trigger_types: list[str] = field(default_factory=list)
    node_count: int = 0
    execution_count: int = 0
    last_execution: str = ""            # ISO-8601
    last_success: str = ""              # ISO-8601
    last_failure: str = ""              # ISO-8601
    status: str = "unknown"             # "active" | "inactive" | "error" | "unknown"
    attention_state: str = "ok"         # "ok" | "warning" | "critical"
    created_at: str = ""                # ISO-8601
    updated_at: str = ""                # ISO-8601
