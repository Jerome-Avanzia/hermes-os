# Architecture Debt Register

Hermes v2.5.0 -- Identified during the Executive Architecture Review of August 2026.

This register records architecture debt that exists in the current system. Each item is observable in the codebase today. No speculative or future debt is included.

---

## Debt Items

### DEBT-001: Impact data structures defined in risk_engine.py

**Severity:** High

**Description:**
`ImpactedObject`, `ImpactCoverage`, `ImpactSummary`, and `ImpactReport` are defined in `src/hermes/context/risk_engine.py` (lines 33-87). These are impact concepts -- produced by `ImpactEngine`, consumed by callers of `ImpactEngine`. The `ImpactEngine` imports them from `risk_engine.py` (line 18-23).

**Rationale:**
This is a conceptual inversion. The consumer module defines the producer's output types. It occurred because RiskEngine was extracted from ImpactEngine (Amendment 1) and the data structures stayed in the extraction target rather than being moved to their conceptual home.

**Impact:**
Any developer looking for "where is ImpactReport defined?" will look in `impact_engine.py` first and be wrong. The coupling makes it harder to reason about module boundaries. If a second consumer of RiskEngine appears that does not need impact types (ReadinessEngine already is one), it inherits import-time coupling to impact-specific structures.

**Suggested milestone:** Next refactoring sprint. Move types to a dedicated `context/impact_types.py` or similar. Both `impact_engine.py` and `risk_engine.py` import from it. Zero behavior change.

**Status:** Open

---

### DEBT-002: Infrastructure API endpoints not workspace-scoped

**Severity:** High

**Description:**
Business object endpoints use workspace-scoped paths: `/v1/workspaces/{workspace_id}/goals`, `/v1/workspaces/{workspace_id}/people`, etc. Infrastructure endpoints use unscoped paths: `/v1/repositories`, `/v1/services`, `/v1/workflows`, `/v1/databases`, `/v1/tables`, `/v1/llm-providers`, `/v1/llm-models` (lines 805-951 in `gateway/app.py`).

**Rationale:**
Infrastructure resources were added as global endpoints because the current deployment has a single workspace. The convention divergence creates two mental models for API consumers.

**Impact:**
If Hermes supports multiple workspaces with different infrastructure configurations, these endpoints require a breaking API change. API consumers must learn two URL patterns instead of one.

**Suggested milestone:** Deferred until multi-workspace infrastructure isolation is needed. Requires a breaking API version bump.

**Status:** Open

---

### DEBT-003: context/__init__.py barrel file under-declares public API

**Severity:** Medium

**Description:**
`src/hermes/context/__init__.py` exports only `ContextGraph`, `GraphData`, and `SUPPORTED_TYPES`. It does not export `ImpactEngine`, `RiskEngine`, `ReadinessEngine`, or any of their data structures (`ImpactReport`, `ImpactedObject`, `ReadinessReport`, `ReadinessSnapshot`, etc.).

**Rationale:**
The barrel file was created with the initial ContextGraph module. As engines were added in subsequent sprints, the barrel file was not updated.

**Impact:**
Consumers must know internal file paths (`from hermes.context.impact_engine import ImpactEngine`) instead of importing from the package (`from hermes.context import ImpactEngine`). The public API of the Executive Intelligence layer is not explicitly declared.

**Suggested milestone:** Next documentation or refactoring sprint. Additive change -- add exports without removing any.

**Status:** Open

---

### DEBT-004: RiskEngine.generate_summary() owns impact-layer responsibility

**Severity:** Medium

**Description:**
`RiskEngine.generate_summary()` (lines 313-377 in `risk_engine.py`) produces an `ImpactSummary`. It collects affected goals, operations, and people; computes `estimated_impact`; determines `safe_to_proceed`; and assembles `recommended_checks`. This is impact summarization, not risk scoring.

**Rationale:**
During the Amendment 1 extraction, the boundary was drawn at "everything that isn't BFS traversal goes to RiskEngine." Summary generation crossed with it because it depends on scored objects.

**Impact:**
RiskEngine carries a responsibility that does not belong to it. ReadinessEngine correctly avoids calling `generate_summary()`, but the coupling is latent. If a third consumer of risk scoring appears, it would inherit impact-specific summary logic.

**Suggested milestone:** Can be deferred. The current system works correctly. Address when RiskEngine gains a third consumer or when the summary logic needs to change independently of scoring.

**Status:** Open

---

### DEBT-005: gateway/app.py is a flat file with no router modularization

**Severity:** Medium

**Description:**
`gateway/app.py` contains all route definitions (965+ lines) in a single file: workspace, operations, chat, decisions, notifications, infrastructure, context, impact, readiness, and health endpoints. No FastAPI routers are used.

**Rationale:**
Routes were added incrementally across sprints. Each sprint appended its endpoints to the same file.

**Impact:**
Adding new endpoints increases the file linearly. Finding a specific route requires scrolling. Related routes (e.g., all infrastructure endpoints) are not grouped into reusable units.

**Suggested milestone:** Next gateway-focused sprint. Mechanical refactor into FastAPI routers (`routers/operations.py`, `routers/infrastructure.py`, etc.). No API changes.

**Status:** Open

---

### DEBT-006: ContextGraph.resolve() returns untyped dict

**Severity:** Medium

