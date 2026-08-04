# Executive Intelligence

Canonical architecture document for the Hermes Executive Intelligence layer. Documents the system as it exists in Hermes v2.5.0.

---

## Purpose

Executive Intelligence answers the questions a founder asks before acting:

1. **What is connected to this?** -- ContextGraph
2. **What are the consequences?** -- ImpactEngine
3. **How risky is it?** -- RiskEngine
4. **Is it safe to proceed?** -- ReadinessEngine

These four engines form a deterministic analysis layer. They use no AI, no heuristics, and no probabilistic methods. Every result is reproducible given the same input data.

---

## Executive Questions

| Question | Engine | API Endpoint |
|----------|--------|-------------|
| Show me everything related to X | ContextGraph | `GET /v1/workspaces/{id}/context/{type}/{object_id}` |
| What happens if X changes? | ImpactEngine | `GET /v1/workspaces/{id}/impact/{type}/{object_id}` |
| What is the risk level of X? | RiskEngine | (consumed internally by ImpactEngine and ReadinessEngine) |
| Is it safe to deploy / merge / maintain? | ReadinessEngine | `GET /v1/workspaces/{id}/readiness?scenario=NAME` |
| Is category Y ready for scenario Z? | ReadinessEngine | `GET /v1/workspaces/{id}/readiness/{category}?scenario=NAME` |

RiskEngine has no direct API endpoint. It is a shared dependency consumed by ImpactEngine and ReadinessEngine.

---

## Executive Reasoning Flow

This is how a founder reasons about a decision:

```
Founder asks a question
        |
        v
   ContextGraph          "What is connected to this?"
        |                 Resolves all relationships for the object.
        v                 Produces a relationship map with attention summary.
   ImpactEngine           "What are the consequences?"
        |                 Expands dependencies breadth-first.
        v                 Builds an impact graph with scored objects.
   RiskEngine             "How risky is it?"
        |                 Scores each object deterministically.
        v                 Propagates risk through dependency chains.
   ReadinessEngine        "Is it safe to proceed?"
        |                 Evaluates scenario-specific readiness.
        v                 Produces blockers, warnings, and a checklist.
   Founder decides
```

This represents the logical reasoning sequence. Each step answers a progressively more actionable question. The founder may use any engine independently -- the sequence is not enforced.

---

## Implementation Dependency Graph

The engines do not form a linear pipeline. Their actual dependencies are:

```
                ContextGraph
               /            \
        ImpactEngine    ReadinessEngine
               \            /
               RiskEngine
```

- **ContextGraph** is a shared upstream dependency. Both ImpactEngine and ReadinessEngine consume it (ImpactEngine directly; ReadinessEngine indirectly through shared data assembly).
- **RiskEngine** is a shared downstream dependency. Both ImpactEngine and ReadinessEngine use it for risk scoring.
- **ImpactEngine and ReadinessEngine are peers.** They do not depend on each other. They answer fundamentally different questions ("what are the consequences?" vs "is it safe to proceed?") and are invoked independently.

The executive reasoning flow and the implementation dependency graph are intentionally different. The reasoning flow represents how a founder thinks. The dependency graph represents how the code is structured. Forcing them to match would create unnecessary coupling.

---

## Package Structure

All Executive Intelligence code lives in `src/hermes/context/`:

| File | Lines | Purpose |
|------|-------|---------|
| `__init__.py` | 3 | Barrel file (exports ContextGraph, GraphData, SUPPORTED_TYPES) |
| `context_graph.py` | 1,773 | Deterministic graph traversal |
| `impact_engine.py` | 518 | Dependency expansion and impact graph construction |
| `risk_engine.py` | 517 | Deterministic risk scoring and executive summary generation |
| `readiness_engine.py` | 791 | Scenario-based executive readiness evaluation |

---

## ContextGraph

### Purpose

Resolves relationships between business objects. Given any object, returns everything connected to it with an attention summary.

