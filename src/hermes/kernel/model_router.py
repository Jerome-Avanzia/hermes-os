"""ModelRouter — deterministic model selection from a PromptPackage.

Sprint 51: Selects the most appropriate model for a PromptPackage using a
declarative routing policy and an injected model registry.  All decisions
are algorithmic — no AI reasoning, no API calls, no provider logic.

Sprint 51 (Amendment 1): The default registry is defined in model_registry.py.
  Changing available models requires only updating model_registry.py.
  The routing algorithm here never changes for registry additions.

Sprint 51 (Amendment 2): Scoring includes capability alignment.  The router
  reasons about abstract ExecutionCapability values — never about provider
  names or provider-specific attributes.

The Model Router:
- Does NOT execute inference
- Does NOT call providers
- Does NOT build prompts
- Does NOT inspect environment variables
- Does NOT contact any API
- IS deterministic: same inputs always produce the same RoutingDecision

Architecture:
  PromptPackage → ModelRouter.route() → RoutingDecision
"""

from __future__ import annotations

import logging

from hermes.kernel.model_registry import DEFAULT_REGISTRY
from hermes.models.prompt_package import PromptPackage
from hermes.models.routing_decision import (
    CostTier,
    ExecutionCapability,
    LatencyTier,
    Locality,
    ModelEntry,
    RejectedModel,
    RoutingDecision,
    RoutingPolicy,
    RoutingReason,
    SelectedModel,
)

logger = logging.getLogger(__name__)


# ── Score component weights ──────────────────────────────────────────────
#
# Deterministic routing score:
#   policy_score     (0–40): locality alignment with policy
#   context_score    (0–30): context window efficiency
#   perf_score       (0–20): policy-weighted cost + latency
#   capability_score (0–10): abstract capability alignment with policy
#   ─────────────────────────────────────────────
#   Total            (0–100)
#
# All factors derive from declarative metadata only (Amendment 3).
# Same inputs always produce the same score — no hidden heuristics.

_POLICY_SCORE_MATCH    = 40.0  # locality perfectly aligns with policy
_POLICY_SCORE_NEUTRAL  = 20.0  # policy has no locality preference
_POLICY_SCORE_MISMATCH =  5.0  # locality opposes policy preference

_CONTEXT_SCORE_TIERS = [
    (0.25, 30.0),   # ≤ 25% window usage → 30 pts (very roomy)
    (0.50, 22.0),   # ≤ 50% usage → 22 pts
    (0.75, 15.0),   # ≤ 75% usage → 15 pts
    (1.00,  8.0),   # ≤ 100% usage → 8 pts (fits but tight)
]

# Performance sub-components (cost + latency → max 20 total per policy)
_COST_CHEAPNESS: dict[CostTier, float] = {
    CostTier.FREE:    14.0,
    CostTier.LOW:     11.0,
    CostTier.MEDIUM:   7.0,
    CostTier.HIGH:     3.0,
    CostTier.PREMIUM:  0.0,
}

_COST_QUALITY: dict[CostTier, float] = {
    CostTier.FREE:     0.0,
    CostTier.LOW:      3.0,
    CostTier.MEDIUM:   7.0,
    CostTier.HIGH:    11.0,
    CostTier.PREMIUM: 14.0,
}

_LATENCY_FAST: dict[LatencyTier, float] = {
    LatencyTier.FAST:   6.0,
    LatencyTier.MEDIUM: 3.0,
    LatencyTier.SLOW:   0.0,
}

_LATENCY_QUALITY: dict[LatencyTier, float] = {
    LatencyTier.FAST:   3.0,  # speed is acceptable
    LatencyTier.MEDIUM: 2.0,
    LatencyTier.SLOW:   6.0,  # thorough/careful = higher quality signal
}

