"""ContextManager — deterministic context selection for a specific request.

Sprint 49: Decides WHAT knowledge and capabilities should be included
for a Founder request.  Produces a typed ContextPackage.

The Context Manager:
- Does NOT generate prompts
- Does NOT call LLMs
- Does NOT compress text
- Does NOT inspect the filesystem directly
- Retrieves information only through existing Hermes abstractions

Selection is deterministic: same query + same workspace state = same
ContextPackage.  No embeddings, no vector search, no AI reasoning.
"""

from __future__ import annotations

import logging

from hermes.kernel.capability_engine import CapabilityEngine
from hermes.kernel.knowledge_engine import KnowledgeEngine
from hermes.models import Capability, KnowledgeDocument, Task
from hermes.models.context_package import (
    CapabilityReference,
    ContextPackage,
    KnowledgeReference,
    TokenBudget,
)

logger = logging.getLogger(__name__)


# ── Architecture category boost rules ──────────────────────────────────────
#
# When the query contains terms from a domain, architecture knowledge in
# the corresponding category receives a relevance boost.  This is pure
# data — no embedded logic.

_CATEGORY_TERM_BOOSTS: dict[str, frozenset[str]] = {
    "architecture": frozenset({
        "architecture", "constitution", "executive", "intelligence",
        "debt", "invariant", "layer",
    }),
    "decisions": frozenset({
        "adr", "dec", "decision", "record", "governance", "delegation",
        "operating", "model",
    }),
    "standards": frozenset({
        "standard", "engineering", "policy", "metadata", "safety",
        "git",
    }),
    "specifications": frozenset({
        "spec", "specification", "business", "object", "goal", "kpi",
        "bottleneck", "opportunity", "experiment", "lesson", "strategy",
    }),
    "contracts": frozenset({
        "contract", "schema", "json", "validation", "payload",
    }),
    "governance": frozenset({
        "principle", "playbook", "sprint", "milestone", "roadmap",
        "deployment", "vision", "operating", "lifecycle",
    }),
}


# ── Context Manager ────────────────────────────────────────────────────────