### Responsibilities

- Declarative edge resolution across 15 object types and 17 relation keys
- Recursive expansion with cycle prevention (visited set)
- Attention summary computation (critical notifications, warnings, blocked heartbeats)
- Object serialization for context delivery
- Owner matching by ID or alias

### Does not own

- Risk scoring
- Impact analysis
- Summary generation
- Side effects of any kind

### Inputs

| Parameter | Type | Description |
|-----------|------|-------------|
| `object_type` | `str` | One of 15 supported types |
| `object_id` | `str` | Unique identifier |
| `data` | `GraphData` | Per-request workspace-scoped data bundle |

**GraphData** contains:
- `workspace_id` -- scoping identifier
- `operations`, `kpis`, `decisions`, `notifications` -- business state
- `heartbeat_store` -- heartbeat persistence
- `repositories`, `services`, `workflows` -- infrastructure state
- `databases`, `tables` -- data state
- `llm_providers`, `models` -- AI state

### Outputs

Returns a `dict` containing:

```python
{
    "object_type": str,
    "object_id": str,
    "object_summary": dict,          # serialized object fields
    "attention": {
        "critical": int,             # critical notification count
        "warning": int,              # warning notification count
        "blocked": int,              # blocked heartbeat count
    },
    "goals": [...],                  # related goals
    "people": [...],                 # related people
    "departments": [...],            # related departments
    "capabilities": [...],           # related capabilities
    "repositories": [...],           # related repositories
    "services": [...],               # related services
    "workflows": [...],              # related workflows
    "databases": [...],              # related databases
    "tables": [...],                 # related tables
    "operations": [...],             # related operations
    "decisions": [...],              # related decisions
    "kpis": [...],                   # related KPIs
    "sops": [...],                   # related SOPs
    "heartbeats": [...],             # related heartbeats
    "notifications": [...],          # related notifications
    "llm_providers": [...],          # related LLM providers
    "models": [...],                 # related models
}
```

Returns `None` if the object is not found. Raises `ValueError` for unknown object types.

### Supported Object Types

```
goal, person, department, capability, operation, decision, kpi, sop,
repository, service, workflow, database, table, llm_provider, model
```

### Declarative Edge Registry

Relationships are defined in `_EDGES` -- a data dictionary mapping `(object_type, relation_key)` to resolver functions. Adding a new relationship is a data change, not a logic change.

### Ownership

Owned by the `context/` package. Instantiated lazily by `HermesService._get_context_graph()` from the five registries (goals, people, departments, capabilities, SOPs).

---

## ImpactEngine

### Purpose

Answers "What are the consequences?" by expanding dependencies breadth-first through ContextGraph, building an impact graph, and delegating risk scoring to RiskEngine.

### Responsibilities

- BFS traversal of the dependency graph via ContextGraph.resolve()
- Frontier management and cycle detection
- Path tracking (how each affected object was reached)
- Relationship reason annotation (Amendment 2)
- Coverage tracking (types analyzed, objects visited, broken links)
- Forward and reverse traversal modes (Amendment 3)

### Does not own

- Risk scoring (delegated to RiskEngine)
- Risk propagation logic (delegated to RiskEngine)
- Executive summary generation (delegated to RiskEngine)
- Graph resolution (delegated to ContextGraph)

### Inputs

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `object_type` | `str` | -- | Source object type |
| `object_id` | `str` | -- | Source object identifier |
| `data` | `GraphData` | -- | Per-request workspace data |
| `max_depth` | `int` | 3 | BFS depth limit (clamped 1-5) |
| `direction` | `str` | `"forward"` | `"forward"` (what depends on this?) or `"reverse"` (what supports this?) |

### Outputs

**ImpactReport:**

