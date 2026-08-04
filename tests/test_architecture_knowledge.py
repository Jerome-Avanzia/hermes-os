"""Tests for Architecture Knowledge (Sprint 48).

Validates that architecture documents, decision records, engineering
standards, specifications, and contracts are discoverable and loadable
as first-class KnowledgeDocument instances.
"""

from pathlib import Path

import pytest

from hermes.kernel.architecture_knowledge import (
    ARCHITECTURE_SOURCES,
    ArchitectureKnowledge,
    ArchitectureSource,
)
from hermes.kernel.knowledge_engine import KnowledgeEngine
from hermes.models import KnowledgeDocument

REPO_ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE_ROOT = REPO_ROOT / "knowledge"


def _arch() -> ArchitectureKnowledge:
    return ArchitectureKnowledge(repo_root=REPO_ROOT)


def _engine() -> KnowledgeEngine:
    return KnowledgeEngine(
        knowledge_root=KNOWLEDGE_ROOT,
        architecture_knowledge=_arch(),
    )


# ── ArchitectureSource data structure ──────────────────────────────────────


class TestArchitectureSource:
    def test_source_is_frozen(self):
        source = ARCHITECTURE_SOURCES[0]
        with pytest.raises(AttributeError):
            source.category = "changed"

    def test_all_sources_have_required_fields(self):
        for source in ARCHITECTURE_SOURCES:
            assert source.category
            assert source.directory
            assert source.pattern
            assert source.description

    def test_six_source_categories_declared(self):
        categories = [s.category for s in ARCHITECTURE_SOURCES]
        assert "architecture" in categories
        assert "governance" in categories
        assert "decisions" in categories
        assert "standards" in categories
        assert "specifications" in categories
        assert "contracts" in categories

    def test_categories_are_unique(self):
        categories = [s.category for s in ARCHITECTURE_SOURCES]
        assert len(categories) == len(set(categories))


# ── ArchitectureKnowledge.load() ───────────────────────────────────────────


class TestArchitectureKnowledgeLoad:
    def test_load_returns_list_of_knowledge_documents(self):
        docs = _arch().load()
        assert isinstance(docs, list)
        assert all(isinstance(d, KnowledgeDocument) for d in docs)

    def test_load_returns_documents(self):
        docs = _arch().load()
        assert len(docs) > 0

    def test_document_ids_have_arch_prefix(self):
        docs = _arch().load()
        for doc in docs:
            assert doc.id.startswith("arch:"), f"ID missing arch: prefix: {doc.id}"

    def test_document_ids_include_category(self):
        docs = _arch().load()
        categories = {s.category for s in ARCHITECTURE_SOURCES}
        for doc in docs:
            parts = doc.id.split(":")
            assert len(parts) == 3, f"ID should be arch:category:stem, got: {doc.id}"
            assert parts[1] in categories, f"Unknown category in ID: {doc.id}"

    def test_document_ids_are_unique(self):
        docs = _arch().load()
        ids = [d.id for d in docs]
        assert len(ids) == len(set(ids)), f"Duplicate IDs found: {ids}"

    def test_documents_have_content(self):
        docs = _arch().load()
        for doc in docs:
            assert doc.content.strip(), f"Empty content: {doc.id}"

    def test_documents_have_titles(self):
        docs = _arch().load()
        for doc in docs:
            assert doc.title, f"Empty title: {doc.id}"

    def test_documents_have_paths(self):
        docs = _arch().load()
        for doc in docs:
            assert doc.path, f"Empty path: {doc.id}"
            assert Path(doc.path).exists(), f"Path does not exist: {doc.path}"


# ── Category filtering ────────────────────────────────────────────────────


class TestCategoryFiltering:
    def test_filter_single_category(self):
        docs = _arch().load(categories=["decisions"])
        assert len(docs) > 0
        for doc in docs:
            assert ":decisions:" in doc.id

    def test_filter_multiple_categories(self):
        docs = _arch().load(categories=["decisions", "standards"])
        assert len(docs) > 0
        for doc in docs:
            assert ":decisions:" in doc.id or ":standards:" in doc.id

    def test_filter_nonexistent_category_returns_empty(self):
        docs = _arch().load(categories=["nonexistent"])
        assert docs == []

    def test_none_categories_loads_all(self):
        all_docs = _arch().load(categories=None)
        filtered_docs = _arch().load(categories=["decisions"])
        assert len(all_docs) > len(filtered_docs)


# ── Known documents exist ─────────────────────────────────────────────────