**Description:**
`ContextGraph.resolve()` returns a plain `dict` with keys like `"object_type"`, `"object_id"`, `"object_summary"`, `"attention"`, plus all 17 relation keys. Both `ImpactEngine` and `RiskEngine` access these keys by convention.

**Rationale:**
The dict shape evolved incrementally as relations were added. A formal type was not introduced.

**Impact:**
A mistyped key produces a runtime `KeyError` instead of a type error during development. The contract between ContextGraph output and engine input is implicit. New developers must read ContextGraph source to understand the shape.

**Suggested milestone:** Deferred. The current system is well-tested and the dict shape is stable. Address when the return shape needs to change or when static type checking is adopted.

**Status:** Open

---

### DEBT-007: service.py as convergence point

**Severity:** Low

**Description:**
`HermesService` (1,800+ lines in `service.py`) orchestrates operations, chat, decisions, notifications, context, impact, readiness, infrastructure, and all registries. Every feature addition requires modifying this file.

**Rationale:**
The service facade pattern is correct. The file has grown because Hermes has grown. Each sprint added methods for its feature area.

**Impact:**
Not yet critical. The file is well-organized with clear method boundaries. Risk increases with each sprint -- eventually the file becomes difficult to navigate. Splitting into domain-specific services (OperationService, ContextService, InfrastructureService) would reduce the per-file growth rate.

**Suggested milestone:** Deferred until the file exceeds ~2,500 lines or a natural domain split presents itself.

**Status:** Open

---

### DEBT-008: Monolithic frontend in index.html

**Severity:** Low

**Description:**
`gateway/static/index.html` is 5,504 lines containing HTML structure, CSS styles, and all JavaScript for 18+ views. Every view's load and render functions live in this single file.

**Rationale:**
The workspace was built as a single-page application with no build toolchain. This was the fastest path to a working UI during rapid sprint delivery.

**Impact:**
Adding new views increases the file linearly. JavaScript functions share a global namespace. There is no module system, no component isolation, no CSS scoping. The file is functional today but will become a maintenance burden.

**Suggested milestone:** Deferred until the UI requires significant new functionality or a frontend build system is introduced.

**Status:** Open

---

### DEBT-009: No structured error types for engine failures

**Severity:** Low

**Description:**
Engines return `None` for "not found" and raise generic `ValueError` for invalid input. There are no engine-specific exception types (`ImpactAnalysisError`, `ReadinessEvaluationError`).

**Rationale:**
The current error surface is small. Engines validate inputs at their boundaries and the service layer catches generic exceptions.

**Impact:**
The gateway cannot distinguish between "invalid object type" and "object not found" at the engine level. As the error surface grows, generic exceptions make targeted error handling harder.

**Suggested milestone:** Deferred. Address when the gateway needs to return different HTTP status codes for different engine failure modes.

**Status:** Open

---

### DEBT-010: Dual risk evaluation paths for overlapping objects

**Severity:** Low

**Description:**
`ImpactEngine.analyze()` scores objects via `RiskEngine.score_object()` during BFS traversal. `ReadinessEngine.evaluate()` independently scores many of the same objects via `RiskEngine.score_object()` during readiness evaluation. Each path assembles its input data through a different method (`_build_graph_data()` vs `_build_readiness_snapshot()`).

**Rationale:**
Impact and Readiness answer different questions and were built in separate sprints. Each assembles the data it needs independently.

**Impact:**
If the two assembly paths produce slightly different data for the same object, the risk scores could diverge. This has not caused issues because both paths read from the same underlying runtime sources. The risk is theoretical, not observed.

**Suggested milestone:** Monitor. No action needed unless score divergence is observed in practice.

**Status:** Open

---

## Architectural Invariants

These properties of the system must never change. They are load-bearing and underpin the correctness guarantees of Hermes.

### 1. Stateless engine instantiation

Engines are created fresh per request. No shared mutable state across requests. This guarantees deterministic, reproducible results and eliminates cache invalidation bugs.

### 2. Deterministic, no-AI scoring

RiskEngine and ReadinessEngine use only observable state -- deterministic rules on object fields. No LLM calls, no heuristics, no probabilistic scoring. Results are reproducible and auditable.

### 3. Declarative rule patterns

`ContextGraph._EDGES`, `ReadinessEngine.CATEGORY_RULES`, and `ReadinessEngine.SCENARIOS` are pure data dictionaries that drive behavior. The separation of rules-as-data from evaluation-as-logic is the system's most important architectural invariant.

### 4. ContextGraph as pure graph traversal

ContextGraph resolves relationships. It does not score, summarize, or produce side effects. It is a deterministic function from (object_type, object_id, data) to a relationship map.

### 5. Provider abstraction boundary

Business logic never touches provider-native APIs. The `runtime/` layer is the only code that knows about Docker, n8n, NocoDB, or specific LLM APIs. Swapping a provider requires zero changes above the runtime layer.

### 6. Operation lifecycle state machine

`VALID_TRANSITIONS` enforcement via `transition_operation()` in `models/operation.py`. Every state transition is validated. Bypassing this would break execution integrity.

### 7. Unidirectional data flow

Gateway -> Service -> Engine -> Runtime. No engine calls upward. No circular dependencies between packages. Data flows strictly downward through the layer stack.