| Field | Type | Description |
|-------|------|-------------|
| `source` | `ImpactedObject` | The analyzed object with intrinsic risk |
| `summary` | `ImpactSummary` | Executive summary with estimated impact |
| `affected` | `dict[str, list[ImpactedObject]]` | Affected objects grouped by type |
| `total_affected` | `int` | Total count of affected objects |
| `max_depth_reached` | `int` | Actual maximum depth traversed |
| `cycle_detected` | `bool` | Whether cycles were found |
| `direction` | `str` | Direction used for analysis |

**ImpactedObject:**

| Field | Type | Description |
|-------|------|-------------|
| `object_type` | `str` | Type of affected object |
| `object_id` | `str` | Identifier |
| `name` | `str` | Display name |
| `risk_level` | `str` | `"none"` / `"low"` / `"medium"` / `"high"` / `"critical"` |
| `risk_reasons` | `list[str]` | Why this risk level was assigned |
| `depth` | `int` | Traversal depth from source (0 = source) |
| `path` | `list[str]` | Traversal chain (e.g., `["repository:hermes-os", "capability:nlp"]`) |
| `relationship_reason` | `str` | Why this object appears in the analysis |

**ImpactSummary:**

| Field | Type | Description |
|-------|------|-------------|
| `estimated_impact` | `str` | `"none"` / `"low"` / `"moderate"` / `"significant"` / `"severe"` |
| `affected_goals` | `list[str]` | Goal IDs affected |
| `affected_operations` | `list[str]` | Operation IDs affected |
| `affected_people` | `list[str]` | Person IDs affected |
| `critical_dependencies` | `int` | Count of critical-risk objects |
| `blocking_risks` | `list[str]` | High/critical risk descriptions |
| `recommended_checks` | `list[str]` | Deterministic action suggestions |
| `safe_to_proceed` | `bool` | True if no high/critical risks |
| `coverage` | `ImpactCoverage` | Analysis quality metrics |

### Relation Classification

ImpactEngine classifies ContextGraph relations into two categories:

**Propagating relations** (expand the BFS frontier):
```
goals, people, departments, capabilities, repositories, services,
workflows, databases, tables, operations, llm_providers, models
```

**Informational relations** (inform risk scoring but do not expand):
```
decisions, kpis, sops, heartbeats, notifications
```

### Ownership

Owned by the `context/` package. Instantiated per-request by `HermesService.impact()`. Receives a `ContextGraph` instance and an optional `RiskEngine` instance.

---

## RiskEngine

### Purpose

Deterministic risk scoring for individual objects and risk propagation through dependency chains.

### Responsibilities

- Intrinsic risk scoring per object type (15 type-specific scorers)
- Informational risk boosts from notifications, heartbeats, and KPIs
- Inherited risk propagation (attenuated from dependencies)
- Executive summary generation for impact analysis
- Estimated impact computation
- Recommended checks generation

### Does not own

- Graph traversal
- Dependency expansion
- Scenario evaluation
- Readiness assessment

### Intrinsic Risk Scoring

Each of the 15 object types has a dedicated scoring function that maps observable state to a risk level:

| Object Type | Risk Triggers (examples) |
|-------------|-------------------------|
| goal | `at_risk` -> high, `blocked` -> medium |
| person | `inactive` -> high |
| department | `inactive` -> high |
| capability | `deprecated` -> critical, `experimental` -> medium |
| operation | `failed` -> critical, `executing` -> medium |
| decision | `proposed` -> low |
| kpi | `off_track` -> high, `at_risk` -> medium |
| sop | `deprecated` -> high, `archived` -> medium |
| repository | Always `none` (no intrinsic risk) |
| service | `unhealthy` -> critical, `critical_resource` -> critical |
| workflow | `critical` attention -> critical, `warning` -> medium |
| database | `degraded` -> high, `unknown` -> medium |
| table | `critical` -> critical, `warning` -> medium |
| llm_provider | `unreachable` -> critical, `degraded` -> high |
| model | `critical` -> critical, `warning` -> medium |

### Risk Levels

