"""PromptCompression — deterministic prompt rendering from a ContextPackage.

Sprint 50: Transforms a ContextPackage (what to include) into a
PromptPackage (the rendered prompt).  All decisions are algorithmic —
no AI summarization, no embeddings, no provider logic.

The Prompt Compression Engine:
- Does NOT decide relevance (Context Manager already did that)
- Does NOT retrieve knowledge (receives document content as input)
- Does NOT call any model
- Does NOT know anything about providers
- IS deterministic: same inputs = same output

Rendering invariant: sections are always produced in SECTION_ORDER.
No dictionary iteration or unordered collections influence output.
"""

from __future__ import annotations

import logging

from hermes.models import KnowledgeDocument
from hermes.models.context_package import (
    CapabilityReference,
    ContextPackage,
    KnowledgeReference,
)
from hermes.models.prompt_package import (
    OmissionReason,
    OmittedReference,
    PromptPackage,
    PromptSection,
    TruncationReport,
)

logger = logging.getLogger(__name__)


# ── Token budget presets ──────────────────────────────────────────────────
#
# Approximate character budgets.  1 token ≈ 4 characters is a common
# heuristic; these are conservative estimates leaving room for the
# model's response.

BUDGET_PRESETS: dict[str, int] = {
    "4k": 12_000,
    "8k": 28_000,
    "16k": 56_000,
    "32k": 112_000,
}

DEFAULT_BUDGET = "8k"

# ── Section rendering order ──────────────────────────────────────────────
#
# Sections are ALWAYS rendered in this explicit, deterministic order.
# This is the rendering invariant: no dictionary iteration or unordered
# collections influence the output.  Each phase has a priority that
# determines truncation order (higher priority = truncated last).

SECTION_ORDER: list[str] = [
    "header",
    "architecture",
    "business_knowledge",
    "capabilities",
    "contracts",
    "standards",
]

# Priority determines truncation order: lowest priority gets truncated first.
_PHASE_PRIORITY: dict[str, int] = {
    "header": 100,            # never truncated
    "architecture": 80,
    "capabilities": 70,
    "business_knowledge": 60,
    "contracts": 50,
    "standards": 40,
}

# Sorted ascending for deterministic budget enforcement.
_TRUNCATION_ORDER: list[str] = sorted(
    SECTION_ORDER,
    key=lambda name: _PHASE_PRIORITY.get(name, 0),
)


# ── Prompt Compression Engine ────────────────────────────────────────────


