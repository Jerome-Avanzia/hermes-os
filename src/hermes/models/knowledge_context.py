from dataclasses import dataclass

from hermes.models.knowledge_document import KnowledgeDocument
from hermes.models.project import Project


@dataclass(slots=True)
class KnowledgeContext:
    project: Project
    documents: list[KnowledgeDocument]

    def __str__(self) -> str:
        return f"Knowledge(project={self.project.id}, documents={len(self.documents)})"