```
none (0) < low (1) < medium (2) < high (3) < critical (4)
```

### Risk Propagation

A dependency at risk level N contributes N-1 (attenuated by one level) to its parent. The effective risk is `max(intrinsic_risk, max(attenuated_dependency_risks))`, capped at `critical`.

### Informational Boosts

Three signals can raise an object's risk by +1 (each, capped at critical):

- Critical notifications present -> +1
- Blocked heartbeats present -> +1
- Off-track KPIs present -> +1

### Inputs

**score_object():**

| Parameter | Type | Description |
|-----------|------|-------------|
| `object_type` | `str` | One of 15 supported types |
| `object_summary` | `dict` | Serialized object fields |
| `context_result` | `dict \| None` | Optional ContextGraph output for boost signals |

**propagate_risk():**

| Parameter | Type | Description |
|-----------|------|-------------|
| `intrinsic_level` | `str` | Object's own risk level |
| `dependency_levels` | `list[str]` | Risk levels of dependencies |

**generate_summary():**

| Parameter | Type | Description |
|-----------|------|-------------|
| `source` | `ImpactedObject` | Analysis source object |
| `affected` | `dict[str, list[ImpactedObject]]` | All affected objects by type |
| `coverage` | `ImpactCoverage` | Analysis quality metrics |
| `direction` | `str` | `"forward"` or `"reverse"` |

### Outputs

- `score_object()` -> `tuple[str, list[str]]` (risk_level, risk_reasons)
- `propagate_risk()` -> `str` (effective risk level)
- `generate_summary()` -> `ImpactSummary`

### Ownership

Owned by the `context/` package. Instantiated per-request. Used by both ImpactEngine and ReadinessEngine. Has no dependencies on other engines.

---

## ReadinessEngine

### Purpose

Answers "Is it safe to proceed?" for named scenarios by evaluating declarative rules across 8 business domains.

### Responsibilities

- Scenario-based evaluation with weighted categories
- Declarative blocker and warning rule evaluation
- Runtime configuration checking (infrastructure, GitHub, n8n, NocoDB, LLM flags)
- Weighted score computation
- Executive readiness checklist generation
- Navigation metadata on every issue (category, object_type, object_id, name)
- Heartbeat enrichment for active operations
- Critical dependency counting (delegated to RiskEngine)

### Does not own

- Risk scoring logic (delegated to RiskEngine)
- Graph traversal
- Impact analysis
- Infrastructure health checking (reads state, does not probe)

### Scenarios

| Scenario | Description | Categories (weighted) |
|----------|-------------|----------------------|
| `deployment` | Full system deployment | infrastructure(20), repositories(5), automation(10), data(10), ai(10), operations(20), business(10), goals(15) |
| `repository_merge` | Code merge readiness | repositories(25), automation(20), operations(20), business(15), goals(20) |
| `workflow_activation` | Workflow enablement | automation(25), data(20), operations(20), business(20), goals(15) |
| `database_maintenance` | Database operations | data(30), automation(20), operations(20), business(15), goals(15) |
| `provider_switch` | AI provider change | ai(30), automation(20), operations(20), business(15), goals(15) |

Categories not listed in a scenario's weights receive `not_applicable` status and are excluded from scoring.

### Categories

| Category | Sources | Configuration Flag |
|----------|---------|-------------------|
| infrastructure | services | `infrastructure_configured` |
| repositories | repositories | `github_configured` |
| automation | workflows | `n8n_configured` |
| data | databases, tables | `nocodb_configured` |
| ai | llm_providers, models | `llm_configured` |
| operations | operations | None (always available) |
| business | capabilities, people, departments | None |
| goals | goals, kpis | None |

### Declarative Rules

Rules are defined in `CATEGORY_RULES` -- a data dictionary mapping each category to its sources, blocker conditions, and warning conditions. Each rule is a tuple of `(field, value, message_template)`.

