"""Sprint 60 — End-to-End Pipeline Integration Tests.

Proves that the complete deterministic Hermes pipeline composes correctly
from Founder Goal through to Execution Contract.

Validation scope:
  Founder Goal
    → ContextManager
    → PromptCompression
    → ModelRouter
    → WorkflowEngine
    → JobEngine
    → OperationEngine
    → SwarmEngine (when applicable)
    → Conductor (orchestrating all of the above)
    → ExecutionGateway
    → Execution Contract

No adapter execution. No provider calls. No external systems.
No filesystem dependency. No network dependency. No randomness.

Architecture invariants verified:
  ✓ Every responsibility has exactly one owner.
  ✓ No duplicated logic exists.
  ✓ No engine bypasses another.
  ✓ Execution Gateway is the only dispatch boundary.
  ✓ Swarm remains a strategy.
  ✓ Conductor remains orchestration only.
  ✓ Jobs own Operations.
  ✓ Operations never execute.
  ✓ Skills never execute.
  ✓ Registry owns metadata only.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ── Engines ────────────────────────────────────────────────────────────────
from hermes.kernel.context_manager import ContextManager
from hermes.kernel.execution_gateway import ExecutionGateway
from hermes.kernel.job_engine import JobEngine
from hermes.kernel.model_router import ModelRouter
from hermes.kernel.operation_engine import OperationEngine
from hermes.kernel.prompt_compression import PromptCompression
from hermes.kernel.skill_registry import SkillRegistry
from hermes.kernel.swarm_engine import SwarmEngine
from hermes.kernel.workflow_engine import WorkflowEngine
from hermes.kernel.conductor import Conductor

# ── Models ─────────────────────────────────────────────────────────────────
from hermes.models.conductor import ConductorRequest, ConductorStage
from hermes.models.context_package import ContextPackage, TokenBudget
from hermes.models.execution_gateway import (
    AdapterRegistration,
    ExecutionAdapter,
    ExecutionStatus,
)
from hermes.models.founder_workflow import (
    ApprovalStatus,
    FounderWorkflow,
    WorkflowStage,
)
from hermes.models.job import (
    JobCapabilityRequirement,
    JobOperationReference,
    JobStatus,
)
from hermes.models.knowledge_document import KnowledgeDocument
from hermes.models.operation import (
    OperationDefinition,
    OperationDependency,
    OperationStatus,
    OperationType,
)
from hermes.models.prompt_package import PromptPackage
from hermes.models.routing_decision import (
    RoutingDecision,
    RoutingPolicy,
)
from hermes.models.skill import (
    ExecutionDeclaration,
    InstalledSkill,
    SkillCapability,
    SkillCompatibility,
    SkillManifest,
    SkillMetadata,
    SkillStatus,
    SkillVersion,
)
from hermes.models.swarm import (
    SwarmDefinition,
    SwarmDependency,
    SwarmMember,
    SwarmStrategy,
)


# ══════════════════════════════════════════════════════════════════════════════
# Shared fixtures
# ══════════════════════════════════════════════════════════════════════════════


def _make_skill(
    skill_id: str,
    capability_ids: list[str],
    keywords: list[str] | None = None,
) -> InstalledSkill:
    """Build a minimal InstalledSkill with no filesystem paths."""
    manifest = SkillManifest(
        id=skill_id,
        name=skill_id.replace("-", " ").title(),
        version=SkillVersion(1, 0, 0),
        status=SkillStatus.ACTIVE,
        description=f"Test skill: {skill_id}",
        metadata=SkillMetadata(owner=None, department_id="engineering"),
        capabilities=tuple(SkillCapability(id=c) for c in capability_ids),
        provides=(),
        keywords=tuple(keywords or capability_ids),
        inputs=(),
        outputs=(),
        depends_on=(),
        sop_refs=(),
        repository_refs=(),
        workflow_refs=(),
        table_refs=(),
        model_refs=(),
        compatibility=None,
        execution=ExecutionDeclaration(adapters=("llm",)),
    )
    return InstalledSkill(
        manifest=manifest,
        path=Path("/test"),
        knowledge_paths=(),
        sop_paths=(),
    )


def _stub_context_manager(workspace_id: str = "ws-test") -> ContextManager:
    """ContextManager stub that produces a deterministic ContextPackage
    without touching the filesystem. Mocks only the two injected dependencies
    (KnowledgeEngine, CapabilityEngine) — the ContextManager itself is real."""
    knowledge_engine = MagicMock()
    knowledge_engine.load_with_architecture.side_effect = ValueError(
        "no knowledge — stub"
    )
    capability_engine = MagicMock()
    capability_engine.match.return_value = []
    return ContextManager(knowledge_engine, capability_engine)


def _build_skill_registry(*skills: InstalledSkill) -> SkillRegistry:
    """Build a SkillRegistry from a collection of InstalledSkill objects."""
    registry = SkillRegistry()
    for skill in skills:
        registry.register(skill)
    return registry


def _build_full_conductor(
    skill_registry: SkillRegistry | None = None,
    workspace_id: str = "ws-test",
) -> Conductor:
    """Build a fully-wired Conductor with all 7 engine dependencies.

    Uses real engine instances. Only KnowledgeEngine and CapabilityEngine
    are stubbed to avoid filesystem access.
    """
    registry = skill_registry or SkillRegistry()
    return Conductor(
        context_manager=_stub_context_manager(workspace_id),
        prompt_compression=PromptCompression(),
        model_router=ModelRouter(),
        workflow_engine=WorkflowEngine(),
        job_engine=JobEngine(registry=registry),
        operation_engine=OperationEngine(),
        swarm_engine=SwarmEngine(),
    )


# ══════════════════════════════════════════════════════════════════════════════
# 1. Complete pipeline — Founder Goal → Execution Contract
# ══════════════════════════════════════════════════════════════════════════════


class TestCompletePipeline:
    """Prove the complete pipeline composes without gaps or short-circuits."""

    def test_minimal_pipeline_succeeds(self) -> None:
        """The simplest possible pipeline (goal only) runs end-to-end."""
        conductor = _build_full_conductor()
        request = ConductorRequest(
            request_id="run-001",
            goal="Draft the Q3 product announcement",
            workspace_id="ws-test",
        )
        result = conductor.orchestrate(request)

        assert result.success is True
        assert result.final_stage == ConductorStage.COMPLETE
        assert result.request_id == "run-001"

    def test_pipeline_produces_all_mandatory_outputs(self) -> None:
        """All mandatory stage outputs are present in ConductorResult."""
        conductor = _build_full_conductor()
        request = ConductorRequest(
            request_id="run-002",
            goal="Analyse the Q3 revenue metrics",
            workspace_id="ws-test",
        )
        result = conductor.orchestrate(request)

        # All 4 non-conditional stages must produce output
        assert result.context_package is not None
        assert result.prompt_package is not None
        assert result.routing_decision is not None
        assert result.workflow is not None

    def test_pipeline_with_job_planning(self) -> None:
        """Pipeline activates JOB_PLANNING when job_definition is provided."""
        copywriting = _make_skill("copywriting", ["copywriting"])
        registry = _build_skill_registry(copywriting)
        conductor = _build_full_conductor(skill_registry=registry)

        op_ref = JobOperationReference(
            sequence_index=0,
            operation_id="op-draft",
            capability_id="copywriting",
        )
        job_def = JobEngine(registry=registry).build_job(
            id="job-copy-001",
            mission_id="mission-launch",
            goal="Draft the announcement",
            capability_requirements=[
                JobCapabilityRequirement(capability_id="copywriting"),
            ],
            operation_refs=[op_ref],
        )
        request = ConductorRequest(
            request_id="run-003",
            goal="Draft the product announcement",
            workspace_id="ws-test",
            job_definition=job_def,
        )
        result = conductor.orchestrate(request)

        assert result.success is True
        assert result.job_result is not None
        assert result.job_result.job_id == "job-copy-001"
        assert result.job_result.status == JobStatus.READY
        assert ConductorStage.JOB_PLANNING in result.audit.stages_completed

    def test_pipeline_with_operation_planning(self) -> None:
        """Pipeline activates OPERATION_PLANNING when operations are provided."""
        conductor = _build_full_conductor()
        op_engine = OperationEngine()
        op = op_engine.build_operation(
            id="op-draft",
            job_id="job-001",
            goal="Draft the announcement",
            operation_type=OperationType.LLM,
            capability_id="copywriting",
        )
        request = ConductorRequest(
            request_id="run-004",
            goal="Draft the product announcement",
            workspace_id="ws-test",
            operations=(op,),
        )
        result = conductor.orchestrate(request)

        assert result.success is True
        assert len(result.operation_results) == 1
        assert result.operation_results[0].operation_id == "op-draft"
        assert result.operation_results[0].status == OperationStatus.READY
        assert ConductorStage.OPERATION_PLANNING in result.audit.stages_completed

    def test_pipeline_with_swarm_planning(self) -> None:
        """Pipeline activates SWARM_PLANNING when cross-skill deps detected."""
        conductor = _build_full_conductor()
        op_engine = OperationEngine()

        op1 = op_engine.build_operation(
            id="op-frontend",
            job_id="job-001",
            goal="Build frontend component",
            operation_type=OperationType.LLM,
            capability_id="frontend",
            sequence_index=0,
        )
        op2 = op_engine.build_operation(
            id="op-backend",
            job_id="job-001",
            goal="Build backend API",
            operation_type=OperationType.LLM,
            capability_id="backend",
            sequence_index=1,
            depends_on=[OperationDependency(operation_id="op-frontend")],
        )

        request = ConductorRequest(
            request_id="run-005",
            goal="Build the checkout feature",
            workspace_id="ws-test",
            operations=(op1, op2),
        )
        result = conductor.orchestrate(request)

        assert result.success is True
        assert result.swarm_result is not None
        assert result.audit.swarm_required is True
        assert ConductorStage.SWARM_PLANNING in result.audit.stages_completed

    def test_pipeline_to_gateway_dispatch(self) -> None:
        """After Conductor completes, operations dispatch through the Gateway."""
        conductor = _build_full_conductor()
        gateway = ExecutionGateway()
        gateway.register(AdapterRegistration(
            adapter=ExecutionAdapter.LLM,
            adapter_id="llm-claude",
            available=True,
            description="Claude via Anthropic API",
        ))

        op_engine = OperationEngine()
        op = op_engine.build_operation(
            id="op-draft",
            job_id="job-001",
            goal="Draft the announcement",
            operation_type=OperationType.LLM,
        )
        request = ConductorRequest(
            request_id="run-006",
            goal="Draft the product announcement",
            workspace_id="ws-test",
            operations=(op,),
        )
        conductor_result = conductor.orchestrate(request)
        assert conductor_result.success is True

        # Each operation in the plan dispatches through the Gateway
        for op_result in conductor_result.operation_results:
            gw_request = gateway.build_request(
                request_id=f"gw-{op_result.operation_id}",
                operation_id=op_result.operation_id,
                adapter_type=ExecutionAdapter.LLM,
                action_id="chat",
                payload={"goal": op.goal},
            )
            gw_result = gateway.dispatch(gw_request)
            assert gw_result.status == ExecutionStatus.DISPATCHED
            assert gw_result.dispatch_decision.dispatched is True
            assert gw_result.dispatch_decision.adapter_registration is not None
            assert gw_result.output == ""  # no adapter invoked — dispatch contract only

    def test_full_pipeline_all_stages(self) -> None:
        """Pipeline with job + operations + swarm exercises all 8 stages."""
        copywriting = _make_skill("copywriting", ["copywriting"])
        frontend = _make_skill("frontend", ["frontend"])
        registry = _build_skill_registry(copywriting, frontend)
        conductor = _build_full_conductor(skill_registry=registry)
        op_engine = OperationEngine()
        job_engine = JobEngine(registry=registry)

        op1 = op_engine.build_operation(
            id="op-copy",
            job_id="job-001",
            goal="Draft copy",
            operation_type=OperationType.LLM,
            capability_id="copywriting",
            sequence_index=0,
        )
        op2 = op_engine.build_operation(
            id="op-frontend",
            job_id="job-001",
            goal="Build frontend",
            operation_type=OperationType.LLM,
            capability_id="frontend",
            sequence_index=1,
            depends_on=[OperationDependency(operation_id="op-copy")],
        )

        job_def = job_engine.build_job(
            id="job-launch-001",
            mission_id="mission-launch",
            goal="Launch the feature",
            capability_requirements=[
                JobCapabilityRequirement(capability_id="copywriting"),
                JobCapabilityRequirement(capability_id="frontend"),
            ],
            operation_refs=[
                JobOperationReference(0, "op-copy", "copywriting"),
                JobOperationReference(1, "op-frontend", "frontend"),
            ],
        )

        request = ConductorRequest(
            request_id="run-007",
            goal="Launch the new checkout feature",
            workspace_id="ws-test",
            job_definition=job_def,
            operations=(op1, op2),
        )
        result = conductor.orchestrate(request)

        assert result.success is True
        assert result.final_stage == ConductorStage.COMPLETE
        expected_stages = {
            ConductorStage.CONTEXT,
            ConductorStage.PROMPT,
            ConductorStage.ROUTING,
            ConductorStage.WORKFLOW,
            ConductorStage.JOB_PLANNING,
            ConductorStage.OPERATION_PLANNING,
            ConductorStage.SWARM_PLANNING,
        }
        assert set(result.audit.stages_completed) == expected_stages


# ══════════════════════════════════════════════════════════════════════════════
# 2. Determinism — identical inputs → identical outputs
# ══════════════════════════════════════════════════════════════════════════════


class TestDeterminism:
    """Prove that same inputs always produce the same outputs at every layer."""

    def test_conductor_is_deterministic(self) -> None:
        """Running orchestrate() twice with same inputs produces equal results."""
        conductor = _build_full_conductor()
        request = ConductorRequest(
            request_id="det-001",
            goal="Plan the Q4 roadmap",
            workspace_id="ws-test",
        )
        r1 = conductor.orchestrate(request)
        r2 = conductor.orchestrate(request)

        assert r1.success == r2.success
        assert r1.final_stage == r2.final_stage
        assert r1.audit.stages_completed == r2.audit.stages_completed

    def test_routing_decision_is_deterministic(self) -> None:
        """ModelRouter.route() with same inputs always selects the same model."""
        router = ModelRouter()
        pkg = PromptCompression().compress(
            package=ContextPackage(
                query="Analyse Q3 metrics",
                workspace_id="ws-det",
                knowledge=[],
                capabilities=[],
                budget=TokenBudget(0, 0, 0, 0),
            ),
            documents={},
            budget="8k",
        )
        r1 = router.route(pkg, policy=RoutingPolicy.BALANCED)
        r2 = router.route(pkg, policy=RoutingPolicy.BALANCED)

        assert r1.selected_model_id == r2.selected_model_id
        assert r1.selected_score == r2.selected_score
        assert r1.fallbacks == r2.fallbacks

    def test_context_assembly_is_deterministic(self) -> None:
        """ContextManager.assemble() with same query always returns equal packages."""
        ctx = _stub_context_manager()
        p1 = ctx.assemble(query="architecture decisions", workspace_id="ws-test")
        p2 = ctx.assemble(query="architecture decisions", workspace_id="ws-test")

        assert p1.query == p2.query
        assert p1.workspace_id == p2.workspace_id
        assert p1.knowledge == p2.knowledge
        assert p1.capabilities == p2.capabilities

    def test_workflow_is_deterministic(self) -> None:
        """WorkflowEngine.build() with same inputs always returns same stage."""
        router = ModelRouter()
        pkg = PromptCompression().compress(
            package=ContextPackage(
                query="generate launch plan",
                workspace_id="ws-det",
                knowledge=[],
                capabilities=[],
                budget=TokenBudget(0, 0, 0, 0),
            ),
            documents={},
            budget="8k",
        )
        routing = router.route(pkg, policy=RoutingPolicy.BALANCED)
        engine = WorkflowEngine()

        w1 = engine.build(pkg, routing)
        w2 = engine.build(pkg, routing)

        assert w1.current_stage == w2.current_stage
        assert w1.next_action == w2.next_action
        assert w1.workflow_intent == w2.workflow_intent
        assert w1.approval_required == w2.approval_required

    def test_operation_planning_is_deterministic(self) -> None:
        """OperationEngine.plan_all() with same inputs always returns same results."""
        engine = OperationEngine()
        ops = [
            engine.build_operation(
                id="op-b", job_id="job-001", goal="B",
                operation_type=OperationType.LLM, sequence_index=1,
            ),
            engine.build_operation(
                id="op-a", job_id="job-001", goal="A",
                operation_type=OperationType.LLM, sequence_index=0,
            ),
        ]
        r1 = engine.plan_all(ops, completed_op_ids=frozenset())
        r2 = engine.plan_all(ops, completed_op_ids=frozenset())

        assert len(r1) == len(r2)
        for res1, res2 in zip(r1, r2):
            assert res1.operation_id == res2.operation_id
            assert res1.status == res2.status
            assert res1.blocking_operations == res2.blocking_operations
            assert res1.cycle_detected == res2.cycle_detected

    def test_gateway_dispatch_is_deterministic(self) -> None:
        """ExecutionGateway.dispatch() with same registry+request always same result."""
        gateway = ExecutionGateway()
        gateway.register(AdapterRegistration(
            adapter=ExecutionAdapter.GIT,
            adapter_id="git-local",
            available=True,
            description="Local Git",
        ))

        req = gateway.build_request(
            request_id="gw-det-001",
            operation_id="op-clone",
            adapter_type=ExecutionAdapter.GIT,
            action_id="clone",
            payload={"repo": "hermes-os"},
        )
        r1 = gateway.dispatch(req)
        r2 = gateway.dispatch(req)

        assert r1.status == r2.status
        assert r1.dispatch_decision.adapter_registration == r2.dispatch_decision.adapter_registration
        assert r1.audit.adapter_selected == r2.audit.adapter_selected

    def test_swarm_planning_is_deterministic(self) -> None:
        """SwarmEngine.plan() with same SwarmDefinition always returns same plan."""
        engine = SwarmEngine()
        swarm = engine.build_swarm(
            id="swarm-det-001",
            job_id="job-001",
            members=[
                SwarmMember(operation_id="op-a", role="producer", sequence_index=0),
                SwarmMember(operation_id="op-b", role="consumer", sequence_index=1),
            ],
            dependencies=[
                SwarmDependency(from_operation_id="op-a", to_operation_id="op-b"),
            ],
        )
        r1 = engine.plan(swarm)
        r2 = engine.plan(swarm)

        assert r1.execution_plan.execution_order == r2.execution_plan.execution_order
        assert r1.conflict_detected == r2.conflict_detected
        assert r1.validation_result.valid == r2.validation_result.valid

    def test_full_pipeline_determinism(self) -> None:
        """Running the full pipeline twice with same ConductorRequest is equal."""
        copywriting = _make_skill("copywriting", ["copywriting"])
        registry = _build_skill_registry(copywriting)
        op_engine = OperationEngine()
        op = op_engine.build_operation(
            id="op-draft",
            job_id="job-001",
            goal="Draft announcement",
            operation_type=OperationType.LLM,
            capability_id="copywriting",
        )

        def _run() -> tuple:
            conductor = _build_full_conductor(skill_registry=registry)
            request = ConductorRequest(
                request_id="det-full-001",
                goal="Draft the product announcement",
                workspace_id="ws-test",
                operations=(op,),
            )
            r = conductor.orchestrate(request)
            return (
                r.success,
                r.final_stage,
                r.audit.stages_completed,
                r.routing_decision.selected_model_id if r.routing_decision else None,
                r.workflow.current_stage if r.workflow else None,
                r.operation_results[0].status if r.operation_results else None,
            )

        run1 = _run()
        run2 = _run()
        assert run1 == run2


# ══════════════════════════════════════════════════════════════════════════════
# 3. Immutable contracts
# ══════════════════════════════════════════════════════════════════════════════


class TestImmutableContracts:
    """Prove planning contracts are typed, structured, and immutable where frozen.

    Sprint 55-59 models (OperationDefinition, SwarmDefinition, ConductorResult,
    ExecutionResult, AdapterRegistration) use frozen=True slots=True.

    Earlier Sprint models (ContextPackage, PromptPackage, RoutingDecision,
    FounderWorkflow) are typed dataclasses but not frozen — mutation is not
    their primary protection; instead they carry no mutable state transitions.
    """

    def test_context_package_is_typed_dataclass(self) -> None:
        """ContextPackage is a structured typed dataclass."""
        ctx = _stub_context_manager()
        package = ctx.assemble(query="test", workspace_id="ws-test")
        assert dataclasses.is_dataclass(package)
        assert package.query == "test"
        assert package.workspace_id == "ws-test"
        assert isinstance(package.knowledge, list)
        assert isinstance(package.capabilities, list)

    def test_prompt_package_is_typed_dataclass(self) -> None:
        """PromptPackage is a structured typed dataclass."""
        ctx = _stub_context_manager()
        cp = ctx.assemble(query="test", workspace_id="ws-test")
        pkg = PromptCompression().compress(cp, documents={})
        assert dataclasses.is_dataclass(pkg)
        assert isinstance(pkg.system_prompt, str)
        assert isinstance(pkg.sections, list)
        assert isinstance(pkg.estimated_chars, int)

    def test_routing_decision_is_typed_dataclass(self) -> None:
        """RoutingDecision is a structured typed dataclass."""
        ctx = _stub_context_manager()
        cp = ctx.assemble(query="test", workspace_id="ws-test")
        pkg = PromptCompression().compress(cp, documents={})
        decision = ModelRouter().route(pkg)
        assert dataclasses.is_dataclass(decision)
        assert isinstance(decision.policy, RoutingPolicy)
        assert isinstance(decision.routed, bool)

    def test_founder_workflow_is_typed_dataclass(self) -> None:
        """FounderWorkflow is a structured typed dataclass."""
        ctx = _stub_context_manager()
        cp = ctx.assemble(query="test", workspace_id="ws-test")
        pkg = PromptCompression().compress(cp, documents={})
        routing = ModelRouter().route(pkg)
        wf = WorkflowEngine().build(pkg, routing)
        assert dataclasses.is_dataclass(wf)
        assert isinstance(wf.current_stage, WorkflowStage)
        assert isinstance(wf.approval_required, bool)

    def test_conductor_result_is_frozen(self) -> None:
        """ConductorResult is a frozen dataclass."""
        conductor = _build_full_conductor()
        request = ConductorRequest(
            request_id="imm-001", goal="test", workspace_id="ws-test",
        )
        result = conductor.orchestrate(request)
        assert dataclasses.is_dataclass(result)
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            result.success = False  # type: ignore[misc]

    def test_execution_result_is_frozen(self) -> None:
        """ExecutionResult (gateway) is a frozen dataclass."""
        gateway = ExecutionGateway()
        gateway.register(AdapterRegistration(
            adapter=ExecutionAdapter.LLM,
            adapter_id="llm-claude",
            available=True,
            description="Claude",
        ))
        req = gateway.build_request(
            request_id="imm-002", operation_id="op-001",
            adapter_type=ExecutionAdapter.LLM, action_id="chat",
        )
        result = gateway.dispatch(req)
        assert dataclasses.is_dataclass(result)
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            result.status = ExecutionStatus.SUCCEEDED  # type: ignore[misc]

    def test_adapter_registration_is_frozen(self) -> None:
        """AdapterRegistration is a frozen dataclass."""
        reg = AdapterRegistration(
            adapter=ExecutionAdapter.GIT,
            adapter_id="git-local",
            available=True,
            description="Local git",
        )
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            reg.available = False  # type: ignore[misc]

    def test_operation_definition_is_frozen(self) -> None:
        """OperationDefinition is a frozen dataclass."""
        engine = OperationEngine()
        op = engine.build_operation(
            id="op-test", job_id="job-test", goal="Test",
        )
        assert dataclasses.is_dataclass(op)
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            op.goal = "mutated"  # type: ignore[misc]

    def test_swarm_definition_is_frozen(self) -> None:
        """SwarmDefinition is a frozen dataclass."""
        engine = SwarmEngine()
        swarm = engine.build_swarm(
            id="swarm-001", job_id="job-001",
            members=[SwarmMember(operation_id="op-a", role="r", sequence_index=0)],
        )
        assert dataclasses.is_dataclass(swarm)
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            swarm.id = "mutated"  # type: ignore[misc]


# ══════════════════════════════════════════════════════════════════════════════
# 4. Engine composition — outputs become next engine's inputs
# ══════════════════════════════════════════════════════════════════════════════


class TestEngineComposition:
    """Prove each engine's output is the correct input to the next engine."""

    def test_context_to_prompt_composition(self) -> None:
        """ContextPackage flows correctly into PromptCompression."""
        ctx = _stub_context_manager()
        package = ctx.assemble(query="Review the deployment plan", workspace_id="ws-compose")
        pkg = PromptCompression().compress(package, documents={}, budget="8k")

        assert pkg.query == package.query
        assert pkg.workspace_id == package.workspace_id
        assert isinstance(pkg.estimated_chars, int)
        assert pkg.estimated_chars > 0

    def test_prompt_to_routing_composition(self) -> None:
        """PromptPackage flows correctly into ModelRouter."""
        ctx = _stub_context_manager()
        cp = ctx.assemble(query="Plan the roadmap", workspace_id="ws-compose")
        pkg = PromptCompression().compress(cp, documents={})
        routing = ModelRouter().route(pkg, policy=RoutingPolicy.BALANCED)

        assert routing.prompt_chars == pkg.estimated_chars
        assert isinstance(routing.routed, bool)

    def test_routing_to_workflow_composition(self) -> None:
        """RoutingDecision flows correctly into WorkflowEngine."""
        ctx = _stub_context_manager()
        cp = ctx.assemble(query="deploy the release", workspace_id="ws-compose")
        pkg = PromptCompression().compress(cp, documents={})
        routing = ModelRouter().route(pkg)
        wf = WorkflowEngine().build(pkg, routing)

        # Workflow intent derived from prompt query
        assert wf.workflow_intent is not None
        # Routing policy drives approval requirement
        assert isinstance(wf.approval_required, bool)

    def test_job_engine_composes_with_skill_registry(self) -> None:
        """JobEngine resolves capabilities through SkillRegistry."""
        skill = _make_skill("data-analysis", ["data-analysis"])
        registry = _build_skill_registry(skill)
        engine = JobEngine(registry=registry)

        job_def = engine.build_job(
            id="job-analysis-001",
            mission_id="mission-insights",
            goal="Analyse Q3 data",
            capability_requirements=[
                JobCapabilityRequirement(capability_id="data-analysis"),
            ],
            operation_refs=[
                JobOperationReference(0, "op-analyse", "data-analysis"),
            ],
        )
        result = engine.plan(job_def)

        assert result.status == JobStatus.READY
        assert "data-analysis" in result.resolved_capabilities
        assert result.unresolved_capabilities == ()

    def test_operations_flow_from_engine_to_conductor(self) -> None:
        """OperationDefinitions produced by OperationEngine flow into Conductor."""
        engine = OperationEngine()
        op = engine.build_operation(
            id="op-analyse",
            job_id="job-001",
            goal="Analyse the dataset",
            operation_type=OperationType.DATABASE,
        )
        conductor = _build_full_conductor()
        request = ConductorRequest(
            request_id="compose-001",
            goal="Analyse the dataset",
            workspace_id="ws-compose",
            operations=(op,),
        )
        result = conductor.orchestrate(request)

        assert result.success is True
        assert any(r.operation_id == "op-analyse" for r in result.operation_results)

    def test_swarm_result_includes_execution_order(self) -> None:
        """SwarmEngine produces a sequenced execution order from member topology."""
        engine = SwarmEngine()
        swarm = engine.build_swarm(
            id="swarm-compose-001",
            job_id="job-001",
            members=[
                SwarmMember(operation_id="op-producer", role="producer", sequence_index=0),
                SwarmMember(operation_id="op-consumer", role="consumer", sequence_index=1),
            ],
            dependencies=[
                SwarmDependency(
                    from_operation_id="op-producer",
                    to_operation_id="op-consumer",
                ),
            ],
        )
        result = engine.plan(swarm)

        assert result.execution_plan is not None
        order = result.execution_plan.execution_order
        assert order.index("op-producer") < order.index("op-consumer")

    def test_conductor_audit_records_all_stage_decisions(self) -> None:
        """ConductorAudit captures one decision per activated stage."""
        conductor = _build_full_conductor()
        request = ConductorRequest(
            request_id="audit-001",
            goal="Plan the roadmap",
            workspace_id="ws-compose",
        )
        result = conductor.orchestrate(request)

        assert len(result.audit.decisions) == len(result.audit.stages_completed)
        stage_values = {d.stage for d in result.audit.decisions}
        for stage in result.audit.stages_completed:
            assert stage in stage_values


