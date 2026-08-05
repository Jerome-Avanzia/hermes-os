"""Tests for Model Router (Sprint 51).

Validates deterministic model selection: same PromptPackage + same policy
+ same registry = same RoutingDecision every time.  All routing logic is
algorithmic — no provider calls, no AI reasoning, no randomness.

Coverage:
- Determinism (same inputs → same output)
- Policy behavior (all 7 policies)
- Routing score determinism (Amendment 3)
- Fallback ordering
- Registry extension without changing router (Amendment 5)
- Context window compatibility (filtering + rejection)
- Capability metadata in registry
- Budget compatibility
- Explanation metadata (Amendment 1 — RoutingReason enum values)
- Rejection reasons (Amendment 4)
- Audit metadata (evaluated_model_ids, rejected_models, scores)
- Edge cases (empty registry, no context, policy exhaustion)
"""

from __future__ import annotations

import pytest

from hermes.kernel.model_registry import DEFAULT_REGISTRY
from hermes.kernel.model_router import (
    ModelRouter,
    _CHARS_PER_TOKEN,
    _POLICY_CAPABILITY_AFFINITY,
)
from hermes.models.prompt_package import (
    OmissionReason,
    PromptPackage,
    PromptSection,
    TruncationReport,
)
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


# ── Helpers ──────────────────────────────────────────────────────────────


def _package(
    chars: int = 1_000,
    budget: str = "8k",
    workspace_id: str = "ws-test",
    query: str = "test query",
) -> PromptPackage:
    """Build a minimal PromptPackage with the given estimated size."""
    section = PromptSection(
        name="header",
        content="x" * chars,
        estimated_chars=chars,
    )
    report = TruncationReport(
        total_sections=1,
        rendered_chars=chars,
        budget_chars=chars,
        utilization=1.0,
    )
    return PromptPackage(
        system_prompt="You are Hermes.",
        sections=[section],
        estimated_chars=chars,
        truncation_report=report,
        query=query,
        workspace_id=workspace_id,
        recommended_budget=budget,
    )


def _minimal_registry(
    *,
    context_window: int = 128_000,
    cost_tier: CostTier = CostTier.MEDIUM,
    latency_tier: LatencyTier = LatencyTier.MEDIUM,
    locality: Locality = Locality.CLOUD,
    supports_tools: bool = True,
    supports_vision: bool = False,
    available: bool = True,
) -> list[ModelEntry]:
    """Single-model registry for isolated tests."""
    return [
        ModelEntry(
            id="test--model-a",
            provider="test",
            name="Test Model A",
            context_window=context_window,
            cost_tier=cost_tier,
            latency_tier=latency_tier,
            locality=locality,
            supports_tools=supports_tools,
            supports_vision=supports_vision,
            available=available,
        )
    ]


def _cloud_local_registry() -> list[ModelEntry]:
    """Two-model registry: one cloud, one local."""
    return [
        ModelEntry(
            id="cloud--alpha",
            provider="cloud",
            name="Cloud Alpha",
            context_window=128_000,
            cost_tier=CostTier.HIGH,
            latency_tier=LatencyTier.MEDIUM,
            locality=Locality.CLOUD,
        ),
        ModelEntry(
            id="local--beta",
            provider="local",
            name="Local Beta",
            context_window=128_000,
            cost_tier=CostTier.FREE,
            latency_tier=LatencyTier.FAST,
            locality=Locality.LOCAL,
        ),
    ]


def _cost_spread_registry() -> list[ModelEntry]:
    """Registry with one model per cost tier (all cloud, all medium latency)."""
    tiers = [CostTier.FREE, CostTier.LOW, CostTier.MEDIUM, CostTier.HIGH, CostTier.PREMIUM]
    return [
        ModelEntry(
            id=f"tier--{t.value}",
            provider="generic",
            name=f"Model {t.value}",
            context_window=128_000,
            cost_tier=t,
            latency_tier=LatencyTier.MEDIUM,
            locality=Locality.CLOUD,
        )
        for t in tiers
    ]


# ── Determinism ───────────────────────────────────────────────────────────


class TestDeterminism:
    """Same inputs must always produce the same RoutingDecision."""

    def test_identical_inputs_produce_identical_decisions(self) -> None:
        router = ModelRouter()
        pkg = _package(chars=4_000)
        d1 = router.route(pkg, RoutingPolicy.BALANCED)
        d2 = router.route(pkg, RoutingPolicy.BALANCED)
        assert d1.selected_model_id == d2.selected_model_id
        assert d1.policy == d2.policy
        assert d1.selected_score == d2.selected_score
        assert d1.fallback_model_ids == d2.fallback_model_ids

    def test_different_policies_may_produce_different_decisions(self) -> None:
        router = ModelRouter(registry=_cloud_local_registry())
        pkg = _package(chars=1_000)
        local_d = router.route(pkg, RoutingPolicy.LOCAL_ONLY)
        cloud_d = router.route(pkg, RoutingPolicy.CLOUD_ONLY)
        assert local_d.selected.locality == Locality.LOCAL
        assert cloud_d.selected.locality == Locality.CLOUD

    def test_repeated_calls_same_fallbacks(self) -> None:
        router = ModelRouter()
        pkg = _package(chars=2_000)
        for _ in range(5):
            d = router.route(pkg, RoutingPolicy.CHEAPEST)
            assert d.fallback_model_ids == router.route(pkg, RoutingPolicy.CHEAPEST).fallback_model_ids

    def test_larger_prompt_may_change_decision(self) -> None:
        router = ModelRouter(registry=[
            ModelEntry(
                id="small--model",
                provider="x",
                name="Small",
                context_window=1_000,
                cost_tier=CostTier.LOW,
                latency_tier=LatencyTier.FAST,
                locality=Locality.CLOUD,
            ),
            ModelEntry(
                id="large--model",
                provider="x",
                name="Large",
                context_window=100_000,
                cost_tier=CostTier.HIGH,
                latency_tier=LatencyTier.SLOW,
                locality=Locality.CLOUD,
            ),
        ])
        small_pkg = _package(chars=100)      # fits both
        large_pkg = _package(chars=10_000)   # only large model fits

        d_small = router.route(small_pkg, RoutingPolicy.CHEAPEST)
        d_large = router.route(large_pkg, RoutingPolicy.CHEAPEST)
        # Large prompt must be routed to large model
        assert d_large.selected_model_id == "large--model"
        # Small prompt should prefer the cheap small model
        assert d_small.selected_model_id == "small--model"


