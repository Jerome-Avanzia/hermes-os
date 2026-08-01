"""Ten-Step CEO Loop — orchestrates Hermes kernel services into a complete
deterministic business review cycle.

Follows docs/24-hermes-agent-integration.md §4.
Pure orchestration — no file writes, no mutation, no persistence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from hermes.kernel.brief_generator import ExecutiveBriefGenerator, GeneratorResult
from hermes.kernel.business_data_loader import BusinessData, BusinessDataLoader
from hermes.kernel.decision_engine import DecisionEngine, EngineResult
from hermes.models import ExecutiveBrief


@dataclass(slots=True)
class StepRecord:
    """Audit record for one step of the Ten-Step Loop."""

    step_number: int  # 1–10
    name: str
    status: str  # "completed" | "skipped" | "failed"
    detail: str
    timestamp: str  # ISO datetime


@dataclass(slots=True)
class CEOReviewResult:
    """Complete output from one execution of the Ten-Step Loop.

    This is an internal orchestration artifact — not a canonical model.
    Warning provenance is preserved by source; use all_warnings for the
    aggregated list.
    """

    review_id: str
    business_id: str
    data: BusinessData
    engine_result: EngineResult
    brief: ExecutiveBrief
    steps: list[StepRecord]
    execution_status: str  # "completed" | "completed_with_warnings" | "failed"
    loader_warnings: list[str] = field(default_factory=list)
    engine_warnings: list[str] = field(default_factory=list)
    generator_warnings: list[str] = field(default_factory=list)
    loop_warnings: list[str] = field(default_factory=list)
    started_at: str = ""
    completed_at: str = ""

    @property
    def all_warnings(self) -> list[str]:
        """Aggregated warnings from all sources."""
        return (
            self.loader_warnings
            + self.engine_warnings
            + self.generator_warnings
            + self.loop_warnings
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _step(number: int, name: str, status: str, detail: str) -> StepRecord:
    return StepRecord(
        step_number=number,
        name=name,
        status=status,
        detail=detail,
        timestamp=_now(),
    )


# -- Empty defaults for failed runs -------------------------------------------

_EMPTY_DATA = BusinessData()
_EMPTY_ENGINE = EngineResult(business_id="")
_EMPTY_BRIEF = ExecutiveBrief(
    brief_id="",
    business_id="",
    reporting_period="daily",
    generated_at="",
    summary="",
    priorities=[],
    recommendations=[],
    status="draft",
)


class CEOLoop:
    """Orchestrates the Ten-Step CEO Review Loop.

    Coordinates BusinessDataLoader, DecisionEngine, and
    ExecutiveBriefGenerator into a single deterministic review cycle.
    Completely stateless.  No file writes.  No persistence.
    """

    def __init__(
        self,
        loader: BusinessDataLoader | None = None,
        engine: DecisionEngine | None = None,
        generator: ExecutiveBriefGenerator | None = None,
    ) -> None:
        self._loader = loader or BusinessDataLoader()
        self._engine = engine or DecisionEngine()
        self._generator = generator or ExecutiveBriefGenerator()

    def run(
        self,
        business_dir: Path,
        reporting_period: str = "daily",
    ) -> CEOReviewResult:
        """Execute the Ten-Step Loop and return a CEOReviewResult."""
        started_at = _now()
        review_id = f"review_{started_at}"
        steps: list[StepRecord] = []
        loop_warnings: list[str] = []

        # Step 1 — Load Governance
        if not business_dir.is_dir():
            steps.append(_step(1, "load_governance", "failed",
                               f"Directory not found: {business_dir}"))
            loop_warnings.append(f"Governance load failed: {business_dir} not found")
            for i, name in _REMAINING_STEPS.items():
                if i > 1:
                    steps.append(_step(i, name, "skipped",
                                       "Skipped due to governance failure"))
            return CEOReviewResult(
                review_id=review_id,
                business_id="",
                data=_EMPTY_DATA,
                engine_result=_EMPTY_ENGINE,
                brief=_EMPTY_BRIEF,
                steps=steps,
                execution_status="failed",
                loop_warnings=loop_warnings,
                started_at=started_at,
                completed_at=_now(),
            )

        steps.append(_step(1, "load_governance", "completed",
                           f"Business directory validated: {business_dir.name}"))

        # Step 2 — Load Business Context
        data = self._loader.load(business_dir)
        loader_warnings = list(data.warnings)
        biz_detail = (
            f"Loaded: {data.business.name}" if data.business
            else "No business found in directory"
        )
        steps.append(_step(2, "load_business_context", "completed", biz_detail))

        business_id = data.business.business_id if data.business else ""

        # Step 3 — Discover Agents (Phase 6 scope)
        steps.append(_step(3, "discover_agents", "skipped",
                           "Agent registry is Phase 6 scope"))

        # Step 4 — Discover Services
        steps.append(_step(4, "discover_services", "completed",
                           "Available: BusinessDataLoader, DecisionEngine, "
                           "ExecutiveBriefGenerator"))

        # Step 5 — Discover Extensions (Phase 6 scope)
        steps.append(_step(5, "discover_extensions", "skipped",
                           "Extension framework is Phase 6 scope"))

        # Step 6 — Build Plan
        steps.append(_step(6, "build_plan", "completed",
                           "Pipeline: Load → Evaluate → Generate Brief"))

        # Step 7 — Execute
        engine_result: EngineResult
        generator_result: GeneratorResult
        try:
            engine_result = self._engine.evaluate(data)
            generator_result = self._generator.generate(
                data, engine_result, reporting_period
            )
            rec_count = len(engine_result.recommendations)
            steps.append(_step(
                7, "execute", "completed",
                f"{rec_count} recommendations generated, brief produced"
            ))
        except Exception as exc:
            steps.append(_step(7, "execute", "failed", str(exc)))
            loop_warnings.append(f"Execution failed: {exc}")
            # Fill remaining steps as skipped
            for i in range(8, 11):
                steps.append(_step(i, _REMAINING_STEPS[i], "skipped",
                                   "Skipped due to execution failure"))
            return CEOReviewResult(
                review_id=review_id,
                business_id=business_id,
                data=data,
                engine_result=_EMPTY_ENGINE,
                brief=_EMPTY_BRIEF,
                steps=steps,
                execution_status="failed",
                loader_warnings=loader_warnings,
                loop_warnings=loop_warnings,
                started_at=started_at,
                completed_at=_now(),
            )

        engine_warnings = list(engine_result.warnings)
        generator_warnings = list(generator_result.warnings)

        # Step 8 — Monitor Execution
        completed = sum(1 for s in steps if s.status == "completed")
        skipped = sum(1 for s in steps if s.status == "skipped")
        failed = sum(1 for s in steps if s.status == "failed")
        steps.append(_step(
            8, "monitor_execution", "completed",
            f"{completed} completed, {skipped} skipped, {failed} failed"
        ))

        # Step 9 — Escalation Check (Phase 6 scope for escalation system)
        escalation_points: list[str] = []
        if not engine_result.recommendations:
            escalation_points.append("No recommendations generated")
        for rec in engine_result.recommendations:
            if rec.confidence == "low":
                escalation_points.append(
                    f"Low confidence: {rec.title}"
                )
        if data.business is None:
            escalation_points.append("No business data loaded")

        if escalation_points:
            detail = f"{len(escalation_points)} escalation point(s): " + "; ".join(escalation_points)
        else:
            detail = "No escalation points identified"
        steps.append(_step(9, "escalation_check", "completed", detail))

        # Step 10 — Record Outcomes
        steps.append(_step(10, "record_outcomes", "completed",
                           f"CEO review complete for {business_id}"))

        # Compute execution status
        execution_status = self._compute_execution_status(
            steps, loader_warnings, engine_warnings,
            generator_warnings, loop_warnings,
        )

        return CEOReviewResult(
            review_id=review_id,
            business_id=business_id,
            data=data,
            engine_result=engine_result,
            brief=generator_result.brief,
            steps=steps,
            execution_status=execution_status,
            loader_warnings=loader_warnings,
            engine_warnings=engine_warnings,
            generator_warnings=generator_warnings,
            loop_warnings=loop_warnings,
            started_at=started_at,
            completed_at=_now(),
        )

    @staticmethod
    def _compute_execution_status(
        steps: list[StepRecord],
        loader_warnings: list[str],
        engine_warnings: list[str],
        generator_warnings: list[str],
        loop_warnings: list[str],
    ) -> str:
        if any(s.status == "failed" for s in steps):
            return "failed"
        total_warnings = (
            len(loader_warnings) + len(engine_warnings)
            + len(generator_warnings) + len(loop_warnings)
        )
        if total_warnings > 0:
            return "completed_with_warnings"
        return "completed"


_REMAINING_STEPS: dict[int, str] = {
    2: "load_business_context",
    3: "discover_agents",
    4: "discover_services",
    5: "discover_extensions",
    6: "build_plan",
    7: "execute",
    8: "monitor_execution",
    9: "escalation_check",
    10: "record_outcomes",
}