# ══════════════════════════════════════════════════════════════════════════════
# 5. Execution Gateway as sole dispatch boundary
# ══════════════════════════════════════════════════════════════════════════════


class TestGatewayBoundary:
    """Prove the Execution Gateway is the only dispatch boundary."""

    def test_gateway_is_only_dispatch_point(self) -> None:
        """Conductor produces plans; Gateway dispatches them. No engine bypasses it."""
        gateway = ExecutionGateway()
        gateway.register(AdapterRegistration(
            adapter=ExecutionAdapter.LLM,
            adapter_id="llm-claude",
            available=True,
            description="Claude",
        ))

        conductor = _build_full_conductor()
        op_engine = OperationEngine()
        op = op_engine.build_operation(
            id="op-draft",
            job_id="job-001",
            goal="Draft",
            operation_type=OperationType.LLM,
        )
        request = ConductorRequest(
            request_id="boundary-001",
            goal="Draft announcement",
            workspace_id="ws-test",
            operations=(op,),
        )
        # Conductor produces a plan — no execution occurs
        plan = conductor.orchestrate(request)
        assert plan.success is True

        # Only through the Gateway does dispatch happen
        for op_result in plan.operation_results:
            gw_req = gateway.build_request(
                request_id=f"gw-{op_result.operation_id}",
                operation_id=op_result.operation_id,
                adapter_type=ExecutionAdapter.LLM,
                action_id="chat",
            )
            gw_result = gateway.dispatch(gw_req)
            assert gw_result.status == ExecutionStatus.DISPATCHED

    def test_gateway_dispatch_is_contract_only(self) -> None:
        """Gateway produces a dispatch contract; output is empty (no execution)."""
        gateway = ExecutionGateway()
        gateway.register(AdapterRegistration(
            adapter=ExecutionAdapter.DOCKER,
            adapter_id="docker-local",
            available=True,
            description="Local Docker",
        ))
        req = gateway.build_request(
            request_id="boundary-002",
            operation_id="op-build",
            adapter_type=ExecutionAdapter.DOCKER,
            action_id="build",
            payload={"image": "hermes-os:latest"},
        )
        result = gateway.dispatch(req)

        assert result.status == ExecutionStatus.DISPATCHED
        assert result.output == ""   # no adapter invoked — dispatch contract only
        assert result.error is None
        assert result.dispatch_decision.dispatched is True

    def test_all_eight_adapter_types_dispatch_correctly(self) -> None:
        """All 8 ExecutionAdapter types produce DISPATCHED with empty output."""
        gateway = ExecutionGateway()
        for adapter in ExecutionAdapter:
            gateway.register(AdapterRegistration(
                adapter=adapter,
                adapter_id=f"{adapter.value}-test",
                available=True,
                description=f"Test {adapter.value} adapter",
            ))

        for adapter in ExecutionAdapter:
            req = gateway.build_request(
                request_id=f"gw-{adapter.value}",
                operation_id=f"op-{adapter.value}",
                adapter_type=adapter,
                action_id="execute",
            )
            result = gateway.dispatch(req)
            assert result.status == ExecutionStatus.DISPATCHED, (
                f"Adapter {adapter.value} should DISPATCH, got {result.status}"
            )
            assert result.output == "", f"Adapter {adapter.value} must not produce output"

    def test_gateway_fails_gracefully_without_adapter(self) -> None:
        """Gateway returns UNSUPPORTED when no adapter is registered for type."""
        gateway = ExecutionGateway()
        req = gateway.build_request(
            request_id="boundary-003",
            operation_id="op-http",
            adapter_type=ExecutionAdapter.HTTP,
            action_id="get",
        )
        result = gateway.dispatch(req)

        assert result.status == ExecutionStatus.UNSUPPORTED
        assert result.dispatch_decision.dispatched is False

    def test_gateway_fails_gracefully_when_adapter_unavailable(self) -> None:
        """Gateway returns FAILED when adapter is registered but unavailable."""
        gateway = ExecutionGateway()
        gateway.register(AdapterRegistration(
            adapter=ExecutionAdapter.DATABASE,
            adapter_id="db-prod",
            available=False,  # unavailable
            description="Production database",
        ))
        req = gateway.build_request(
            request_id="boundary-004",
            operation_id="op-query",
            adapter_type=ExecutionAdapter.DATABASE,
            action_id="select",
        )
        result = gateway.dispatch(req)

        assert result.status == ExecutionStatus.FAILED
        assert result.dispatch_decision.dispatched is False

    def test_gateway_registration_is_first_wins(self) -> None:
        """Second registration of same adapter type is silently ignored."""
        gateway = ExecutionGateway()
        r1 = gateway.register(AdapterRegistration(
            adapter=ExecutionAdapter.GIT,
            adapter_id="git-v1",
            available=True,
            description="First",
        ))
        r2 = gateway.register(AdapterRegistration(
            adapter=ExecutionAdapter.GIT,
            adapter_id="git-v2",
            available=True,
            description="Second (ignored)",
        ))

        assert r1 is True
        assert r2 is False
        assert gateway.resolve(ExecutionAdapter.GIT).adapter_id == "git-v1"

    def test_gateway_audit_trail_is_complete(self) -> None:
        """GatewayAudit captures evaluated adapters and selected adapter."""
        gateway = ExecutionGateway()
        gateway.register(AdapterRegistration(
            adapter=ExecutionAdapter.LLM,
            adapter_id="llm-claude",
            available=True,
            description="Claude",
        ))
        gateway.register(AdapterRegistration(
            adapter=ExecutionAdapter.GIT,
            adapter_id="git-local",
            available=True,
            description="Git",
        ))
        req = gateway.build_request(
            request_id="audit-gw-001",
            operation_id="op-chat",
            adapter_type=ExecutionAdapter.LLM,
            action_id="chat",
        )
        result = gateway.dispatch(req)

        assert result.audit.validation_passed is True
        assert result.audit.adapter_selected == "llm-claude"
        # All registered adapters appear in audit
        assert "llm-claude" in result.audit.adapters_evaluated
        assert "git-local" in result.audit.adapters_evaluated

    def test_gateway_payload_is_deterministic(self) -> None:
        """build_request() normalises dict payload to sorted tuple."""
        gateway = ExecutionGateway()
        req = gateway.build_request(
            request_id="payload-001",
            operation_id="op-001",
            adapter_type=ExecutionAdapter.GENERIC,
            action_id="run",
            payload={"z_key": "z_val", "a_key": "a_val", "m_key": "m_val"},
        )
        # Payload is sorted by key
        keys = [k for k, _ in req.payload]
        assert keys == sorted(keys)


