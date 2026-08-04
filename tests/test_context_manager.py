"""Tests for Context Manager (Sprint 49).

Validates deterministic context selection: same query + same workspace
state = same ContextPackage.  The Context Manager consumes only existing
Hermes abstractions (KnowledgeEngine, CapabilityEngine) and produces
a typed ContextPackage.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes.kernel.architecture_knowledge import ArchitectureKnowledge
from hermes.kernel.capability_engine import CapabilityEngine
from hermes.kernel.context_manager import ContextManager, _CATEGORY_TERM_BOOSTS
from hermes.kernel.knowledge_engine import KnowledgeEngine
from hermes.models import Capability, KnowledgeDocument
from hermes.models.context_package import (
    CapabilityReference,
    ContextPackage,
    KnowledgeReference,
    TokenBudget,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE_ROOT = REPO_ROOT / "knowledge"


def _manager() -> ContextManager:
    arch = ArchitectureKnowledge(repo_root=REPO_ROOT)
    ke = KnowledgeEngine(knowledge_root=KNOWLEDGE_ROOT, architecture_knowledge=arch)
    ce = CapabilityEngine()
    return ContextManager(knowledge_engine=ke, capability_engine=ce)


# ── ContextPackage data structure ────────────────────────────────────────


class TestContextPackageModel:
    def test_knowledge_reference_fields(self):
        ref = KnowledgeReference(
            document_id="test:doc",
            title="Test",
            source="business",
            category="",
            relevance_score=3.0,
            size=100,
        )
        assert ref.document_id == "test:doc"
        assert ref.source == "business"
        assert ref.relevance_score == 3.0
        assert ref.size == 100

    def test_capability_reference_fields(self):
        ref = CapabilityReference(
            capability_id="cap-1",
            name="Test Cap",
            keywords=["test", "cap"],
        )
        assert ref.capability_id == "cap-1"
        assert ref.keywords == ["test", "cap"]
        assert ref.sop_refs == []
        assert ref.workflow_refs == []

    def test_token_budget_defaults(self):
        budget = TokenBudget()
        assert budget.total_knowledge_chars == 0
        assert budget.total_capability_chars == 0
        assert budget.knowledge_count == 0
        assert budget.capability_count == 0

    def test_context_package_business_knowledge_filter(self):
        biz = KnowledgeReference("b1", "Biz", "business", "", 1.0, 50)
        arch = KnowledgeReference("a1", "Arch", "architecture", "decisions", 2.0, 100)
        pkg = ContextPackage(
            query="test",
            workspace_id="ws",
            knowledge=[biz, arch],
            capabilities=[],
            budget=TokenBudget(),
        )
        assert len(pkg.business_knowledge) == 1
        assert pkg.business_knowledge[0].document_id == "b1"

    def test_context_package_architecture_knowledge_filter(self):
        biz = KnowledgeReference("b1", "Biz", "business", "", 1.0, 50)
        arch = KnowledgeReference("a1", "Arch", "architecture", "decisions", 2.0, 100)
        pkg = ContextPackage(
            query="test",
            workspace_id="ws",
            knowledge=[biz, arch],
            capabilities=[],
            budget=TokenBudget(),
        )
        assert len(pkg.architecture_knowledge) == 1
        assert pkg.architecture_knowledge[0].document_id == "a1"


# ── Category term boosts data ────────────────────────────────────────────


class TestCategoryTermBoosts:
    def test_all_six_categories_declared(self):
        expected = {"architecture", "decisions", "standards",
                    "specifications", "contracts", "governance"}
        assert set(_CATEGORY_TERM_BOOSTS.keys()) == expected

    def test_categories_have_terms(self):
        for category, terms in _CATEGORY_TERM_BOOSTS.items():
            assert len(terms) > 0, f"Empty terms for {category}"
            assert isinstance(terms, frozenset)

    def test_no_overlapping_terms_architecture_governance(self):
        """Architecture and governance terms should not overlap
        (except 'operating' which is deliberate)."""
        arch = _CATEGORY_TERM_BOOSTS["architecture"]
        gov = _CATEGORY_TERM_BOOSTS["governance"]
        overlap = arch & gov
        assert overlap == frozenset(), f"Unexpected overlap: {overlap}"


# ── ContextManager.assemble() ────────────────────────────────────────────


class TestAssemble:
    def test_returns_context_package(self):
        pkg = _manager().assemble("test query", "AVANZIA")
        assert isinstance(pkg, ContextPackage)

    def test_package_preserves_query(self):
        pkg = _manager().assemble("executive intelligence", "AVANZIA")
        assert pkg.query == "executive intelligence"

    def test_package_preserves_workspace_id(self):
        pkg = _manager().assemble("test", "AVANZIA")
        assert pkg.workspace_id == "AVANZIA"

    def test_knowledge_contains_references(self):
        pkg = _manager().assemble("executive intelligence", "AVANZIA")
        assert len(pkg.knowledge) > 0
        assert all(isinstance(k, KnowledgeReference) for k in pkg.knowledge)

    def test_knowledge_references_have_scores(self):
        pkg = _manager().assemble("executive intelligence", "AVANZIA")
        for ref in pkg.knowledge:
            assert isinstance(ref.relevance_score, float)

    def test_knowledge_sorted_by_score_descending(self):
        pkg = _manager().assemble("architecture debt", "AVANZIA")
        scores = [k.relevance_score for k in pkg.knowledge]
        assert scores == sorted(scores, reverse=True)

    def test_max_knowledge_limit(self):
        pkg = _manager().assemble("test", "AVANZIA", max_knowledge=3)
        assert len(pkg.knowledge) <= 3

    def test_max_capabilities_limit(self):
        pkg = _manager().assemble("test", "AVANZIA", max_capabilities=2)
        assert len(pkg.capabilities) <= 2

    def test_budget_knowledge_count_matches(self):
        pkg = _manager().assemble("executive intelligence", "AVANZIA")
        assert pkg.budget.knowledge_count == len(pkg.knowledge)

    def test_budget_capability_count_matches(self):
        pkg = _manager().assemble("test", "AVANZIA")
        assert pkg.budget.capability_count == len(pkg.capabilities)

    def test_budget_total_knowledge_chars_positive(self):
        pkg = _manager().assemble("executive intelligence", "AVANZIA")
        assert pkg.budget.total_knowledge_chars > 0

    def test_budget_total_knowledge_chars_equals_sum(self):
        pkg = _manager().assemble("executive intelligence", "AVANZIA")
        expected = sum(k.size for k in pkg.knowledge)
        assert pkg.budget.total_knowledge_chars == expected


# ── Knowledge source classification ──────────────────────────────────────


class TestKnowledgeSourceClassification:
    def test_business_docs_marked_as_business(self):
        pkg = _manager().assemble("brand personality", "AVANZIA")
        biz = pkg.business_knowledge
        for ref in biz:
            assert ref.source == "business"
            assert ref.category == ""

    def test_architecture_docs_marked_as_architecture(self):
        pkg = _manager().assemble("architecture constitution", "AVANZIA")
        arch = pkg.architecture_knowledge
        for ref in arch:
            assert ref.source == "architecture"
            assert ref.category != ""

    def test_architecture_docs_have_valid_category(self):
        pkg = _manager().assemble("architecture decision standard", "AVANZIA")
        valid = set(_CATEGORY_TERM_BOOSTS.keys())
        for ref in pkg.architecture_knowledge:
            assert ref.category in valid, f"Invalid category: {ref.category}"


# ── Architecture category boosts ─────────────────────────────────────────


class TestCategoryBoosts:
    def test_architecture_query_boosts_architecture_docs(self):
        pkg = _manager().assemble("architecture constitution invariant", "AVANZIA")
        arch = pkg.architecture_knowledge
        assert len(arch) > 0
        # Architecture docs should score higher with boost
        arch_scores = {r.document_id: r.relevance_score for r in arch}
        assert any(s > 0 for s in arch_scores.values())

    def test_decision_query_boosts_decision_docs(self):
        pkg = _manager().assemble("ADR decision record governance", "AVANZIA")
        arch = pkg.architecture_knowledge
        decision_refs = [r for r in arch if r.category == "decisions"]
        assert len(decision_refs) > 0

    def test_specification_query_boosts_spec_docs(self):
        pkg = _manager().assemble("goal specification KPI", "AVANZIA")
        arch = pkg.architecture_knowledge
        spec_refs = [r for r in arch if r.category == "specifications"]
        assert len(spec_refs) > 0

    def test_business_query_no_category_boost(self):
        """Pure business query should not artificially boost arch docs."""
        pkg = _manager().assemble("brand personality customer", "AVANZIA")
        if pkg.knowledge:
            first = pkg.knowledge[0]
            assert first.source == "business", (
                "Business query should rank business docs first"
            )


# ── Determinism ──────────────────────────────────────────────────────────


class TestDeterminism:
    def test_same_query_produces_same_package(self):
        """Core invariant: same inputs = same outputs."""
        pkg1 = _manager().assemble("executive intelligence", "AVANZIA")
        pkg2 = _manager().assemble("executive intelligence", "AVANZIA")

        assert len(pkg1.knowledge) == len(pkg2.knowledge)
        ids1 = [k.document_id for k in pkg1.knowledge]
        ids2 = [k.document_id for k in pkg2.knowledge]
        assert ids1 == ids2

        scores1 = [k.relevance_score for k in pkg1.knowledge]
        scores2 = [k.relevance_score for k in pkg2.knowledge]
        assert scores1 == scores2

    def test_different_queries_may_produce_different_packages(self):
        pkg1 = _manager().assemble("architecture debt", "AVANZIA")
        pkg2 = _manager().assemble("brand personality", "AVANZIA")

        ids1 = [k.document_id for k in pkg1.knowledge]
        ids2 = [k.document_id for k in pkg2.knowledge]
        # Different queries should generally produce different rankings
        assert ids1 != ids2 or pkg1.knowledge[0].relevance_score != pkg2.knowledge[0].relevance_score


# ── Edge cases ───────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_query_returns_package(self):
        pkg = _manager().assemble("", "AVANZIA")
        assert isinstance(pkg, ContextPackage)
        assert len(pkg.knowledge) > 0

    def test_whitespace_query_returns_package(self):
        pkg = _manager().assemble("   ", "AVANZIA")
        assert isinstance(pkg, ContextPackage)

    def test_empty_query_scores_are_zero(self):
        pkg = _manager().assemble("", "AVANZIA")
        for ref in pkg.knowledge:
            assert ref.relevance_score == 0.0

    def test_unknown_workspace_returns_empty_knowledge(self):
        pkg = _manager().assemble("test", "NONEXISTENT_WORKSPACE")
        assert len(pkg.knowledge) == 0

    def test_unknown_workspace_budget_is_zero(self):
        pkg = _manager().assemble("test", "NONEXISTENT_WORKSPACE")
        assert pkg.budget.total_knowledge_chars == 0
        assert pkg.budget.knowledge_count == 0


# ── Capability matching ──────────────────────────────────────────────────


class TestCapabilityMatching:
    def test_capabilities_are_capability_references(self):
        pkg = _manager().assemble("test", "AVANZIA")
        for cap in pkg.capabilities:
            assert isinstance(cap, CapabilityReference)

    def test_capability_references_have_keywords(self):
        pkg = _manager().assemble("test", "AVANZIA")
        for cap in pkg.capabilities:
            assert isinstance(cap.keywords, list)

    def test_capability_refs_have_cross_references(self):
        pkg = _manager().assemble("test", "AVANZIA")
        for cap in pkg.capabilities:
            assert isinstance(cap.sop_refs, list)
            assert isinstance(cap.repository_refs, list)
            assert isinstance(cap.workflow_refs, list)
            assert isinstance(cap.table_refs, list)
            assert isinstance(cap.model_refs, list)


# ── Internal scoring delegation ──────────────────────────────────────────


class TestScoringDelegation:
    """Verify that scoring delegates to KnowledgeEngine's existing methods."""

    def test_score_documents_returns_scored_pairs(self):
        mgr = _manager()
        docs = [
            KnowledgeDocument(id="d1", title="Executive Intelligence",
                              path="/tmp/d1.md", content="The executive layer"),
            KnowledgeDocument(id="d2", title="Brand Guide",
                              path="/tmp/d2.md", content="Brand personality"),
        ]
        scored = mgr._score_documents(docs, "executive intelligence")
        assert len(scored) == 2
        assert all(isinstance(pair, tuple) and len(pair) == 2 for pair in scored)

    def test_score_documents_empty_query(self):
        mgr = _manager()
        docs = [
            KnowledgeDocument(id="d1", title="Test", path="/tmp/d1.md", content="x"),
        ]
        scored = mgr._score_documents(docs, "")
        assert scored[0][1] == 0.0

    def test_score_documents_none_query(self):
        mgr = _manager()
        docs = [
            KnowledgeDocument(id="d1", title="Test", path="/tmp/d1.md", content="x"),
        ]
        scored = mgr._score_documents(docs, None)
        assert scored[0][1] == 0.0

    def test_score_documents_empty_list(self):
        mgr = _manager()
        scored = mgr._score_documents([], "anything")
        assert scored == []

    def test_apply_category_boosts_adds_score(self):
        mgr = _manager()
        doc = KnowledgeDocument(
            id="arch:architecture:test",
            title="Test",
            path="/tmp/test.md",
            content="architecture layer",
        )
        scored = [(doc, 1.0)]
        boosted = mgr._apply_category_boosts(scored, "architecture layer")
        assert boosted[0][1] == 3.0  # 1.0 original + 2.0 boost

    def test_apply_category_boosts_no_boost_for_business(self):
        mgr = _manager()
        doc = KnowledgeDocument(
            id="brand-guide",
            title="Brand Guide",
            path="/tmp/bg.md",
            content="brand personality",
        )
        scored = [(doc, 5.0)]
        boosted = mgr._apply_category_boosts(scored, "architecture layer")
        assert boosted[0][1] == 5.0  # unchanged

    def test_apply_category_boosts_empty_query(self):
        mgr = _manager()
        doc = KnowledgeDocument(
            id="arch:decisions:test",
            title="Test",
            path="/tmp/test.md",
            content="decision",
        )
        scored = [(doc, 1.0)]
        boosted = mgr._apply_category_boosts(scored, "")
        assert boosted[0][1] == 1.0  # no boost on empty query


