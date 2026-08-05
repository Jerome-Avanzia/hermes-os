"""RoutingDecision — typed contract between Model Router and LLM Runtime.

Sprint 51: The RoutingDecision is the deterministic output of the Model
Router.  It specifies which model should execute a PromptPackage and
provides an ordered fallback list, a deterministic score, and full audit
metadata for every evaluated and rejected model.

The Model Router produces it.  The LLM Runtime consumes it for
execution.  This data structure is the boundary between routing and
inference.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class RoutingPolicy(enum.Enum):
    """Declarative routing policy — pure data, no logic.

    Each value represents a preference for model selection.
    Policies are applied deterministically by the Model Router.
    """

    PREFER_LOCAL = "prefer_local"
    PREFER_CLOUD = "prefer_cloud"
    CLOUD_ONLY = "cloud_only"
    LOCAL_ONLY = "local_only"
    CHEAPEST = "cheapest"
    HIGHEST_QUALITY = "highest_quality"
    BALANCED = "balanced"


class RoutingReason(enum.Enum):
    """Deterministic reason code for routing decisions.

    Each value is a specific, testable condition — never free text.
    Used in SelectedModel.reasons, RejectedModel.reason, and
    RoutingDecision.reasons.
    """

    # ── Primary selection reasons ──────────────────────────────────
    PRIMARY_RECOMMENDATION = "primary_recommendation"  # highest-scored model
    CONTEXT_FITS = "context_fits"                       # prompt fits context window
    CONTEXT_WINDOW_FIT = "context_window_fit"           # synonym (backward compat)
    POLICY_PREFERRED = "policy_preferred"               # matches active policy

    # ── Locality reasons ──────────────────────────────────────────
    LOCALITY_MATCH = "locality_match"                   # locality matches policy
    LOCAL_MODEL_AVAILABLE = "local_model_available"     # local model was selected
    CLOUD_MODEL_AVAILABLE = "cloud_model_available"     # cloud model was selected
    POLICY_PREFER_LOCAL = "policy_prefer_local"         # policy prefers local
    POLICY_PREFER_CLOUD = "policy_prefer_cloud"         # policy prefers cloud

    # ── Optimisation reasons ──────────────────────────────────────
    COST_OPTIMIZED = "cost_optimized"                   # selected for cost
    QUALITY_OPTIMIZED = "quality_optimized"             # selected for quality
    LATENCY_OPTIMIZED = "latency_optimized"             # selected for speed

    # ── Capability reasons ────────────────────────────────────────
    TOOLS_REQUIRED = "tools_required"                   # model selected for tool support
    VISION_REQUIRED = "vision_required"                 # model selected for vision support

    # ── Availability ──────────────────────────────────────────────
    ONLY_AVAILABLE = "only_available"                   # sole surviving candidate

    # ── Fallback ──────────────────────────────────────────────────
    FALLBACK_SELECTED = "fallback_selected"             # ordered fallback position

    # ── Rejection reasons ─────────────────────────────────────────
    CONTEXT_TOO_LARGE = "context_too_large"             # prompt exceeds context window
    POLICY_EXCLUDED = "policy_excluded"                 # excluded by hard policy filter

    # ── Error ─────────────────────────────────────────────────────
    NO_MODELS_AVAILABLE = "no_models_available"         # registry exhausted


class ExecutionCapability(enum.Enum):
    """Abstract execution capability taxonomy.

    Sprint 51 (Amendment 2): Declarative capability labels that the
    Model Router uses to reason about model fit without referencing
    any provider name.  Assigned to ModelEntry.capabilities.

    The router reasons about capabilities — never about providers.
    """

    CODE_GENERATION = "code_generation"         # generates or analyses source code
    LONG_CONTEXT = "long_context"               # handles large context windows (≥ 128k)
    REASONING = "reasoning"                     # multi-step chain-of-thought / planning
    FAST_RESPONSE = "fast_response"             # low-latency inference
    LOW_COST = "low_cost"                       # free or low cost tier
    OFFLINE = "offline"                         # runs without network access
    LOCAL = "local"                             # runs on local infrastructure
    MULTIMODAL = "multimodal"                   # handles image / audio / video input
    STRUCTURED_OUTPUT = "structured_output"     # reliable JSON / schema output


class CostTier(enum.Enum):
    """Model cost classification — declarative, no runtime pricing."""

    FREE = "free"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    PREMIUM = "premium"


class LatencyTier(enum.Enum):
    """Model latency classification — declarative, no runtime measurement."""

    FAST = "fast"
    MEDIUM = "medium"
    SLOW = "slow"


class Locality(enum.Enum):
    """Model deployment locality."""

    LOCAL = "local"
    CLOUD = "cloud"


@dataclass(slots=True)
class ModelEntry:
    """Declarative model registry entry.

    Describes a model's capabilities and metadata without any provider
    logic or API calls.  Extensible by adding entries to the registry.
    Adding a new model requires only adding a new ModelEntry — the
    router implementation never changes (Amendment 5).

    The router reasons about ExecutionCapability values — never about
    provider names or provider-specific attributes (Amendment 2).
    """

    id: str                               # e.g. "anthropic--claude-sonnet-4"
    provider: str                         # e.g. "anthropic", "ollama"
    name: str                             # human display name
    context_window: int                   # context window in tokens
    cost_tier: CostTier = CostTier.MEDIUM
    latency_tier: LatencyTier = LatencyTier.MEDIUM
    locality: Locality = Locality.CLOUD
    supports_tools: bool = False
    supports_vision: bool = False
    supports_streaming: bool = True
    supports_reasoning: bool = False
    available: bool = True
    family: str = ""                      # e.g. "claude", "gpt", "llama"
    capabilities: frozenset[ExecutionCapability] = field(
        default_factory=frozenset,
    )                                     # abstract execution capabilities


@dataclass(slots=True)
class RejectedModel:
    """A model that was evaluated and rejected, with its reason.

    Included in RoutingDecision.rejected_models for full audit trails.
    """

    model_id: str
    provider: str
    name: str
    reason: RoutingReason


@dataclass(slots=True)
class SelectedModel:
    """A model selected by the router with reason codes and deterministic score."""

    model_id: str
    provider: str
    name: str
    context_window: int
    reasons: list[RoutingReason]
    score: float = 0.0
    locality: Locality = Locality.CLOUD


@dataclass(slots=True)
class RoutingDecision:
    """Deterministic routing output from the Model Router.

    Produced by ModelRouter.  Consumed by the LLM Runtime.
    Contains the selected model, ordered fallbacks, deterministic scores,
    and full audit metadata for every evaluated and rejected model
    (Amendment 4).
    """

    selected: SelectedModel | None
    fallbacks: list[SelectedModel]
    policy: RoutingPolicy
    reasons: list[RoutingReason]
    estimated_context_usage: float = 0.0   # 0.0–1.0 ratio of context window
    budget_compatible: bool = True
    recommended_budget: str = ""           # from PromptPackage
    prompt_chars: int = 0                  # from PromptPackage.estimated_chars
    models_evaluated: int = 0             # total available models considered
    models_filtered: int = 0              # models excluded by policy/fit

    # ── Audit metadata (Amendment 4) ──────────────────────────────
    evaluated_model_ids: list[str] = field(default_factory=list)
    rejected_models: list[RejectedModel] = field(default_factory=list)
    selected_score: float = 0.0            # deterministic score of selected model
    fallback_scores: list[float] = field(default_factory=list)

    # ── Convenience properties ────────────────────────────────────

    @property
    def selected_model_id(self) -> str:
        """ID of the selected model, or empty string if none."""
        return self.selected.model_id if self.selected else ""

    @property
    def selected_provider(self) -> str:
        """Provider of the selected model, or empty string if none."""
        return self.selected.provider if self.selected else ""

    @property
    def fallback_model_ids(self) -> list[str]:
        """IDs of fallback models, in order."""
        return [f.model_id for f in self.fallbacks]

    @property
    def rejected_model_ids(self) -> list[str]:
        """IDs of rejected models, for audit."""
        return [r.model_id for r in self.rejected_models]

    @property
    def has_fallbacks(self) -> bool:
        """Whether any fallback models are available."""
        return len(self.fallbacks) > 0

    @property
    def routed(self) -> bool:
        """Whether a model was successfully selected."""
        return self.selected is not None