# Capability affinity per policy (Amendment 2).
# Maps each policy to the set of abstract capabilities it prefers.
# The router is capability-driven — never provider-driven.
_POLICY_CAPABILITY_AFFINITY: dict[RoutingPolicy, frozenset[ExecutionCapability]] = {
    RoutingPolicy.CHEAPEST:        frozenset({ExecutionCapability.LOW_COST}),
    RoutingPolicy.HIGHEST_QUALITY: frozenset({ExecutionCapability.REASONING}),
    RoutingPolicy.PREFER_LOCAL:    frozenset({ExecutionCapability.LOCAL, ExecutionCapability.OFFLINE}),
    RoutingPolicy.LOCAL_ONLY:      frozenset({ExecutionCapability.LOCAL, ExecutionCapability.OFFLINE}),
    RoutingPolicy.PREFER_CLOUD:    frozenset(),
    RoutingPolicy.CLOUD_ONLY:      frozenset(),
    RoutingPolicy.BALANCED:        frozenset(),
}

_CHARS_PER_TOKEN = 4


# ── Model Router ─────────────────────────────────────────────────────────


class ModelRouter:
    """Deterministic model selection for a PromptPackage.

    Accepts an injected model registry (Amendment 1).  Defaults to
    DEFAULT_REGISTRY from model_registry.py.  The routing algorithm
    never changes when the registry is extended.

    Reasons about abstract ExecutionCapability values — never about
    provider names (Amendment 2).

    Produces a deterministic RoutingDecision with a fully explainable
    score and complete audit metadata.
    """

    def __init__(
        self,
        registry: list[ModelEntry] | None = None,
    ) -> None:
        self._registry = list(registry) if registry is not None else list(DEFAULT_REGISTRY)

    @property
    def registry(self) -> list[ModelEntry]:
        """The model registry (read-only copy)."""
        return list(self._registry)

    def route(
        self,
        package: PromptPackage,
        policy: RoutingPolicy = RoutingPolicy.BALANCED,
        max_fallbacks: int = 3,
    ) -> RoutingDecision:
        """Route a PromptPackage to the best model.

        Algorithm (fully deterministic):
        1. Estimate token requirements from PromptPackage
        2. Identify available models from injected registry
        3. Reject models whose context window is too small
        4. Apply hard policy filters (CLOUD_ONLY / LOCAL_ONLY)
        5. Compute a deterministic score per model (policy + context + perf + capability)
        6. Sort by score descending, then model ID for tie-breaking
        7. Select primary model and ordered fallbacks
        8. Build RoutingDecision with full audit metadata

        Returns a fully populated RoutingDecision — no provider calls.
        """
        estimated_tokens = package.estimated_chars // _CHARS_PER_TOKEN

        # ── 1. Collect available models ───────────────────────────────
        available = [m for m in self._registry if m.available]
        evaluated_ids = [m.id for m in available]
        rejected: list[RejectedModel] = []

        # ── 2. Filter by context window ───────────────────────────────
        fitting: list[ModelEntry] = []
        for m in available:
            if m.context_window >= estimated_tokens:
                fitting.append(m)
            else:
                rejected.append(RejectedModel(
                    model_id=m.id,
                    provider=m.provider,
                    name=m.name,
                    reason=RoutingReason.CONTEXT_TOO_LARGE,
                ))

        # ── 3. Apply hard policy locality filter ──────────────────────
        policy_filtered: list[ModelEntry] = []
        for m in fitting:
            if policy == RoutingPolicy.CLOUD_ONLY and m.locality != Locality.CLOUD:
                rejected.append(RejectedModel(
                    model_id=m.id,
                    provider=m.provider,
                    name=m.name,
                    reason=RoutingReason.POLICY_EXCLUDED,
                ))
            elif policy == RoutingPolicy.LOCAL_ONLY and m.locality != Locality.LOCAL:
                rejected.append(RejectedModel(
                    model_id=m.id,
                    provider=m.provider,
                    name=m.name,
                    reason=RoutingReason.POLICY_EXCLUDED,
                ))
            else:
                policy_filtered.append(m)

        # If hard filter empties the candidates, fall back to all fitting
        if not policy_filtered and fitting:
            policy_filtered = fitting

        models_filtered = len(available) - len(policy_filtered)

        # ── 4. Score and sort ─────────────────────────────────────────
        scored = [
            (self._compute_score(m, policy, estimated_tokens), m)
            for m in policy_filtered
        ]
        # Descending score; model ID breaks ties for full determinism
        scored.sort(key=lambda t: (-t[0], t[1].id))

        # ── 5. No candidates ──────────────────────────────────────────
        if not scored:
            return RoutingDecision(
                selected=None,
                fallbacks=[],
                policy=policy,
                reasons=[RoutingReason.NO_MODELS_AVAILABLE],
                estimated_context_usage=0.0,
                budget_compatible=False,
                recommended_budget=package.recommended_budget,
                prompt_chars=package.estimated_chars,
                models_evaluated=len(available),
                models_filtered=models_filtered,
                evaluated_model_ids=evaluated_ids,
                rejected_models=rejected,
                selected_score=0.0,
                fallback_scores=[],
            )

        # ── 6. Select primary model ───────────────────────────────────
        primary_score, primary = scored[0]
        fallback_entries = scored[1:max_fallbacks + 1]

        context_usage = (
            estimated_tokens / primary.context_window
            if primary.context_window > 0 else 0.0
        )
        budget_compatible = estimated_tokens <= primary.context_window

        primary_reasons = self._compute_reasons(primary, policy, scored)
        selected = SelectedModel(
            model_id=primary.id,
            provider=primary.provider,
            name=primary.name,
            context_window=primary.context_window,
            reasons=primary_reasons,
            score=primary_score,
            locality=primary.locality,
        )

        fallbacks = [
            SelectedModel(
                model_id=m.id,
                provider=m.provider,
                name=m.name,
                context_window=m.context_window,
                reasons=[RoutingReason.FALLBACK_SELECTED],
                score=s,
                locality=m.locality,
            )
            for s, m in fallback_entries
        ]
        fallback_scores = [s for s, _ in fallback_entries]

        decision = RoutingDecision(
            selected=selected,
            fallbacks=fallbacks,
            policy=policy,
            reasons=primary_reasons,
            estimated_context_usage=context_usage,
            budget_compatible=budget_compatible,
            recommended_budget=package.recommended_budget,
            prompt_chars=package.estimated_chars,
            models_evaluated=len(available),
            models_filtered=models_filtered,
            evaluated_model_ids=evaluated_ids,
            rejected_models=rejected,
            selected_score=primary_score,
            fallback_scores=fallback_scores,
        )

        logger.info(
            "Routed: model=%s provider=%s policy=%s score=%.1f "
            "fallbacks=%d context_usage=%.1f%% budget_compatible=%s",
            selected.model_id,
            selected.provider,
            policy.value,
            primary_score,
            len(fallbacks),
            context_usage * 100,
            budget_compatible,
        )

        return decision

    # ── Deterministic scoring ─────────────────────────────────────────
    #
    # Score = policy_score (0–40)
    #       + context_score (0–30)
    #       + perf_score (0–20)
    #       + capability_score (0–10)
    #       = 0–100

    @staticmethod
    def _compute_score(
        model: ModelEntry,
        policy: RoutingPolicy,
        estimated_tokens: int,
    ) -> float:
        """Compute a deterministic routing score (0.0–100.0).

        Four additive components, each from declarative metadata only.
        Same inputs always produce the same score (Amendment 3).
        No hidden heuristics; no AI reasoning; no provider-specific logic.
        """
        # ── Policy alignment (0–40) ───────────────────────────────────
        local_policies = (RoutingPolicy.PREFER_LOCAL, RoutingPolicy.LOCAL_ONLY)
        cloud_policies = (RoutingPolicy.PREFER_CLOUD, RoutingPolicy.CLOUD_ONLY)

        if policy in local_policies:
            policy_score = (
                _POLICY_SCORE_MATCH if model.locality == Locality.LOCAL
                else _POLICY_SCORE_MISMATCH
            )
        elif policy in cloud_policies:
            policy_score = (
                _POLICY_SCORE_MATCH if model.locality == Locality.CLOUD
                else _POLICY_SCORE_MISMATCH
            )
        else:
            policy_score = _POLICY_SCORE_NEUTRAL

        # ── Context fit (0–30) ────────────────────────────────────────
        context_score = 0.0
        if model.context_window > 0:
            usage = (
                estimated_tokens / model.context_window
                if estimated_tokens > 0 else 0.0
            )
            for threshold, pts in _CONTEXT_SCORE_TIERS:
                if usage <= threshold:
                    context_score = pts
                    break

        # ── Performance (0–20): policy-weighted cost + latency ────────
        if policy == RoutingPolicy.CHEAPEST:
            perf_score = (
                _COST_CHEAPNESS[model.cost_tier]
                + _LATENCY_FAST[model.latency_tier]
            )
        elif policy == RoutingPolicy.HIGHEST_QUALITY:
            perf_score = (
                _COST_QUALITY[model.cost_tier]
                + _LATENCY_QUALITY[model.latency_tier]
            )
        else:
            # BALANCED / PREFER_* / *_ONLY: balance quality and speed
            perf_score = (
                _COST_QUALITY[model.cost_tier]
                + _LATENCY_FAST[model.latency_tier]
            )

        # ── Capability alignment (0–10): Amendment 2 ─────────────────
        # Router reasons about abstract capabilities — never providers.
        affinity = _POLICY_CAPABILITY_AFFINITY.get(policy, frozenset())
        if not affinity:
            # Policy has no capability preference → neutral
            capability_score = 5.0
        else:
            matched = affinity & model.capabilities
            if matched == affinity:
                capability_score = 10.0  # all preferred capabilities present
            elif matched:
                capability_score = 5.0   # partial match
            else:
                capability_score = 0.0   # no match

        return round(policy_score + context_score + perf_score + capability_score, 2)

    # ── Reason computation ────────────────────────────────────────────

    @staticmethod
    def _compute_reasons(
        model: ModelEntry,
        policy: RoutingPolicy,
        scored: list[tuple[float, ModelEntry]],
    ) -> list[RoutingReason]:
        """Compute deterministic reason codes for a selected model.

        Uses RoutingReason enum values only — never free text (Amendment 1).
        """
        reasons: list[RoutingReason] = [RoutingReason.PRIMARY_RECOMMENDATION]

        reasons.append(RoutingReason.CONTEXT_FITS)

        if model.locality == Locality.LOCAL:
            reasons.append(RoutingReason.LOCAL_MODEL_AVAILABLE)
        else:
            reasons.append(RoutingReason.CLOUD_MODEL_AVAILABLE)

        if policy in (RoutingPolicy.PREFER_LOCAL, RoutingPolicy.LOCAL_ONLY):
            reasons.append(RoutingReason.POLICY_PREFER_LOCAL)
            if model.locality == Locality.LOCAL:
                reasons.append(RoutingReason.LOCALITY_MATCH)
        elif policy in (RoutingPolicy.PREFER_CLOUD, RoutingPolicy.CLOUD_ONLY):
            reasons.append(RoutingReason.POLICY_PREFER_CLOUD)
            if model.locality == Locality.CLOUD:
                reasons.append(RoutingReason.LOCALITY_MATCH)
        elif policy == RoutingPolicy.CHEAPEST:
            reasons.append(RoutingReason.COST_OPTIMIZED)
        elif policy == RoutingPolicy.HIGHEST_QUALITY:
            reasons.append(RoutingReason.QUALITY_OPTIMIZED)
        else:
            reasons.append(RoutingReason.POLICY_PREFERRED)

        if len(scored) == 1:
            reasons.append(RoutingReason.ONLY_AVAILABLE)

        return reasons
