from pathlib import Path

from hermes.kernel.knowledge_engine import KnowledgeEngine
from hermes.models import KnowledgeContext, Project

REPO_ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE_ROOT = REPO_ROOT / "knowledge"


def _load_avanzia() -> KnowledgeContext:
    engine = KnowledgeEngine(knowledge_root=KNOWLEDGE_ROOT)
    return engine.load("AVANZIA")


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