class TestKnownDocuments:
    """Verify that specific architecture documents we know exist are found."""

    def _ids(self) -> set[str]:
        return {d.id for d in _arch().load()}

    def test_hermes_constitution_loaded(self):
        assert "arch:architecture:HERMES_CONSTITUTION" in self._ids()

    def test_executive_intelligence_loaded(self):
        assert "arch:architecture:executive-intelligence" in self._ids()

    def test_architecture_debt_register_loaded(self):
        assert "arch:architecture:architecture-debt-register" in self._ids()

    def test_founder_playbook_loaded(self):
        assert "arch:governance:founder-development-playbook" in self._ids()

    def test_hermes_principles_loaded(self):
        assert "arch:governance:HERMES_PRINCIPLES" in self._ids()

    def test_project_log_loaded(self):
        assert "arch:governance:99-project-log" in self._ids()

    def test_adr_0001_loaded(self):
        assert "arch:decisions:ADR-0001-canonical-hermes-workspace-shell" in self._ids()

    def test_adr_0004_loaded(self):
        assert "arch:decisions:ADR-0004-operation-unit-of-business-execution" in self._ids()

    def test_dec_0002_loaded(self):
        assert "arch:decisions:DEC-0002-hermes-executive-operating-model" in self._ids()

    def test_engineering_standard_loaded(self):
        assert "arch:standards:ENGINEERING" in self._ids()

    def test_goal_spec_loaded(self):
        assert "arch:specifications:goal" in self._ids()

    def test_operation_contract_loaded(self):
        assert "arch:contracts:operation.schema" in self._ids()


# ── Deduplication ──────────────────────────────────────────────────────────


class TestDeduplication:
    def test_no_duplicate_paths(self):
        """docs/architecture/*.md is a subset of docs/*.md — must not duplicate."""
        docs = _arch().load()
        paths = [Path(d.path).resolve() for d in docs]
        assert len(paths) == len(set(paths)), "Duplicate file paths found"


# ── list_sources() ─────────────────────────────────────────────────────────


class TestListSources:
    def test_returns_list(self):
        sources = _arch().list_sources()
        assert isinstance(sources, list)

    def test_returns_all_categories(self):
        sources = _arch().list_sources()
        categories = {s["category"] for s in sources}
        assert "architecture" in categories
        assert "decisions" in categories
        assert "standards" in categories
        assert "specifications" in categories
        assert "contracts" in categories

    def test_each_source_has_required_fields(self):
        for source in _arch().list_sources():
            assert "category" in source
            assert "directory" in source
            assert "description" in source
            assert "document_count" in source
            assert "available" in source

    def test_available_sources_have_documents(self):
        for source in _arch().list_sources():
            if source["available"]:
                assert source["document_count"] > 0, (
                    f"Available source {source['category']} has 0 documents"
                )


# ── KnowledgeEngine integration ───────────────────────────────────────────


class TestKnowledgeEngineIntegration:
    def test_load_architecture_returns_documents(self):
        docs = _engine().load_architecture()
        assert isinstance(docs, list)
        assert len(docs) > 0
        assert all(isinstance(d, KnowledgeDocument) for d in docs)

    def test_load_architecture_with_category_filter(self):
        docs = _engine().load_architecture(categories=["decisions"])
        assert len(docs) > 0
        for doc in docs:
            assert ":decisions:" in doc.id

    def test_load_with_architecture_merges_both_sources(self):
        context = _engine().load_with_architecture("AVANZIA")
        ids = [d.id for d in context.documents]

        # Business documents present (no arch: prefix)
        business_docs = [i for i in ids if not i.startswith("arch:")]
        assert len(business_docs) > 0

        # Architecture documents present (arch: prefix)
        arch_docs = [i for i in ids if i.startswith("arch:")]
        assert len(arch_docs) > 0

    def test_load_with_architecture_business_docs_come_first(self):
        context = _engine().load_with_architecture("AVANZIA")
        first_doc = context.documents[0]
        assert not first_doc.id.startswith("arch:"), (
            "First document should be business knowledge, not architecture"
        )

    def test_load_with_architecture_preserves_project(self):
        context = _engine().load_with_architecture("AVANZIA")
        assert context.project.id == "AVANZIA"

    def test_list_architecture_sources_delegates(self):
        sources = _engine().list_architecture_sources()
        assert isinstance(sources, list)
        assert len(sources) == len(ARCHITECTURE_SOURCES)


# ── Selection with architecture documents ──────────────────────────────────