# ══════════════════════════════════════════════════════════════════════════════
# 6. Provider independence
# ══════════════════════════════════════════════════════════════════════════════


class TestProviderIndependence:
    """Prove no engine contains provider-specific logic."""

    def test_model_router_selects_by_abstract_policy_not_provider(self) -> None:
        """ModelRouter selects on abstract capabilities, not provider names."""
        ctx = _stub_context_manager()
        cp = ctx.assemble(query="analyse the report", workspace_id="ws-pi")
        pkg = PromptCompression().compress(cp, documents={})

        # Same package, different policies
        balanced = ModelRouter().route(pkg, policy=RoutingPolicy.BALANCED)
        cheapest = ModelRouter().route(pkg, policy=RoutingPolicy.CHEAPEST)
        quality = ModelRouter().route(pkg, policy=RoutingPolicy.HIGHEST_QUALITY)

        # All produce a valid selection
        assert balanced.selected is not None
        assert cheapest.selected is not None
        assert quality.selected is not None
        # Different policies may select different models
        # Key: routing decision exposes policy, not provider implementation
        assert balanced.policy == RoutingPolicy.BALANCED
        assert cheapest.policy == RoutingPolicy.CHEAPEST
        assert quality.policy == RoutingPolicy.HIGHEST_QUALITY

    def test_workflow_engine_is_provider_agnostic(self) -> None:
        """WorkflowEngine transitions are driven by routing policy and approval
        state — never by provider-specific logic.

        Note: WorkflowAudit.routing_summary stores the selected model ID for
        traceability. Model IDs may contain provider prefixes (e.g.
        "anthropic--claude-opus-4") — this is expected. The engine's decision
        logic itself (TRANSITION_TABLE, APPROVAL_REQUIRED_POLICIES,
        INTENT_KEYWORDS) never references provider names.
        """
        ctx = _stub_context_manager()
        cp = ctx.assemble(query="generate the report", workspace_id="ws-pi")
        pkg = PromptCompression().compress(cp, documents={})

        # Same prompt, different routing policies → different approval behaviour
        routing_balanced = ModelRouter().route(pkg, policy=RoutingPolicy.BALANCED)
        routing_quality = ModelRouter().route(pkg, policy=RoutingPolicy.HIGHEST_QUALITY)

        wf_balanced = WorkflowEngine().build(pkg, routing_balanced)
        wf_quality = WorkflowEngine().build(pkg, routing_quality)

        # HIGHEST_QUALITY requires founder approval (declarative policy rule)
        # BALANCED does not
        assert wf_quality.approval_required is True
        assert wf_balanced.approval_required is False

        # Stage differs based on policy — no provider name in stage or action
        assert "anthropic" not in wf_quality.current_stage.value
        assert "claude" not in wf_quality.next_action.value

    def test_conductor_produces_no_provider_references(self) -> None:
        """ConductorAudit decisions contain no provider-specific strings."""
        conductor = _build_full_conductor()
        request = ConductorRequest(
            request_id="pi-001",
            goal="Plan the product launch",
            workspace_id="ws-pi",
        )
        result = conductor.orchestrate(request)

        provider_names = {"claude", "anthropic", "openai", "ollama", "gemini"}
        for decision in result.audit.decisions:
            outcome_lower = decision.outcome.lower()
            for name in provider_names:
                assert name not in outcome_lower, (
                    f"Conductor stage {decision.stage.value!r} outcome must not "
                    f"reference provider {name!r}: {decision.outcome!r}"
                )

    def test_gateway_is_provider_independent(self) -> None:
        """Gateway dispatches to adapter types, not provider implementations."""
        gateway = ExecutionGateway()
        gateway.register(AdapterRegistration(
            adapter=ExecutionAdapter.LLM,
            adapter_id="llm-any-provider",
            available=True,
            description="Provider-agnostic LLM adapter",
        ))
        req = gateway.build_request(
            request_id="pi-gw-001",
            operation_id="op-inference",
            adapter_type=ExecutionAdapter.LLM,
            action_id="chat",
        )
        result = gateway.dispatch(req)

        assert result.status == ExecutionStatus.DISPATCHED
        # Decision references adapter type, not provider
        assert result.dispatch_decision.adapter == ExecutionAdapter.LLM

    def test_operation_engine_is_provider_agnostic(self) -> None:
        """OperationEngine plans using OperationType — no provider names."""
        engine = OperationEngine()
        for op_type in OperationType:
            op = engine.build_operation(
                id=f"op-{op_type.value}",
                job_id="job-pi",
                goal=f"Test {op_type.value}",
                operation_type=op_type,
            )
            result = engine.plan(op, all_ops=[op], completed_op_ids=frozenset())
            assert result.status == OperationStatus.READY


