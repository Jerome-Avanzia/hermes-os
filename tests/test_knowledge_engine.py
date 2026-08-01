from pathlib import Path

from hermes.kernel.knowledge_engine import KnowledgeEngine
from hermes.models import KnowledgeContext, KnowledgeDocument, Project

REPO_ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE_ROOT = REPO_ROOT / "knowledge"


def _engine() -> KnowledgeEngine:
    return KnowledgeEngine(knowledge_root=KNOWLEDGE_ROOT)


def _load_avanzia() -> KnowledgeContext:
    return _engine().load("AVANZIA")


def test_project_loads():
    context = _load_avanzia()

    assert isinstance(context.project, Project)
    assert context.project.id == "AVANZIA"
    assert context.project.name == "AVANZIA"
    assert context.project.path == str(KNOWLEDGE_ROOT / "AVANZIA")


def test_manifest_loads():
    context = _load_avanzia()

    assert isinstance(context, KnowledgeContext)
    assert isinstance(context.documents, list)
    assert len(context.documents) > 0


def test_twelve_avanzia_documents_load_successfully():
    context = _load_avanzia()

    assert len(context.documents) == 12
    for document in context.documents:
        assert document.content.strip() != ""
        assert document.title != ""


# -- Selection: unit tests -------------------------------------------------


def _make_docs() -> list[KnowledgeDocument]:
    """Minimal synthetic documents for unit testing selection."""
    return [
        KnowledgeDocument(id="purpose", title="Purpose", path="p", content="We exist to build businesses."),
        KnowledgeDocument(id="brand", title="Brand Personality", path="b", content="Strategic, calm, direct."),
        KnowledgeDocument(id="tech", title="Homepage Tech Spec", path="t", content="Next.js TypeScript Tailwind homepage."),
        KnowledgeDocument(id="services", title="Services", path="s", content="AI strategy and automation services."),
    ]


def test_select_returns_list():
    docs = _make_docs()
    result = _engine().select(docs, "brand")
    assert isinstance(result, list)
    assert all(isinstance(d, KnowledgeDocument) for d in result)


def test_select_with_empty_query_returns_manifest_order():
    docs = _make_docs()
    result = _engine().select(docs, "")
    assert [d.id for d in result] == ["purpose", "brand", "tech", "services"]


def test_select_with_none_query_returns_manifest_order():
    docs = _make_docs()
    result = _engine().select(docs, None)
    assert [d.id for d in result] == ["purpose", "brand", "tech", "services"]


def test_select_respects_max_docs():
    docs = _make_docs()
    result = _engine().select(docs, None, max_docs=2)
    assert len(result) == 2
    assert [d.id for d in result] == ["purpose", "brand"]


def test_select_title_match_ranks_higher():
    docs = _make_docs()
    result = _engine().select(docs, "brand")
    assert result[0].id == "brand"


def test_select_content_match_works():
    docs = _make_docs()
    result = _engine().select(docs, "automation")
    assert any(d.id == "services" for d in result)


def test_select_no_match_falls_back_to_manifest_order():
    docs = _make_docs()
    result = _engine().select(docs, "xyznonexistent", max_docs=2)
    assert len(result) == 2
    assert [d.id for d in result] == ["purpose", "brand"]


def test_select_empty_documents_returns_empty():
    result = _engine().select([], "brand")
    assert result == []


def test_select_max_docs_larger_than_results():
    docs = _make_docs()
    result = _engine().select(docs, "brand", max_docs=100)
    # Should return scored docs, not pad to 100
    assert len(result) <= len(docs)


# -- Selection: business acceptance tests ----------------------------------
# These use real AVANZIA knowledge to validate that the Founder gets
# relevant answers to real business questions.


def test_brand_question_returns_brand_document():
    """When the Founder asks about brand, the brand personality doc should be included."""
    context = _load_avanzia()
    result = _engine().select(context.documents, "What is AVANZIA's brand personality?")
    titles = [d.title for d in result]
    assert "AVANZIA Brand Personality" in titles


def test_homepage_question_returns_homepage_documents():
    """When the Founder asks about the homepage, homepage-related docs should rank high."""
    context = _load_avanzia()
    result = _engine().select(context.documents, "How should the homepage be structured?")
    ids = [d.id for d in result]
    assert any("homepage" in doc_id for doc_id in ids)


def test_services_question_returns_services_document():
    """When the Founder asks about services, the services doc should be included."""
    context = _load_avanzia()
    result = _engine().select(context.documents, "What services does AVANZIA offer?")
    titles = [d.title for d in result]
    assert any("Services" in t for t in titles)


def test_purpose_question_returns_purpose_document():
    """When the Founder asks why AVANZIA exists, the purpose doc should rank first."""
    context = _load_avanzia()
    result = _engine().select(context.documents, "Why does AVANZIA exist?")
    assert result[0].title == "AVANZIA Purpose"


def test_generic_greeting_returns_foundational_documents():
    """A generic greeting with no keywords should return docs in manifest order."""
    context = _load_avanzia()
    result = _engine().select(context.documents, "Hello, how are you?", max_docs=3)
    # Fallback: first 3 docs in manifest order
    assert len(result) == 3
    assert result[0].id == "01-purpose"


def test_tech_stack_question_returns_tech_spec():
    """When the Founder asks about the tech stack, the tech spec doc should be included."""
    context = _load_avanzia()
    result = _engine().select(context.documents, "What tech stack should we use for the website?")
    ids = [d.id for d in result]
    assert "12-homepage-tech-spec" in ids