class PromptCompression:
    """Renders a ContextPackage into a PromptPackage.

    Consumes a ContextPackage and a document content map.
    Produces a deterministic PromptPackage within a token budget.

    Rendering invariant: sections follow SECTION_ORDER.  Truncation
    follows _TRUNCATION_ORDER (lowest priority first).  Both are
    explicit lists — never derived from dict iteration.
    """

    def compress(
        self,
        package: ContextPackage,
        documents: dict[str, KnowledgeDocument],
        budget: str | int = DEFAULT_BUDGET,
    ) -> PromptPackage:
        """Compress a ContextPackage into a PromptPackage.

        Args:
            package: The ContextPackage from Context Manager.
            documents: Map of document_id → KnowledgeDocument with content.
            budget: Either a preset name ("4k", "8k", "16k", "32k") or
                    an integer character budget.

        Returns:
            A fully rendered PromptPackage.
        """
        budget_chars = self._resolve_budget(budget)
        budget_label = self._resolve_budget_label(budget)

        # ── 1. Render system prompt ───────────────────────────────────
        system_prompt = self._render_system_prompt(package)

        # ── 2. Render all sections in SECTION_ORDER ───────────────────
        sections, omitted = self._render_sections(package, documents)

        # ── 3. Apply budget truncation ────────────────────────────────
        remaining = budget_chars - len(system_prompt)
        pre_truncation_chars = sum(s.estimated_chars for s in sections)
        sections, extra_omitted = self._apply_budget(sections, remaining)
        omitted.extend(extra_omitted)

        # ── 4. Compute totals ─────────────────────────────────────────
        rendered_chars = len(system_prompt) + sum(
            s.estimated_chars for s in sections
        )
        truncated_count = sum(1 for s in sections if s.truncated)
        sections_removed = sum(
            1 for o in omitted
            if o.reason == OmissionReason.BUDGET_EXCEEDED
            or o.reason == OmissionReason.LOWER_PRIORITY
        )
        chars_removed = pre_truncation_chars - sum(
            s.estimated_chars for s in sections
        )

        report = TruncationReport(
            total_sections=len(sections),
            truncated_sections=truncated_count,
            omitted_references=omitted,
            budget_chars=budget_chars,
            rendered_chars=rendered_chars,
            utilization=(
                rendered_chars / budget_chars if budget_chars > 0 else 0.0
            ),
            chars_removed=chars_removed,
            sections_removed=sections_removed,
            references_removed=len(omitted),
        )

        # ── 5. Determine recommended budget ───────────────────────────
        recommended = self._recommend_budget(rendered_chars, budget_label)

        prompt_package = PromptPackage(
            system_prompt=system_prompt,
            sections=sections,
            estimated_chars=rendered_chars,
            truncation_report=report,
            query=package.query,
            workspace_id=package.workspace_id,
            recommended_budget=recommended,
        )

        logger.info(
            "Prompt compressed: sections=%d truncated=%d omitted=%d "
            "budget=%d rendered=%d utilization=%.1f%% recommended=%s",
            len(sections),
            truncated_count,
            len(omitted),
            budget_chars,
            rendered_chars,
            report.utilization * 100,
            recommended,
        )

        return prompt_package

    # ── System prompt ─────────────────────────────────────────────────

    @staticmethod
    def _render_system_prompt(package: ContextPackage) -> str:
        """Render the fixed system prompt header."""
        return (
            f"You are Hermes, the executive intelligence for "
            f"workspace '{package.workspace_id}'.\n"
            f"Answer the following request using only the context "
            f"provided below.\n"
            f"Do not invent information beyond what is given."
        )

    # ── Section rendering (SECTION_ORDER enforced) ────────────────────

    def _render_sections(
        self,
        package: ContextPackage,
        documents: dict[str, KnowledgeDocument],
    ) -> tuple[list[PromptSection], list[OmittedReference]]:
        """Render all sections from the ContextPackage in SECTION_ORDER.

        Rendering invariant: sections are always appended in the order
        defined by SECTION_ORDER.  This is an explicit list — no
        dictionary iteration or unordered collections.
        """
        # Build all sections keyed by name, in SECTION_ORDER
        rendered: dict[str, PromptSection] = {}
        omitted: list[OmittedReference] = []

        arch_refs = package.architecture_knowledge
        biz_refs = package.business_knowledge

        # Render each phase
        for phase in SECTION_ORDER:
            if phase == "header":
                rendered["header"] = self._render_header(package)

            elif phase == "architecture":
                section, phase_omitted = self._render_knowledge_section(
                    "architecture", "Architecture Knowledge", arch_refs, documents,
                )
                if section.content:
                    rendered["architecture"] = section
                omitted.extend(phase_omitted)

            elif phase == "business_knowledge":
                section, phase_omitted = self._render_knowledge_section(
                    "business_knowledge", "Business Knowledge", biz_refs, documents,
                )
                if section.content:
                    rendered["business_knowledge"] = section
                omitted.extend(phase_omitted)

            elif phase == "capabilities":
                section = self._render_capabilities(package.capabilities)
                if section.content:
                    rendered["capabilities"] = section

            elif phase == "contracts":
                contract_refs = [r for r in arch_refs if r.category == "contracts"]
                if contract_refs:
                    section, phase_omitted = self._render_knowledge_section(
                        "contracts", "Contracts", contract_refs, documents,
                    )
                    if section.content:
                        rendered["contracts"] = section
                    omitted.extend(phase_omitted)

            elif phase == "standards":
                standard_refs = [r for r in arch_refs if r.category == "standards"]
                if standard_refs:
                    section, phase_omitted = self._render_knowledge_section(
                        "standards", "Standards", standard_refs, documents,
                    )
                    if section.content:
                        rendered["standards"] = section
                    omitted.extend(phase_omitted)

        # Assemble sections in SECTION_ORDER (deterministic)
        sections = [rendered[name] for name in SECTION_ORDER if name in rendered]

        return sections, omitted

    @staticmethod
    def _render_header(package: ContextPackage) -> PromptSection:
        """Render the context header with query and metadata."""
        lines = [
            f"## Request\n\n{package.query}" if package.query else "## Request\n\n(no query)",
            f"\n\n## Context Summary\n\n"
            f"- Knowledge documents: {package.budget.knowledge_count}\n"
            f"- Capabilities: {package.budget.capability_count}\n"
            f"- Workspace: {package.workspace_id}",
        ]
        content = "".join(lines)
        return PromptSection(
            name="header",
            content=content,
            estimated_chars=len(content),
        )

    def _render_knowledge_section(
        self,
        name: str,
        heading: str,
        refs: list[KnowledgeReference],
        documents: dict[str, KnowledgeDocument],
    ) -> tuple[PromptSection, list[OmittedReference]]:
        """Render a knowledge section from references.

        Preserves ordering from the ContextPackage (list iteration).
        """
        parts: list[str] = []
        omitted: list[OmittedReference] = []

        for ref in refs:
            doc = documents.get(ref.document_id)
            if doc is None:
                omitted.append(OmittedReference(
                    reference_id=ref.document_id,
                    title=ref.title,
                    source="knowledge",
                    reason=OmissionReason.MISSING_DOCUMENT,
                    original_chars=ref.size,
                ))
                continue

            if not doc.content.strip():
                omitted.append(OmittedReference(
                    reference_id=ref.document_id,
                    title=ref.title,
                    source="knowledge",
                    reason=OmissionReason.EMPTY_CONTENT,
                    original_chars=ref.size,
                ))
                continue

            parts.append(f"### {ref.title}\n\n{doc.content}")

        if not parts:
            return (
                PromptSection(name=name, content="", estimated_chars=0),
                omitted,
            )

        content = f"## {heading}\n\n" + "\n\n---\n\n".join(parts)
        return (
            PromptSection(
                name=name,
                content=content,
                estimated_chars=len(content),
            ),
            omitted,
        )

    @staticmethod
    def _render_capabilities(
        capabilities: list[CapabilityReference],
    ) -> PromptSection:
        """Render capability references as a structured section."""
        if not capabilities:
            return PromptSection(
                name="capabilities", content="", estimated_chars=0,
            )

        parts: list[str] = []
        for cap in capabilities:
            lines = [f"### {cap.name}"]
            if cap.keywords:
                lines.append(f"Keywords: {', '.join(cap.keywords)}")
            if cap.sop_refs:
                lines.append(f"SOPs: {', '.join(cap.sop_refs)}")
            if cap.workflow_refs:
                lines.append(f"Workflows: {', '.join(cap.workflow_refs)}")
            if cap.repository_refs:
                lines.append(f"Repositories: {', '.join(cap.repository_refs)}")
            if cap.table_refs:
                lines.append(f"Tables: {', '.join(cap.table_refs)}")
            if cap.model_refs:
                lines.append(f"Models: {', '.join(cap.model_refs)}")
            parts.append("\n".join(lines))

        content = "## Capabilities\n\n" + "\n\n".join(parts)
        return PromptSection(
            name="capabilities",
            content=content,
            estimated_chars=len(content),
        )

    # ── Budget enforcement ────────────────────────────────────────────

    def _apply_budget(
        self,
        sections: list[PromptSection],
        remaining_chars: int,
    ) -> tuple[list[PromptSection], list[OmittedReference]]:
        """Truncate sections to fit within the character budget.

        Sections are truncated in _TRUNCATION_ORDER (an explicit list
        sorted by ascending priority).  The header is never truncated.
        """
        total = sum(s.estimated_chars for s in sections)
        if total <= remaining_chars:
            return sections, []

        # Build index by name for O(1) lookup
        section_by_name: dict[str, int] = {
            s.name: i for i, s in enumerate(sections)
        }

        omitted: list[OmittedReference] = []
        overflow = total - remaining_chars

        # Truncate in deterministic _TRUNCATION_ORDER (lowest priority first)
        for phase_name in _TRUNCATION_ORDER:
            if overflow <= 0:
                break

            if phase_name not in section_by_name:
                continue

            idx = section_by_name[phase_name]
            section = sections[idx]

            priority = _PHASE_PRIORITY.get(section.name, 0)
            if priority >= 100:
                # Never truncate header
                continue

            if section.estimated_chars <= overflow:
                # Remove entire section
                overflow -= section.estimated_chars
                omitted.append(OmittedReference(
                    reference_id=section.name,
                    title=section.name,
                    source="knowledge",
                    reason=OmissionReason.BUDGET_EXCEEDED,
                    original_chars=section.estimated_chars,
                ))
                sections[idx] = PromptSection(
                    name=section.name,
                    content="",
                    estimated_chars=0,
                    truncated=True,
                    original_chars=section.estimated_chars,
                )
            else:
                # Truncate section content to fit
                keep = section.estimated_chars - overflow
                truncated_content = section.content[:keep]
                # Cut at last newline to avoid broken lines
                last_nl = truncated_content.rfind("\n")
                if last_nl > 0:
                    truncated_content = truncated_content[:last_nl]

                truncated_content += "\n\n[... truncated due to budget ...]"
                sections[idx] = PromptSection(
                    name=section.name,
                    content=truncated_content,
                    estimated_chars=len(truncated_content),
                    truncated=True,
                    original_chars=section.estimated_chars,
                )
                overflow = 0

        # Remove empty sections (fully truncated)
        result = [s for s in sections if s.content]
        return result, omitted

    # ── Budget resolution ─────────────────────────────────────────────

    @staticmethod
    def _resolve_budget(budget: str | int) -> int:
        """Resolve a budget specification to a character count."""
        if isinstance(budget, int):
            return budget
        key = budget.lower().strip()
        if key in BUDGET_PRESETS:
            return BUDGET_PRESETS[key]
        raise ValueError(
            f"Unknown budget preset: {budget!r}. "
            f"Valid presets: {', '.join(sorted(BUDGET_PRESETS.keys()))}"
        )

    @staticmethod
    def _resolve_budget_label(budget: str | int) -> str:
        """Resolve a budget specification to a preset label, or empty."""
        if isinstance(budget, str):
            key = budget.lower().strip()
            if key in BUDGET_PRESETS:
                return key
        return ""

    @staticmethod
    def _recommend_budget(rendered_chars: int, current_label: str) -> str:
        """Recommend the smallest budget preset that fits the rendered content.

        Returns a preset label ("4k", "8k", "16k", "32k").  If the
        content fits within the current budget, recommends the current.
        If not, recommends the smallest preset that would fit.
        """
        # Check presets in ascending order (deterministic — sorted list)
        preset_order = ["4k", "8k", "16k", "32k"]
        for preset in preset_order:
            if rendered_chars <= BUDGET_PRESETS[preset]:
                return preset

        # Content exceeds all presets — recommend the largest
        return preset_order[-1]