# ══════════════════════════════════════════════════════════════════════════════
# 7. No filesystem or network dependency
# ══════════════════════════════════════════════════════════════════════════════


class TestNoExternalDependency:
    """Prove the pipeline works with zero filesystem or network access."""

    def test_pipeline_runs_with_no_knowledge_files(self) -> None:
        """Conductor completes even when KnowledgeEngine has no documents."""
        conductor = _build_full_conductor()
        request = ConductorRequest(
            request_id="nofs-001",
            goal="Draft the announcement",
            workspace_id="ws-empty",
        )
        result = conductor.orchestrate(request)

        assert result.success is True
        assert result.context_package is not None
        assert result.context_package.knowledge == []
        assert result.context_package.capabilities == []

    def test_pipeline_runs_with_no_skill_registry(self) -> None:
        """Conductor completes with an empty SkillRegistry."""
        conductor = _build_full_conductor(skill_registry=SkillRegistry())
        request = ConductorRequest(
            request_id="nofs-002",
            goal="Review the architecture",
            workspace_id="ws-empty",
        )
        result = conductor.orchestrate(request)

        assert result.success is True

    def test_gateway_requires_no_filesystem(self) -> None:
        """ExecutionGateway builds dispatch contracts with no filesystem access."""
        gateway = ExecutionGateway()
        gateway.register(AdapterRegistration(
            adapter=ExecutionAdapter.FILESYSTEM,
            adapter_id="fs-local",
            available=True,
            description="Local filesystem adapter",
        ))
        req = gateway.build_request(
            request_id="nofs-003",
            operation_id="op-read",
            adapter_type=ExecutionAdapter.FILESYSTEM,
            action_id="read",
            payload={"path": "/data/report.md"},
        )
        result = gateway.dispatch(req)

        # Dispatch contract built — no actual filesystem access
        assert result.status == ExecutionStatus.DISPATCHED
        assert result.output == ""

    def test_operation_engine_requires_no_filesystem(self) -> None:
        """OperationEngine plans operations with no filesystem access."""
        engine = OperationEngine()
        ops = [
            engine.build_operation(
                id=f"op-{i}", job_id="job-001", goal=f"Step {i}",
                sequence_index=i,
            )
            for i in range(5)
        ]
        results = engine.plan_all(ops, completed_op_ids=frozenset())
        assert all(r.status == OperationStatus.READY for r in results)

    def test_swarm_engine_requires_no_filesystem(self) -> None:
        """SwarmEngine produces execution plans with no filesystem access."""
        engine = SwarmEngine()
        swarm = engine.build_swarm(
            id="swarm-nofs",
            job_id="job-001",
            members=[
                SwarmMember(operation_id="op-a", role="r", sequence_index=0),
                SwarmMember(operation_id="op-b", role="r", sequence_index=1),
            ],
            dependencies=[
                SwarmDependency(from_operation_id="op-a", to_operation_id="op-b"),
            ],
        )
        result = engine.plan(swarm)
        assert result.execution_plan is not None
        assert "op-a" in result.execution_plan.execution_order