# ── Reference builders ───────────────────────────────────────────────────


class TestReferenceBuilders:
    def test_to_knowledge_ref_business(self):
        doc = KnowledgeDocument(
            id="brand-guide", title="Brand Guide",
            path="/tmp/bg.md", content="Brand content here",
        )
        ref = ContextManager._to_knowledge_ref(doc, 5.0)
        assert ref.source == "business"
        assert ref.category == ""
        assert ref.relevance_score == 5.0
        assert ref.size == len(doc.content)

    def test_to_knowledge_ref_architecture(self):
        doc = KnowledgeDocument(
            id="arch:decisions:ADR-0001", title="ADR 0001",
            path="/tmp/adr.md", content="Decision record",
        )
        ref = ContextManager._to_knowledge_ref(doc, 7.0)
        assert ref.source == "architecture"
        assert ref.category == "decisions"
        assert ref.relevance_score == 7.0

    def test_to_capability_ref(self):
        cap = Capability(
            id="cap-1",
            name="Test Capability",
            version="1.0",
            provides=["testing"],
            keywords=["test", "cap"],
            sop_refs=["sop-1"],
            repository_refs=["repo-1"],
            workflow_refs=["wf-1"],
            table_refs=["tbl-1"],
            model_refs=["mdl-1"],
        )
        ref = ContextManager._to_capability_ref(cap)
        assert ref.capability_id == "cap-1"
        assert ref.name == "Test Capability"
        assert ref.keywords == ["test", "cap"]
        assert ref.sop_refs == ["sop-1"]
        assert ref.repository_refs == ["repo-1"]
        assert ref.workflow_refs == ["wf-1"]
        assert ref.table_refs == ["tbl-1"]
        assert ref.model_refs == ["mdl-1"]
