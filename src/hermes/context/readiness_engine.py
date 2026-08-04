"""ReadinessEngine — scenario-based executive readiness evaluation.

Sprint 47: Answers "Is it safe to proceed?" by evaluating all Hermes
domains against declarative rules for named scenarios.

Amendment 1: Scenario-based evaluation (deployment, repository_merge, etc.)
Amendment 2: Declarative category rules (data-driven, extensible)
Amendment 3: NOT_APPLICABLE state for categories absent from a scenario
Amendment 4: Navigation metadata on every issue (object_type, object_id, reason)
Amendment 5: Deterministic Readiness Checklist ordered by priority
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from hermes.context.risk_engine import RiskEngine


# ── Status constants ────────────────────────────────────────────────────────

CATEGORY_STATUSES = frozenset({"ready", "warning", "blocked", "not_applicable"})
OVERALL_STATUSES = frozenset({"ready", "not_ready"})


# ── Data structures ─────────────────────────────────────────────────────────


@dataclass(slots=True)
class ReadinessSnapshot:
    """Frozen-in-time data bundle for readiness evaluation."""

    workspace_id: str
    services: list = field(default_factory=list)
    repositories: list = field(default_factory=list)
    workflows: list = field(default_factory=list)
    databases: list = field(default_factory=list)
    tables: list = field(default_factory=list)
    llm_providers: list = field(default_factory=list)
    models: list = field(default_factory=list)
    operations: list = field(default_factory=list)
    notifications: list = field(default_factory=list)
    capabilities: list = field(default_factory=list)
    goals: list = field(default_factory=list)
    people: list = field(default_factory=list)
    departments: list = field(default_factory=list)
    kpis: list = field(default_factory=list)
    decisions: list = field(default_factory=list)
    heartbeats_by_operation: dict = field(default_factory=dict)
    # Runtime configuration flags
    infrastructure_configured: bool = False
    github_configured: bool = False
    n8n_configured: bool = False
    nocodb_configured: bool = False
    llm_configured: bool = False


@dataclass(slots=True)
class ReadinessIssue:
    """A single blocker or warning with navigation metadata (Amendment 4)."""

    category: str
    object_type: str
    object_id: str
    name: str
    reason: str
    severity: str  # "blocker" | "warning"


@dataclass(slots=True)
class CategoryResult:
    """Evaluation result for a single readiness category."""

    category: str
    status: str  # "ready" | "warning" | "blocked" | "not_applicable"
    score: int  # 0-100
    blockers: list[ReadinessIssue] = field(default_factory=list)
    warnings: list[ReadinessIssue] = field(default_factory=list)
    checked: int = 0
    healthy: int = 0


@dataclass(slots=True)
class ChecklistItem:
    """A single item in the executive readiness checklist (Amendment 5)."""

    priority: int
    category: str
    check: str
    status: str  # "pass" | "fail" | "warning" | "skip"
    issues: list[ReadinessIssue] = field(default_factory=list)


@dataclass(slots=True)
class ReadinessReport:
    """Complete readiness evaluation result."""

    workspace_id: str
    scenario: str
    scenario_label: str
    overall_status: str  # "ready" | "not_ready"
    overall_score: int  # 0-100
    categories: dict[str, CategoryResult] = field(default_factory=dict)
    blockers: list[ReadinessIssue] = field(default_factory=list)
    warnings: list[ReadinessIssue] = field(default_factory=list)
    checklist: list[ChecklistItem] = field(default_factory=list)
    critical_dependencies: int = 0
    timestamp: str = ""


# ── All category names ──────────────────────────────────────────────────────

ALL_CATEGORIES = frozenset({
    "infrastructure", "repositories", "automation", "data",
    "ai", "operations", "business", "goals",
})


# ── Scenarios (Amendment 1) ─────────────────────────────────────────────────
#
# Each scenario defines which categories are relevant and their weights.
# Categories absent from a scenario's weights are NOT_APPLICABLE.

SCENARIOS: dict[str, dict[str, Any]] = {
    "deployment": {
        "label": "Deployment",
        "description": "Full system deployment readiness",
        "weights": {
            "infrastructure": 20,
            "repositories": 5,
            "automation": 10,
            "data": 10,
            "ai": 10,
            "operations": 20,
            "business": 10,
            "goals": 15,
        },
    },
    "repository_merge": {
        "label": "Repository Merge",
        "description": "Code merge readiness",
        "weights": {
            "repositories": 25,
            "automation": 20,
            "operations": 20,
            "business": 15,
            "goals": 20,
        },
    },
    "workflow_activation": {
        "label": "Workflow Activation",
        "description": "Workflow enablement readiness",
        "weights": {
            "automation": 25,
            "infrastructure": 20,
            "operations": 20,
            "data": 20,
            "ai": 15,
        },
    },
    "database_maintenance": {
        "label": "Database Maintenance",
        "description": "Database maintenance readiness",
        "weights": {
            "data": 30,
            "infrastructure": 20,
            "operations": 20,
            "automation": 15,
            "business": 15,
        },
    },
    "provider_switch": {
        "label": "Provider Switch",
        "description": "AI provider change readiness",
        "weights": {
            "ai": 30,
            "business": 20,
            "operations": 20,
            "automation": 15,
            "infrastructure": 15,
        },
    },
}

SCENARIO_NAMES = frozenset(SCENARIOS.keys())


# ── Declarative category rules (Amendment 2) ────────────────────────────────
#
# Structure mirrors ContextGraph's _EDGES — pure data, no embedded logic.
#
# Each category has:
#   configured_flag: field on ReadinessSnapshot (None = always available)
#   sources: list of source specs
#     source_field: field on ReadinessSnapshot
#     object_type: for navigation metadata (Amendment 4)
#     id_field: field on object dict for object_id
#     name_field: field on object dict for display name
#     blockers: list of (field, value, message_template) tuples
#     warnings: list of (field, value, message_template) tuples
#   checklist_label: label for checklist item (Amendment 5)

CATEGORY_RULES: dict[str, dict[str, Any]] = {
    "infrastructure": {
        "configured_flag": "infrastructure_configured",
        "sources": [
            {
                "source_field": "services",
                "object_type": "service",
                "id_field": "id",
                "name_field": "name",
                "blockers": [
                    ("health", "unhealthy", "Service '{name}' is unhealthy"),
                    ("resource_state", "critical",
                     "Service '{name}' has critical resource usage"),
                ],
                "warnings": [
                    ("status", "restarting", "Service '{name}' is restarting"),
                    ("status", "paused", "Service '{name}' is paused"),
                    ("resource_state", "elevated",
                     "Service '{name}' has elevated resource usage"),
                ],
            },
        ],
        "checklist_label": "Infrastructure health",
    },
    "repositories": {
        "configured_flag": "github_configured",
        "sources": [
            {
                "source_field": "repositories",
                "object_type": "repository",
                "id_field": "id",
                "name_field": "name",
                "blockers": [],
                "warnings": [],
            },
        ],
        "checklist_label": "Repository availability",
    },
    "automation": {
        "configured_flag": "n8n_configured",
        "sources": [
            {
                "source_field": "workflows",
                "object_type": "workflow",
                "id_field": "id",
                "name_field": "name",
                "blockers": [
                    ("attention_state", "critical",
                     "Workflow '{name}' has critical failures"),
                ],
                "warnings": [
                    ("attention_state", "warning",
                     "Workflow '{name}' has execution warnings"),
                    ("status", "inactive",
                     "Workflow '{name}' is inactive"),
                ],
            },
        ],
        "checklist_label": "Automation health",
    },
    "data": {
        "configured_flag": "nocodb_configured",
        "sources": [
            {
                "source_field": "databases",
                "object_type": "database",
                "id_field": "id",
                "name_field": "name",
                "blockers": [
                    ("health_state", "degraded",
                     "Database '{name}' health is degraded"),
                ],
                "warnings": [
                    ("health_state", "unknown",
                     "Database '{name}' health is unknown"),
                ],
            },
            {
                "source_field": "tables",
                "object_type": "table",
                "id_field": "id",
                "name_field": "name",
                "blockers": [
                    ("attention_state", "critical",
                     "Table '{name}' has structural issues"),
                ],
                "warnings": [
                    ("attention_state", "warning",
                     "Table '{name}' has metadata warnings"),
                ],
            },
        ],
        "checklist_label": "Data integrity",
    },
    "ai": {
        "configured_flag": "llm_configured",
        "sources": [
            {
                "source_field": "llm_providers",
                "object_type": "llm_provider",
                "id_field": "id",
                "name_field": "name",
                "blockers": [
                    ("health_state", "unreachable",
                     "LLM provider '{name}' is unreachable"),
                ],
                "warnings": [
                    ("health_state", "degraded",
                     "LLM provider '{name}' is degraded"),
                    ("health_state", "unconfigured",
                     "LLM provider '{name}' is unconfigured"),
                ],
            },
            {
                "source_field": "models",
                "object_type": "model",
                "id_field": "id",
                "name_field": "name",
                "blockers": [
                    ("attention_state", "critical",
                     "Model '{name}' provider is unreachable"),
                ],
                "warnings": [
                    ("attention_state", "warning",
                     "Model '{name}' is unavailable"),
                ],
            },
        ],
        "checklist_label": "AI provider availability",
    },
    "operations": {
        "configured_flag": None,
        "sources": [
            {
                "source_field": "operations",
                "object_type": "operation",
                "id_field": "id",
                "name_field": "request",
                "blockers": [
                    ("status", "failed",
                     "Operation '{name}' has failed"),
                    ("_heartbeat_status", "blocked",
                     "Operation '{name}' is blocked"),
                ],
                "warnings": [
                    ("status", "executing",
                     "Operation '{name}' is currently executing"),
                ],
            },
        ],
        "checklist_label": "Operations clear",
    },
    "business": {
        "configured_flag": None,
        "sources": [
            {
                "source_field": "capabilities",
                "object_type": "capability",
                "id_field": "id",
                "name_field": "name",
                "blockers": [
                    ("status", "deprecated",
                     "Capability '{name}' is deprecated"),
                ],
                "warnings": [
                    ("status", "experimental",
                     "Capability '{name}' is experimental"),
                    ("status", "draft",
                     "Capability '{name}' is in draft"),
                ],
            },
            {
                "source_field": "people",
                "object_type": "person",
                "id_field": "id",
                "name_field": "name",
                "blockers": [],
                "warnings": [
                    ("status", "inactive",
                     "Person '{name}' is inactive"),
                ],
            },
            {
                "source_field": "departments",
                "object_type": "department",
                "id_field": "id",
                "name_field": "name",
                "blockers": [],
                "warnings": [
                    ("status", "inactive",
                     "Department '{name}' is inactive"),
                ],
            },
        ],
        "checklist_label": "Business readiness",
    },
    "goals": {
        "configured_flag": None,
        "sources": [
            {
                "source_field": "goals",
                "object_type": "goal",
                "id_field": "id",
                "name_field": "title",
                "blockers": [
                    ("status", "at_risk",
                     "Goal '{name}' is at risk"),
                ],
                "warnings": [
                    ("status", "blocked",
                     "Goal '{name}' is blocked"),
                ],
            },
            {
                "source_field": "kpis",
                "object_type": "kpi",
                "id_field": "id",
                "name_field": "name",
                "blockers": [
                    ("status", "off_track",
                     "KPI '{name}' is off track"),
                ],
                "warnings": [
                    ("status", "at_risk",
                     "KPI '{name}' is at risk"),
                ],
            },
        ],
        "checklist_label": "Goal alignment",
    },
}

# Object types scored by RiskEngine for critical_dependencies count
_CRITICAL_SOURCES: list[tuple[str, str]] = [
    ("service", "services"),
    ("workflow", "workflows"),
    ("database", "databases"),
    ("table", "tables"),
    ("llm_provider", "llm_providers"),
    ("model", "models"),
    ("operation", "operations"),
    ("capability", "capabilities"),
    ("goal", "goals"),
]


# ── ReadinessEngine ─────────────────────────────────────────────────────────


class ReadinessEngine:
    """Scenario-based executive readiness evaluation.

    Consumes RiskEngine for critical-dependency counting.
    All category evaluation is driven by declarative CATEGORY_RULES.
    """

    def __init__(self, risk_engine: RiskEngine | None = None) -> None:
        self._risk = risk_engine or RiskEngine()

    def evaluate(
        self,
        snapshot: ReadinessSnapshot,
        scenario: str = "deployment",
    ) -> ReadinessReport:
        """Full readiness evaluation for a named scenario.

        Args:
            snapshot: Frozen-in-time data bundle.
            scenario: Scenario name (must be in SCENARIOS).

        Returns:
            ReadinessReport with overall status, categories, checklist.

        Raises:
            ValueError: Unknown scenario name.
        """
        scenario_def = SCENARIOS.get(scenario)
        if scenario_def is None:
            raise ValueError(
                f"Unknown scenario: {scenario}. "
                f"Valid: {', '.join(sorted(SCENARIO_NAMES))}"
            )

        weights = scenario_def["weights"]

        # Preprocess operations with heartbeat status
        enriched_snapshot = self._enrich_operations(snapshot)

        # Evaluate every category
        categories: dict[str, CategoryResult] = {}
        for cat_name in ALL_CATEGORIES:
            if cat_name in weights:
                categories[cat_name] = self._evaluate_category(
                    cat_name, enriched_snapshot,
                )
            else:
                categories[cat_name] = CategoryResult(
                    category=cat_name,
                    status="not_applicable",
                    score=0,
                )

        # Overall score = weighted average of scenario categories
        total_weight = sum(weights.values())
        weighted_sum = sum(
            weights[cat] * categories[cat].score
            for cat in weights
        )
        overall_score = round(weighted_sum / total_weight) if total_weight else 0

        # Any blocked category → not_ready
        any_blocked = any(
            categories[cat].status == "blocked"
            for cat in weights
        )
        overall_status = "not_ready" if any_blocked else "ready"

        # Aggregate issues from scenario categories only
        all_blockers: list[ReadinessIssue] = []
        all_warnings: list[ReadinessIssue] = []
        for cat in weights:
            all_blockers.extend(categories[cat].blockers)
            all_warnings.extend(categories[cat].warnings)

        # Checklist ordered by priority (Amendment 5)
        checklist = self._build_checklist(categories, weights)

        # Critical dependencies via RiskEngine
        critical_count = self._count_critical(snapshot)

        return ReadinessReport(
            workspace_id=snapshot.workspace_id,
            scenario=scenario,
            scenario_label=scenario_def["label"],
            overall_status=overall_status,
            overall_score=overall_score,
            categories=categories,
            blockers=all_blockers,
            warnings=all_warnings,
            checklist=checklist,
            critical_dependencies=critical_count,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    def evaluate_category(
        self,
        snapshot: ReadinessSnapshot,
        category: str,
        scenario: str = "deployment",
    ) -> CategoryResult:
        """Evaluate a single category within a scenario context.

        Returns NOT_APPLICABLE if the category is absent from the scenario.
        """
        if category not in ALL_CATEGORIES:
            raise ValueError(
                f"Unknown category: {category}. "
                f"Valid: {', '.join(sorted(ALL_CATEGORIES))}"
            )
        scenario_def = SCENARIOS.get(scenario)
        if scenario_def is None:
            raise ValueError(
                f"Unknown scenario: {scenario}. "
                f"Valid: {', '.join(sorted(SCENARIO_NAMES))}"
            )

        if category not in scenario_def["weights"]:
            return CategoryResult(
                category=category,
                status="not_applicable",
                score=0,
            )

        enriched_snapshot = self._enrich_operations(snapshot)
        return self._evaluate_category(category, enriched_snapshot)

    # ── Internal evaluation ─────────────────────────────────────────────────

    def _evaluate_category(
        self,
        category_name: str,
        snapshot: ReadinessSnapshot,
    ) -> CategoryResult:
        """Apply declarative rules to evaluate a single category."""
        rules = CATEGORY_RULES[category_name]
        configured_flag = rules["configured_flag"]

        # Check runtime configuration
        if configured_flag is not None:
            is_configured = getattr(snapshot, configured_flag, False)
            if not is_configured:
                label = rules["checklist_label"]
                warning = ReadinessIssue(
                    category=category_name,
                    object_type="runtime",
                    object_id=category_name,
                    name=label,
                    reason=f"{label} runtime is not configured",
                    severity="warning",
                )
                return CategoryResult(
                    category=category_name,
                    status="warning",
                    score=0,
                    warnings=[warning],
                )

        blockers: list[ReadinessIssue] = []
        warnings: list[ReadinessIssue] = []
        total_checked = 0
        total_healthy = 0

        for source in rules["sources"]:
            objects = getattr(snapshot, source["source_field"], [])

            for obj in objects:
                total_checked += 1
                obj_id = str(obj.get(source["id_field"], ""))
                obj_name = str(obj.get(source["name_field"], obj_id))
                is_healthy = True

                # Check blocker rules
                for fld, val, msg_tpl in source["blockers"]:
                    if obj.get(fld) == val:
                        is_healthy = False
                        blockers.append(ReadinessIssue(
                            category=category_name,
                            object_type=source["object_type"],
                            object_id=obj_id,
                            name=obj_name,
                            reason=msg_tpl.format(name=obj_name, id=obj_id),
                            severity="blocker",
                        ))
                        break  # one blocker per object

                # Check warning rules only if no blocker matched
                if is_healthy:
                    for fld, val, msg_tpl in source["warnings"]:
                        if obj.get(fld) == val:
                            is_healthy = False
                            warnings.append(ReadinessIssue(
                                category=category_name,
                                object_type=source["object_type"],
                                object_id=obj_id,
                                name=obj_name,
                                reason=msg_tpl.format(
                                    name=obj_name, id=obj_id,
                                ),
                                severity="warning",
                            ))
                            break  # one warning per object

                if is_healthy:
                    total_healthy += 1

        # Determine status
        if blockers:
            status = "blocked"
        elif warnings:
            status = "warning"
        else:
            status = "ready"

        # Compute score
        if total_checked > 0:
            score = round((total_healthy / total_checked) * 100)
        else:
            score = 100  # nothing to check = all clear

        return CategoryResult(
            category=category_name,
            status=status,
            score=score,
            blockers=blockers,
            warnings=warnings,
            checked=total_checked,
            healthy=total_healthy,
        )

    @staticmethod
    def _enrich_operations(snapshot: ReadinessSnapshot) -> ReadinessSnapshot:
        """Add _heartbeat_status to active operations for declarative rules.

        Returns a new snapshot with enriched operation copies.
        Does not mutate the original snapshot.
        """
        if not snapshot.heartbeats_by_operation:
            return snapshot

        enriched_ops = []
        for op in snapshot.operations:
            op_copy = dict(op)
            op_id = op.get("id", "")
            # Only check heartbeats for active operations
            if op.get("status") in ("executing", "created"):
                hbs = snapshot.heartbeats_by_operation.get(op_id, [])
                if hbs:
                    latest_status = hbs[0].get("status", "")
                    if latest_status == "blocked":
                        op_copy["_heartbeat_status"] = "blocked"
            enriched_ops.append(op_copy)

        # Shallow copy snapshot with enriched operations
        return ReadinessSnapshot(
            workspace_id=snapshot.workspace_id,
            services=snapshot.services,
            repositories=snapshot.repositories,
            workflows=snapshot.workflows,
            databases=snapshot.databases,
            tables=snapshot.tables,
            llm_providers=snapshot.llm_providers,
            models=snapshot.models,
            operations=enriched_ops,
            notifications=snapshot.notifications,
            capabilities=snapshot.capabilities,
            goals=snapshot.goals,
            people=snapshot.people,
            departments=snapshot.departments,
            kpis=snapshot.kpis,
            decisions=snapshot.decisions,
            heartbeats_by_operation=snapshot.heartbeats_by_operation,
            infrastructure_configured=snapshot.infrastructure_configured,
            github_configured=snapshot.github_configured,
            n8n_configured=snapshot.n8n_configured,
            nocodb_configured=snapshot.nocodb_configured,
            llm_configured=snapshot.llm_configured,
        )

    def _build_checklist(
        self,
        categories: dict[str, CategoryResult],
        weights: dict[str, int],
    ) -> list[ChecklistItem]:
        """Build ordered checklist from category results (Amendment 5).

        Priority 1 = highest weight category. Only scenario categories
        appear in the checklist — NOT_APPLICABLE categories are excluded.
        """
        # Sort by weight descending
        sorted_cats = sorted(
            weights.items(),
            key=lambda x: x[1],
            reverse=True,
        )

        items: list[ChecklistItem] = []
        for priority, (cat_name, _weight) in enumerate(sorted_cats, start=1):
            result = categories[cat_name]
            label = CATEGORY_RULES.get(cat_name, {}).get(
                "checklist_label", cat_name,
            )

            if result.status == "blocked":
                status = "fail"
                n = len(result.blockers)
                check = f"{label} — {n} blocking issue{'s' if n != 1 else ''}"
            elif result.status == "warning":
                status = "warning"
                n = len(result.warnings)
                check = f"{label} — {n} warning{'s' if n != 1 else ''}"
            elif result.status == "not_applicable":
                status = "skip"
                check = f"{label} — not applicable"
            else:
                status = "pass"
                check = f"{label} — all clear"

            items.append(ChecklistItem(
                priority=priority,
                category=cat_name,
                check=check,
                status=status,
                issues=result.blockers + result.warnings,
            ))

        return items

    def _count_critical(self, snapshot: ReadinessSnapshot) -> int:
        """Count objects at critical risk level via RiskEngine."""
        count = 0
        for obj_type, source_field in _CRITICAL_SOURCES:
            for obj in getattr(snapshot, source_field, []):
                level, _ = self._risk.score_object(obj_type, obj)
                if level == "critical":
                    count += 1
        return count