class ContextManager:
    """Produces a ContextPackage for a specific query.

    Consumes only existing Hermes abstractions:
    - KnowledgeEngine for business and architecture knowledge
    - CapabilityEngine for capability matching
    """

    def __init__(
        self,
        knowledge_engine: KnowledgeEngine,
        capability_engine: CapabilityEngine,
    ) -> None:
        self._knowledge = knowledge_engine
        self._capabilities = capability_engine

    def assemble(
        self,
        query: str,
        workspace_id: str,
        max_knowledge: int = 10,
        max_capabilities: int = 5,
    ) -> ContextPackage:
        """Assemble a ContextPackage for the given query.

        1. Load all available knowledge (business + architecture)
        2. Score and rank by relevance to the query
        3. Apply architecture category boosts for domain-specific queries
        4. Match capabilities
        5. Build token budget metadata

        Returns a fully populated ContextPackage.
        """
        # ── 1. Load knowledge ──────────────────────────────────────────
        try:
            context = self._knowledge.load_with_architecture(workspace_id)
            all_docs = context.documents
        except (ValueError, FileNotFoundError):
            logger.info("No knowledge found for workspace %s", workspace_id)
            all_docs = []

        # ── 2. Score and rank ──────────────────────────────────────────
        scored_docs = self._score_documents(all_docs, query)

        # ── 3. Apply architecture category boosts ──────────────────────
        if query and query.strip():
            scored_docs = self._apply_category_boosts(scored_docs, query)

        # ── 4. Sort by score descending, take top N ────────────────────
        scored_docs.sort(key=lambda pair: (-pair[1], pair[0].id))
        selected = scored_docs[:max_knowledge]

        # ── 5. Match capabilities ──────────────────────────────────────
        task = Task(id="context-manager", business=workspace_id, request=query)
        matched_capabilities = self._capabilities.match(task)[:max_capabilities]

        # ── 6. Build references ────────────────────────────────────────
        knowledge_refs = [
            self._to_knowledge_ref(doc, score) for doc, score in selected
        ]
        capability_refs = [
            self._to_capability_ref(cap) for cap in matched_capabilities
        ]

        # ── 7. Compute budget ──────────────────────────────────────────
        budget = TokenBudget(
            total_knowledge_chars=sum(r.size for r in knowledge_refs),
            total_capability_chars=sum(
                len(cap.name) + sum(len(k) for k in cap.keywords)
                for cap in capability_refs
            ),
            knowledge_count=len(knowledge_refs),
            capability_count=len(capability_refs),
        )

        package = ContextPackage(
            query=query,
            workspace_id=workspace_id,
            knowledge=knowledge_refs,
            capabilities=capability_refs,
            budget=budget,
        )

        logger.info(
            "Context package assembled: query=%r knowledge=%d "
            "(business=%d arch=%d) capabilities=%d budget=%d chars",
            query[:60] if query else "",
            len(knowledge_refs),
            len(package.business_knowledge),
            len(package.architecture_knowledge),
            len(capability_refs),
            budget.total_knowledge_chars,
        )

        return package

    # ── Internal scoring ───────────────────────────────────────────────

    def _score_documents(
        self,
        documents: list[KnowledgeDocument],
        query: str | None,
    ) -> list[tuple[KnowledgeDocument, float]]:
        """Score all documents against the query using KnowledgeEngine scoring.

        Delegates to KnowledgeEngine's existing term extraction and scoring.
        """
        if not documents:
            return []

        if not query or not query.strip():
            # No query: return all docs with score 0 (manifest order)
            return [(doc, 0.0) for doc in documents]

        terms = KnowledgeEngine._extract_terms(query)
        if not terms:
            return [(doc, 0.0) for doc in documents]

        return [
            (doc, KnowledgeEngine._score_document(doc, terms))
            for doc in documents
        ]

    def _apply_category_boosts(
        self,
        scored_docs: list[tuple[KnowledgeDocument, float]],
        query: str,
    ) -> list[tuple[KnowledgeDocument, float]]:
        """Boost architecture documents whose category matches query terms.

        Architecture documents in categories whose domain terms appear
        in the query receive a relevance boost of +2.0 per matching
        category.
        """
        terms = KnowledgeEngine._extract_terms(query)
        if not terms:
            return scored_docs

        query_terms = frozenset(terms)

        # Determine which categories are relevant to this query
        boosted_categories: set[str] = set()
        for category, category_terms in _CATEGORY_TERM_BOOSTS.items():
            if query_terms & category_terms:
                boosted_categories.add(category)

        if not boosted_categories:
            return scored_docs

        result: list[tuple[KnowledgeDocument, float]] = []
        for doc, score in scored_docs:
            if doc.id.startswith("arch:"):
                # Extract category from ID: arch:category:stem
                parts = doc.id.split(":", 2)
                if len(parts) >= 2 and parts[1] in boosted_categories:
                    score += 2.0
            result.append((doc, score))

        return result

    # ── Reference builders ─────────────────────────────────────────────

    @staticmethod
    def _to_knowledge_ref(
        doc: KnowledgeDocument, score: float,
    ) -> KnowledgeReference:
        """Convert a scored KnowledgeDocument to a KnowledgeReference."""
        if doc.id.startswith("arch:"):
            source = "architecture"
            parts = doc.id.split(":", 2)
            category = parts[1] if len(parts) >= 2 else ""
        else:
            source = "business"
            category = ""

        return KnowledgeReference(
            document_id=doc.id,
            title=doc.title,
            source=source,
            category=category,
            relevance_score=score,
            size=len(doc.content),
        )

    @staticmethod
    def _to_capability_ref(cap: Capability) -> CapabilityReference:
        """Convert a Capability to a CapabilityReference."""
        return CapabilityReference(
            capability_id=cap.id,
            name=cap.name,
            keywords=list(cap.keywords),
            sop_refs=list(cap.sop_refs),
            repository_refs=list(cap.repository_refs),
            workflow_refs=list(cap.workflow_refs),
            table_refs=list(cap.table_refs),
            model_refs=list(cap.model_refs),
        )
