from dataclasses import dataclass


@dataclass(slots=True)
class KnowledgeDocument:
    id: str
    title: str
    path: str
    content: str