# ══════════════════════════════════════════════════════════════════════════════
# 8. Architecture verification — single ownership, no bypass
# ══════════════════════════════════════════════════════════════════════════════


class TestArchitectureVerification:
    """Verify architecture invariants by observable behaviour."""

    def test_skill_registry_owns_metadata_only(self) -> None:
        """SkillRegistry stores metadata. It does not execute skills."""
        skill = _make_skill("copywriting", ["copywriting"])
        registry = SkillRegistry()
        result = registry.register(skill)

        # Only metadata is stored — find_by_id returns a RegistryEntry
        found = registry.find_by_id("copywriting")
        assert found is not None
        assert found.skill.manifest.id == "copywriting"

        # No execution methods exist on SkillRegistry
        assert not hasattr(registry, "execute")
        assert not hasattr(registry, "run")
        assert not hasattr(registry, "invoke")

    def test_operation_engine_never_executes(self) -> None:
        """OperationEngine produces planning results — never execution results."""
        engine = OperationEngine()
        op = engine.build_operation(
            id="op-test", job_id="job-001", goal="Test op",
            operation_type=OperationType.LLM,
        )
        result = engine.plan(op, all_ops=[op], completed_op_ids=frozenset())

        # Status is a planning status, not an execution status
        assert result.status in (
            OperationStatus.READY,
            OperationStatus.BLOCKED,
            OperationStatus.FAILED,
        )
        # OperationEngine has no execution methods
        assert not hasattr(engine, "execute")
        assert not hasattr(engine, "run")
        assert not hasattr(engine, "invoke")

    def test_swarm_engine_is_a_strategy_not_a_layer(self) -> None:
        """SwarmEngine holds no state — it is stateless coordination, not a layer."""
        engine = SwarmEngine()

        # No registry, no index, no references
        assert not hasattr(engine, "_registry")
        assert not hasattr(engine, "_index")
        assert not hasattr(engine, "_state")

        # Each call is independent — same inputs, same outputs
        swarm = engine.build_swarm(
            id="swarm-strategy-001",
            job_id="job-001",
            members=[
                SwarmMember(operation_id="op-a", role="r", sequence_index=0),
            ],
        )
        r1 = engine.plan(swarm)
        r2 = engine.plan(swarm)
        assert r1.execution_plan.execution_order == r2.execution_plan.execution_order

    def test_conductor_does_not_implement_engine_logic(self) -> None:
        """Conductor has no methods that duplicate engine responsibilities."""
        # Conductor owns exactly: orchestrate, validate_request,
        # _swarm_required, _build_auto_swarm
        # It does NOT have: route(), build() (workflow), plan(), compress(), assemble()
        conductor_methods = {
            name for name in dir(Conductor)
            if not name.startswith("__")
        }

        engine_only_methods = {
            "route",      # ModelRouter.route()
            "compress",   # PromptCompression.compress()
            "assemble",   # ContextManager.assemble()
            "dispatch",   # ExecutionGateway.dispatch()
        }
        for method in engine_only_methods:
            assert method not in conductor_methods, (
                f"Conductor must not implement {method!r} — "
                f"that responsibility belongs to a specific engine"
            )

    def test_jobs_own_operations(self) -> None:
        """JobDefinition owns OperationReferences; OperationDefinition is independent."""
        job_engine = JobEngine(registry=SkillRegistry())
        op_engine = OperationEngine()

        op = op_engine.build_operation(
            id="op-child", job_id="job-parent", goal="Child op",
        )
        job = job_engine.build_job(
            id="job-parent",
            mission_id="mission-001",
            goal="Parent job",
            operation_refs=[JobOperationReference(0, "op-child", "")],
        )

        # Job references operation by ID
        assert any(ref.operation_id == "op-child" for ref in job.operation_refs)
        # Operation carries parent job_id
        assert op.job_id == "job-parent"
        # But operation is independent data — does not contain job data
        assert not hasattr(op, "mission_id")

    def test_workflow_engine_never_calls_providers(self) -> None:
        """WorkflowEngine.build() produces a workflow without any external calls."""
        ctx = _stub_context_manager()
        cp = ctx.assemble(query="execute the deployment", workspace_id="ws-arch")
        pkg = PromptCompression().compress(cp, documents={})
        routing = ModelRouter().route(pkg)

        # This would raise if any provider was called (no stubs needed)
        wf = WorkflowEngine().build(pkg, routing)
        assert isinstance(wf, FounderWorkflow)
        assert wf.current_stage is not None

    def test_conductor_is_orchestrator_not_planner(self) -> None:
        """Conductor produces ConductorResult — it does not own planning logic."""
        conductor = _build_full_conductor()
        request = ConductorRequest(
            request_id="arch-cond-001",
            goal="Plan the roadmap",
            workspace_id="ws-arch",
        )
        result = conductor.orchestrate(request)

        # Conductor returns assembled outputs from all engines
        from hermes.models.conductor import ConductorResult
        assert isinstance(result, ConductorResult)

        # Each output comes from its authoritative engine, assembled by Conductor
        assert result.context_package is not None    # from ContextManager
        assert result.prompt_package is not None     # from PromptCompression
        assert result.routing_decision is not None   # from ModelRouter
        assert result.workflow is not None           # from WorkflowEngine