# ── Policy Behavior ───────────────────────────────────────────────────────


class TestPolicies:
    """Each policy must select models according to its declared preference."""

    def test_local_only_selects_local(self) -> None:
        router = ModelRouter(registry=_cloud_local_registry())
        d = router.route(_package(), RoutingPolicy.LOCAL_ONLY)
        assert d.routed
        assert d.selected.locality == Locality.LOCAL

    def test_cloud_only_selects_cloud(self) -> None:
        router = ModelRouter(registry=_cloud_local_registry())
        d = router.route(_package(), RoutingPolicy.CLOUD_ONLY)
        assert d.routed
        assert d.selected.locality == Locality.CLOUD

    def test_prefer_local_prioritises_local(self) -> None:
        router = ModelRouter(registry=_cloud_local_registry())
        d = router.route(_package(), RoutingPolicy.PREFER_LOCAL)
        assert d.routed
        assert d.selected.locality == Locality.LOCAL

    def test_prefer_cloud_prioritises_cloud(self) -> None:
        router = ModelRouter(registry=_cloud_local_registry())
        d = router.route(_package(), RoutingPolicy.PREFER_CLOUD)
        assert d.routed
        assert d.selected.locality == Locality.CLOUD

    def test_cheapest_selects_free_model(self) -> None:
        router = ModelRouter(registry=_cost_spread_registry())
        d = router.route(_package(), RoutingPolicy.CHEAPEST)
        assert d.routed
        assert d.selected.model_id == "tier--free"

    def test_highest_quality_selects_premium_model(self) -> None:
        router = ModelRouter(registry=_cost_spread_registry())
        d = router.route(_package(), RoutingPolicy.HIGHEST_QUALITY)
        assert d.routed
        assert d.selected.model_id == "tier--premium"

    def test_balanced_policy_selects_model(self) -> None:
        router = ModelRouter()
        d = router.route(_package(), RoutingPolicy.BALANCED)
        assert d.routed
        assert d.policy == RoutingPolicy.BALANCED

    def test_local_only_fallback_when_no_local(self) -> None:
        """LOCAL_ONLY with no local models falls back to fitting models."""
        cloud_only_registry = [
            ModelEntry(
                id="cloud--only",
                provider="cloud",
                name="Cloud Only",
                context_window=128_000,
                cost_tier=CostTier.MEDIUM,
                latency_tier=LatencyTier.MEDIUM,
                locality=Locality.CLOUD,
            )
        ]
        router = ModelRouter(registry=cloud_only_registry)
        d = router.route(_package(), RoutingPolicy.LOCAL_ONLY)
        # Should still route (fallback to fitting) not fail
        assert d.routed

    def test_cloud_only_fallback_when_no_cloud(self) -> None:
        """CLOUD_ONLY with no cloud models falls back to fitting models."""
        local_only_registry = [
            ModelEntry(
                id="local--only",
                provider="local",
                name="Local Only",
                context_window=128_000,
                cost_tier=CostTier.FREE,
                latency_tier=LatencyTier.FAST,
                locality=Locality.LOCAL,
            )
        ]
        router = ModelRouter(registry=local_only_registry)
        d = router.route(_package(), RoutingPolicy.CLOUD_ONLY)
        assert d.routed


# ── Scoring (Amendment 3) ─────────────────────────────────────────────────