class TestSelectionWithArchitecture:
    """Architecture documents participate in the same selection as business docs."""

    def test_select_finds_architecture_document_by_title(self):
        context = _engine().load_with_architecture("AVANZIA")
        result = _engine().select(context.documents, "executive intelligence")
        titles = [d.title for d in result]
        assert "Executive Intelligence" in titles

    def test_select_finds_decision_record(self):
        context = _engine().load_with_architecture("AVANZIA")
        result = _engine().select(context.documents, "operation unit of business execution")
        ids = [d.id for d in result]
        assert any("ADR-0004" in i for i in ids)

    def test_select_finds_architecture_debt(self):
        context = _engine().load_with_architecture("AVANZIA")
        result = _engine().select(context.documents, "architecture debt")
        ids = [d.id for d in result]
        assert any("architecture-debt-register" in i for i in ids)

    def test_select_finds_engineering_standard(self):
        context = _engine().load_with_architecture("AVANZIA")
        result = _engine().select(context.documents, "engineering standard")
        ids = [d.id for d in result]
        assert any("ENGINEERING" in i for i in ids)

    def test_select_finds_playbook(self):
        context = _engine().load_with_architecture("AVANZIA")
        result = _engine().select(context.documents, "development playbook sprint")
        ids = [d.id for d in result]
        assert any("playbook" in i for i in ids)

    def test_select_finds_readiness_engine_docs(self):
        context = _engine().load_with_architecture("AVANZIA")
        result = _engine().select(context.documents, "readiness scenario evaluation")
        ids = [d.id for d in result]
        assert any("executive-intelligence" in i for i in ids)

    def test_business_query_still_finds_business_docs(self):
        """Architecture docs should not crowd out business knowledge."""
        context = _engine().load_with_architecture("AVANZIA")
        result = _engine().select(context.documents, "brand personality")
        first = result[0]
        assert not first.id.startswith("arch:"), (
            "Business query should return business docs first"
        )


# ── Edge cases ─────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_missing_repo_root_directory(self, tmp_path):
        """ArchitectureKnowledge with empty root returns empty list."""
        arch = ArchitectureKnowledge(repo_root=tmp_path)
        docs = arch.load()
        assert docs == []

    def test_missing_single_source_directory(self, tmp_path):
        """Missing individual source directories are skipped gracefully."""
        # Create only the decisions directory
        decisions_dir = tmp_path / "decisions"
        decisions_dir.mkdir()
        (decisions_dir / "DEC-0001-test.md").write_text("# Test Decision\n\nContent.")

        arch = ArchitectureKnowledge(repo_root=tmp_path)
        docs = arch.load()
        assert len(docs) == 1
        assert docs[0].id == "arch:decisions:DEC-0001-test"

    def test_unreadable_file_skipped(self, tmp_path):
        """Files that cannot be read are skipped with a warning."""
        docs_dir = tmp_path / "decisions"
        docs_dir.mkdir()
        bad_file = docs_dir / "bad.md"
        bad_file.write_bytes(b"\x80\x81\x82\x83")  # invalid UTF-8

        arch = ArchitectureKnowledge(repo_root=tmp_path)
        docs = arch.load(categories=["decisions"])
        # Should skip the bad file, not crash
        assert isinstance(docs, list)

    def test_empty_file_loads_with_fallback_title(self, tmp_path):
        """Empty files get filename stem as title."""
        specs_dir = tmp_path / "specs"
        specs_dir.mkdir()
        (specs_dir / "empty.md").write_text("")

        arch = ArchitectureKnowledge(repo_root=tmp_path)
        docs = arch.load(categories=["specifications"])
        # Empty content is technically valid
        assert len(docs) == 1
        assert docs[0].title == "empty"

    def test_title_extraction_from_heading(self, tmp_path):
        decisions_dir = tmp_path / "decisions"
        decisions_dir.mkdir()
        (decisions_dir / "test.md").write_text(
            "---\nid: DEC\n---\n\n# My Decision Title\n\nBody text."
        )
        arch = ArchitectureKnowledge(repo_root=tmp_path)
        docs = arch.load(categories=["decisions"])
        assert docs[0].title == "My Decision Title"

    def test_title_extraction_skips_frontmatter_dashes(self, tmp_path):
        """YAML frontmatter lines starting with --- should not be titles."""
        decisions_dir = tmp_path / "decisions"
        decisions_dir.mkdir()
        (decisions_dir / "test.md").write_text(
            "---\nid: test\n---\n\n# Real Title\n\nContent."
        )
        arch = ArchitectureKnowledge(repo_root=tmp_path)
        docs = arch.load(categories=["decisions"])
        assert docs[0].title == "Real Title"
