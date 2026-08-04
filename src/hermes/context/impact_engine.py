"""ImpactEngine — dependency expansion and impact graph construction.

Answers "What are the consequences?" by consuming ContextGraph.resolve()
breadth-first, building an impact graph, and delegating risk scoring
to RiskEngine.

Amendment 1: Delegates all scoring to RiskEngine.
Amendment 2: Every affected object includes relationship_reason.
Amendment 3: Forward and Reverse traversal modes.
Amendment 4: ImpactCoverage in executive summary (via RiskEngine).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from hermes.context.risk_engine import (
    ImpactCoverage,
    ImpactedObject,
    ImpactReport,
    RiskEngine,
)

if TYPE_CHECKING:
    from hermes.context.context_graph import ContextGraph, GraphData

logger = logging.getLogger(__name__)


# Relations that propagate impact (dependency chains)
PROPAGATING_RELATIONS = frozenset({
    "goals", "people", "departments", "capabilities",
    "repositories", "services", "workflows",
    "databases", "tables", "operations",
    "llm_providers", "models",
})

# Relations that inform risk but don't expand the frontier
INFORMATIONAL_RELATIONS = frozenset({
    "decisions", "kpis", "sops",
    "heartbeats", "notifications",
})

# Mapping from plural relation key → singular object_type for ContextGraph
_RELATION_TO_TYPE: dict[str, str] = {
    "goals": "goal",
    "people": "person",
    "departments": "department",
    "capabilities": "capability",
    "repositories": "repository",
    "services": "service",
    "workflows": "workflow",
    "databases": "database",
    "tables": "table",
    "operations": "operation",
    "llm_providers": "llm_provider",
    "models": "model",
}

# Human-readable ID field per type (used to extract ID from context result dicts)
_ID_FIELD: dict[str, str] = {
    "goal": "id",
    "person": "id",
    "department": "id",
    "capability": "id",
    "repository": "id",
    "service": "id",
    "workflow": "id",
    "database": "id",
    "table": "id",
    "operation": "id",
    "decision": "id",
    "kpi": "id",
    "sop": "id",
    "llm_provider": "id",
    "model": "id",
}

# Human-readable name field per type
_NAME_FIELD: dict[str, str] = {
    "goal": "title",
    "person": "name",
    "department": "name",
    "capability": "name",
    "repository": "name",
    "service": "name",
    "workflow": "name",
    "database": "name",
    "table": "name",
    "operation": "request",
    "decision": "title",
    "kpi": "name",
    "sop": "title",
    "llm_provider": "name",
    "model": "name",
}

# Amendment 2: Relationship reason templates.
# Key: (source_type, relation_key) → reason template.
# Uses the generic pattern when no specific override exists.
_RELATIONSHIP_REASONS: dict[tuple[str, str], str] = {
    # Capability refs
    ("capability", "repositories"): "Capability references repository_refs",
    ("capability", "workflows"): "Capability references workflow_refs",
    ("capability", "tables"): "Capability references table_refs",
    ("capability", "models"): "Capability references model_refs",
    ("capability", "sops"): "Capability references sop_refs",
    ("capability", "departments"): "Capability belongs to department",
    ("capability", "people"): "Capability is owned by person",
    ("capability", "goals"): "Capability owner matches goal owner",
    # Repository → via capability refs
    ("repository", "capabilities"): "Capability references this repository in repository_refs",
    ("repository", "services"): "Service deploys from this repository",
    ("repository", "workflows"): "Workflow linked via shared capabilities",
    ("repository", "people"): "Person owns capability referencing this repository",
    ("repository", "departments"): "Department owns capability referencing this repository",
    ("repository", "goals"): "Goal owner matches capability referencing this repository",
    # Service → via repository_ref
    ("service", "repositories"): "Service references repository via repository_ref",
    ("service", "capabilities"): "Capability references service's repository",
    ("service", "people"): "Person owns capability linked to service's repository",
    ("service", "departments"): "Department owns capability linked to service's repository",
    ("service", "goals"): "Goal owner matches capability linked to service's repository",
    ("service", "workflows"): "Workflow linked via service's capability chain",
    ("service", "operations"): "Operation linked via service's capability chain",
    # Workflow → via capability refs
    ("workflow", "capabilities"): "Capability references this workflow in workflow_refs",
    ("workflow", "repositories"): "Repository linked via capabilities referencing this workflow",
    ("workflow", "services"): "Service linked via capability chain referencing this workflow",
    ("workflow", "people"): "Person owns capability referencing this workflow",
    ("workflow", "departments"): "Department owns capability referencing this workflow",
    ("workflow", "goals"): "Goal owner matches capability referencing this workflow",
    ("workflow", "operations"): "Operation triggered by this workflow",
    # Database/Table
    ("database", "tables"): "Table belongs to this database",
    ("database", "capabilities"): "Capability references tables in this database",
    ("table", "databases"): "Table belongs to this database",
    ("table", "capabilities"): "Capability references this table in table_refs",
    # LLM
    ("llm_provider", "models"): "Model is provided by this LLM provider",
    ("llm_provider", "capabilities"): "Capability references models from this provider",
    ("model", "llm_providers"): "Model is hosted by this provider",
    ("model", "capabilities"): "Capability references this model in model_refs",
    # Goal
    ("goal", "people"): "Person is goal owner",
    ("goal", "departments"): "Department contains goal owner",
    ("goal", "capabilities"): "Capability owner matches goal owner",
    ("goal", "kpis"): "KPI tracks this goal",
    ("goal", "decisions"): "Decision linked to this goal",
    ("goal", "operations"): "Operation linked via goal's decisions",
    # Person
    ("person", "goals"): "Person is goal owner",
    ("person", "capabilities"): "Person owns this capability",
    ("person", "departments"): "Person belongs to this department",
    ("person", "operations"): "Person owns this operation",
    # Department
    ("department", "people"): "Person belongs to this department",
    ("department", "capabilities"): "Capability belongs to this department",
    ("department", "goals"): "Goal owned by department member",
    ("department", "operations"): "Operation owned by department member",
    ("department", "sops"): "SOP linked via department's capabilities",
    # Operation
    ("operation", "decisions"): "Operation linked to this decision",
    ("operation", "goals"): "Operation linked to goal via decision",
    ("operation", "capabilities"): "Operation's SOP referenced by capability",
    ("operation", "departments"): "Operation linked to department via capability",
    ("operation", "people"): "Person owns this operation",
    ("operation", "sops"): "Operation executes this SOP",
    ("operation", "workflows"): "Operation triggered by this workflow",
}


def _get_relationship_reason(
    source_type: str,
    relation_key: str,
    target_type: str,
) -> str:
    """Amendment 2: Get human-readable reason for a relationship."""
    specific = _RELATIONSHIP_REASONS.get((source_type, relation_key))
    if specific:
        return specific
    # Generic fallback
    src_label = source_type.replace("_", " ").title()
    rel_label = relation_key.replace("_", " ")
    return f"{src_label} has related {rel_label}"


class ImpactEngine:
    """Dependency expansion and impact graph construction.

    Consumes ContextGraph.resolve() to expand dependencies breadth-first.
    Delegates all risk scoring to RiskEngine.

    Amendment 3: Supports forward and reverse traversal via the same
    ContextGraph — forward expands "what depends on this?" and reverse
    expands "what supports this?" Both use the same edge set since the
    ContextGraph already defines bidirectional edges declaratively.
    The direction label is informational; traversal logic is identical
    because ContextGraph edges are already symmetric for each type.
    """

    def __init__(
        self,
        context_graph: ContextGraph,
        risk_engine: RiskEngine | None = None,
    ) -> None:
        self._graph = context_graph
        self._risk = risk_engine or RiskEngine()

    def analyze(
        self,
        object_type: str,
        object_id: str,
        data: GraphData,
        max_depth: int = 3,
        direction: str = "forward",
    ) -> ImpactReport:
        """Compute full impact analysis for a business object.

        Args:
            object_type: The type of the source object.
            object_id: The ID of the source object.
            data: GraphData assembled by HermesService.
            max_depth: Maximum traversal depth (1-5).
            direction: "forward" (what depends on this?) or
                       "reverse" (what supports this?).

        Returns:
            ImpactReport with scored affected objects and executive summary.
        """
        max_depth = max(1, min(5, max_depth))

        # Resolve source object
        source_ctx = self._graph.resolve(object_type, object_id, data)
        if source_ctx is None:
            return self._empty_report(object_type, object_id, direction)

        source_summary = source_ctx.get("object_summary", {})
        source_name = source_summary.get(
            _NAME_FIELD.get(object_type, "name"), object_id
        )

        # Score source intrinsic risk
        src_risk, src_reasons = self._risk.score_object(
            object_type, source_summary, source_ctx,
        )

        source = ImpactedObject(
            object_type=object_type,
            object_id=object_id,
            name=source_name,
            risk_level=src_risk,
            risk_reasons=src_reasons,
            depth=0,
            path=[],
            relationship_reason="Analysis source",
        )

        # BFS expansion
        visited: set[tuple[str, str]] = {(object_type, object_id)}
        affected: dict[str, list[ImpactedObject]] = {}
        frontier: list[tuple[str, str, int, list[str], str, str]] = []
        # Each frontier entry: (obj_type, obj_id, depth, path, rel_key, parent_type)
        cycle_detected = False
        actual_max_depth = 0

        # Coverage tracking (Amendment 4)
        types_seen: set[str] = {object_type}
        relationships_traversed = 0
        unknown_refs = 0
        broken_links: list[str] = []

        # Seed frontier from source context
        if self._expand_frontier(
            source_ctx, object_type, object_id,
            0, [], frontier, visited,
            types_seen, direction,
        ):
            cycle_detected = True
        relationships_traversed += self._count_relations(source_ctx)

        # BFS loop
        while frontier:
            next_frontier: list[tuple[str, str, int, list[str], str, str]] = []

            for (otype, oid, depth, path, rel_key, parent_type) in frontier:
                if depth > max_depth:
                    continue

                actual_max_depth = max(actual_max_depth, depth)

                # Resolve this object's context
                obj_ctx = self._graph.resolve(otype, oid, data)
                if obj_ctx is None:
                    # Broken link — object referenced but not found
                    unknown_refs += 1
                    broken_links.append(f"{otype}:{oid}")
                    continue

                obj_summary = obj_ctx.get("object_summary", {})
                obj_name = obj_summary.get(
                    _NAME_FIELD.get(otype, "name"), oid
                )

                # Score risk
                risk_level, risk_reasons = self._risk.score_object(
                    otype, obj_summary, obj_ctx,
                )

                # Amendment 2: relationship reason
                reason = _get_relationship_reason(parent_type, rel_key, otype)

                impacted = ImpactedObject(
                    object_type=otype,
                    object_id=oid,
                    name=obj_name,
                    risk_level=risk_level,
                    risk_reasons=risk_reasons,
                    depth=depth,
                    path=path,
                    relationship_reason=reason,
                )

                if otype not in affected:
                    affected[otype] = []
                affected[otype].append(impacted)

                # Expand frontier if not at max depth
                if depth < max_depth:
                    if self._expand_frontier(
                        obj_ctx, otype, oid,
                        depth, path, next_frontier, visited,
                        types_seen, direction,
                    ):
                        cycle_detected = True
                    relationships_traversed += self._count_relations(obj_ctx)

            # Check for cycles: if any frontier items were already visited
            new_frontier = []
            for item in next_frontier:
                key = (item[0], item[1])
                if key in visited:
                    cycle_detected = True
                else:
                    visited.add(key)
                    new_frontier.append(item)

            frontier = new_frontier

        # Propagate inherited risk
        self._propagate_inherited_risk(source, affected)

        # Build coverage (Amendment 4)
        from hermes.context.context_graph import SUPPORTED_TYPES
        coverage = ImpactCoverage(
            types_analyzed=len(types_seen),
            types_available=len(SUPPORTED_TYPES),
            objects_analyzed=len(visited),
            relationships_traversed=relationships_traversed,
            unknown_references=unknown_refs,
            broken_links=broken_links,
        )

        # Generate executive summary
        summary = self._risk.generate_summary(
            source, affected, coverage, direction,
        )

        total = sum(len(objs) for objs in affected.values())

        return ImpactReport(
            source=source,
            summary=summary,
            affected=affected,
            total_affected=total,
            max_depth_reached=actual_max_depth,
            cycle_detected=cycle_detected,
            direction=direction,
        )

    def _expand_frontier(
        self,
        ctx_result: dict,
        source_type: str,
        source_id: str,
        current_depth: int,
        current_path: list[str],
        frontier: list[tuple[str, str, int, list[str], str, str]],
        visited: set[tuple[str, str]],
        types_seen: set[str],
        direction: str,
    ) -> bool:
        """Extract related objects from a context result and add to frontier.

        Returns True if a back-edge (cycle) to an already-visited node was found.
        """
        new_path = current_path + [f"{source_type}:{source_id}"]
        found_cycle = False

        for relation_key in PROPAGATING_RELATIONS:
            related = ctx_result.get(relation_key, [])
            if not related:
                continue

            target_type = _RELATION_TO_TYPE.get(relation_key)
            if not target_type:
                continue

            types_seen.add(target_type)

            for item in related:
                item_id = item.get("id", "")
                if not item_id:
                    continue

                key = (target_type, item_id)
                if key in visited:
                    found_cycle = True
                    continue

                frontier.append((
                    target_type,
                    item_id,
                    current_depth + 1,
                    new_path,
                    relation_key,
                    source_type,
                ))

        return found_cycle

    def _count_relations(self, ctx_result: dict) -> int:
        """Count total relation items in a context result."""
        count = 0
        for key in PROPAGATING_RELATIONS | INFORMATIONAL_RELATIONS:
            count += len(ctx_result.get(key, []))
        return count

    def _propagate_inherited_risk(
        self,
        source: ImpactedObject,
        affected: dict[str, list[ImpactedObject]],
    ) -> None:
        """Apply inherited risk propagation.

        Objects at greater depth inherit attenuated risk from their
        dependencies. A dependency at risk N contributes N-1 to parent.
        """
        # Build depth-ordered list of all impacted objects
        all_objects: list[ImpactedObject] = []
        for objs in affected.values():
            all_objects.extend(objs)

        if not all_objects:
            return

        # Group by depth (process deepest first for upward propagation)
        max_d = max(o.depth for o in all_objects)
        by_depth: dict[int, list[ImpactedObject]] = {}
        for o in all_objects:
            by_depth.setdefault(o.depth, []).append(o)

        # Build parent→children index from paths
        # Each child's path[-1] is "parent_type:parent_id"
        risk_at: dict[tuple[str, str], str] = {
            (source.object_type, source.object_id): source.risk_level,
        }
        for o in all_objects:
            risk_at[(o.object_type, o.object_id)] = o.risk_level

        # Propagate from deepest to shallowest
        for depth in range(max_d, 0, -1):
            for obj in by_depth.get(depth, []):
                # Find children of this object (depth+1 whose path ends with this)
                my_key = f"{obj.object_type}:{obj.object_id}"
                child_risks = []
                for child_depth_objs in [by_depth.get(depth + 1, [])]:
                    for child in child_depth_objs:
                        if child.path and child.path[-1] == my_key:
                            child_risks.append(child.risk_level)

                if child_risks:
                    new_level = self._risk.propagate_risk(
                        obj.risk_level, child_risks,
                    )
                    if new_level != obj.risk_level:
                        obj.risk_level = new_level
                        risk_at[(obj.object_type, obj.object_id)] = new_level

    def _empty_report(
        self,
        object_type: str,
        object_id: str,
        direction: str,
    ) -> ImpactReport:
        """Return an empty report when the source object is not found."""
        source = ImpactedObject(
            object_type=object_type,
            object_id=object_id,
            name=object_id,
            risk_level="none",
            risk_reasons=["Object not found"],
            depth=0,
            path=[],
            relationship_reason="Analysis source",
        )
        coverage = ImpactCoverage()
        summary = self._risk.generate_summary(source, {}, coverage, direction)
        return ImpactReport(
            source=source,
            summary=summary,
            affected={},
            total_affected=0,
            max_depth_reached=0,
            cycle_detected=False,
            direction=direction,
        )
