"""Tests for Prompt Compression Engine (Sprint 50).

Validates deterministic prompt rendering: same ContextPackage + same
budget = same PromptPackage.  The engine transforms references into
rendered prompt text using purely algorithmic compression.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes.kernel.architecture_knowledge import ArchitectureKnowledge
from hermes.kernel.capability_engine import CapabilityEngine
from hermes.kernel.context_manager import ContextManager
from hermes.kernel.knowledge_engine import KnowledgeEngine
from hermes.kernel.prompt_compression import (
    BUDGET_PRESETS,
    DEFAULT_BUDGET,
    SECTION_ORDER,
    PromptCompression,
    _PHASE_PRIORITY,
    _TRUNCATION_ORDER,
)
from hermes.models import KnowledgeDocument
from hermes.models.context_package import (
    CapabilityReference,
    ContextPackage,
    KnowledgeReference,
    TokenBudget,
)
from hermes.models.prompt_package import (
    OmissionReason,
    OmittedReference,
    PromptPackage,
    PromptSection,
    TruncationReport,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE_ROOT = REPO_ROOT / "knowledge"


def _engine() -> PromptCompression:
    return PromptCompression()


def _context_manager() -> ContextManager:
    arch = ArchitectureKnowledge(repo_root=REPO_ROOT)
    ke = KnowledgeEngine(knowledge_root=KNOWLEDGE_ROOT, architecture_knowledge=arch)
    ce = CapabilityEngine()
    return ContextManager(knowledge_engine=ke, capability_engine=ce)


def _knowledge_engine() -> KnowledgeEngine:
    arch = ArchitectureKnowledge(repo_root=REPO_ROOT)
    return KnowledgeEngine(knowledge_root=KNOWLEDGE_ROOT, architecture_knowledge=arch)


def _sample_package() -> ContextPackage:
    """A minimal ContextPackage for unit tests."""
    return ContextPackage(
        query="test query",
        workspace_id="TEST",
        knowledge=[
            KnowledgeReference(
                document_id="doc-1",
                title="Test Document",
                source="business",
                category="",
                relevance_score=5.0,
                size=100,
            ),
            KnowledgeReference(
                document_id="arch:architecture:test-arch",
                title="Test Architecture",
                source="architecture",
                category="architecture",
                relevance_score=3.0,
                size=200,
            ),
        ],
        capabilities=[
            CapabilityReference(
                capability_id="cap-1",
                name="Test Capability",
                keywords=["test", "cap"],
                sop_refs=["sop-1"],
                workflow_refs=["wf-1"],
            ),
        ],
        budget=TokenBudget(
            total_knowledge_chars=300,
            total_capability_chars=20,
            knowledge_count=2,
            capability_count=1,
        ),
    )


def _sample_documents() -> dict[str, KnowledgeDocument]:
    """Document content map matching _sample_package references."""
    return {
        "doc-1": KnowledgeDocument(
            id="doc-1",
            title="Test Document",
            path="/tmp/doc-1.md",
            content="This is the business knowledge content for testing.",
        ),
        "arch:architecture:test-arch": KnowledgeDocument(
            id="arch:architecture:test-arch",
            title="Test Architecture",
            path="/tmp/arch.md",
            content="This is the architecture knowledge content.",
        ),
    }


# ── OmissionReason enum (Amendment 1) ────────────────────────────────────


class TestOmissionReason:
    def test_is_enum(self):
        assert isinstance(OmissionReason.BUDGET_EXCEEDED, OmissionReason)

    def test_all_reasons_defined(self):
        names = {r.name for r in OmissionReason}
        assert "BUDGET_EXCEEDED" in names
        assert "LOWER_PRIORITY" in names
        assert "MISSING_DOCUMENT" in names
        assert "EMPTY_CONTENT" in names
        assert "FILTERED" in names

    def test_reasons_have_string_values(self):
        for reason in OmissionReason:
            assert isinstance(reason.value, str)

    def test_omitted_reference_uses_enum(self):
        o = OmittedReference(
            reference_id="ref-1",
            title="Title",
            source="knowledge",
            reason=OmissionReason.BUDGET_EXCEEDED,
        )
        assert o.reason == OmissionReason.BUDGET_EXCEEDED
        assert isinstance(o.reason, OmissionReason)

    def test_missing_document_reason(self):
        docs = {"doc-1": KnowledgeDocument(
            id="doc-1", title="Test", path="/tmp/d.md", content="Content.",
        )}
        result = _engine().compress(_sample_package(), docs)
        missing = [
            o for o in result.truncation_report.omitted_references
            if o.reason == OmissionReason.MISSING_DOCUMENT
        ]
        assert len(missing) > 0
        assert missing[0].reference_id == "arch:architecture:test-arch"

    def test_empty_content_reason(self):
        docs = {
            "doc-1": KnowledgeDocument(
                id="doc-1", title="Test", path="/tmp/d.md", content="  ",
            ),
            "arch:architecture:test-arch": KnowledgeDocument(
                id="arch:architecture:test-arch",
                title="Arch", path="/tmp/a.md", content="Real content.",
            ),
        }
        result = _engine().compress(_sample_package(), docs)
        empty = [
            o for o in result.truncation_report.omitted_references
            if o.reason == OmissionReason.EMPTY_CONTENT
        ]
        assert len(empty) > 0
        assert empty[0].reference_id == "doc-1"

    def test_budget_exceeded_reason(self):
        result = _engine().compress(
            _sample_package(), _sample_documents(), budget=200,
        )
        budget_omissions = [
            o for o in result.truncation_report.omitted_references
            if o.reason == OmissionReason.BUDGET_EXCEEDED
        ]
        assert len(budget_omissions) > 0


# ── PromptPackage model ──────────────────────────────────────────────────


class TestPromptPackageModel:
    def test_prompt_section_fields(self):
        s = PromptSection(name="test", content="abc", estimated_chars=3)
        assert s.name == "test"
        assert s.content == "abc"
        assert s.truncated is False
        assert s.original_chars == 0

    def test_truncated_section(self):
        s = PromptSection(
            name="test", content="ab", estimated_chars=2,
            truncated=True, original_chars=100,
        )
        assert s.truncated is True
        assert s.original_chars == 100

    def test_omitted_reference_fields(self):
        o = OmittedReference(
            reference_id="ref-1", title="Title",
            source="knowledge", reason=OmissionReason.BUDGET_EXCEEDED,
            original_chars=500,
        )
        assert o.reference_id == "ref-1"
        assert o.reason == OmissionReason.BUDGET_EXCEEDED

    def test_truncation_report_defaults(self):
        r = TruncationReport()
        assert r.total_sections == 0
        assert r.truncated_sections == 0
        assert r.omitted_references == []
        assert r.utilization == 0.0
        assert r.chars_removed == 0
        assert r.sections_removed == 0
        assert r.references_removed == 0

    def test_prompt_package_full_prompt(self):
        pkg = PromptPackage(
            system_prompt="System prompt.",
            sections=[
                PromptSection(name="a", content="Section A", estimated_chars=9),
                PromptSection(name="b", content="Section B", estimated_chars=9),
            ],
            estimated_chars=30,
            truncation_report=TruncationReport(),
            query="test",
            workspace_id="ws",
        )
        full = pkg.full_prompt
        assert "System prompt." in full
        assert "Section A" in full
        assert "Section B" in full
        assert full.startswith("System prompt.")

    def test_prompt_package_section_names(self):
        pkg = PromptPackage(
            system_prompt="x",
            sections=[
                PromptSection(name="header", content="h", estimated_chars=1),
                PromptSection(name="architecture", content="a", estimated_chars=1),
            ],
            estimated_chars=2,
            truncation_report=TruncationReport(),
            query="q",
            workspace_id="ws",
        )
        assert pkg.section_names == ["header", "architecture"]

    def test_prompt_package_omitted_count(self):
        pkg = PromptPackage(
            system_prompt="x",
            sections=[],
            estimated_chars=0,
            truncation_report=TruncationReport(
                omitted_references=[
                    OmittedReference(
                        "r1", "T1", "knowledge",
                        OmissionReason.BUDGET_EXCEEDED,
                    ),
                ],
            ),
            query="q",
            workspace_id="ws",
        )
        assert pkg.omitted_count == 1

    def test_empty_sections_excluded_from_full_prompt(self):
        pkg = PromptPackage(
            system_prompt="System.",
            sections=[
                PromptSection(name="a", content="Content", estimated_chars=7),
                PromptSection(name="b", content="", estimated_chars=0),
            ],
            estimated_chars=7,
            truncation_report=TruncationReport(),
            query="q",
            workspace_id="ws",
        )
        assert pkg.full_prompt == "System.\n\nContent"


# ── Expanded TruncationReport (Amendment 2) ──────────────────────────────


class TestExpandedTruncationReport:
    def test_report_has_chars_removed(self):
        result = _engine().compress(
            _sample_package(), _sample_documents(), budget="32k",
        )
        assert isinstance(result.truncation_report.chars_removed, int)
        assert result.truncation_report.chars_removed >= 0

    def test_report_has_sections_removed(self):
        result = _engine().compress(
            _sample_package(), _sample_documents(), budget="32k",
        )
        assert isinstance(result.truncation_report.sections_removed, int)

    def test_report_has_references_removed(self):
        result = _engine().compress(
            _sample_package(), _sample_documents(), budget="32k",
        )
        assert isinstance(result.truncation_report.references_removed, int)

    def test_no_truncation_zero_chars_removed(self):
        result = _engine().compress(
            _sample_package(), _sample_documents(), budget="32k",
        )
        assert result.truncation_report.chars_removed == 0

    def test_truncation_positive_chars_removed(self):
        result = _engine().compress(
            _sample_package(), _sample_documents(), budget=200,
        )
        assert result.truncation_report.chars_removed > 0

    def test_sections_removed_matches_budget_omissions(self):
        result = _engine().compress(
            _sample_package(), _sample_documents(), budget=200,
        )
        budget_omissions = [
            o for o in result.truncation_report.omitted_references
            if o.reason == OmissionReason.BUDGET_EXCEEDED
            or o.reason == OmissionReason.LOWER_PRIORITY
        ]
        assert result.truncation_report.sections_removed == len(budget_omissions)

    def test_references_removed_matches_total_omitted(self):
        result = _engine().compress(
            _sample_package(), _sample_documents(), budget="8k",
        )
        assert result.truncation_report.references_removed == len(
            result.truncation_report.omitted_references,
        )

    def test_budget_chars_equals_requested(self):
        result = _engine().compress(
            _sample_package(), _sample_documents(), budget="16k",
        )
        assert result.truncation_report.budget_chars == BUDGET_PRESETS["16k"]

    def test_rendered_chars_equals_estimated(self):
        result = _engine().compress(
            _sample_package(), _sample_documents(), budget="8k",
        )
        assert result.truncation_report.rendered_chars == result.estimated_chars

    def test_utilization_correct(self):
        result = _engine().compress(
            _sample_package(), _sample_documents(), budget="8k",
        )
        expected = result.estimated_chars / BUDGET_PRESETS["8k"]
        assert abs(result.truncation_report.utilization - expected) < 0.001


# ── SECTION_ORDER rendering invariant (Amendment 3) ──────────────────────


class TestSectionOrder:
    def test_section_order_is_list(self):
        assert isinstance(SECTION_ORDER, list)

    def test_section_order_has_all_phases(self):
        expected = {"header", "architecture", "business_knowledge",
                    "capabilities", "contracts", "standards"}
        assert set(SECTION_ORDER) == expected

    def test_section_order_no_duplicates(self):
        assert len(SECTION_ORDER) == len(set(SECTION_ORDER))

    def test_header_is_first_in_order(self):
        assert SECTION_ORDER[0] == "header"

    def test_truncation_order_is_list(self):
        assert isinstance(_TRUNCATION_ORDER, list)

    def test_truncation_order_sorted_by_priority(self):
        priorities = [_PHASE_PRIORITY.get(name, 0) for name in _TRUNCATION_ORDER]
        assert priorities == sorted(priorities)

    def test_output_sections_follow_section_order(self):
        result = _engine().compress(_sample_package(), _sample_documents())
        names = result.section_names
        # Verify order: each section must appear after the ones before it
        # in SECTION_ORDER
        order_positions = {name: i for i, name in enumerate(SECTION_ORDER)}
        positions = [order_positions[n] for n in names if n in order_positions]
        assert positions == sorted(positions), (
            f"Sections not in SECTION_ORDER: {names}"
        )

    def test_output_sections_never_out_of_order(self):
        """Even with truncation, remaining sections preserve SECTION_ORDER."""
        result = _engine().compress(
            _sample_package(), _sample_documents(), budget=500,
        )
        names = result.section_names
        order_positions = {name: i for i, name in enumerate(SECTION_ORDER)}
        positions = [order_positions[n] for n in names if n in order_positions]
        assert positions == sorted(positions)

    def test_full_prompt_sections_in_order(self):
        """full_prompt concatenation follows section list order."""
        result = _engine().compress(_sample_package(), _sample_documents())
        full = result.full_prompt
        last_pos = -1
        for section in result.sections:
            if section.content:
                pos = full.find(section.content[:50])
                assert pos > last_pos, (
                    f"Section {section.name} appears out of order in full_prompt"
                )
                last_pos = pos


# ── Recommended budget metadata (Amendment 4) ────────────────────────────


class TestRecommendedBudget:
    def test_recommended_budget_present(self):
        result = _engine().compress(_sample_package(), _sample_documents())
        assert isinstance(result.recommended_budget, str)
        assert result.recommended_budget != ""

    def test_recommended_budget_is_valid_preset(self):
        result = _engine().compress(_sample_package(), _sample_documents())
        assert result.recommended_budget in BUDGET_PRESETS

    def test_recommended_budget_fits_content(self):
        result = _engine().compress(
            _sample_package(), _sample_documents(), budget="32k",
        )
        recommended_chars = BUDGET_PRESETS[result.recommended_budget]
        assert result.estimated_chars <= recommended_chars

    def test_recommended_budget_is_smallest_fit(self):
        result = _engine().compress(
            _sample_package(), _sample_documents(), budget="32k",
        )
        preset_order = ["4k", "8k", "16k", "32k"]
        idx = preset_order.index(result.recommended_budget)
        # All smaller presets should NOT fit
        for smaller in preset_order[:idx]:
            assert result.estimated_chars > BUDGET_PRESETS[smaller]

    def test_recommended_budget_deterministic(self):
        r1 = _engine().compress(_sample_package(), _sample_documents())
        r2 = _engine().compress(_sample_package(), _sample_documents())
        assert r1.recommended_budget == r2.recommended_budget

    def test_large_content_recommends_larger_budget(self):
        big_docs = {}
        refs = []
        for i in range(20):
            doc_id = f"doc-{i}"
            refs.append(KnowledgeReference(
                document_id=doc_id, title=f"Doc {i}",
                source="business", category="",
                relevance_score=float(20 - i), size=5000,
            ))
            big_docs[doc_id] = KnowledgeDocument(
                id=doc_id, title=f"Doc {i}",
                path=f"/tmp/{doc_id}.md", content="x" * 5000,
            )
        big_pkg = ContextPackage(
            query="test", workspace_id="TEST",
            knowledge=refs, capabilities=[],
            budget=TokenBudget(total_knowledge_chars=100000, knowledge_count=20),
        )
        result = _engine().compress(big_pkg, big_docs, budget="32k")
        # Large content should recommend at least 8k
        assert result.recommended_budget in ("8k", "16k", "32k")

    def test_default_value_is_empty_string(self):
        """PromptPackage default recommended_budget is empty string."""
        pkg = PromptPackage(
            system_prompt="x", sections=[], estimated_chars=0,
            truncation_report=TruncationReport(), query="q", workspace_id="ws",
        )
        assert pkg.recommended_budget == ""


# ── Budget presets ────────────────────────────────────────────────────────


class TestBudgetPresets:
    def test_all_presets_defined(self):
        assert "4k" in BUDGET_PRESETS
        assert "8k" in BUDGET_PRESETS
        assert "16k" in BUDGET_PRESETS
        assert "32k" in BUDGET_PRESETS

    def test_presets_are_ascending(self):
        values = [BUDGET_PRESETS[k] for k in ["4k", "8k", "16k", "32k"]]
        assert values == sorted(values)
        assert all(v > 0 for v in values)

    def test_default_budget_is_valid(self):
        assert DEFAULT_BUDGET in BUDGET_PRESETS

    def test_resolve_preset_string(self):
        engine = _engine()
        assert engine._resolve_budget("4k") == BUDGET_PRESETS["4k"]
        assert engine._resolve_budget("8k") == BUDGET_PRESETS["8k"]

    def test_resolve_integer_budget(self):
        engine = _engine()
        assert engine._resolve_budget(5000) == 5000

    def test_resolve_unknown_preset_raises(self):
        engine = _engine()
        with pytest.raises(ValueError, match="Unknown budget preset"):
            engine._resolve_budget("99k")

    def test_resolve_case_insensitive(self):
        engine = _engine()
        assert engine._resolve_budget("4K") == BUDGET_PRESETS["4k"]
        assert engine._resolve_budget("  8k  ") == BUDGET_PRESETS["8k"]


# ── Phase configuration ──────────────────────────────────────────────────


class TestPhaseConfiguration:
    def test_phase_order_defined(self):
        assert len(SECTION_ORDER) >= 5

    def test_all_phases_have_priority(self):
        for phase in SECTION_ORDER:
            assert phase in _PHASE_PRIORITY, f"Missing priority for {phase}"

    def test_header_has_highest_priority(self):
        assert _PHASE_PRIORITY["header"] >= 100

    def test_priorities_are_positive(self):
        for phase, priority in _PHASE_PRIORITY.items():
            assert priority > 0, f"Non-positive priority for {phase}"


# ── Basic compression ────────────────────────────────────────────────────


class TestBasicCompression:
    def test_returns_prompt_package(self):
        result = _engine().compress(_sample_package(), _sample_documents())
        assert isinstance(result, PromptPackage)

    def test_preserves_query(self):
        result = _engine().compress(_sample_package(), _sample_documents())
        assert result.query == "test query"

    def test_preserves_workspace_id(self):
        result = _engine().compress(_sample_package(), _sample_documents())
        assert result.workspace_id == "TEST"

    def test_system_prompt_not_empty(self):
        result = _engine().compress(_sample_package(), _sample_documents())
        assert len(result.system_prompt) > 0

    def test_system_prompt_mentions_workspace(self):
        result = _engine().compress(_sample_package(), _sample_documents())
        assert "TEST" in result.system_prompt

    def test_sections_not_empty(self):
        result = _engine().compress(_sample_package(), _sample_documents())
        assert len(result.sections) > 0

    def test_has_header_section(self):
        result = _engine().compress(_sample_package(), _sample_documents())
        names = result.section_names
        assert "header" in names

    def test_estimated_chars_positive(self):
        result = _engine().compress(_sample_package(), _sample_documents())
        assert result.estimated_chars > 0

    def test_truncation_report_present(self):
        result = _engine().compress(_sample_package(), _sample_documents())
        assert isinstance(result.truncation_report, TruncationReport)

    def test_report_section_count_matches(self):
        result = _engine().compress(_sample_package(), _sample_documents())
        assert result.truncation_report.total_sections == len(result.sections)


# ── Content rendering ────────────────────────────────────────────────────


class TestContentRendering:
    def test_business_knowledge_rendered(self):
        result = _engine().compress(_sample_package(), _sample_documents())
        assert "business knowledge content" in result.full_prompt

    def test_architecture_knowledge_rendered(self):
        result = _engine().compress(_sample_package(), _sample_documents())
        assert "architecture knowledge content" in result.full_prompt

    def test_capability_rendered(self):
        result = _engine().compress(_sample_package(), _sample_documents())
        assert "Test Capability" in result.full_prompt

    def test_capability_keywords_rendered(self):
        result = _engine().compress(_sample_package(), _sample_documents())
        assert "test, cap" in result.full_prompt

    def test_capability_sop_refs_rendered(self):
        result = _engine().compress(_sample_package(), _sample_documents())
        assert "sop-1" in result.full_prompt

    def test_capability_workflow_refs_rendered(self):
        result = _engine().compress(_sample_package(), _sample_documents())
        assert "wf-1" in result.full_prompt

    def test_query_appears_in_header(self):
        result = _engine().compress(_sample_package(), _sample_documents())
        header = [s for s in result.sections if s.name == "header"][0]
        assert "test query" in header.content

    def test_document_titles_rendered(self):
        result = _engine().compress(_sample_package(), _sample_documents())
        assert "Test Document" in result.full_prompt
        assert "Test Architecture" in result.full_prompt


# ── Ordering preservation ────────────────────────────────────────────────


class TestOrderingPreservation:
    def test_header_comes_first(self):
        result = _engine().compress(_sample_package(), _sample_documents())
        assert result.sections[0].name == "header"

    def test_architecture_before_business(self):
        result = _engine().compress(_sample_package(), _sample_documents())
        names = result.section_names
        if "architecture" in names and "business_knowledge" in names:
            assert names.index("architecture") < names.index("business_knowledge")

    def test_knowledge_before_capabilities(self):
        result = _engine().compress(_sample_package(), _sample_documents())
        names = result.section_names
        knowledge_names = {"architecture", "business_knowledge"}
        knowledge_indices = [
            names.index(n) for n in knowledge_names if n in names
        ]
        if "capabilities" in names and knowledge_indices:
            assert max(knowledge_indices) < names.index("capabilities")


# ── Budget enforcement ────────────────────────────────────────────────────


class TestBudgetEnforcement:
    def test_output_within_budget(self):
        result = _engine().compress(
            _sample_package(), _sample_documents(), budget="4k",
        )
        assert result.estimated_chars <= BUDGET_PRESETS["4k"]

    def test_small_budget_triggers_truncation(self):
        big_docs = {}
        refs = []
        for i in range(20):
            doc_id = f"doc-{i}"
            refs.append(KnowledgeReference(
                document_id=doc_id, title=f"Document {i}",
                source="business", category="",
                relevance_score=float(20 - i), size=2000,
            ))
            big_docs[doc_id] = KnowledgeDocument(
                id=doc_id, title=f"Document {i}",
                path=f"/tmp/{doc_id}.md", content="x" * 2000,
            )
        big_package = ContextPackage(
            query="test", workspace_id="TEST",
            knowledge=refs, capabilities=[],
            budget=TokenBudget(total_knowledge_chars=40000, knowledge_count=20),
        )
        result = _engine().compress(big_package, big_docs, budget=500)
        assert result.estimated_chars <= 500
        assert result.truncation_report.truncated_sections > 0 or result.omitted_count > 0

    def test_large_budget_no_truncation(self):
        result = _engine().compress(
            _sample_package(), _sample_documents(), budget="32k",
        )
        assert result.truncation_report.truncated_sections == 0
        budget_omissions = [
            o for o in result.truncation_report.omitted_references
            if o.reason == OmissionReason.BUDGET_EXCEEDED
        ]
        assert len(budget_omissions) == 0

    def test_integer_budget_works(self):
        result = _engine().compress(
            _sample_package(), _sample_documents(), budget=50000,
        )
        assert isinstance(result, PromptPackage)
        assert result.estimated_chars <= 50000

    def test_utilization_between_zero_and_one(self):
        result = _engine().compress(
            _sample_package(), _sample_documents(), budget="8k",
        )
        assert 0.0 <= result.truncation_report.utilization <= 1.0

    def test_budget_chars_recorded_in_report(self):
        result = _engine().compress(
            _sample_package(), _sample_documents(), budget="16k",
        )
        assert result.truncation_report.budget_chars == BUDGET_PRESETS["16k"]

    def test_header_never_truncated(self):
        """Even with tiny budget, header survives."""
        result = _engine().compress(
            _sample_package(), _sample_documents(), budget=500,
        )
        names = result.section_names
        assert "header" in names


# ── Missing document handling ─────────────────────────────────────────────


class TestMissingDocuments:
    def test_missing_document_omitted(self):
        partial_docs = {
            "doc-1": KnowledgeDocument(
                id="doc-1", title="Test Document",
                path="/tmp/doc-1.md", content="Content here.",
            ),
        }
        result = _engine().compress(_sample_package(), partial_docs)
        omitted_ids = [
            o.reference_id for o in result.truncation_report.omitted_references
        ]
        assert "arch:architecture:test-arch" in omitted_ids

    def test_missing_document_has_correct_reason(self):
        partial_docs = {
            "doc-1": KnowledgeDocument(
                id="doc-1", title="Test Document",
                path="/tmp/doc-1.md", content="Content here.",
            ),
        }
        result = _engine().compress(_sample_package(), partial_docs)
        missing = [
            o for o in result.truncation_report.omitted_references
            if o.reference_id == "arch:architecture:test-arch"
        ]
        assert missing[0].reason == OmissionReason.MISSING_DOCUMENT

    def test_empty_content_omitted(self):
        docs = {
            "doc-1": KnowledgeDocument(
                id="doc-1", title="Test Document",
                path="/tmp/doc-1.md", content="  ",
            ),
            "arch:architecture:test-arch": KnowledgeDocument(
                id="arch:architecture:test-arch",
                title="Test Architecture",
                path="/tmp/arch.md", content="Real content.",
            ),
        }
        result = _engine().compress(_sample_package(), docs)
        empty = [
            o for o in result.truncation_report.omitted_references
            if o.reference_id == "doc-1"
        ]
        assert empty[0].reason == OmissionReason.EMPTY_CONTENT

    def test_all_documents_missing_produces_valid_package(self):
        result = _engine().compress(_sample_package(), {})
        assert isinstance(result, PromptPackage)
        assert len(result.system_prompt) > 0


# ── Determinism ──────────────────────────────────────────────────────────


class TestDeterminism:
    def test_same_inputs_produce_same_output(self):
        pkg = _sample_package()
        docs = _sample_documents()
        r1 = _engine().compress(pkg, docs, budget="8k")
        r2 = _engine().compress(pkg, docs, budget="8k")

        assert r1.full_prompt == r2.full_prompt
        assert r1.estimated_chars == r2.estimated_chars
        assert r1.section_names == r2.section_names

    def test_same_inputs_same_truncation_report(self):
        pkg = _sample_package()
        docs = _sample_documents()
        r1 = _engine().compress(pkg, docs, budget="4k")
        r2 = _engine().compress(pkg, docs, budget="4k")

        assert r1.truncation_report.truncated_sections == r2.truncation_report.truncated_sections
        assert r1.truncation_report.rendered_chars == r2.truncation_report.rendered_chars
        assert r1.truncation_report.chars_removed == r2.truncation_report.chars_removed
        assert r1.truncation_report.sections_removed == r2.truncation_report.sections_removed
        assert r1.truncation_report.references_removed == r2.truncation_report.references_removed
        assert len(r1.truncation_report.omitted_references) == len(r2.truncation_report.omitted_references)

    def test_same_inputs_same_recommended_budget(self):
        pkg = _sample_package()
        docs = _sample_documents()
        r1 = _engine().compress(pkg, docs, budget="8k")
        r2 = _engine().compress(pkg, docs, budget="8k")
        assert r1.recommended_budget == r2.recommended_budget

    def test_different_budgets_may_produce_different_output(self):
        pkg = _sample_package()
        docs = _sample_documents()
        r_small = _engine().compress(pkg, docs, budget=200)
        r_large = _engine().compress(pkg, docs, budget="32k")
        assert r_small.estimated_chars <= r_large.estimated_chars


# ── Edge cases ───────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_package(self):
        empty = ContextPackage(
            query="", workspace_id="EMPTY",
            knowledge=[], capabilities=[],
            budget=TokenBudget(),
        )
        result = _engine().compress(empty, {})
        assert isinstance(result, PromptPackage)
        assert len(result.system_prompt) > 0

    def test_empty_query(self):
        pkg = ContextPackage(
            query="", workspace_id="TEST",
            knowledge=_sample_package().knowledge,
            capabilities=_sample_package().capabilities,
            budget=_sample_package().budget,
        )
        result = _engine().compress(pkg, _sample_documents())
        assert "(no query)" in result.full_prompt

    def test_no_capabilities(self):
        pkg = ContextPackage(
            query="test", workspace_id="TEST",
            knowledge=_sample_package().knowledge,
            capabilities=[],
            budget=TokenBudget(knowledge_count=2),
        )
        result = _engine().compress(pkg, _sample_documents())
        assert "capabilities" not in result.section_names

    def test_only_capabilities(self):
        pkg = ContextPackage(
            query="test", workspace_id="TEST",
            knowledge=[],
            capabilities=_sample_package().capabilities,
            budget=TokenBudget(capability_count=1),
        )
        result = _engine().compress(pkg, {})
        assert "capabilities" in result.section_names

    def test_capability_with_all_refs(self):
        cap = CapabilityReference(
            capability_id="full-cap", name="Full Capability",
            keywords=["a", "b"],
            sop_refs=["sop-1"], repository_refs=["repo-1"],
            workflow_refs=["wf-1"], table_refs=["tbl-1"],
            model_refs=["mdl-1"],
        )
        pkg = ContextPackage(
            query="test", workspace_id="TEST",
            knowledge=[], capabilities=[cap],
            budget=TokenBudget(capability_count=1),
        )
        result = _engine().compress(pkg, {})
        full = result.full_prompt
        assert "repo-1" in full
        assert "tbl-1" in full
        assert "mdl-1" in full


# ── Integration with real data ────────────────────────────────────────────


class TestRealDataIntegration:
    """End-to-end: Context Manager → Prompt Compression with real workspace."""

    def _compress_real(
        self, query: str, budget: str = "8k",
    ) -> PromptPackage:
        cm = _context_manager()
        ke = _knowledge_engine()
        engine = _engine()

        package = cm.assemble(query, "AVANZIA")
        context = ke.load_with_architecture("AVANZIA")
        doc_map = {doc.id: doc for doc in context.documents}

        return engine.compress(package, doc_map, budget=budget)

    def test_real_compression_returns_prompt_package(self):
        result = self._compress_real("executive intelligence")
        assert isinstance(result, PromptPackage)

    def test_real_compression_has_content(self):
        result = self._compress_real("executive intelligence")
        assert result.estimated_chars > 0
        assert len(result.sections) > 1

    def test_real_compression_within_budget(self):
        result = self._compress_real("architecture debt", budget="4k")
        assert result.estimated_chars <= BUDGET_PRESETS["4k"]

    def test_real_compression_deterministic(self):
        r1 = self._compress_real("goal specification KPI")
        r2 = self._compress_real("goal specification KPI")
        assert r1.full_prompt == r2.full_prompt
        assert r1.recommended_budget == r2.recommended_budget

    def test_real_compression_system_prompt_mentions_workspace(self):
        result = self._compress_real("brand personality")
        assert "AVANZIA" in result.system_prompt

    def test_real_compression_utilization_reasonable(self):
        result = self._compress_real("executive intelligence", budget="32k")
        assert result.truncation_report.utilization > 0.0
        assert result.truncation_report.utilization <= 1.0

    def test_real_compression_4k_may_truncate(self):
        result = self._compress_real(
            "architecture decision standard governance", budget="4k",
        )
        assert isinstance(result, PromptPackage)
        assert result.estimated_chars <= BUDGET_PRESETS["4k"]

    def test_real_compression_has_recommended_budget(self):
        result = self._compress_real("executive intelligence")
        assert result.recommended_budget in BUDGET_PRESETS

    def test_real_compression_sections_follow_order(self):
        result = self._compress_real("architecture debt")
        names = result.section_names
        order_positions = {name: i for i, name in enumerate(SECTION_ORDER)}
        positions = [order_positions[n] for n in names if n in order_positions]
        assert positions == sorted(positions)

    def test_real_compression_omission_reasons_are_enum(self):
        result = self._compress_real("architecture debt", budget="4k")
        for o in result.truncation_report.omitted_references:
            assert isinstance(o.reason, OmissionReason)