# ══════════════════════════════════════════════════════════════════════════════
# 9. Complete audit trail
# ══════════════════════════════════════════════════════════════════════════════


class TestAuditTrail:
    """Prove the complete pipeline produces a verifiable audit trail."""

    def test_conductor_audit_contains_all_stages(self) -> None:
        """ConductorAudit records every stage that ran."""
        conductor = _build_full_conductor()
        request = ConductorRequest(
            request_id="audit-trail-001",
            goal="Review the plan",
            workspace_id="ws-audit",
        )
        result = conductor.orchestrate(request)

        mandatory = {
            ConductorStage.CONTEXT,
            ConductorStage.PROMPT,
            ConductorStage.ROUTING,
            ConductorStage.WORKFLOW,
        }
        completed = set(result.audit.stages_completed)
        assert mandatory.issubset(completed)

    def test_gateway_audit_captures_decision_chain(self) -> None:
        """GatewayAudit captures the full evaluation chain for every dispatch."""
        gateway = ExecutionGateway()
        gateway.register(AdapterRegistration(
            adapter=ExecutionAdapter.LLM,
            adapter_id="llm-claude",
            available=True,
            description="Claude",
        ))
        req = gateway.build_request(
            request_id="audit-trail-002",
            operation_id="op-generate",
            adapter_type=ExecutionAdapter.LLM,
            action_id="chat",
        )
        result = gateway.dispatch(req)

        assert result.audit.request_id == "audit-trail-002"
        assert result.audit.validation_passed is True
        assert result.audit.adapter_selected == "llm-claude"
        assert len(result.audit.adapters_evaluated) >= 1

    def test_validation_result_included_in_every_dispatch(self) -> None:
        """Every ExecutionResult carries the GatewayValidationResult."""
        gateway = ExecutionGateway()
        gateway.register(AdapterRegistration(
            adapter=ExecutionAdapter.GENERIC,
            adapter_id="generic-test",
            available=True,
            description="Generic",
        ))
        req = gateway.build_request(
            request_id="audit-trail-003",
            operation_id="op-generic",
            adapter_type=ExecutionAdapter.GENERIC,
            action_id="run",
        )
        result = gateway.dispatch(req)

        assert result.validation_result is not None
        assert result.validation_result.valid is True
        assert result.validation_result.errors == ()

    def test_dispatch_decision_records_reason(self) -> None:
        """DispatchDecision.reason is always set — no silent dispatch."""
        gateway = ExecutionGateway()
        gateway.register(AdapterRegistration(
            adapter=ExecutionAdapter.HTTP,
            adapter_id="http-client",
            available=True,
            description="HTTP",
        ))
        req = gateway.build_request(
            request_id="audit-trail-004",
            operation_id="op-request",
            adapter_type=ExecutionAdapter.HTTP,
            action_id="post",
        )
        result = gateway.dispatch(req)

        assert result.dispatch_decision.reason != ""
        assert "http-client" in result.dispatch_decision.reason

    def test_workflow_audit_records_all_transitions(self) -> None:
        """WorkflowAudit records every stage transition that occurred."""
        ctx = _stub_context_manager()
        cp = ctx.assemble(query="generate the brief", workspace_id="ws-audit")
        pkg = PromptCompression().compress(cp, documents={})
        routing = ModelRouter().route(pkg, policy=RoutingPolicy.BALANCED)
        wf = WorkflowEngine().build(pkg, routing)

        # At minimum: visited stages are recorded
        assert len(wf.audit.visited_stages) >= 1
        # Current stage is always in visited stages
        assert wf.current_stage in wf.audit.visited_stages

    def test_conductor_decisions_are_human_readable(self) -> None:
        """ConductorDecision.outcome strings are non-empty and informative."""
        conductor = _build_full_conductor()
        request = ConductorRequest(
            request_id="audit-trail-005",
            goal="Review the deployment plan",
            workspace_id="ws-audit",
        )
        result = conductor.orchestrate(request)

        for decision in result.audit.decisions:
            assert isinstance(decision.outcome, str)
            assert len(decision.outcome) > 0
            assert "_" in decision.outcome  # machine-readable format


