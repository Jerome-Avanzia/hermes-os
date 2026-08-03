"""Executive Context Graph — deterministic graph over Hermes business objects.

Computes related context for any business object by traversing
declaratively-defined relationships across registries and stores.

Amendment 1: Lives in hermes.context, not kernel — orchestrates services.
Amendment 2: Relationships defined declaratively in _EDGES dict.
Amendment 3: Every response includes an attention summary.
Amendment 4: Recursion guard via visited set.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from hermes.kernel.heartbeat_runtime import get_latest, list_heartbeats

logger = logging.getLogger(__name__)


# -- Owner matching (same logic as HermesService._owner_matches) ---------------


def _owner_matches(owner_str: str | None, person) -> bool:
    """Check if an owner string matches a person by ID or owner_aliases."""
    if not owner_str:
        return False
    lower = owner_str.strip().lower()
    if lower == person.id.lower():
        return True
    return any(alias.lower() == lower for alias in person.owner_aliases)


# -- Per-request data ----------------------------------------------------------


@dataclass(slots=True)
class GraphData:
    """Per-request data for graph resolution.

    Registries are long-lived; GraphData is assembled per request by the
    service layer to supply workspace-scoped operations, business data
    (KPIs, Decisions), computed notifications, and the heartbeat store.
    """

    workspace_id: str
    operations: list = field(default_factory=list)
    kpis: list = field(default_factory=list)
    decisions: list = field(default_factory=list)
    notifications: list = field(default_factory=list)
    heartbeat_store: Any = None
    repositories: list = field(default_factory=list)


# -- Internal resolution context -----------------------------------------------


@dataclass
class _Ctx:
    """Assembled once per resolve() call.  Holds all data + lookup indexes."""

    workspace_id: str
    all_goals: list
    all_people: list
    all_departments: list
    all_capabilities: list
    all_sops: list
    operations: list
    kpis: list
    decisions: list
    notifications: list
    heartbeat_store: Any
    repositories: list
    goal_index: dict
    person_index: dict
    dept_index: dict
    cap_index: dict
    sop_index: dict
    repo_index: dict


# -- Serializers ---------------------------------------------------------------


def _ser_goal(g) -> dict:
    return {"id": g.goal_id, "title": g.title, "owner": g.owner,
            "status": g.status, "target_date": g.target_date}


def _ser_person(p) -> dict:
    return {"id": p.id, "name": p.name, "title": p.title, "status": p.status}


def _ser_dept(d) -> dict:
    return {"id": d.id, "name": d.name, "status": d.status}


def _ser_cap(c) -> dict:
    return {"id": c.id, "name": c.name, "status": c.status}


def _ser_sop(s) -> dict:
    return {"id": s.id, "title": s.title, "status": s.status}


def _ser_op(o) -> dict:
    return {"id": o.id, "request": o.request, "status": o.status}


def _ser_decision(d) -> dict:
    return {"id": d.decision_id, "title": d.title, "status": d.status,
            "date": d.decision_date}


def _ser_kpi(k) -> dict:
    return {"id": k.kpi_id, "name": k.name, "current": k.current_value,
            "target": k.target_value, "unit": k.unit, "status": k.status}


def _ser_hb(hb) -> dict:
    return {"id": hb.id, "operation_id": hb.operation_id, "status": hb.status,
            "summary": hb.summary, "timestamp": hb.timestamp.isoformat()}


def _ser_notif(n) -> dict:
    return {"id": n.id, "title": n.title, "severity": n.severity,
            "category": n.category, "summary": n.summary}


def _ser_repo(r) -> dict:
    return {"id": r.id, "name": r.name, "provider": r.provider,
            "language": r.language, "url": r.url, "visibility": r.visibility}


# -- Object finders ------------------------------------------------------------


_FINDERS: dict[str, Callable] = {
    "goal":       lambda ctx, oid: ctx.goal_index.get(oid),
    "person":     lambda ctx, oid: ctx.person_index.get(oid),
    "department": lambda ctx, oid: ctx.dept_index.get(oid),
    "capability": lambda ctx, oid: ctx.cap_index.get(oid),
    "sop":        lambda ctx, oid: ctx.sop_index.get(oid),
    "operation":  lambda ctx, oid: next((o for o in ctx.operations if o.id == oid), None),
    "decision":   lambda ctx, oid: next((d for d in ctx.decisions if d.decision_id == oid), None),
    "kpi":        lambda ctx, oid: next((k for k in ctx.kpis if k.kpi_id == oid), None),
    "repository": lambda ctx, oid: ctx.repo_index.get(oid),
}

_SUMMARIZERS: dict[str, Callable] = {
    "goal":       _ser_goal,
    "person":     _ser_person,
    "department": _ser_dept,
    "capability": _ser_cap,
    "sop":        _ser_sop,
    "operation":  _ser_op,
    "decision":   _ser_decision,
    "kpi":        _ser_kpi,
    "repository": _ser_repo,
}


# -- Shared helpers ------------------------------------------------------------


def _people_by_owner(owner_str, ctx):
    """Find all people matching an owner string."""
    return [_ser_person(p) for p in ctx.all_people if _owner_matches(owner_str, p)]


def _latest_heartbeats(ops, ctx):
    """Get latest heartbeat per operation."""
    if not ctx.heartbeat_store:
        return []
    result = []
    for op in ops:
        hb = get_latest(ctx.heartbeat_store, ctx.workspace_id, op.id)
        if hb:
            result.append(_ser_hb(hb))
    return result


def _notifs_for_ids(object_ids, ctx):
    """Find notifications whose related_object_id is in the given set."""
    id_set = set(object_ids)
    return [_ser_notif(n) for n in ctx.notifications
            if n.related_object_id in id_set]


# -- Edge resolvers: Goal ------------------------------------------------------


def _goal_people(goal, ctx):
    return _people_by_owner(goal.owner, ctx)


def _goal_departments(goal, ctx):
    people = [p for p in ctx.all_people if _owner_matches(goal.owner, p)]
    dept_ids: set[str] = set()
    for p in people:
        dept_ids.update(p.department_ids)
    return [_ser_dept(ctx.dept_index[d]) for d in dept_ids if d in ctx.dept_index]


def _goal_capabilities(goal, ctx):
    return [_ser_cap(c) for c in ctx.all_capabilities
            if c.owner and c.owner.strip().lower() == goal.owner.strip().lower()]


def _goal_kpis(goal, ctx):
    return [_ser_kpi(k) for k in ctx.kpis if k.goal_id == goal.goal_id]


def _goal_decisions(goal, ctx):
    return [_ser_decision(d) for d in ctx.decisions if d.goal_id == goal.goal_id]


def _goal_operations(goal, ctx):
    dec_ids = {d.decision_id for d in ctx.decisions if d.goal_id == goal.goal_id}
    return [_ser_op(o) for o in ctx.operations
            if o.decision_id and o.decision_id in dec_ids]


def _goal_heartbeats(goal, ctx):
    dec_ids = {d.decision_id for d in ctx.decisions if d.goal_id == goal.goal_id}
    ops = [o for o in ctx.operations
           if o.decision_id and o.decision_id in dec_ids]
    return _latest_heartbeats(ops, ctx)


def _goal_notifications(goal, ctx):
    kpi_ids = [k.kpi_id for k in ctx.kpis if k.goal_id == goal.goal_id]
    dec_ids = {d.decision_id for d in ctx.decisions if d.goal_id == goal.goal_id}
    op_ids = [o.id for o in ctx.operations
              if o.decision_id and o.decision_id in dec_ids]
    return _notifs_for_ids(kpi_ids + op_ids, ctx)


# -- Edge resolvers: Person ----------------------------------------------------


def _person_departments(person, ctx):
    dept_ids = set(person.department_ids)
    for d in ctx.all_departments:
        if _owner_matches(d.owner, person):
            dept_ids.add(d.id)
    return [_ser_dept(ctx.dept_index[d]) for d in dept_ids if d in ctx.dept_index]


def _person_capabilities(person, ctx):
    return [_ser_cap(c) for c in ctx.all_capabilities
            if _owner_matches(c.owner, person)]


def _person_goals(person, ctx):
    return [_ser_goal(g) for g in ctx.all_goals
            if _owner_matches(g.owner, person)]


def _person_operations(person, ctx):
    return [_ser_op(o) for o in ctx.operations
            if o.status in ("created", "executing")
            and _owner_matches(o.extra_fields.get("owner"), person)]


def _person_heartbeats(person, ctx):
    ops = [o for o in ctx.operations
           if o.status in ("created", "executing")
           and _owner_matches(o.extra_fields.get("owner"), person)]
    return _latest_heartbeats(ops, ctx)


def _person_notifications(person, ctx):
    op_ids = [o.id for o in ctx.operations
              if o.status in ("created", "executing")
              and _owner_matches(o.extra_fields.get("owner"), person)]
    return _notifs_for_ids(op_ids, ctx)


# -- Edge resolvers: Department ------------------------------------------------


def _dept_people(dept, ctx):
    return [_ser_person(p) for p in ctx.all_people
            if dept.id in p.department_ids or _owner_matches(dept.owner, p)]


def _dept_capabilities(dept, ctx):
    return [_ser_cap(c) for c in ctx.all_capabilities
            if c.department_id == dept.id]


def _dept_goals(dept, ctx):
    dept_people = [p for p in ctx.all_people if dept.id in p.department_ids]
    return [_ser_goal(g) for g in ctx.all_goals
            if any(_owner_matches(g.owner, p) for p in dept_people)]


def _dept_operations(dept, ctx):
    dept_people = [p for p in ctx.all_people if dept.id in p.department_ids]
    return [_ser_op(o) for o in ctx.operations
            if any(_owner_matches(o.extra_fields.get("owner"), p)
                   for p in dept_people)]


def _dept_sops(dept, ctx):
    caps = [c for c in ctx.all_capabilities if c.department_id == dept.id]
    sop_ids: set[str] = set()
    for c in caps:
        sop_ids.update(c.sop_refs)
    return [_ser_sop(ctx.sop_index[s]) for s in sop_ids if s in ctx.sop_index]


# -- Edge resolvers: Capability ------------------------------------------------


def _cap_departments(cap, ctx):
    dept = ctx.dept_index.get(cap.department_id)
    return [_ser_dept(dept)] if dept else []


def _cap_people(cap, ctx):
    return _people_by_owner(cap.owner, ctx)


def _cap_sops(cap, ctx):
    return [_ser_sop(ctx.sop_index[s]) for s in cap.sop_refs
            if s in ctx.sop_index]


def _cap_goals(cap, ctx):
    return [_ser_goal(g) for g in ctx.all_goals
            if cap.owner and g.owner
            and g.owner.strip().lower() == cap.owner.strip().lower()]


def _cap_operations(cap, ctx):
    sop_set = set(cap.sop_refs)
    return [_ser_op(o) for o in ctx.operations
            if o.sop_id and o.sop_id in sop_set]


# -- Edge resolvers: Operation -------------------------------------------------


def _op_decisions(op, ctx):
    if not op.decision_id:
        return []
    d = next((d for d in ctx.decisions if d.decision_id == op.decision_id), None)
    return [_ser_decision(d)] if d else []


def _op_goals(op, ctx):
    if not op.decision_id:
        return []
    d = next((d for d in ctx.decisions if d.decision_id == op.decision_id), None)
    if not d or not d.goal_id:
        return []
    g = ctx.goal_index.get(d.goal_id)
    return [_ser_goal(g)] if g else []


def _op_capabilities(op, ctx):
    if not op.sop_id:
        return []
    return [_ser_cap(c) for c in ctx.all_capabilities
            if op.sop_id in c.sop_refs]


def _op_departments(op, ctx):
    if not op.sop_id:
        return []
    caps = [c for c in ctx.all_capabilities if op.sop_id in c.sop_refs]
    dept_ids = {c.department_id for c in caps if c.department_id}
    return [_ser_dept(ctx.dept_index[d]) for d in dept_ids
            if d in ctx.dept_index]


def _op_people(op, ctx):
    return _people_by_owner(op.extra_fields.get("owner"), ctx)


def _op_sops(op, ctx):
    if not op.sop_id:
        return []
    s = ctx.sop_index.get(op.sop_id)
    return [_ser_sop(s)] if s else []


def _op_heartbeats(op, ctx):
    if not ctx.heartbeat_store:
        return []
    hbs = list_heartbeats(ctx.heartbeat_store, ctx.workspace_id, op.id)
    return [_ser_hb(hb) for hb in hbs]


def _op_notifications(op, ctx):
    return _notifs_for_ids([op.id], ctx)


# -- Edge resolvers: Decision --------------------------------------------------


def _dec_goals(dec, ctx):
    if not dec.goal_id:
        return []
    g = ctx.goal_index.get(dec.goal_id)
    return [_ser_goal(g)] if g else []


def _dec_people(dec, ctx):
    return _people_by_owner(dec.owner, ctx)


def _dec_operations(dec, ctx):
    return [_ser_op(o) for o in ctx.operations
            if o.decision_id == dec.decision_id]


def _dec_heartbeats(dec, ctx):
    ops = [o for o in ctx.operations if o.decision_id == dec.decision_id]
    return _latest_heartbeats(ops, ctx)


# -- Edge resolvers: KPI -------------------------------------------------------


def _kpi_goals(kpi, ctx):
    g = ctx.goal_index.get(kpi.goal_id)
    return [_ser_goal(g)] if g else []


def _kpi_people(kpi, ctx):
    return _people_by_owner(kpi.owner, ctx)


def _kpi_notifications(kpi, ctx):
    return _notifs_for_ids([kpi.kpi_id], ctx)


# -- Edge resolvers: SOP -------------------------------------------------------


def _sop_capabilities(sop, ctx):
    return [_ser_cap(c) for c in ctx.all_capabilities if sop.id in c.sop_refs]


def _sop_departments(sop, ctx):
    caps = [c for c in ctx.all_capabilities if sop.id in c.sop_refs]
    dept_ids = {c.department_id for c in caps if c.department_id}
    return [_ser_dept(ctx.dept_index[d]) for d in dept_ids
            if d in ctx.dept_index]


def _sop_operations(sop, ctx):
    return [_ser_op(o) for o in ctx.operations if o.sop_id == sop.id]


# -- Edge resolvers: Repository ------------------------------------------------


def _repo_capabilities(repo, ctx):
    return [_ser_cap(c) for c in ctx.all_capabilities
            if repo.id in c.repository_refs]


def _repo_people(repo, ctx):
    """People resolved through capabilities (Amendment 7)."""
    caps = [c for c in ctx.all_capabilities if repo.id in c.repository_refs]
    seen: set[str] = set()
    result = []
    for cap in caps:
        for p in ctx.all_people:
            if p.id not in seen and _owner_matches(cap.owner, p):
                seen.add(p.id)
                result.append(_ser_person(p))
    return result


def _repo_departments(repo, ctx):
    caps = [c for c in ctx.all_capabilities if repo.id in c.repository_refs]
    dept_ids = {c.department_id for c in caps if c.department_id}
    return [_ser_dept(ctx.dept_index[d]) for d in dept_ids
            if d in ctx.dept_index]


def _repo_goals(repo, ctx):
    """Goal→Repository via explicit Capability links only (Amendment 4, 8)."""
    caps = [c for c in ctx.all_capabilities if repo.id in c.repository_refs]
    if not caps:
        return []
    cap_owners = {c.owner.strip().lower() for c in caps if c.owner}
    return [_ser_goal(g) for g in ctx.all_goals
            if g.owner and g.owner.strip().lower() in cap_owners]


# -- Repository edges on existing types ----------------------------------------


def _goal_repositories(goal, ctx):
    """Goal→Repository via capabilities only (Amendment 4 — no owner inference)."""
    goal_caps = [c for c in ctx.all_capabilities
                 if c.owner and goal.owner
                 and c.owner.strip().lower() == goal.owner.strip().lower()]
    repo_ids: set[str] = set()
    for c in goal_caps:
        repo_ids.update(c.repository_refs)
    return [_ser_repo(ctx.repo_index[r]) for r in repo_ids
            if r in ctx.repo_index]


def _person_repositories(person, ctx):
    caps = [c for c in ctx.all_capabilities if _owner_matches(c.owner, person)]
    repo_ids: set[str] = set()
    for c in caps:
        repo_ids.update(c.repository_refs)
    return [_ser_repo(ctx.repo_index[r]) for r in repo_ids
            if r in ctx.repo_index]


def _dept_repositories(dept, ctx):
    caps = [c for c in ctx.all_capabilities if c.department_id == dept.id]
    repo_ids: set[str] = set()
    for c in caps:
        repo_ids.update(c.repository_refs)
    return [_ser_repo(ctx.repo_index[r]) for r in repo_ids
            if r in ctx.repo_index]


def _cap_repositories(cap, ctx):
    return [_ser_repo(ctx.repo_index[r]) for r in cap.repository_refs
            if r in ctx.repo_index]


# -- Declarative edge registry (Amendment 2) -----------------------------------


_EDGES: dict[str, dict[str, Callable]] = {
    "goal": {
        "people": _goal_people,
        "departments": _goal_departments,
        "capabilities": _goal_capabilities,
        "repositories": _goal_repositories,
        "kpis": _goal_kpis,
        "decisions": _goal_decisions,
        "operations": _goal_operations,
        "heartbeats": _goal_heartbeats,
        "notifications": _goal_notifications,
    },
    "person": {
        "departments": _person_departments,
        "capabilities": _person_capabilities,
        "repositories": _person_repositories,
        "goals": _person_goals,
        "operations": _person_operations,
        "heartbeats": _person_heartbeats,
        "notifications": _person_notifications,
    },
    "department": {
        "people": _dept_people,
        "capabilities": _dept_capabilities,
        "repositories": _dept_repositories,
        "goals": _dept_goals,
        "operations": _dept_operations,
        "sops": _dept_sops,
    },
    "capability": {
        "departments": _cap_departments,
        "people": _cap_people,
        "repositories": _cap_repositories,
        "sops": _cap_sops,
        "goals": _cap_goals,
        "operations": _cap_operations,
    },
    "operation": {
        "decisions": _op_decisions,
        "goals": _op_goals,
        "capabilities": _op_capabilities,
        "departments": _op_departments,
        "people": _op_people,
        "sops": _op_sops,
        "heartbeats": _op_heartbeats,
        "notifications": _op_notifications,
    },
    "decision": {
        "goals": _dec_goals,
        "people": _dec_people,
        "operations": _dec_operations,
        "heartbeats": _dec_heartbeats,
    },
    "kpi": {
        "goals": _kpi_goals,
        "people": _kpi_people,
        "notifications": _kpi_notifications,
    },
    "sop": {
        "capabilities": _sop_capabilities,
        "departments": _sop_departments,
        "operations": _sop_operations,
    },
    "repository": {
        "capabilities": _repo_capabilities,
        "people": _repo_people,
        "departments": _repo_departments,
        "goals": _repo_goals,
    },
}

ALL_RELATION_KEYS = frozenset({
    "goals", "people", "departments", "capabilities",
    "repositories", "operations", "decisions", "kpis", "sops",
    "heartbeats", "notifications",
})

SUPPORTED_TYPES = frozenset(_EDGES.keys())


# -- ContextGraph service ------------------------------------------------------


class ContextGraph:
    """Stateless service that resolves context for any business object.

    No persistence. No cache. No AI. Pure deterministic graph traversal
    over existing registries and stores.
    """

    def __init__(
        self,
        goal_registry,
        people_registry,
        department_registry,
        capability_registry,
        sop_registry,
    ) -> None:
        self._goals = goal_registry
        self._people = people_registry
        self._departments = department_registry
        self._capabilities = capability_registry
        self._sops = sop_registry

    def resolve(
        self,
        object_type: str,
        object_id: str,
        data: GraphData,
        _visited: set | None = None,
    ) -> dict | None:
        """Resolve context for a business object.

        Returns None if the object is not found.
        Raises ValueError for unknown object_type.

        Amendment 4: _visited set prevents accidental recursive expansion.
        """
        if object_type not in SUPPORTED_TYPES:
            raise ValueError(
                f"Unknown object type: {object_type}. "
                f"Supported: {', '.join(sorted(SUPPORTED_TYPES))}"
            )

        # Amendment 4: recursion guard
        if _visited is None:
            _visited = set()
        key = (object_type, object_id)
        if key in _visited:
            return None
        _visited.add(key)

        # Build internal context (loads all registries once)
        ctx = self._build_ctx(data)

        # Find source object
        finder = _FINDERS[object_type]
        obj = finder(ctx, object_id)
        if obj is None:
            return None

        # Serialize source object
        summary = _SUMMARIZERS[object_type](obj)

        # Resolve all edges declaratively (Amendment 2)
        edges = _EDGES[object_type]
        relations: dict[str, list[dict]] = {}
        for relation_name in ALL_RELATION_KEYS:
            resolver = edges.get(relation_name)
            relations[relation_name] = resolver(obj, ctx) if resolver else []

        # Amendment 3: compute attention summary
        notifs = relations.get("notifications", [])
        heartbeats_list = relations.get("heartbeats", [])
        attention = {
            "critical": sum(1 for n in notifs
                            if n.get("severity") == "critical"),
            "warning": sum(1 for n in notifs
                           if n.get("severity") == "warning"),
            "blocked": sum(1 for hb in heartbeats_list
                           if hb.get("status") == "blocked"),
        }

        return {
            "object_type": object_type,
            "object_id": object_id,
            "object_summary": summary,
            "attention": attention,
            **relations,
        }

    def _build_ctx(self, data: GraphData) -> _Ctx:
        all_goals = self._goals.list()
        all_people = self._people.list()
        all_departments = self._departments.list()
        all_capabilities = self._capabilities.list()
        all_sops = self._sops.list()

        return _Ctx(
            workspace_id=data.workspace_id,
            all_goals=all_goals,
            all_people=all_people,
            all_departments=all_departments,
            all_capabilities=all_capabilities,
            all_sops=all_sops,
            operations=data.operations,
            kpis=data.kpis,
            decisions=data.decisions,
            notifications=data.notifications,
            heartbeat_store=data.heartbeat_store,
            repositories=data.repositories,
            goal_index={g.goal_id: g for g in all_goals},
            person_index={p.id: p for p in all_people},
            dept_index={d.id: d for d in all_departments},
            cap_index={c.id: c for c in all_capabilities},
            sop_index={s.id: s for s in all_sops},
            repo_index={r.id: r for r in data.repositories},
        )
