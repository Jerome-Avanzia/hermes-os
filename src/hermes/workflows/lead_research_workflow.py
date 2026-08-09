"""Lead / Company Research Workflow.

Execution flow:
  Founder → FounderJobRouter → lead.research capability
         → LeadResearchWorkflow → WebResearcher (httpx) → evidence collection
         → synthesis (sales-consultant profile) → CEO review → Founder

Architecture position:
  Parallel to CompetitorIntelligenceWorkflow. Reuses WebResearcher and
  _infer_domain from competitor_intelligence_workflow — no duplication.

  It never calls adapters directly, never writes files, never stores state,
  and never initiates external outreach. The report text is the deliverable.

Evidence protocol (enforced by prompt, not code):
  [FACT]      — directly retrieved from a fetched page; URL cited.
  [INFERRED]  — derived from retrieved facts; basis stated.
  [UNKNOWN]   — not available from evidence; never guessed.

Advisory label: FOUNDER-INTERNAL — ADVISORY ONLY (required on every report).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from hermes.workflows.competitor_intelligence_workflow import (
    FetchResult,
    WebResearcher,
    _infer_domain,
)

if TYPE_CHECKING:
    from hermes.conductor import Conductor

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LeadResearchRequest:
    """Input to the Lead Research workflow."""

    company_name: str
    question: str = ""  # optional qualification context
    workspace_id: str = ""


@dataclass(frozen=True)
class LeadResearchReport:
    """Output of the Lead Research workflow."""

    company_name: str
    question: str
    report_text: str
    pages_fetched: int
    pages_successful: int
    advisory_label: str = "FOUNDER-INTERNAL — ADVISORY ONLY"
    source_log: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------


_SYNTHESIS_PROFILE = "sales-consultant"


class LeadResearchWorkflow:
    """Orchestrates a Lead / Company Research Report.

    Construction::

        workflow = LeadResearchWorkflow(
            conductor=conductor,
            researcher=WebResearcher(),   # or a stub for tests
        )
        report = workflow.execute(LeadResearchRequest(
            company_name="Acme",
            question="Are they a fit for AVANZIA?",
        ))
    """

    def __init__(
        self,
        conductor: "Conductor",
        researcher: WebResearcher | None = None,
    ) -> None:
        self._conductor = conductor
        self._researcher = researcher or WebResearcher()

    def execute(self, request: LeadResearchRequest) -> LeadResearchReport:
        """Run the full lead research pipeline."""
        name = request.company_name
        question = request.question or "General lead qualification"

        logger.info(
            "LeadResearchWorkflow: starting company=%r question=%r",
            name,
            question,
        )

        # Step 1 — Fetch evidence
        pages = self._fetch_company_profile(name)
        successful = [p for p in pages if p.success]
        failed = [p for p in pages if not p.success]

        logger.info(
            "LeadResearchWorkflow: fetched %d pages (%d ok, %d failed)",
            len(pages),
            len(successful),
            len(failed),
        )

        # Step 2 — Build evidence block
        evidence_block = _build_evidence_block(pages)

        # Step 3 — Build synthesis prompt
        prompt = _build_synthesis_prompt(name, question, evidence_block, pages)

        # Step 4 — Stream synthesis from sales-consultant profile
        from hermes.providers.ollama_provider import ChatMessage

        messages: list[ChatMessage] = [
            ChatMessage(role="user", content=prompt),
        ]

        tokens: list[str] = []
        for chunk in self._conductor.stream_chat(messages, profile_id=_SYNTHESIS_PROFILE):
            tokens.append(chunk)
        report_text = "".join(tokens)

        # Step 5 — Build source log
        source_log = tuple(
            f"{p.url} — HTTP {p.http_status if p.http_status is not None else 'ERR'} — {p.title or '(no title)'}"
            for p in pages
        )

        logger.info(
            "LeadResearchWorkflow: synthesis complete length=%d",
            len(report_text),
        )

        return LeadResearchReport(
            company_name=name,
            question=question,
            report_text=report_text,
            pages_fetched=len(pages),
            pages_successful=len(successful),
            source_log=source_log,
        )

    # Alternative TLDs tried when the inferred .com domain yields zero successes.
    _FALLBACK_TLDS = (".io", ".app", ".co", ".ai")

    def _fetch_company_profile(self, company_name: str) -> list[FetchResult]:
        """Fetch evidence for a lead company with multi-source fallback.

        Sources (in order):
        1. Primary domain (inferred via _infer_domain — defaults to .com).
        2. Alternative TLD fallback (.io / .app / .co / .ai) — tried only when
           the primary domain returns zero successful pages.  The first
           alternative that returns a successful homepage replaces the primary
           domain for the remaining pages.
        3. Wikipedia — always fetched as an additional source.  Provides a
           reliable, bot-protection-free overview for well-known companies.
        """
        domain = _infer_domain(company_name)
        results = self._fetch_domain_pages(domain)

        # Fallback: if primary domain returned nothing, try alternative TLDs.
        if not any(r.success for r in results) and domain.endswith(".com"):
            base = domain[:-4]
            for tld in self._FALLBACK_TLDS:
                alt_domain = base + tld
                probe = self._researcher.fetch(f"https://{alt_domain}/")
                logger.debug(
                    "LeadResearchWorkflow: TLD fallback alt_domain=%r success=%s",
                    alt_domain, probe.success,
                )
                if probe.success:
                    logger.info(
                        "LeadResearchWorkflow: primary domain %r failed; "
                        "using alternative domain %r",
                        domain, alt_domain,
                    )
                    results = [probe] + self._fetch_domain_pages(
                        alt_domain, skip_home=True
                    )
                    break

        # Wikipedia: always included as an additional public source.
        wiki_slug = re.sub(r"[^\w]+", "_", company_name.strip()).strip("_")
        wiki_url = f"https://en.wikipedia.org/wiki/{wiki_slug}"
        wiki_result = self._researcher.fetch(wiki_url)
        logger.debug(
            "LeadResearchWorkflow: wikipedia url=%r success=%s",
            wiki_url, wiki_result.success,
        )
        results.append(wiki_result)

        return results

    def _fetch_domain_pages(
        self, domain: str, skip_home: bool = False
    ) -> list[FetchResult]:
        """Fetch the standard page set for a given domain."""
        paths = ["about", "pricing", "team", "blog"]
        if not skip_home:
            paths = [""] + paths
        results = []
        for path in paths:
            url = f"https://{domain}/{path}" if path else f"https://{domain}/"
            result = self._researcher.fetch(url)
            logger.debug(
                "LeadResearchWorkflow: url=%r status=%s success=%s",
                url, result.http_status, result.success,
            )
            results.append(result)
        return results


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------


def _build_evidence_block(pages: list[FetchResult]) -> str:
    parts: list[str] = []
    for p in pages:
        if p.success and p.content:
            parts.append(
                f"--- SOURCE: {p.url} (HTTP {p.http_status}) ---\n"
                f"Title: {p.title}\n"
                f"{p.content}\n"
            )
        else:
            status = str(p.http_status) if p.http_status is not None else "CONNECTION ERROR"
            parts.append(f"--- SOURCE: {p.url} (FAILED: {status}) ---\n[No content retrieved]\n")
    return "\n".join(parts)


def _build_synthesis_prompt(
    company_name: str,
    question: str,
    evidence_block: str,
    pages: list[FetchResult],
) -> str:
    successful_count = sum(1 for p in pages if p.success)
    failed_urls = [p.url for p in pages if not p.success]
    failed_str = ", ".join(failed_urls) if failed_urls else "none"

    return f"""You are producing a Lead Intelligence Report for internal Sales and Business Development use.

