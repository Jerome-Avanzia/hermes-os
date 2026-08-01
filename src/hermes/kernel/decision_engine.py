"""Decision Engine — produces ranked Recommendations from BusinessData.

Follows docs/12-decision-engine-specification.md and docs/13-scoring-model.md.
All scoring is deterministic.  No keyword overlap, no fuzzy matching, no NLP.
Pure analysis only — no file writes, no mutation, no lifecycle transitions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from hermes.kernel.business_data_loader import BusinessData
from hermes.models import Bottleneck, KPI, Lesson, Opportunity


# -- Output dataclasses (internal analysis artifacts, NOT canonical models) ----


@dataclass(slots=True)
class DimensionScore:
    """Score for a single scoring dimension."""

    dimension: str
    score: int  # 1–5
    rationale: str


@dataclass(slots=True)
class Recommendation:
    """A scored, ranked recommendation from the Decision Engine.

    This is an analysis artifact — NOT a canonical Business Object.
    """

    recommendation_id: str
    business_id: str
    title: str
    context: str
    rationale: str
    source_type: str  # "bottleneck" | "opportunity" | "kpi"
    source_id: str
    dimension_scores: list[DimensionScore]
    priority_score: float  # weighted composite, 1.0–5.0
    confidence: str  # "low" | "medium" | "high"
    suggested_action: str  # "decide" | "experiment" | "monitor"


@dataclass(slots=True)
class EngineConfig:
    """Scoring weights configuration."""

    weights: dict[str, float]  # dimension → weight (must sum to 1.0)


@dataclass(slots=True)
class EngineResult:
    """Complete output from a Decision Engine run."""

    business_id: str
    recommendations: list[Recommendation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# -- Default weights per docs/13 ----------------------------------------------

DIMENSIONS = [
    "strategic_alignment",
    "expected_impact",
    "required_effort",
    "risk",
    "urgency",
    "confidence",
    "historical_success",
]

DEFAULT_WEIGHTS: dict[str, float] = {
    "strategic_alignment": 0.25,
    "expected_impact": 0.25,
    "required_effort": 0.15,
    "risk": 0.10,
    "urgency": 0.10,
    "confidence": 0.10,
    "historical_success": 0.05,
}

# -- Impact/effort mappings ----------------------------------------------------

_IMPACT_MAP: dict[str, int] = {"high": 5, "medium": 3, "low": 2}
_EFFORT_MAP: dict[str, int] = {"low": 5, "medium": 3, "high": 2}
_URGENCY_MAP: dict[str, int] = {
    "open": 5,
    "active": 5,
    "in_progress": 4,
    "planned": 3,
    "backlog": 2,
}


# -- Engine --------------------------------------------------------------------


class DecisionEngine:
    """Produces ranked Recommendations from BusinessData.

    Completely stateless.  Accepts optional EngineConfig at construction.
    Single public method: evaluate(BusinessData) → EngineResult.
    """

    def __init__(self, config: EngineConfig | None = None) -> None:
        if config is None:
            self._weights = dict(DEFAULT_WEIGHTS)
        else:
            self._weights = dict(config.weights)
        total = sum(self._weights.values())
        if abs(total - 1.0) > 0.01:
            raise ValueError(
                f"Weights must sum to 1.0, got {total:.4f}"
            )

    def evaluate(self, data: BusinessData) -> EngineResult:
        """Evaluate BusinessData and return ranked Recommendations."""
        warnings: list[str] = list(data.warnings)

        if data.business is None:
            warnings.append("No business loaded — cannot evaluate")
            return EngineResult(business_id="", warnings=warnings)

        business_id = data.business.business_id
        all_recs: list[Recommendation] = []

        all_recs.extend(
            self._evaluate_bottlenecks(data, business_id, warnings)
        )
        all_recs.extend(
            self._evaluate_opportunities(data, business_id, warnings)
        )
        all_recs.extend(
            self._evaluate_kpis(data, business_id, warnings)
        )

        all_recs = self._rank_and_deduplicate(all_recs)

        return EngineResult(
            business_id=business_id,
            recommendations=all_recs,
            warnings=warnings,
        )

    # -- Source evaluators -----------------------------------------------------

    def _evaluate_bottlenecks(
        self,
        data: BusinessData,
        business_id: str,
        warnings: list[str],
    ) -> list[Recommendation]:
        recs: list[Recommendation] = []
        for bot in data.bottlenecks:
            scores = self._score_bottleneck(bot, data, warnings)
            priority = self._compute_priority(scores)
            confidence = self._determine_confidence(scores)
            action = self._determine_action(confidence)
            recs.append(
                Recommendation(
                    recommendation_id=f"rec_{bot.bottleneck_id.lower()}",
                    business_id=business_id,
                    title=f"Address bottleneck: {bot.title}",
                    context=f"Bottleneck {bot.bottleneck_id} in {bot.category} "
                    f"with {bot.impact} impact is {bot.status}",
                    rationale=f"Open bottleneck with {bot.impact} impact "
                    f"in {bot.category} area",
                    source_type="bottleneck",
                    source_id=bot.bottleneck_id,
                    dimension_scores=scores,
                    priority_score=priority,
                    confidence=confidence,
                    suggested_action=action,
                )
            )
        return recs

    def _evaluate_opportunities(
        self,
        data: BusinessData,
        business_id: str,
        warnings: list[str],
    ) -> list[Recommendation]:
        recs: list[Recommendation] = []
        for opp in data.opportunities:
            scores = self._score_opportunity(opp, data, warnings)
            priority = self._compute_priority(scores)
            confidence = self._determine_confidence(scores)
            action = self._determine_action(confidence)
            recs.append(
                Recommendation(
                    recommendation_id=f"rec_{opp.opportunity_id.lower()}",
                    business_id=business_id,
                    title=f"Pursue opportunity: {opp.title}",
                    context=f"Opportunity {opp.opportunity_id} with "
                    f"{opp.expected_impact} expected impact and "
                    f"{opp.estimated_effort} effort",
                    rationale=f"Opportunity with {opp.expected_impact} impact "
                    f"and {opp.estimated_effort} effort",
                    source_type="opportunity",
                    source_id=opp.opportunity_id,
                    dimension_scores=scores,
                    priority_score=priority,
                    confidence=confidence,
                    suggested_action=action,
                )
            )
        return recs

    def _evaluate_kpis(
        self,
        data: BusinessData,
        business_id: str,
        warnings: list[str],
    ) -> list[Recommendation]:
        recs: list[Recommendation] = []
        for kpi in data.kpis:
            if kpi.target_value == 0:
                continue
            gap_ratio = (kpi.target_value - kpi.current_value) / kpi.target_value
            if gap_ratio <= 0:
                continue  # on or above target
            scores = self._score_kpi(kpi, gap_ratio, data, warnings)
            priority = self._compute_priority(scores)
            confidence = self._determine_confidence(scores)
            action = self._determine_action(confidence)
            recs.append(
                Recommendation(
                    recommendation_id=f"rec_{kpi.kpi_id.lower()}",
                    business_id=business_id,
                    title=f"Improve KPI: {kpi.name}",
                    context=f"KPI {kpi.kpi_id} ({kpi.name}) is at "
                    f"{kpi.current_value} vs target {kpi.target_value}",
                    rationale=f"KPI below target by {gap_ratio:.0%}",
                    source_type="kpi",
                    source_id=kpi.kpi_id,
                    dimension_scores=scores,
                    priority_score=priority,
                    confidence=confidence,
                    suggested_action=action,
                )
            )
        return recs

    # -- Dimension scoring -----------------------------------------------------

    def _score_bottleneck(
        self,
        bot: Bottleneck,
        data: BusinessData,
        warnings: list[str],
    ) -> list[DimensionScore]:
        # Strategic Alignment — bottleneck has no strategy_id/goal_id fields
        warnings.append(
            f"No structured strategy link for {bot.bottleneck_id}"
        )
        strategic = DimensionScore(
            dimension="strategic_alignment",
            score=3,
            rationale="No structured strategy/goal link on bottleneck",
        )

        # Expected Impact
        impact_score = _IMPACT_MAP.get(bot.impact, 3)
        impact = DimensionScore(
            dimension="expected_impact",
            score=impact_score,
            rationale=f"Impact field: {bot.impact}",
        )

        # Required Effort — no effort field on bottleneck
        warnings.append(
            f"No effort data for {bot.bottleneck_id}, using neutral score"
        )
        effort = DimensionScore(
            dimension="required_effort",
            score=3,
            rationale="No effort field on bottleneck, neutral score",
        )

        # Risk — bottleneck = blocking risk
        risk = DimensionScore(
            dimension="risk", score=4, rationale="Bottleneck: blocking risk"
        )

        # Urgency — from status
        urgency_score = _URGENCY_MAP.get(bot.status, 3)
        urgency = DimensionScore(
            dimension="urgency",
            score=urgency_score,
            rationale=f"Status: {bot.status}",
        )

        # Confidence — count structured evidence
        evidence = self._count_evidence(data)
        conf = DimensionScore(
            dimension="confidence",
            score=self._evidence_to_score(evidence),
            rationale=f"{evidence} evidence items in BusinessData",
        )

        # Historical Success — match lessons by source vs category
        lesson_count = self._count_matching_lessons(
            data.lessons, bot.category
        )
        hist = DimensionScore(
            dimension="historical_success",
            score=self._lessons_to_score(lesson_count),
            rationale=f"{lesson_count} lessons matching category '{bot.category}'",
        )

        return [strategic, impact, effort, risk, urgency, conf, hist]

    def _score_opportunity(
        self,
        opp: Opportunity,
        data: BusinessData,
        warnings: list[str],
    ) -> list[DimensionScore]:
        # Strategic Alignment — check strategy_id/goal_id
        has_link = (opp.strategy_id is not None) or (opp.goal_id is not None)
        if has_link:
            strategic = DimensionScore(
                dimension="strategic_alignment",
                score=5,
                rationale="Linked via structured strategy/goal field",
            )
        else:
            warnings.append(
                f"No structured strategy link for {opp.opportunity_id}"
            )
            strategic = DimensionScore(
                dimension="strategic_alignment",
                score=3,
                rationale="No structured strategy/goal link on opportunity",
            )

        # Expected Impact
        impact_score = _IMPACT_MAP.get(opp.expected_impact, 3)
        impact = DimensionScore(
            dimension="expected_impact",
            score=impact_score,
            rationale=f"Expected impact: {opp.expected_impact}",
        )

        # Required Effort
        effort_score = _EFFORT_MAP.get(opp.estimated_effort, 3)
        effort = DimensionScore(
            dimension="required_effort",
            score=effort_score,
            rationale=f"Estimated effort: {opp.estimated_effort}",
        )

        # Risk — opportunity = execution risk
        risk = DimensionScore(
            dimension="risk", score=3, rationale="Opportunity: execution risk"
        )

        # Urgency — from status
        urgency_score = _URGENCY_MAP.get(opp.status, 3)
        urgency = DimensionScore(
            dimension="urgency",
            score=urgency_score,
            rationale=f"Status: {opp.status}",
        )

        # Confidence
        evidence = self._count_evidence(data)
        conf = DimensionScore(
            dimension="confidence",
            score=self._evidence_to_score(evidence),
            rationale=f"{evidence} evidence items in BusinessData",
        )

        # Historical Success
        lesson_count = self._count_matching_lessons(
            data.lessons, "opportunity"
        )
        hist = DimensionScore(
            dimension="historical_success",
            score=self._lessons_to_score(lesson_count),
            rationale=f"{lesson_count} lessons matching source 'opportunity'",
        )

        return [strategic, impact, effort, risk, urgency, conf, hist]

    def _score_kpi(
        self,
        kpi: KPI,
        gap_ratio: float,
        data: BusinessData,
        warnings: list[str],
    ) -> list[DimensionScore]:
        # Strategic Alignment — KPI always has goal_id (required field)
        strategic = DimensionScore(
            dimension="strategic_alignment",
            score=5,
            rationale=f"KPI linked to goal {kpi.goal_id}",
        )

        # Expected Impact — from gap ratio
        if gap_ratio > 0.50:
            impact_score = 5
        elif gap_ratio > 0.25:
            impact_score = 3
        else:
            impact_score = 2
        impact = DimensionScore(
            dimension="expected_impact",
            score=impact_score,
            rationale=f"KPI gap ratio: {gap_ratio:.0%}",
        )

        # Required Effort — no effort field on KPI
        warnings.append(
            f"No effort data for {kpi.kpi_id}, using neutral score"
        )
        effort = DimensionScore(
            dimension="required_effort",
            score=3,
            rationale="No effort field on KPI, neutral score",
        )

        # Risk — KPI = monitoring risk
        risk = DimensionScore(
            dimension="risk", score=2, rationale="KPI: monitoring risk"
        )

        # Urgency — from status
        urgency_score = _URGENCY_MAP.get(kpi.status, 3)
        urgency = DimensionScore(
            dimension="urgency",
            score=urgency_score,
            rationale=f"Status: {kpi.status}",
        )

        # Confidence — KPIs have numeric data = strong evidence
        evidence = self._count_evidence(data)
        conf = DimensionScore(
            dimension="confidence",
            score=self._evidence_to_score(evidence),
            rationale=f"{evidence} evidence items in BusinessData",
        )

        # Historical Success
        lesson_count = self._count_matching_lessons(data.lessons, "review")
        hist = DimensionScore(
            dimension="historical_success",
            score=self._lessons_to_score(lesson_count),
            rationale=f"{lesson_count} lessons matching source 'review'",
        )

        return [strategic, impact, effort, risk, urgency, conf, hist]

    # -- Computation helpers ---------------------------------------------------

    def _compute_priority(self, scores: list[DimensionScore]) -> float:
        total = 0.0
        for ds in scores:
            weight = self._weights.get(ds.dimension, 0.0)
            total += ds.score * weight
        return round(total, 2)

    @staticmethod
    def _determine_confidence(scores: list[DimensionScore]) -> str:
        for ds in scores:
            if ds.dimension == "confidence":
                if ds.score >= 4:
                    return "high"
                if ds.score >= 2:
                    return "medium"
                return "low"
        return "low"

    @staticmethod
    def _determine_action(confidence: str) -> str:
        if confidence == "high":
            return "decide"
        if confidence == "medium":
            return "experiment"
        return "monitor"

    @staticmethod
    def _count_evidence(data: BusinessData) -> int:
        count = len(data.kpis)
        count += sum(1 for e in data.experiments if e.outcome is not None)
        count += len(data.decisions)
        return count

    @staticmethod
    def _evidence_to_score(count: int) -> int:
        if count >= 4:
            return 5
        if count >= 3:
            return 4
        if count >= 2:
            return 3
        if count >= 1:
            return 2
        return 1

    @staticmethod
    def _count_matching_lessons(
        lessons: list[Lesson], category: str,
    ) -> int:
        target = category.strip().lower()
        return sum(1 for l in lessons if l.source.strip().lower() == target)

    @staticmethod
    def _lessons_to_score(count: int) -> int:
        if count >= 2:
            return 5
        if count >= 1:
            return 4
        return 3  # neutral

    @staticmethod
    def _rank_and_deduplicate(
        recs: list[Recommendation],
    ) -> list[Recommendation]:
        seen: set[str] = set()
        unique: list[Recommendation] = []
        for r in recs:
            if r.recommendation_id not in seen:
                seen.add(r.recommendation_id)
                unique.append(r)
        unique.sort(key=lambda r: r.priority_score, reverse=True)
        return unique