# ══════════════════════════════════════════════════════════════════════════════
# 10. Extension points — future adapters, skills, operations add without redesign
# ══════════════════════════════════════════════════════════════════════════════


class TestExtensionPoints:
    """Prove the architecture accommodates future extensions without redesign."""

    def test_new_skill_registers_without_gateway_changes(self) -> None:
        """A new Skill can be registered without modifying any engine."""
        registry = SkillRegistry()
        new_skill = _make_skill("ml-training", ["ml-training", "pytorch"])
        result = registry.register(new_skill)

        assert result.status.value == "registered"
        found = registry.find_by_capability("ml-training")
        assert len(found) == 1

    def test_new_adapter_registers_without_gateway_changes(self) -> None:
        """A new adapter can be registered without modifying Gateway dispatch logic."""
        gateway = ExecutionGateway()

        # Adding a new adapter is a registration call — no code change
        registered = gateway.register(AdapterRegistration(
            adapter=ExecutionAdapter.AUTOMATION,
            adapter_id="playwright-browser",
            available=True,
            description="Browser automation via Playwright",
        ))
        assert registered is True

        req = gateway.build_request(
            request_id="ext-001",
            operation_id="op-click",
            adapter_type=ExecutionAdapter.AUTOMATION,
            action_id="click",
        )
        result = gateway.dispatch(req)
        assert result.status == ExecutionStatus.DISPATCHED

    def test_new_operation_type_dispatches_through_gateway(self) -> None:
        """Each OperationType maps to an ExecutionAdapter type."""
        # OperationType values mirror ExecutionAdapter values
        op_type_values = {ot.value for ot in OperationType}
        adapter_values = {ea.value for ea in ExecutionAdapter}
        # Every OperationType must have a corresponding ExecutionAdapter
        assert op_type_values == adapter_values, (
            "OperationType and ExecutionAdapter must remain in sync. "
            f"Missing in adapters: {op_type_values - adapter_values}. "
            f"Missing in operations: {adapter_values - op_type_values}."
        )

    def test_skill_registry_extends_without_conductor_changes(self) -> None:
        """Adding skills to the registry does not require Conductor changes."""
        skills = [
            _make_skill(f"skill-{i}", [f"cap-{i}"])
            for i in range(10)
        ]
        registry = _build_skill_registry(*skills)
        conductor = _build_full_conductor(skill_registry=registry)

        request = ConductorRequest(
            request_id="ext-002",
            goal="Use all capabilities",
            workspace_id="ws-ext",
        )
        result = conductor.orchestrate(request)
        assert result.success is True

    def test_additional_operations_plan_without_engine_changes(self) -> None:
        """OperationEngine plans any number of operations without code changes."""
        engine = OperationEngine()
        ops = [
            engine.build_operation(
                id=f"op-{i}",
                job_id="job-ext",
                goal=f"Step {i}",
                sequence_index=i,
            )
            for i in range(20)
        ]
        results = engine.plan_all(ops, completed_op_ids=frozenset())
        assert len(results) == 20
        assert all(r.status == OperationStatus.READY for r in results)

    def test_gateway_lists_registrations_in_deterministic_order(self) -> None:
        """list_registrations() always returns adapters in lexicographic order."""
        gateway = ExecutionGateway()
        # Register out of alphabetical order
        gateway.register(AdapterRegistration(
            adapter=ExecutionAdapter.LLM, adapter_id="llm", available=True, description="",
        ))
        gateway.register(AdapterRegistration(
            adapter=ExecutionAdapter.GIT, adapter_id="git", available=True, description="",
        ))
        gateway.register(AdapterRegistration(
            adapter=ExecutionAdapter.DOCKER, adapter_id="docker", available=True, description="",
        ))

        registrations = gateway.list_registrations()
        adapter_values = [r.adapter.value for r in registrations]
        assert adapter_values == sorted(adapter_values)
