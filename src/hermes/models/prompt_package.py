"""PromptPackage — typed contract between Prompt Compression and Model Router.

Sprint 50: The PromptPackage is the deterministic, provider-independent
output of the Prompt Compression Engine.  It contains a fully rendered
prompt with metadata about what was included, truncated, or omitted.

The Prompt Compression Engine produces it.  The Model Router (Sprint 51)
will consume it.  This data structure is the boundary between prompt
serialization and model invocation.

Rendering invariant: PromptPackage serialization order is explicit and
deterministic.  Sections are stored in a list (ordered).  No dictionary
iteration or unordered collections are used in the rendering pipeline.
The section order is: header → architecture → business_knowledge →
capabilities → contracts → standards.  This order is enforced by the
SECTION_ORDER constant in prompt_compression.py.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class OmissionReason(enum.Enum):
    """Deterministic reason why a reference was omitted from the prompt.

    Each value represents a specific, testable condition — never free text.
    """

    BUDGET_EXCEEDED = "budget_exceeded"
    LOWER_PRIORITY = "lower_priority"
    MISSING_DOCUMENT = "missing_document"
    EMPTY_CONTENT = "empty_content"
    FILTERED = "filtered"


@dataclass(slots=True)
class PromptSection:
    """A discrete section of the rendered prompt.

    Each section corresponds to a phase in the compression pipeline
    (header, architecture, business knowledge, capabilities, etc.).
    Sections are always stored in deterministic SECTION_ORDER.
    """

    name: str
    content: str
    estimated_chars: int
    truncated: bool = False
    original_chars: int = 0           # pre-truncation size; 0 = no truncation


@dataclass(slots=True)
class OmittedReference:
    """A knowledge or capability reference that was omitted."""

    reference_id: str
    title: str
    source: str                       # "knowledge" | "capability"
    reason: OmissionReason
    original_chars: int = 0


@dataclass(slots=True)
class TruncationReport:
    """Comprehensive summary of compression decisions.

    All fields are deterministic for a given ContextPackage + budget.
    """

    total_sections: int = 0
    truncated_sections: int = 0
    omitted_references: list[OmittedReference] = field(default_factory=list)
    budget_chars: int = 0             # requested budget in characters
    rendered_chars: int = 0           # actual rendered size in characters
    utilization: float = 0.0          # rendered / budget (0.0–1.0)
    chars_removed: int = 0            # total characters removed by truncation
    sections_removed: int = 0         # sections fully removed
    references_removed: int = 0       # individual references omitted


@dataclass(slots=True)
class PromptPackage:
    """Deterministic prompt output from the Prompt Compression Engine.

    Produced by PromptCompression.  Consumed by the Model Router.
    Contains fully rendered prompt text plus compression metadata.

    Rendering invariant: sections is an ordered list following the
    explicit SECTION_ORDER defined in prompt_compression.py.  The
    full_prompt property concatenates in list order.  No unordered
    collections influence serialization.
    """

    system_prompt: str
    sections: list[PromptSection]
    estimated_chars: int
    truncation_report: TruncationReport
    query: str
    workspace_id: str
    recommended_budget: str = ""      # e.g. "4k", "8k", "16k", "32k"

    @property
    def full_prompt(self) -> str:
        """Concatenate system prompt and all sections into a single string.

        Order is deterministic: system_prompt first, then sections in
        list order (which follows SECTION_ORDER).
        """
        parts = [self.system_prompt]
        for section in self.sections:
            if section.content:
                parts.append(section.content)
        return "\n\n".join(parts)

    @property
    def section_names(self) -> list[str]:
        """Names of all rendered sections, in deterministic order."""
        return [s.name for s in self.sections]

    @property
    def omitted_count(self) -> int:
        """Number of references omitted due to any reason."""
        return len(self.truncation_report.omitted_references)
