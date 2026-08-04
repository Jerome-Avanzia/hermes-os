"""LlmProviderInfo model — provider-independent AI provider metadata.

Contains ``id`` as the stable Hermes identity (slugified provider name).
No business relationship fields — relationships are resolved exclusively
through the Context Graph.

Amendment 2: ``priority`` field for configured provider preference.
"""

from __future__ import annotations

from dataclasses import dataclass


PROVIDER_HEALTH_STATES = frozenset({"healthy", "degraded", "unreachable", "unconfigured"})


def compute_provider_health(
    configured: bool,
    authenticated: bool,
    reachable: bool,
) -> str:
    """Return health state for an LLM provider.

    * ``"healthy"``      — configured + authenticated + reachable
    * ``"degraded"``     — configured + reachable but not authenticated
    * ``"unreachable"``  — configured but not reachable
    * ``"unconfigured"`` — not configured
    """
    if not configured:
        return "unconfigured"
    if not reachable:
        return "unreachable"
    if not authenticated:
        return "degraded"
    return "healthy"


@dataclass(slots=True)
class LlmProviderInfo:
    """Provider-independent representation of an AI/LLM provider.

    The ``id`` is the provider's own slug (e.g. ``"ollama"``, ``"openai"``).
    Unlike Database/Table/Workflow, there is no ``provider_id`` because the
    provider IS the identity source.
    """

    id: str                             # stable Hermes ID (e.g. "ollama")
    name: str                           # human display name (e.g. "Ollama")
    provider_type: str = ""             # "local" | "cloud"
    configured: bool = False
    authenticated: bool = False
    reachable: bool = False
    default_model: str = ""             # first/primary model name
    model_count: int = 0
    health_state: str = "unconfigured"  # "healthy" | "degraded" | "unreachable" | "unconfigured"
    priority: int = 0                   # Amendment 2: configured preference (lower = higher priority)