COMPANY: {company_name}
QUALIFICATION CONTEXT: {question}

EVIDENCE COLLECTION SUMMARY
  Pages attempted: {len(pages)}
  Pages successful: {successful_count}
  Pages failed: {failed_str}

RETRIEVED EVIDENCE
{evidence_block}

INSTRUCTIONS
1. Produce the report using ONLY the evidence above. Do not use model memory as evidence.
2. Tag every claim with exactly one of:
   [FACT]      — directly from a retrieved page; cite the URL in parentheses.
   [INFERRED]  — derived from retrieved facts; state the basis.
   [UNKNOWN]   — not available from evidence; do not guess.
3. Never invent names, financials, employee counts, contracts, or customer relationships.
4. If a page fetch failed, mark all claims that would depend on it as [UNKNOWN].
5. Every report must open with the advisory label: FOUNDER-INTERNAL — ADVISORY ONLY
6. Focus on commercial relevance: what they do, who they serve, how they make money,
   what signals suggest need or fit, and where AVANZIA could be relevant.

REPORT STRUCTURE (follow exactly):

LEAD INTELLIGENCE REPORT
Advisory label: FOUNDER-INTERNAL — ADVISORY ONLY

COMPANY: {company_name}
QUALIFICATION CONTEXT: {question}

1. COMPANY OVERVIEW
2. PRODUCTS / SERVICES
3. TARGET MARKET
4. BUSINESS MODEL
5. LEADERSHIP & COMPANY SIGNALS
6. LIKELY AVANZIA-RELEVANT OPPORTUNITIES
7. UNKNOWNS / GAPS

