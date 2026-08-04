"""LlmModel model — provider-independent AI model metadata.

Contains ``id`` as the stable Hermes identity (``provider--model-slug``).

Amendment 3: ``ModelCapabilities`` value object replaces individual boolean fields.
Amendment 5: Model IDs are explicitly immutable. Display name changes must
never alter the canonical ``provider--model`` identifier.

No business relationship fields — relationships are resolved exclusively
through the Context Graph.
"""

from __future__ import annotations

from dataclasses import dataclass


MODEL_STATUSES = frozenset({"available", "unavailable", "unknown"})
MODEL_ATTENTION_STATES = frozenset({"ok", "warning", "critical"})


def compute_model_attention(status: str, provider_reachable: bool) -> str:
    """Return attention state derived from observable metadata.

    * ``"critical"`` — provider unreachable
    * ``"warning"``  — model status is ``"unavailable"`` or ``"unknown"``
    * ``"ok"``       — model available and provider healthy
    """
    if not provider_reachable:
        return "critical"
    if status != "available":
        return "warning"
    return "ok"


@dataclass(slots=True)
class ModelCapabilities:
    """Amendment 3: Nested value object for model capability flags.

    Extensible without breaking changes — new capabilities can be added
    with defaults without affecting existing consumers.
    """

    streaming: bool = False
    tools: bool = False
    reasoning: bool = False
    vision: bool = False


@dataclass(slots=True)
class LlmModel:
    """Provider-independent representation of an AI/LLM model.

    Amendment 5: ``id`` is explicitly immutable. It is derived from the
    canonical provider name and model identifier at creation time and must
    never change even if the display name changes.
    """

    id: str                             # immutable: "provider--model-slug" (Amendment 5)
    name: str                           # human display name
    provider_id: str = ""               # Hermes provider ID (e.g. "anthropic")
    provider: str = ""                  # provider display name
    family: str = ""                    # model family (e.g. "claude", "gpt", "llama")
    context_window: int = 0             # context window in tokens
    capabilities: ModelCapabilities = None  # type: ignore[assignment]  # Amendment 3
    status: str = "unknown"             # "available" | "unavailable" | "unknown"
    attention_state: str = "ok"         # "ok" | "warning" | "critical"

    def __post_init__(self) -> None:
        if self.capabilities is None:
            self.capabilities = ModelCapabilities()