class TestScoring:
    """Routing score must be deterministic and reflect policy intent."""

    def test_score_is_non_negative(self) -> None:
        router = ModelRouter()
        pkg = _package(chars=1_000)
        for policy in RoutingPolicy:
            d = router.route(pkg, policy)
            if d.routed:
                assert d.selected_score >= 0.0

    def test_score_within_100(self) -> None:
        router = ModelRouter()
        pkg = _package(chars=1_000)
        for policy in RoutingPolicy:
            d = router.route(pkg, policy)
            if d.routed:
                assert d.selected_score <= 100.0

    def test_cheapest_policy_prefers_lower_cost_score(self) -> None:
        registry = _cost_spread_registry()
        router = ModelRouter(registry=registry)
        pkg = _package(chars=100)
        d = router.route(pkg, RoutingPolicy.CHEAPEST)
        # Free model should have highest score under CHEAPEST
        free_score = router._compute_score(
            next(m for m in registry if m.cost_tier == CostTier.FREE),
            RoutingPolicy.CHEAPEST, 100 // _CHARS_PER_TOKEN,
        )
        premium_score = router._compute_score(
            next(m for m in registry if m.cost_tier == CostTier.PREMIUM),
            RoutingPolicy.CHEAPEST, 100 // _CHARS_PER_TOKEN,
        )
        assert free_score > premium_score

    def test_highest_quality_prefers_higher_cost_score(self) -> None:
        registry = _cost_spread_registry()
        router = ModelRouter(registry=registry)
        estimated_tokens = 100 // _CHARS_PER_TOKEN
        premium_score = router._compute_score(
            next(m for m in registry if m.cost_tier == CostTier.PREMIUM),
            RoutingPolicy.HIGHEST_QUALITY, estimated_tokens,
        )
        free_score = router._compute_score(
            next(m for m in registry if m.cost_tier == CostTier.FREE),
            RoutingPolicy.HIGHEST_QUALITY, estimated_tokens,
        )
        assert premium_score > free_score

    def test_local_policy_scores_local_higher(self) -> None:
        registry = _cloud_local_registry()
        router = ModelRouter(registry=registry)
        estimated_tokens = 100 // _CHARS_PER_TOKEN
        local_score = router._compute_score(
            next(m for m in registry if m.locality == Locality.LOCAL),
            RoutingPolicy.PREFER_LOCAL, estimated_tokens,
        )
        cloud_score = router._compute_score(
            next(m for m in registry if m.locality == Locality.CLOUD),
            RoutingPolicy.PREFER_LOCAL, estimated_tokens,
        )
        assert local_score > cloud_score

    def test_cloud_policy_scores_cloud_higher(self) -> None:
        registry = _cloud_local_registry()
        router = ModelRouter(registry=registry)
        estimated_tokens = 100 // _CHARS_PER_TOKEN
        cloud_score = router._compute_score(
            next(m for m in registry if m.locality == Locality.CLOUD),
            RoutingPolicy.PREFER_CLOUD, estimated_tokens,
        )
        local_score = router._compute_score(
            next(m for m in registry if m.locality == Locality.LOCAL),
            RoutingPolicy.PREFER_CLOUD, estimated_tokens,
        )
        assert cloud_score > local_score

    def test_larger_context_window_scores_higher_for_same_prompt(self) -> None:
        small = ModelEntry(
            id="small", provider="x", name="Small",
            context_window=10_000, cost_tier=CostTier.MEDIUM,
            latency_tier=LatencyTier.MEDIUM, locality=Locality.CLOUD,
        )
        large = ModelEntry(
            id="large", provider="x", name="Large",
            context_window=200_000, cost_tier=CostTier.MEDIUM,
            latency_tier=LatencyTier.MEDIUM, locality=Locality.CLOUD,
        )
        router = ModelRouter(registry=[small, large])
        # Prompt uses 50% of small model's window → tighter fit
        estimated_tokens = 5_000
        small_score = router._compute_score(small, RoutingPolicy.BALANCED, estimated_tokens)
        large_score = router._compute_score(large, RoutingPolicy.BALANCED, estimated_tokens)
        # Large window → lower usage ratio → higher context score
        assert large_score > small_score

    def test_same_score_inputs_produce_same_score(self) -> None:
        model = ModelEntry(
            id="m", provider="p", name="M",
            context_window=64_000, cost_tier=CostTier.HIGH,
            latency_tier=LatencyTier.FAST, locality=Locality.CLOUD,
        )
        router = ModelRouter(registry=[model])
        for _ in range(10):
            s = router._compute_score(model, RoutingPolicy.BALANCED, 1_000)
            assert s == router._compute_score(model, RoutingPolicy.BALANCED, 1_000)

    def test_selected_score_matches_fallback_ordering(self) -> None:
        router = ModelRouter()
        pkg = _package(chars=1_000)
        d = router.route(pkg, RoutingPolicy.BALANCED)
        assert d.selected_score >= 0.0
        # Each fallback should have score ≤ selected score
        for fs in d.fallback_scores:
            assert d.selected_score >= fs

    def test_fallback_scores_are_descending(self) -> None:
        router = ModelRouter()
        pkg = _package(chars=1_000)
        d = router.route(pkg, RoutingPolicy.BALANCED, max_fallbacks=10)
        scores = d.fallback_scores
        for i in range(len(scores) - 1):
            assert scores[i] >= scores[i + 1]


# ── Fallback Ordering ─────────────────────────────────────────────────────


class TestFallbacks:
    """Fallback list must be deterministic and ordered by score."""

    def test_fallbacks_are_produced(self) -> None:
        router = ModelRouter()
        d = router.route(_package(), RoutingPolicy.BALANCED)
        assert d.has_fallbacks

    def test_fallback_count_respects_max(self) -> None:
        router = ModelRouter()
        d = router.route(_package(), RoutingPolicy.BALANCED, max_fallbacks=2)
        assert len(d.fallbacks) <= 2

    def test_fallback_is_not_primary(self) -> None:
        router = ModelRouter()
        d = router.route(_package(), RoutingPolicy.BALANCED)
        for fb in d.fallbacks:
            assert fb.model_id != d.selected_model_id

    def test_fallback_model_ids_match_list(self) -> None:
        router = ModelRouter()
        d = router.route(_package(), RoutingPolicy.BALANCED)
        assert d.fallback_model_ids == [f.model_id for f in d.fallbacks]

    def test_fallbacks_have_fallback_reason(self) -> None:
        router = ModelRouter()
        d = router.route(_package(), RoutingPolicy.BALANCED)
        for fb in d.fallbacks:
            assert RoutingReason.FALLBACK_SELECTED in fb.reasons

    def test_zero_max_fallbacks_produces_no_fallbacks(self) -> None:
        router = ModelRouter()
        d = router.route(_package(), RoutingPolicy.BALANCED, max_fallbacks=0)
        assert not d.has_fallbacks

    def test_same_fallback_order_on_repeated_calls(self) -> None:
        router = ModelRouter()
        pkg = _package(chars=2_000)
        orders = [
            router.route(pkg, RoutingPolicy.CHEAPEST).fallback_model_ids
            for _ in range(5)
        ]
        for order in orders[1:]:
            assert order == orders[0]


# ── Context Window Compatibility ──────────────────────────────────────────