Example (infrastructure category, services source):
- **Blocker:** `health == "unhealthy"` -> `"Service '{name}' is unhealthy"`
- **Warning:** `resource_state == "elevated"` -> `"Service '{name}' has elevated resource usage"`

### Inputs

**evaluate():**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `snapshot` | `ReadinessSnapshot` | -- | Frozen-in-time workspace data |
| `scenario` | `str` | `"deployment"` | Scenario name |

**ReadinessSnapshot** contains all domain objects (services, repositories, workflows, databases, tables, llm_providers, models, operations, notifications, capabilities, goals, people, departments, kpis, decisions), heartbeats by operation, and runtime configuration flags.

### Outputs

**ReadinessReport:**

| Field | Type | Description |
|-------|------|-------------|
| `workspace_id` | `str` | Workspace identifier |
| `scenario` | `str` | Scenario evaluated |
| `scenario_label` | `str` | Human-readable scenario name |
| `overall_status` | `str` | `"ready"` or `"not_ready"` |
| `overall_score` | `int` | 0-100 weighted average |
| `categories` | `dict[str, CategoryResult]` | All 8 category results |
| `blockers` | `list[ReadinessIssue]` | All blocking issues |
| `warnings` | `list[ReadinessIssue]` | All warning issues |
| `checklist` | `list[ChecklistItem]` | Ordered by category weight |
| `critical_dependencies` | `int` | Count from RiskEngine |
| `timestamp` | `str` | ISO 8601 |

**CategoryResult:**

| Field | Type | Description |
|-------|------|-------------|
| `status` | `str` | `"ready"` / `"warning"` / `"blocked"` / `"not_applicable"` |
| `score` | `int` | 0-100 (healthy / checked * 100) |
| `blockers` | `list[ReadinessIssue]` | Blocking issues in this category |
| `warnings` | `list[ReadinessIssue]` | Warning issues in this category |
| `checked` | `int` | Objects evaluated |
| `healthy` | `int` | Objects without issues |

**Status determination:**
- Any blocker in any scenario category -> `overall_status = "not_ready"`
- Warnings only -> `overall_status = "ready"`
- No issues -> `overall_status = "ready"`

### Ownership

Owned by the `context/` package. Instantiated per-request by `HermesService.readiness()`. Receives an optional `RiskEngine` instance (defaults to creating its own).

---

## Interaction Model

### HermesService orchestration

`HermesService` in `service.py` is the sole consumer of Executive Intelligence engines. It creates engines, assembles data, and serializes results.

**Context resolution:**
```
HermesService.get_context()
  -> _get_context_graph()          # lazily builds ContextGraph from registries
  -> _build_graph_data()           # assembles GraphData from all runtime sources
  -> ContextGraph.resolve()        # returns relationship map
  -> serialize to JSON
```

**Impact analysis:**
```
HermesService.impact()
  -> _get_context_graph()
  -> _build_graph_data()
  -> RiskEngine()                  # fresh instance
  -> ImpactEngine(graph, risk)     # fresh instance
  -> ImpactEngine.analyze()
     -> ContextGraph.resolve()     # for each BFS node
     -> RiskEngine.score_object()  # for each discovered object
     -> RiskEngine.propagate_risk()# for inherited risk
     -> RiskEngine.generate_summary()
  -> serialize ImpactReport to JSON
```

**Readiness evaluation:**
```
HermesService.readiness()
  -> _build_readiness_snapshot()   # assembles from all runtime sources + registries
  -> RiskEngine()                  # fresh instance
  -> ReadinessEngine(risk)         # fresh instance
  -> ReadinessEngine.evaluate()
     -> _enrich_operations()       # adds heartbeat status
     -> _evaluate_category()       # for each scenario category
     -> _build_checklist()         # ordered by weight
     -> _count_critical()          # via RiskEngine.score_object()
  -> serialize ReadinessReport to JSON
```

### Engine-to-engine calls

