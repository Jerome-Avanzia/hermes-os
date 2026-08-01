"""Executive Brief Generator — synthesises BusinessData + EngineResult into an ExecutiveBrief.

All output is deterministic.  No NLP, no LLM reasoning, no keyword matching.
Pure synthesis only — no file writes, no mutation, no lifecycle transitions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from hermes.kernel.business_data_loader import BusinessData
from hermes.kernel.decision_engine import EngineResult, Recommendation
from hermes.models import ExecutiveBrief

_VALID_PERIODS = frozenset({"daily", "weekly", "monthly", "quarterly", "annual"})


@dataclass(slots=True)
class GeneratorResult:
    """Output from the Executive Brief Generator.

    Wraps the canonical ExecutiveBrief plus generator warnings.
    This is an internal artifact — not a canonical model.
    """

    brief: ExecutiveBrief
    warnings: list[str] = field(default_factory=list)


class ExecutiveBriefGenerator:
    """Produces an ExecutiveBrief from BusinessData and EngineResult.

    Completely stateless.  Single public method: generate().
    """

    def generate(
        self,
        data: BusinessData,
        engine_result: EngineResult,
        reporting_period: str = "daily",
    ) -> GeneratorResult:
        """Synthesise an ExecutiveBrief from structured business data."""
        warnings: list[str] = list(engine_result.warnings)
        now = datetime.now(timezone.utc).isoformat()

        if reporting_period not in _VALID_PERIODS:
            warnings.append(
                f"Invalid reporting_period '{reporting_period}', defaulting to 'daily'"
            )
            reporting_period = "daily"

        if data.business is None:
            warnings.append("No business loaded — brief is empty")
            brief = ExecutiveBrief(
                brief_id=f"brief___{now}",
                business_id="",
                reporting_period=reporting_period,
                generated_at=now,
                summary="No business data available.",
                priorities=[],
                recommendations=[],
                status="draft",
                risks=[],
                created_at=now,
            )
            return GeneratorResult(brief=brief, warnings=warnings)

        business_id = data.business.business_id
        recs = engine_result.recommendations

        summary = self._build_summary(data, engine_result, reporting_period)
        priorities = self._build_priorities(recs, warnings)
        recommendations = self._build_recommendations(recs, warnings)
        risks = self._build_risks(data, warnings)

        # Low-confidence recommendations → warnings (not risks)
        for rec in recs:
            if rec.confidence == "low":
                warnings.append(
                    f"Low confidence recommendation: {rec.title} "
                    f"— insufficient evidence for action"
                )

        brief_id = f"brief_{business_id}_{now}"

        brief = ExecutiveBrief(
            brief_id=brief_id,
            business_id=business_id,
            reporting_period=reporting_period,
            generated_at=now,
            summary=summary,
            priorities=priorities,
            recommendations=recommendations,
            status="draft",
            risks=risks,
            created_at=now,
        )

        return GeneratorResult(brief=brief, warnings=warnings)

    # -- Private builders ------------------------------------------------------

    @staticmethod
    def _build_summary(
        data: BusinessData,
        engine_result: EngineResult,
        reporting_period: str,
    ) -> str:
        biz = data.business
        strategy_title = data.strategy.title if data.strategy else "No strategy defined"

        n_high_bottlenecks = sum(
            1 for b in data.bottlenecks if b.impact == "high"
        )

        lines: list[str] = [
            f"Business: {biz.name}",
            f"Strategy: {strategy_title}",
            f"Period: {reporting_period}",
            "",
            f"Bottlenecks: {len(data.bottlenecks)} active ({n_high_bottlenecks} high impact)",
            f"Opportunities: {len(data.opportunities)} identified",
            f"Decisions: {len(data.decisions)} recorded",
            f"Experiments: {len(data.experiments)} tracked",
            f"Recommendations: {len(engine_result.recommendations)} generated",
            "",
        ]

        # KPI Status
        lines.append("KPI Status:")
        if data.kpis:
            for kpi in data.kpis:
                lines.append(
                    f"- {kpi.name}: {kpi.current_value}/{kpi.target_value} "
                    f"{kpi.unit} ({kpi.status})"
                )
        else:
            lines.append("- No KPI data available.")

        lines.append("")

        # Overall Confidence
        confidence = _compute_overall_confidence(engine_result.recommendations)
        lines.append(f"Overall Confidence: {confidence}")

        return "\n".join(lines)

    @staticmethod
    def _build_priorities(
        recs: list[Recommendation], warnings: list[str],
    ) -> list[str]:
        if not recs:
            warnings.append("No recommendations available for priorities")
            return []
        top = recs[:5]
        return [
            f"[{r.priority_score:.1f}] {r.title} ({r.suggested_action})"
            for r in top
        ]

    @staticmethod
    def _build_recommendations(
        recs: list[Recommendation], warnings: list[str],
    ) -> list[str]:
        if not recs:
            warnings.append("No recommendations generated")
            return []
        result: list[str] = []
        for r in recs:
            if r.suggested_action == "decide":
                result.append(f"{r.title}: {r.rationale}")
            elif r.suggested_action == "experiment":
                result.append(f"Experiment: {r.title} — {r.rationale}")
            else:
                result.append(f"Monitor: {r.title}")
        return result

    @staticmethod
    def _build_risks(
        data: BusinessData, warnings: list[str],
    ) -> list[str]:
        risks: list[str] = []

        # Factual risks from bottlenecks
        for bot in data.bottlenecks:
            if bot.impact == "high":
                risks.append(
                    f"Bottleneck {bot.bottleneck_id}: {bot.title} "
                    f"(impact: high, status: {bot.status})"
                )

        # Factual risks from KPIs
        for kpi in data.kpis:
            if kpi.status == "off_track":
                risks.append(
                    f"KPI {kpi.name}: {kpi.current_value}/{kpi.target_value} "
                    f"{kpi.unit} (off_track)"
                )

        return risks


def _compute_overall_confidence(recs: list[Recommendation]) -> str:
    if not recs:
        return "insufficient_data"
    confidences = [r.confidence for r in recs]
    if all(c == "high" for c in confidences):
        return "high"
    if any(c == "low" for c in confidences):
        return "low"
    return "medium"