class TestContextWindowCompatibility:
    """Models that cannot fit the prompt must be rejected."""

    def test_oversized_prompt_rejects_small_model(self) -> None:
        registry = [
            ModelEntry(
                id="tiny", provider="x", name="Tiny",
                context_window=100, cost_tier=CostTier.FREE,
                latency_tier=LatencyTier.FAST, locality=Locality.CLOUD,
            ),
            ModelEntry(
                id="huge", provider="x", name="Huge",
                context_window=1_000_000, cost_tier=CostTier.HIGH,
                latency_tier=LatencyTier.SLOW, locality=Locality.CLOUD,
            ),
        ]
        router = ModelRouter(registry=registry)
        # Prompt far exceeds tiny model's window
        pkg = _package(chars=100_000)
        d = router.route(pkg, RoutingPolicy.BALANCED)
        assert d.routed
        assert d.selected_model_id == "huge"
        rejected_ids = d.rejected_model_ids
        assert "tiny" in rejected_ids

    def test_rejection_reason_for_oversized_prompt(self) -> None:
        registry = [
            ModelEntry(
                id="tiny", provider="x", name="Tiny",
                context_window=10, cost_tier=CostTier.FREE,
                latency_tier=LatencyTier.FAST, locality=Locality.CLOUD,
            ),
            ModelEntry(
                id="huge", provider="x", name="Huge",
                context_window=1_000_000, cost_tier=CostTier.HIGH,
                latency_tier=LatencyTier.SLOW, locality=Locality.CLOUD,
            ),
        ]
        router = ModelRouter(registry=registry)
        pkg = _package(chars=10_000)
        d = router.route(pkg, RoutingPolicy.BALANCED)
        tiny_rejection = next(
            (r for r in d.rejected_models if r.model_id == "tiny"), None
        )
        assert tiny_rejection is not None
        assert tiny_rejection.reason == RoutingReason.CONTEXT_TOO_LARGE

    def test_context_usage_ratio_is_correct(self) -> None:
        model = ModelEntry(
            id="m", provider="p", name="M",
            context_window=100_000, cost_tier=CostTier.MEDIUM,
            latency_tier=LatencyTier.MEDIUM, locality=Locality.CLOUD,
        )
        router = ModelRouter(registry=[model])
        # 40_000 chars / 4 = 10_000 tokens; 10_000 / 100_000 = 0.10
        pkg = _package(chars=40_000)
        d = router.route(pkg, RoutingPolicy.BALANCED)
        assert abs(d.estimated_context_usage - 0.10) < 0.01

    def test_budget_compatible_true_when_fits(self) -> None:
        router = ModelRouter(registry=_minimal_registry(context_window=128_000))
        d = router.route(_package(chars=1_000), RoutingPolicy.BALANCED)
        assert d.budget_compatible is True

    def test_budget_compatible_when_all_models_reject(self) -> None:
        tiny = ModelEntry(
            id="tiny", provider="x", name="Tiny",
            context_window=1, cost_tier=CostTier.FREE,
            latency_tier=LatencyTier.FAST, locality=Locality.CLOUD,
        )
        router = ModelRouter(registry=[tiny])
        pkg = _package(chars=100_000)
        d = router.route(pkg, RoutingPolicy.BALANCED)
        assert d.budget_compatible is False

    def test_prompt_chars_propagated(self) -> None:
        router = ModelRouter()
        pkg = _package(chars=12_345)
        d = router.route(pkg, RoutingPolicy.BALANCED)
        assert d.prompt_chars == 12_345

    def test_recommended_budget_propagated(self) -> None:
        router = ModelRouter()
        pkg = _package(chars=1_000, budget="16k")
        d = router.route(pkg, RoutingPolicy.BALANCED)
        assert d.recommended_budget == "16k"


# ── Registry Extension (Amendment 5) ─────────────────────────────────────


class TestRegistryExtension:
    """Adding a new model must not require changing router logic."""

    def test_custom_registry_overrides_default(self) -> None:
        custom = [
            ModelEntry(
                id="custom--model-x",
                provider="custom",
                name="Custom X",
                context_window=256_000,
                cost_tier=CostTier.MEDIUM,
                latency_tier=LatencyTier.MEDIUM,
                locality=Locality.CLOUD,
            )
        ]
        router = ModelRouter(registry=custom)
        d = router.route(_package(), RoutingPolicy.BALANCED)
        assert d.selected_model_id == "custom--model-x"
        assert d.selected_provider == "custom"

    def test_adding_model_to_registry_works(self) -> None:
        base_registry = list(DEFAULT_REGISTRY)
        new_model = ModelEntry(
            id="new--provider-model",
            provider="new_provider",
            name="New Provider Model",
            context_window=500_000,
            cost_tier=CostTier.MEDIUM,
            latency_tier=LatencyTier.FAST,
            locality=Locality.CLOUD,
            supports_tools=True,
            supports_vision=True,
            supports_streaming=True,
            supports_reasoning=True,
            family="new",
        )
        extended_registry = base_registry + [new_model]
        router = ModelRouter(registry=extended_registry)
        d = router.route(_package(), RoutingPolicy.BALANCED)
        # Router routes successfully with the new model in registry
        assert d.routed
        assert "new--provider-model" in d.evaluated_model_ids

    def test_router_registry_is_read_only_copy(self) -> None:
        registry = list(DEFAULT_REGISTRY)
        router = ModelRouter(registry=registry)
        # Mutating external list should not affect router
        registry.clear()
        d = router.route(_package(), RoutingPolicy.BALANCED)
        assert d.routed

    def test_router_property_is_copy(self) -> None:
        router = ModelRouter()
        r1 = router.registry
        r2 = router.registry
        assert r1 is not r2
        assert len(r1) == len(r2)

    def test_unavailable_models_excluded(self) -> None:
        registry = [
            ModelEntry(
                id="offline--model",
                provider="x",
                name="Offline",
                context_window=128_000,
                cost_tier=CostTier.LOW,
                latency_tier=LatencyTier.FAST,
                locality=Locality.CLOUD,
                available=False,
            ),
            ModelEntry(
                id="online--model",
                provider="x",
                name="Online",
                context_window=128_000,
                cost_tier=CostTier.HIGH,
                latency_tier=LatencyTier.SLOW,
                locality=Locality.CLOUD,
                available=True,
            ),
        ]
        router = ModelRouter(registry=registry)
        d = router.route(_package(), RoutingPolicy.BALANCED)
        assert d.selected_model_id == "online--model"
        assert "offline--model" not in d.evaluated_model_ids


# ── Audit Metadata (Amendment 4) ─────────────────────────────────────────