| Caller | Callee | Methods Used |
|--------|--------|-------------|
| ImpactEngine | ContextGraph | `resolve()` |
| ImpactEngine | RiskEngine | `score_object()`, `propagate_risk()`, `generate_summary()` |
| ReadinessEngine | RiskEngine | `score_object()` (via `_count_critical()` only) |

No engine calls ImpactEngine. No engine calls ReadinessEngine. No engine calls HermesService. Dependencies are strictly downward.

---

## Deterministic Guarantees

1. **Same input, same output.** Given identical GraphData or ReadinessSnapshot, every engine produces identical results. No randomness, no timestamps in scoring logic, no external calls during analysis.

2. **No AI in the scoring path.** Risk levels are computed from observable object state using deterministic rules. LLM providers are objects being scored, not tools being called.

3. **No shared mutable state.** Engines are instantiated fresh per request. No caching, no session state, no cross-request contamination.

4. **Finite traversal.** BFS depth is clamped to 1-5. Visited sets prevent infinite cycles. Every analysis terminates.

5. **Coverage transparency.** ImpactCoverage tracks exactly what was analyzed: types seen, objects visited, relationships traversed, broken links found. The consumer knows what the analysis covered and what it missed.

---

## Architectural Invariants

These properties must be preserved across all future changes:

1. **Engines are stateless.** Created per request, discarded after use.
2. **Scoring is deterministic.** No AI, no heuristics, no probabilistic methods.
3. **Rules are data.** `_EDGES`, `CATEGORY_RULES`, `SCENARIOS`, `_RISK_SCORERS` are dictionaries, not procedural code.
4. **ContextGraph is pure traversal.** No scoring, no summaries, no side effects.
5. **Dependencies flow downward.** No engine calls upward to the service layer.
6. **RiskEngine is a shared leaf.** Both ImpactEngine and ReadinessEngine depend on it. It depends on nothing in `context/`.
7. **ImpactEngine and ReadinessEngine are independent peers.** Neither depends on the other.

---

## Extension Points

### Adding a new object type

1. Add to `SUPPORTED_TYPES` in `context_graph.py`
2. Add edge resolvers to `_EDGES` dictionary
3. Add a serializer function (`_ser_<type>`)
4. Add an intrinsic risk scorer to `_RISK_SCORERS` in `risk_engine.py`
5. Add to `_RELATION_TO_TYPE`, `_ID_FIELD`, `_NAME_FIELD` in `impact_engine.py`
6. Add source rules to relevant categories in `CATEGORY_RULES` in `readiness_engine.py`

Each step is a data change. No control flow modifications needed.

### Adding a new readiness scenario

Add an entry to `SCENARIOS` in `readiness_engine.py` with a label, description, and category weight map. The evaluation engine is fully generic.

### Adding a new risk boost source

Extend `_apply_boosts()` in `risk_engine.py`. The boost mechanism (check signal, raise by +1, cap at critical) is uniform.

### Adding a new infrastructure or LLM provider

Implement the `InfrastructureProvider` or `LlmProvider` abstract base class in `runtime/`. Register in the corresponding runtime aggregator. Zero changes to Executive Intelligence engines.

---

## Current Architecture vs Future Refactoring Opportunities

Everything above documents the system as it exists in v2.5.0. The following are refactoring opportunities identified during the architecture review. They are not planned work.

| Opportunity | What would change | What would not change |
|-------------|-------------------|----------------------|
| Move impact data structures to `context/impact_types.py` | File organization | Behavior, API, tests |
| Move `generate_summary()` from RiskEngine to ImpactEngine | Internal call structure | External API, results |
| Type the ContextGraph output as a dataclass | Return type annotation | Behavior, field names |
| Expand `context/__init__.py` exports | Import paths available | Existing import paths |
| Consolidate `_RELATIONSHIP_REASONS` templates | Template count | Output content |

These are tracked in `docs/architecture/architecture-debt-register.md`.
