"""ContextPackage — typed contract between Context Manager and Prompt Compression.

Sprint 49: The ContextPackage is a deterministic, provider-independent
selection of knowledge and capabilities relevant to a specific request.

The Context Manager produces it.  Prompt Compression (Sprint 50) will
consume it.  This data structure is the boundary between selection and
serialization.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class KnowledgeReference:
    """A selected knowledge document with relevance metadata."""

    document_id: str
    title: str
    source: str                       # "business" | "architecture"
    category: str                     # e.g. "decisions", "governance", "" for business
    relevance_score: float            # from KnowledgeEngine scoring
    size: int                         # content length in characters


@dataclass(slots=True)
class CapabilityReference:
    """A matched capability with cross-references to related resources."""

    capability_id: str
    name: str
    keywords: list[str]
    sop_refs: list[str] = field(default_factory=list)
    repository_refs: list[str] = field(default_factory=list)
    workflow_refs: list[str] = field(default_factory=list)
    table_refs: list[str] = field(default_factory=list)
    model_refs: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TokenBudget:
    """Estimated token budget metadata for downstream prompt generation.

    Character counts are provided so that Prompt Compression (Sprint 50)
    can allocate tokens without re-reading documents.  No actual
    tokenization is performed here — character counts are a proxy.
    """

    total_knowledge_chars: int = 0    # sum of all selected knowledge content
    total_capability_chars: int = 0   # sum of capability description text
    knowledge_count: int = 0          # number of knowledge documents selected
    capability_count: int = 0         # number of capabilities matched


@dataclass(slots=True)
class ContextPackage:
    """Deterministic selection of context for a specific request.

    Produced by the Context Manager.  Consumed by Prompt Compression.
    Contains only references and metadata — no serialized prompt text.
    """

    query: str
    workspace_id: str
    knowledge: list[KnowledgeReference]
    capabilities: list[CapabilityReference]
    budget: TokenBudget

    @property
    def business_knowledge(self) -> list[KnowledgeReference]:
        """Knowledge references from business sources only."""
        return [k for k in self.knowledge if k.source == "business"]

    @property
    def architecture_knowledge(self) -> list[KnowledgeReference]:
        """Knowledge references from architecture sources only."""
        return [k for k in self.knowledge if k.source == "architecture"]