class TestAuditMetadata:
    """RoutingDecision must expose full audit trail for every decision."""

    def test_evaluated_model_ids_populated(self) -> None:
        router = ModelRouter()
        d = router.route(_package(), RoutingPolicy.BALANCED)
        assert len(d.evaluated_model_ids) > 0

    def test_evaluated_ids_include_all_available(self) -> None:
        registry = _cloud_local_registry()
        router = ModelRouter(registry=registry)
        d = router.route(_package(), RoutingPolicy.BALANCED)
        for m in registry:
            assert m.id in d.evaluated_model_ids

    def test_rejected_models_populated_when_context_too_large(self) -> None:
        registry = [
            ModelEntry(
                id="tiny", provider="x", name="Tiny",
                context_window=5, cost_tier=CostTier.FREE,
                latency_tier=LatencyTier.FAST, locality=Locality.CLOUD,
            ),
            ModelEntry(
                id="big", provider="x", name="Big",
                context_window=500_000, cost_tier=CostTier.HIGH,
                latency_tier=LatencyTier.SLOW, locality=Locality.CLOUD,
            ),
        ]
        router = ModelRouter(registry=registry)
        d = router.route(_package(chars=5_000), RoutingPolicy.BALANCED)
        assert len(d.rejected_models) >= 1
        assert any(r.model_id == "tiny" for r in d.rejected_models)

    def test_rejected_models_populated_for_policy_exclusion(self) -> None:
        registry = _cloud_local_registry()
        router = ModelRouter(registry=registry)
        d = router.route(_package(), RoutingPolicy.CLOUD_ONLY)
        local_rejection = next(
            (r for r in d.rejected_models if r.model_id == "local--beta"), None
        )
        assert local_rejection is not None
        assert local_rejection.reason == RoutingReason.POLICY_EXCLUDED

    def test_models_evaluated_count_correct(self) -> None:
        registry = _cloud_local_registry()
        router = ModelRouter(registry=registry)
        d = router.route(_package(), RoutingPolicy.BALANCED)
        assert d.models_evaluated == 2

    def test_models_filtered_count_correct(self) -> None:
        registry = _cloud_local_registry()
        router = ModelRouter(registry=registry)
        # CLOUD_ONLY filters out the local model
        d = router.route(_package(), RoutingPolicy.CLOUD_ONLY)
        assert d.models_filtered >= 1

    def test_selected_score_in_decision(self) -> None:
        router = ModelRouter()
        d = router.route(_package(), RoutingPolicy.BALANCED)
        assert isinstance(d.selected_score, float)
        assert d.selected_score > 0.0

    def test_fallback_scores_count_matches_fallbacks(self) -> None:
        router = ModelRouter()
        d = router.route(_package(), RoutingPolicy.BALANCED, max_fallbacks=3)
        assert len(d.fallback_scores) == len(d.fallbacks)

    def test_selected_score_matches_selected_model_score(self) -> None:
        router = ModelRouter()
        d = router.route(_package(), RoutingPolicy.BALANCED)
        assert d.selected_score == d.selected.score

    def test_rejected_model_has_provider_and_name(self) -> None:
        registry = [
            ModelEntry(
                id="x--tiny",
                provider="x_provider",
                name="Tiny X",
                context_window=1,
                cost_tier=CostTier.FREE,
                latency_tier=LatencyTier.FAST,
                locality=Locality.CLOUD,
            ),
            ModelEntry(
                id="y--large",
                provider="y_provider",
                name="Large Y",
                context_window=1_000_000,
                cost_tier=CostTier.HIGH,
                latency_tier=LatencyTier.SLOW,
                locality=Locality.CLOUD,
            ),
        ]
        router = ModelRouter(registry=registry)
        d = router.route(_package(chars=1_000), RoutingPolicy.BALANCED)
        rejection = next(r for r in d.rejected_models if r.model_id == "x--tiny")
        assert rejection.provider == "x_provider"
        assert rejection.name == "Tiny X"


# ── Explanation Metadata / RoutingReason Enum (Amendment 1) ──────────────


class TestExplanationMetadata:
    """RoutingDecision must explain why a model was selected."""

    def test_primary_recommendation_in_reasons(self) -> None:
        router = ModelRouter()
        d = router.route(_package(), RoutingPolicy.BALANCED)
        assert RoutingReason.PRIMARY_RECOMMENDATION in d.reasons

    def test_context_fits_in_reasons(self) -> None:
        router = ModelRouter()
        d = router.route(_package(), RoutingPolicy.BALANCED)
        assert RoutingReason.CONTEXT_FITS in d.reasons

    def test_local_policy_reason_included(self) -> None:
        router = ModelRouter(registry=_cloud_local_registry())
        d = router.route(_package(), RoutingPolicy.PREFER_LOCAL)
        assert RoutingReason.POLICY_PREFER_LOCAL in d.reasons

    def test_cloud_policy_reason_included(self) -> None:
        router = ModelRouter(registry=_cloud_local_registry())
        d = router.route(_package(), RoutingPolicy.PREFER_CLOUD)
        assert RoutingReason.POLICY_PREFER_CLOUD in d.reasons

    def test_cost_optimized_reason_for_cheapest(self) -> None:
        router = ModelRouter(registry=_cost_spread_registry())
        d = router.route(_package(), RoutingPolicy.CHEAPEST)
        assert RoutingReason.COST_OPTIMIZED in d.reasons

    def test_quality_optimized_reason_for_highest_quality(self) -> None:
        router = ModelRouter(registry=_cost_spread_registry())
        d = router.route(_package(), RoutingPolicy.HIGHEST_QUALITY)
        assert RoutingReason.QUALITY_OPTIMIZED in d.reasons

    def test_locality_match_when_local_preferred_and_found(self) -> None:
        router = ModelRouter(registry=_cloud_local_registry())
        d = router.route(_package(), RoutingPolicy.LOCAL_ONLY)
        assert RoutingReason.LOCALITY_MATCH in d.reasons

    def test_local_model_available_reason_for_local(self) -> None:
        registry = _minimal_registry(locality=Locality.LOCAL)
        router = ModelRouter(registry=registry)
        d = router.route(_package(), RoutingPolicy.BALANCED)
        assert RoutingReason.LOCAL_MODEL_AVAILABLE in d.reasons

    def test_cloud_model_available_reason_for_cloud(self) -> None:
        registry = _minimal_registry(locality=Locality.CLOUD)
        router = ModelRouter(registry=registry)
        d = router.route(_package(), RoutingPolicy.BALANCED)
        assert RoutingReason.CLOUD_MODEL_AVAILABLE in d.reasons

    def test_only_available_when_single_candidate(self) -> None:
        router = ModelRouter(registry=_minimal_registry())
        d = router.route(_package(), RoutingPolicy.BALANCED)
        assert RoutingReason.ONLY_AVAILABLE in d.reasons

    def test_reasons_are_enum_values_not_strings(self) -> None:
        router = ModelRouter()
        d = router.route(_package(), RoutingPolicy.BALANCED)
        for reason in d.reasons:
            assert isinstance(reason, RoutingReason)

    def test_no_models_available_reason_when_empty_registry(self) -> None:
        router = ModelRouter(registry=[])
        d = router.route(_package(), RoutingPolicy.BALANCED)
        assert not d.routed
        assert RoutingReason.NO_MODELS_AVAILABLE in d.reasons

    def test_routing_decision_policy_field_correct(self) -> None:
        router = ModelRouter()
        for policy in RoutingPolicy:
            d = router.route(_package(), policy)
            assert d.policy == policy

    def test_routing_reason_enum_has_required_values(self) -> None:
        """All Amendment 1 example values must exist in the enum."""
        required = [
            "PRIMARY_RECOMMENDATION",
            "POLICY_PREFER_LOCAL",
            "POLICY_PREFER_CLOUD",
            "LOCAL_MODEL_AVAILABLE",
            "CLOUD_MODEL_AVAILABLE",
            "CONTEXT_TOO_LARGE",
            "CONTEXT_FITS",
            "TOOLS_REQUIRED",
            "VISION_REQUIRED",
            "COST_OPTIMIZED",
            "QUALITY_OPTIMIZED",
            "LATENCY_OPTIMIZED",
            "FALLBACK_SELECTED",
        ]
        enum_names = {r.name for r in RoutingReason}
        for name in required:
            assert name in enum_names, f"RoutingReason.{name} is missing"