EVIDENCE SUMMARY
  Pages fetched: {len(pages)}
  Pages successful: {successful_count}
  Pages failed: {failed_str}

SOURCE LOG
  (list each URL with HTTP status and title)

Produce the complete report now. Do not summarise these instructions. Do not add commentary after the report.
"""


# ---------------------------------------------------------------------------
# FounderJobRouter integration — module-level factory
# ---------------------------------------------------------------------------


def _extract_company_name(objective: str) -> str:
    """Extract the company name from a plain-text Founder objective.

    Handles common phrasings:
      "Research Acme as a potential client"     → "Acme"
      "Research Acme Corp as a potential AVANZIA client" → "Acme Corp"
      "Research acme.com as a lead"             → "acme.com"
      "Qualify Acme as a prospect"              → "Acme"
      "Company research on Acme"                → "Acme"
      "Tell me about Acme"                      → "Acme"

    Falls back to the full objective string so domain inference always has
    something to work with.
    """
    # Pattern 1: "research [company] ..." / "qualify [company] ..." / "profile [company] ..."
    # Split verb match (case-insensitive) from name capture (case-sensitive) so that
    # IGNORECASE does not cause lowercase words like "as" to match Title-case slots.
    verb_m = re.search(
        r"(?:research|qualify|profile|investigate|analyse|analyze)\s+",
        objective,
        re.IGNORECASE,
    )
    if verb_m:
        rest = objective[verb_m.end():]
        name_m = re.match(
            r"([A-Z][A-Za-z0-9]*(?:\s+[A-Z][A-Za-z0-9]*){0,3}|[a-z0-9][a-z0-9.-]+\.[a-z]{2,})",
            rest,
        )
        if name_m:
            return name_m.group(1).strip().rstrip(".,?! ")

    # Pattern 2: "company research on Acme" / "account research for Acme"
    m = re.search(
        r"(?:company|account|lead|client|prospect)\s+research\s+(?:on|for|of)\s+([A-Za-z0-9][^\n,?!]{1,50})",
        objective,
        re.IGNORECASE,
    )
    if m:
        return m.group(1).strip().rstrip(".,?! ")

    # Pattern 3: "tell me about Acme" / "about Acme"
    m = re.search(
        r"(?:tell\s+me\s+about|about)\s+([A-Z][A-Za-z0-9\s\(\)\.]{1,40})",
        objective,
        re.IGNORECASE,
    )
    if m:
        return m.group(1).strip().rstrip(".,?! ")

    return objective  # fallback


def execute_from_objective(
    objective: str,
    workspace_id: str,
    conductor: "Conductor",
) -> str:
    """Factory adapter invoked by FounderJobRouter for lead.research capability.

    Extracts the company name from a free-text Founder objective, constructs
    a LeadResearchRequest, runs the workflow, and returns the report text.
    """
    company_name = _extract_company_name(objective)
    logger.info(
        "execute_from_objective: extracted company_name=%r from objective=%r",
        company_name,
        objective,
    )
    request = LeadResearchRequest(
        company_name=company_name,
        workspace_id=workspace_id,
    )
    workflow = LeadResearchWorkflow(conductor=conductor, researcher=WebResearcher())
    report = workflow.execute(request)
    return report.report_text
