"""RiskEngine — deterministic risk scoring and executive summary generation.

Amendment 1: Extracted from ImpactEngine. Responsible only for:
- Intrinsic risk scoring per object type
- Inherited risk propagation
- Informational risk boosts (notifications, heartbeats, KPIs)
- Executive summary generation with ImpactCoverage (Amendment 4)

No AI. No heuristics beyond deterministic rules on observable state.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ── Risk levels ──────────────────────────────────────────────────────────────

RISK_LEVELS = frozenset({"none", "low", "medium", "high", "critical"})
RISK_ORDER: dict[str, int] = {
    "none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4,
}
_RISK_FROM_ORDER = {v: k for k, v in RISK_ORDER.items()}

ESTIMATED_IMPACT_LEVELS = frozenset({
    "none", "low", "moderate", "significant", "severe",
})


# ── Data structures ─────────────────────────────────────────────────────────


@dataclass(slots=True)
class ImpactedObject:
    """A single object affected by the analysis source."""

    object_type: str
    object_id: str
    name: str
    risk_level: str                   # "none"|"low"|"medium"|"high"|"critical"
    risk_reasons: list[str]
    depth: int                        # traversal depth from source (0 = source)
    path: list[str]                   # ["repository:hermes-os", "capability:nlp", ...]
    relationship_reason: str = ""     # Amendment 2: why this object appears


@dataclass(slots=True)
class ImpactCoverage:
    """Amendment 4: Deterministic analysis quality metrics."""

    types_analyzed: int = 0           # how many distinct types were encountered
    types_available: int = 0          # total supported types
    objects_analyzed: int = 0         # total objects visited
    relationships_traversed: int = 0  # total edges followed
    unknown_references: int = 0       # refs pointing to objects not found
    broken_links: list[str] = field(default_factory=list)  # specific broken refs


@dataclass(slots=True)
class ImpactSummary:
    """Executive summary of the impact analysis."""

    source_type: str
    source_id: str
    source_name: str
    estimated_impact: str
    affected_goals: list[str]
    affected_operations: list[str]
    affected_people: list[str]
    critical_dependencies: int
    blocking_risks: list[str]
    recommended_checks: list[str]
    safe_to_proceed: bool
    coverage: ImpactCoverage = field(default_factory=ImpactCoverage)


@dataclass(slots=True)
class ImpactReport:
    """Complete impact analysis result."""

    source: ImpactedObject
    summary: ImpactSummary
    affected: dict[str, list[ImpactedObject]]
    total_affected: int
    max_depth_reached: int
    cycle_detected: bool
    direction: str = "forward"        # Amendment 3: "forward" or "reverse"


# ── Intrinsic risk scoring ───────────────────────────────────────────────────


def _intrinsic_risk_goal(s: dict) -> tuple[str, list[str]]:
    status = s.get("status", "")
    if status == "at_risk":
        return "high", ["Goal status is at_risk"]
    if status == "blocked":
        return "medium", ["Goal status is blocked"]
    if status == "completed":
        return "none", []
    if status == "active":
        return "low", []
    return "none", []


def _intrinsic_risk_person(s: dict) -> tuple[str, list[str]]:
    if s.get("status") == "inactive":
        return "high", ["Person is inactive"]
    return "none", []


def _intrinsic_risk_department(s: dict) -> tuple[str, list[str]]:
    if s.get("status") == "inactive":
        return "high", ["Department is inactive"]
    return "none", []


def _intrinsic_risk_capability(s: dict) -> tuple[str, list[str]]:
    status = s.get("status", "")
    if status == "deprecated":
        return "critical", ["Capability is deprecated"]
    if status == "experimental":
        return "medium", ["Capability is experimental"]
    if status == "draft":
        return "low", ["Capability is in draft"]
    return "none", []


def _intrinsic_risk_operation(s: dict) -> tuple[str, list[str]]:
    status = s.get("status", "")
    if status == "failed":
        return "critical", ["Operation has failed"]
    if status == "executing":
        return "medium", ["Operation is currently executing"]
    if status == "created":
        return "low", ["Operation is pending execution"]
    return "none", []


def _intrinsic_risk_decision(s: dict) -> tuple[str, list[str]]:
    status = s.get("status", "")
    if status == "proposed":
        return "low", ["Decision is pending review"]
    return "none", []


def _intrinsic_risk_kpi(s: dict) -> tuple[str, list[str]]:
    status = s.get("status", "")
    if status == "off_track":
        return "high", ["KPI is off track"]
    if status == "at_risk":
        return "medium", ["KPI is at risk"]
    return "none", []


def _intrinsic_risk_sop(s: dict) -> tuple[str, list[str]]:
    status = s.get("status", "")
    if status == "deprecated":
        return "high", ["SOP is deprecated"]
    if status == "archived":
        return "medium", ["SOP is archived"]
    return "none", []


def _intrinsic_risk_repository(s: dict) -> tuple[str, list[str]]:
    return "none", []


def _intrinsic_risk_service(s: dict) -> tuple[str, list[str]]:
    health = s.get("health", "")
    resource = s.get("resource_state", "")
    reasons = []
    level = "none"
    if health == "unhealthy":
        level = "critical"
        reasons.append("Service is unhealthy")
    elif resource == "critical":
        level = "critical"
        reasons.append("Service has critical resource usage")
    elif resource == "elevated":
        level = "high"
        reasons.append("Service has elevated resource usage")
    elif s.get("status") == "restarting":
        level = "medium"
        reasons.append("Service is restarting")
    elif s.get("status") == "paused":
        level = "low"
        reasons.append("Service is paused")
    return level, reasons


def _intrinsic_risk_workflow(s: dict) -> tuple[str, list[str]]:
    att = s.get("attention_state", "")
    if att == "critical":
        return "critical", ["Workflow has recent failures"]
    if att == "warning":
        return "medium", ["Workflow has execution warnings"]
    status = s.get("status", "")
    if status == "inactive":
        return "low", ["Workflow is inactive"]
    return "none", []


def _intrinsic_risk_database(s: dict) -> tuple[str, list[str]]:
    health = s.get("health_state", "")
    if health == "degraded":
        return "high", ["Database health is degraded"]
    if health == "unknown":
        return "medium", ["Database health is unknown"]
    return "none", []


def _intrinsic_risk_table(s: dict) -> tuple[str, list[str]]:
    att = s.get("attention_state", "")
    if att == "critical":
        return "critical", ["Table has structural issues"]
    if att == "warning":
        return "medium", ["Table has metadata warnings"]
    return "none", []


def _intrinsic_risk_llm_provider(s: dict) -> tuple[str, list[str]]:
    health = s.get("health_state", "")
    if health == "unreachable":
        return "critical", ["LLM provider is unreachable"]
    if health == "degraded":
        return "high", ["LLM provider is degraded"]
    if health == "unconfigured":
        return "medium", ["LLM provider is unconfigured"]
    return "none", []


def _intrinsic_risk_model(s: dict) -> tuple[str, list[str]]:
    att = s.get("attention_state", "")
    if att == "critical":
        return "critical", ["Model provider is unreachable"]
    if att == "warning":
        return "medium", ["Model is unavailable"]
    return "none", []


_RISK_SCORERS: dict[str, object] = {
    "goal": _intrinsic_risk_goal,
    "person": _intrinsic_risk_person,
    "department": _intrinsic_risk_department,
    "capability": _intrinsic_risk_capability,
    "operation": _intrinsic_risk_operation,
    "decision": _intrinsic_risk_decision,
    "kpi": _intrinsic_risk_kpi,
    "sop": _intrinsic_risk_sop,
    "repository": _intrinsic_risk_repository,
    "service": _intrinsic_risk_service,
    "workflow": _intrinsic_risk_workflow,
    "database": _intrinsic_risk_database,
    "table": _intrinsic_risk_table,
    "llm_provider": _intrinsic_risk_llm_provider,
    "model": _intrinsic_risk_model,
}


# ── RiskEngine ───────────────────────────────────────────────────────────────


class RiskEngine:
    """Deterministic risk scoring and executive summary generation.

    Amendment 1: Separated from ImpactEngine. Receives an impact graph
    (list of ImpactedObjects) and scores each one, then generates the
    executive summary.
    """

    def score_object(
        self,
        object_type: str,
        object_summary: dict,
        context_result: dict | None = None,
    ) -> tuple[str, list[str]]:
        """Score intrinsic risk for a single object with informational boosts.

        Returns (risk_level, risk_reasons).
        """
        scorer = _RISK_SCORERS.get(object_type)
        if not scorer:
            return "none", []

        level, reasons = scorer(object_summary)

        # Informational boosts from context result
        if context_result:
            level, reasons = self._apply_boosts(level, reasons, context_result)

        return level, reasons

    def propagate_risk(
        self,
        intrinsic_level: str,
        dependency_levels: list[str],
    ) -> str:
        """Compute effective risk from intrinsic + dependencies.

        A dependency at risk N contributes N-1 to its parent.
        Final risk = max(intrinsic, max(attenuated dependency risks)).
        """
        intrinsic_ord = RISK_ORDER.get(intrinsic_level, 0)
        max_dep = 0
        for dep_level in dependency_levels:
            dep_ord = RISK_ORDER.get(dep_level, 0)
            attenuated = max(0, dep_ord - 1)
            max_dep = max(max_dep, attenuated)
        final_ord = max(intrinsic_ord, max_dep)
        return _RISK_FROM_ORDER.get(min(final_ord, 4), "critical")

    def generate_summary(
        self,
        source: ImpactedObject,
        affected: dict[str, list[ImpactedObject]],
        coverage: ImpactCoverage,
        direction: str = "forward",
    ) -> ImpactSummary:
        """Generate the executive summary from scored impact data."""
        # Collect affected IDs by type
        affected_goals = [
            o.object_id for o in affected.get("goal", [])
        ]
        affected_operations = [
            o.object_id for o in affected.get("operation", [])
        ]
        affected_people = [
            o.object_id for o in affected.get("person", [])
        ]

        # Count critical dependencies
        critical_count = 0
        for objs in affected.values():
            for o in objs:
                if o.risk_level == "critical":
                    critical_count += 1

        # Blocking risks — objects at high or critical
        blocking_risks: list[str] = []
        for objs in affected.values():
            for o in objs:
                if o.risk_level in ("critical", "high"):
                    label = f"{o.object_type.replace('_', ' ').title()} "
                    label += f"'{o.name}'"
                    if o.risk_reasons:
                        label += f" — {o.risk_reasons[0].lower()}"
                    label += f" ({o.risk_level})"
                    blocking_risks.append(label)

        # Recommended checks
        recommended = self._compute_recommended_checks(affected)

        # Safe to proceed
        safe = all(
            o.risk_level not in ("critical", "high")
            for objs in affected.values()
            for o in objs
        )

        # Estimated impact
        estimated = self._compute_estimated_impact(affected, affected_goals)

        return ImpactSummary(
            source_type=source.object_type,
            source_id=source.object_id,
            source_name=source.name,
            estimated_impact=estimated,
            affected_goals=affected_goals,
            affected_operations=affected_operations,
            affected_people=affected_people,
            critical_dependencies=critical_count,
            blocking_risks=blocking_risks,
            recommended_checks=recommended,
            safe_to_proceed=safe,
            coverage=coverage,
        )

    def _apply_boosts(
        self,
        level: str,
        reasons: list[str],
        context_result: dict,
    ) -> tuple[str, list[str]]:
        """Apply informational risk boosts from notifications/heartbeats/KPIs."""
        level_ord = RISK_ORDER.get(level, 0)
        reasons = list(reasons)  # copy

        # Critical notifications boost +1
        notifs = context_result.get("notifications", [])
        crit_notifs = sum(
            1 for n in notifs if n.get("severity") == "critical"
        )
        if crit_notifs > 0:
            level_ord = min(4, level_ord + 1)
            reasons.append(
                f"{crit_notifs} critical notification(s)"
            )

        # Blocked heartbeats boost +1
        heartbeats = context_result.get("heartbeats", [])
        blocked = sum(
            1 for hb in heartbeats if hb.get("status") == "blocked"
        )
        if blocked > 0:
            level_ord = min(4, level_ord + 1)
            reasons.append(
                f"{blocked} blocked heartbeat(s)"
            )

        # Off-track KPIs boost +1
        kpis = context_result.get("kpis", [])
        off_track = sum(
            1 for k in kpis if k.get("status") == "off_track"
        )
        if off_track > 0:
            level_ord = min(4, level_ord + 1)
            reasons.append(
                f"{off_track} off-track KPI(s)"
            )

        return _RISK_FROM_ORDER.get(level_ord, "critical"), reasons

    def _compute_estimated_impact(
        self,
        affected: dict[str, list[ImpactedObject]],
        affected_goals: list[str],
    ) -> str:
        """Determine estimated impact level from risk distribution."""
        total = sum(len(objs) for objs in affected.values())
        if total == 0:
            return "none"

        max_risk = 0
        for objs in affected.values():
            for o in objs:
                max_risk = max(max_risk, RISK_ORDER.get(o.risk_level, 0))

        # Any critical OR any goal at high+ → severe
        high_goals = any(
            o.risk_level in ("critical", "high")
            for o in affected.get("goal", [])
        )
        if max_risk >= RISK_ORDER["critical"] or high_goals:
            return "severe"

        # Any high OR total > 20 → significant
        if max_risk >= RISK_ORDER["high"] or total > 20:
            return "significant"

        # Any medium OR total > 10 → moderate
        if max_risk >= RISK_ORDER["medium"] or total > 10:
            return "moderate"

        # max low
        if max_risk >= RISK_ORDER["low"]:
            return "low"

        return "none"

    def _compute_recommended_checks(
        self,
        affected: dict[str, list[ImpactedObject]],
    ) -> list[str]:
        """Generate deterministic check suggestions."""
        checks: list[str] = []
        seen: set[str] = set()

        def _add(check: str) -> None:
            if check not in seen:
                seen.add(check)
                checks.append(check)

        for o in affected.get("service", []):
            if RISK_ORDER.get(o.risk_level, 0) >= RISK_ORDER["medium"]:
                _add("Verify infrastructure health before proceeding")
                break

        for o in affected.get("workflow", []):
            if RISK_ORDER.get(o.risk_level, 0) >= RISK_ORDER["medium"]:
                _add("Check automation execution logs for recent failures")
                break

        has_db_risk = any(
            RISK_ORDER.get(o.risk_level, 0) >= RISK_ORDER["medium"]
            for o in affected.get("database", [])
        )
        has_tbl_risk = any(
            RISK_ORDER.get(o.risk_level, 0) >= RISK_ORDER["medium"]
            for o in affected.get("table", [])
        )
        if has_db_risk or has_tbl_risk:
            _add("Verify data integrity in affected databases")

        has_llm_risk = any(
            RISK_ORDER.get(o.risk_level, 0) >= RISK_ORDER["medium"]
            for o in affected.get("llm_provider", [])
        )
        has_model_risk = any(
            RISK_ORDER.get(o.risk_level, 0) >= RISK_ORDER["medium"]
            for o in affected.get("model", [])
        )
        if has_llm_risk or has_model_risk:
            _add("Confirm AI provider availability")

        for o in affected.get("goal", []):
            if RISK_ORDER.get(o.risk_level, 0) >= RISK_ORDER["medium"]:
                _add("Review affected goal timelines with stakeholders")
                break

        for o in affected.get("operation", []):
            if o.risk_level == "medium":
                _add("Monitor in-progress operations for conflicts")
                break

        return checks