# ── Capability Metadata ───────────────────────────────────────────────────


class TestCapabilityMetadata:
    """Registry entries must accurately expose capability flags."""

    def test_model_entry_supports_tools_field(self) -> None:
        model = ModelEntry(
            id="m", provider="p", name="M",
            context_window=128_000, supports_tools=True,
        )
        assert model.supports_tools is True

    def test_model_entry_supports_vision_field(self) -> None:
        model = ModelEntry(
            id="m", provider="p", name="M",
            context_window=128_000, supports_vision=True,
        )
        assert model.supports_vision is True

    def test_model_entry_supports_reasoning_field(self) -> None:
        model = ModelEntry(
            id="m", provider="p", name="M",
            context_window=128_000, supports_reasoning=True,
        )
        assert model.supports_reasoning is True

    def test_default_registry_has_tool_supporting_models(self) -> None:
        tool_models = [m for m in DEFAULT_REGISTRY if m.supports_tools]
        assert len(tool_models) > 0

    def test_default_registry_has_vision_supporting_models(self) -> None:
        vision_models = [m for m in DEFAULT_REGISTRY if m.supports_vision]
        assert len(vision_models) > 0

    def test_default_registry_has_local_and_cloud_models(self) -> None:
        local = [m for m in DEFAULT_REGISTRY if m.locality == Locality.LOCAL]
        cloud = [m for m in DEFAULT_REGISTRY if m.locality == Locality.CLOUD]
        assert len(local) > 0
        assert len(cloud) > 0

    def test_default_registry_has_no_provider_specific_logic(self) -> None:
        """All models must be pure declarative data."""
        for model in DEFAULT_REGISTRY:
            assert isinstance(model, ModelEntry)
            assert isinstance(model.id, str)
            assert isinstance(model.provider, str)
            assert isinstance(model.context_window, int)
            assert isinstance(model.cost_tier, CostTier)
            assert isinstance(model.latency_tier, LatencyTier)
            assert isinstance(model.locality, Locality)


# ── Edge Cases ────────────────────────────────────────────────────────────


class TestEdgeCases:
    """Router must handle unusual inputs gracefully."""

    def test_empty_registry_no_models(self) -> None:
        router = ModelRouter(registry=[])
        d = router.route(_package(), RoutingPolicy.BALANCED)
        assert not d.routed
        assert d.selected is None
        assert d.fallbacks == []

    def test_zero_char_prompt(self) -> None:
        router = ModelRouter()
        pkg = _package(chars=0)
        d = router.route(pkg, RoutingPolicy.BALANCED)
        assert d.routed

    def test_very_large_prompt_no_candidates(self) -> None:
        registry = [
            ModelEntry(
                id="m", provider="p", name="M",
                context_window=100, cost_tier=CostTier.HIGH,
                latency_tier=LatencyTier.FAST, locality=Locality.CLOUD,
            )
        ]
        router = ModelRouter(registry=registry)
        pkg = _package(chars=10_000_000)
        d = router.route(pkg, RoutingPolicy.BALANCED)
        assert not d.routed
        assert RoutingReason.NO_MODELS_AVAILABLE in d.reasons

    def test_all_models_unavailable(self) -> None:
        registry = [
            ModelEntry(
                id="m", provider="p", name="M",
                context_window=128_000, cost_tier=CostTier.MEDIUM,
                latency_tier=LatencyTier.MEDIUM, locality=Locality.CLOUD,
                available=False,
            )
        ]
        router = ModelRouter(registry=registry)
        d = router.route(_package(), RoutingPolicy.BALANCED)
        assert not d.routed

    def test_routing_decision_convenience_properties(self) -> None:
        router = ModelRouter()
        d = router.route(_package(), RoutingPolicy.BALANCED)
        assert isinstance(d.selected_model_id, str)
        assert isinstance(d.selected_provider, str)
        assert isinstance(d.fallback_model_ids, list)
        assert isinstance(d.rejected_model_ids, list)
        assert isinstance(d.has_fallbacks, bool)
        assert isinstance(d.routed, bool)

    def test_no_selected_returns_empty_strings(self) -> None:
        router = ModelRouter(registry=[])
        d = router.route(_package(), RoutingPolicy.BALANCED)
        assert d.selected_model_id == ""
        assert d.selected_provider == ""
        assert d.fallback_model_ids == []

    def test_routing_does_not_mutate_package(self) -> None:
        router = ModelRouter()
        pkg = _package(chars=5_000)
        original_chars = pkg.estimated_chars
        original_query = pkg.query
        router.route(pkg, RoutingPolicy.BALANCED)
        assert pkg.estimated_chars == original_chars
        assert pkg.query == original_query

    def test_routing_does_not_mutate_registry(self) -> None:
        router = ModelRouter()
        before = [m.id for m in router.registry]
        router.route(_package(), RoutingPolicy.BALANCED)
        after = [m.id for m in router.registry]
        assert before == after

    def test_policy_field_set_even_when_no_models(self) -> None:
        router = ModelRouter(registry=[])
        d = router.route(_package(), RoutingPolicy.CHEAPEST)
        assert d.policy == RoutingPolicy.CHEAPEST

    def test_single_model_has_no_fallbacks(self) -> None:
        router = ModelRouter(registry=_minimal_registry())
        d = router.route(_package(), RoutingPolicy.BALANCED)
        assert not d.has_fallbacks
        assert d.fallbacks == []


# ── Registry Module Separation (Sprint 51 — Amendment 1) ─────────────────


class TestRegistryModuleSeparation:
    """DEFAULT_REGISTRY must live in model_registry.py, not model_router.py.
    Adding models must never require changing routing logic.
    """

    def test_default_registry_is_in_model_registry_module(self) -> None:
        import hermes.kernel.model_registry as reg_module
        assert hasattr(reg_module, "DEFAULT_REGISTRY")
        assert isinstance(reg_module.DEFAULT_REGISTRY, list)
        assert len(reg_module.DEFAULT_REGISTRY) > 0

    def test_model_router_does_not_define_registry_inline(self) -> None:
        import hermes.kernel.model_router as router_module
        # Router module must NOT define DEFAULT_MODEL_REGISTRY
        assert not hasattr(router_module, "DEFAULT_MODEL_REGISTRY")

    def test_router_uses_injected_registry(self) -> None:
        custom = [
            ModelEntry(
                id="injected--model",
                provider="injected",
                name="Injected",
                context_window=64_000,
                cost_tier=CostTier.MEDIUM,
                latency_tier=LatencyTier.MEDIUM,
                locality=Locality.CLOUD,
            )
        ]
        router = ModelRouter(registry=custom)
        d = router.route(_package(), RoutingPolicy.BALANCED)
        assert d.selected_model_id == "injected--model"
        assert d.selected_provider == "injected"

    def test_router_defaults_to_default_registry(self) -> None:
        router = ModelRouter()
        registry_ids = {m.id for m in DEFAULT_REGISTRY}
        router_ids = set(router.registry[i].id for i in range(len(router.registry)))
        assert registry_ids == router_ids

    def test_adding_model_to_registry_module_does_not_change_router(self) -> None:
        """Simulate what happens when a new provider is added to the registry."""
        new_model = ModelEntry(
            id="future--provider-model",
            provider="future_provider",
            name="Future Provider Model",
            context_window=2_000_000,
            cost_tier=CostTier.HIGH,
            latency_tier=LatencyTier.FAST,
            locality=Locality.CLOUD,
            supports_tools=True,
            supports_vision=True,
            supports_streaming=True,
            supports_reasoning=True,
            family="future",
            capabilities=frozenset({
                ExecutionCapability.REASONING,
                ExecutionCapability.LONG_CONTEXT,
                ExecutionCapability.MULTIMODAL,
            }),
        )
        extended = list(DEFAULT_REGISTRY) + [new_model]
        # Router receives the extended registry via injection — no code change needed
        router = ModelRouter(registry=extended)
        d = router.route(_package(), RoutingPolicy.BALANCED)
        assert d.routed
        assert "future--provider-model" in d.evaluated_model_ids

    def test_default_registry_all_entries_are_model_entries(self) -> None:
        for entry in DEFAULT_REGISTRY:
            assert isinstance(entry, ModelEntry)
            assert isinstance(entry.capabilities, frozenset)


# ── Capability Taxonomy (Sprint 51 — Amendment 2) ────────────────────────


class TestCapabilityTaxonomy:
    """ExecutionCapability enum must exist; router must reason about capabilities."""

    def test_execution_capability_enum_has_required_values(self) -> None:
        required = [
            "CODE_GENERATION",
            "LONG_CONTEXT",
            "REASONING",
            "FAST_RESPONSE",
            "LOW_COST",
            "OFFLINE",
            "LOCAL",
            "MULTIMODAL",
            "STRUCTURED_OUTPUT",
        ]
        enum_names = {c.name for c in ExecutionCapability}
        for name in required:
            assert name in enum_names, f"ExecutionCapability.{name} is missing"

    def test_model_entry_has_capabilities_field(self) -> None:
        model = ModelEntry(
            id="m", provider="p", name="M", context_window=64_000,
        )
        assert hasattr(model, "capabilities")
        assert isinstance(model.capabilities, frozenset)

    def test_capabilities_default_is_empty_frozenset(self) -> None:
        model = ModelEntry(
            id="m", provider="p", name="M", context_window=64_000,
        )
        assert model.capabilities == frozenset()

    def test_capabilities_are_immutable(self) -> None:
        model = ModelEntry(
            id="m", provider="p", name="M", context_window=64_000,
            capabilities=frozenset({ExecutionCapability.REASONING}),
        )
        assert isinstance(model.capabilities, frozenset)

    def test_default_registry_models_have_capabilities(self) -> None:
        models_with_caps = [m for m in DEFAULT_REGISTRY if m.capabilities]
        assert len(models_with_caps) == len(DEFAULT_REGISTRY), (
            "Every model in DEFAULT_REGISTRY must have at least one capability"
        )

    def test_reasoning_models_have_reasoning_capability(self) -> None:
        """Models declaring supports_reasoning should have REASONING capability."""
        for m in DEFAULT_REGISTRY:
            if m.supports_reasoning:
                assert ExecutionCapability.REASONING in m.capabilities, (
                    f"{m.id} supports_reasoning=True but lacks REASONING capability"
                )

    def test_local_models_have_local_capability(self) -> None:
        """Models with LOCAL locality should declare LOCAL and OFFLINE capabilities."""
        for m in DEFAULT_REGISTRY:
            if m.locality == Locality.LOCAL:
                assert ExecutionCapability.LOCAL in m.capabilities, (
                    f"{m.id} is LOCAL but lacks LOCAL capability"
                )
                assert ExecutionCapability.OFFLINE in m.capabilities, (
                    f"{m.id} is LOCAL but lacks OFFLINE capability"
                )

    def test_vision_models_have_multimodal_capability(self) -> None:
        """Models with supports_vision should have MULTIMODAL capability."""
        for m in DEFAULT_REGISTRY:
            if m.supports_vision:
                assert ExecutionCapability.MULTIMODAL in m.capabilities, (
                    f"{m.id} supports_vision=True but lacks MULTIMODAL capability"
                )

    def test_capability_score_rewards_matching_capabilities(self) -> None:
        """Models with LOW_COST capability score higher under CHEAPEST policy."""
        with_low_cost = ModelEntry(
            id="cheap", provider="x", name="Cheap",
            context_window=128_000, cost_tier=CostTier.FREE,
            latency_tier=LatencyTier.FAST, locality=Locality.CLOUD,
            capabilities=frozenset({ExecutionCapability.LOW_COST}),
        )
        without_low_cost = ModelEntry(
            id="not-cheap", provider="x", name="NotCheap",
            context_window=128_000, cost_tier=CostTier.FREE,
            latency_tier=LatencyTier.FAST, locality=Locality.CLOUD,
            capabilities=frozenset(),
        )
        router = ModelRouter(registry=[with_low_cost, without_low_cost])
        estimated_tokens = 100 // _CHARS_PER_TOKEN
        score_with = router._compute_score(with_low_cost, RoutingPolicy.CHEAPEST, estimated_tokens)
        score_without = router._compute_score(without_low_cost, RoutingPolicy.CHEAPEST, estimated_tokens)
        assert score_with > score_without

    def test_capability_score_rewards_reasoning_under_quality_policy(self) -> None:
        """Models with REASONING capability score higher under HIGHEST_QUALITY policy."""
        with_reasoning = ModelEntry(
            id="smart", provider="x", name="Smart",
            context_window=128_000, cost_tier=CostTier.HIGH,
            latency_tier=LatencyTier.SLOW, locality=Locality.CLOUD,
            capabilities=frozenset({ExecutionCapability.REASONING}),
        )
        without_reasoning = ModelEntry(
            id="dumb", provider="x", name="Dumb",
            context_window=128_000, cost_tier=CostTier.HIGH,
            latency_tier=LatencyTier.SLOW, locality=Locality.CLOUD,
            capabilities=frozenset(),
        )
        router = ModelRouter(registry=[with_reasoning, without_reasoning])
        estimated_tokens = 100 // _CHARS_PER_TOKEN
        score_with = router._compute_score(with_reasoning, RoutingPolicy.HIGHEST_QUALITY, estimated_tokens)
        score_without = router._compute_score(without_reasoning, RoutingPolicy.HIGHEST_QUALITY, estimated_tokens)
        assert score_with > score_without

    def test_capability_score_rewards_local_under_prefer_local_policy(self) -> None:
        """Models with LOCAL+OFFLINE capabilities score higher under PREFER_LOCAL."""
        with_local = ModelEntry(
            id="local", provider="x", name="Local",
            context_window=128_000, cost_tier=CostTier.FREE,
            latency_tier=LatencyTier.FAST, locality=Locality.LOCAL,
            capabilities=frozenset({ExecutionCapability.LOCAL, ExecutionCapability.OFFLINE}),
        )
        without_local = ModelEntry(
            id="cloud", provider="x", name="Cloud",
            context_window=128_000, cost_tier=CostTier.FREE,
            latency_tier=LatencyTier.FAST, locality=Locality.LOCAL,
            capabilities=frozenset(),
        )
        router = ModelRouter(registry=[with_local, without_local])
        estimated_tokens = 100 // _CHARS_PER_TOKEN
        score_with = router._compute_score(with_local, RoutingPolicy.PREFER_LOCAL, estimated_tokens)
        score_without = router._compute_score(without_local, RoutingPolicy.PREFER_LOCAL, estimated_tokens)
        assert score_with > score_without

    def test_policy_capability_affinity_covers_all_policies(self) -> None:
        """Every RoutingPolicy must have an entry in the affinity map."""
        for policy in RoutingPolicy:
            assert policy in _POLICY_CAPABILITY_AFFINITY, (
                f"RoutingPolicy.{policy.name} missing from _POLICY_CAPABILITY_AFFINITY"
            )

    def test_router_reasons_about_capabilities_not_provider_names(self) -> None:
        """The router code must not reference specific provider names in logic."""
        import inspect
        import hermes.kernel.model_router as router_module
        source = inspect.getsource(router_module)
        # The router source should not hardcode provider names in logic
        forbidden = ["anthropic", "openai", "ollama", "gemini", "moonshot"]
        for provider in forbidden:
            # Allow provider names only in comments/strings, not in logic
            # The registry module handles provider names; router should not
            assert f'== "{provider}"' not in source, (
                f"Router must not compare against provider name '{provider}'"
            )
            assert f"== '{provider}'" not in source, (
                f"Router must not compare against provider name '{provider}'"
            )
