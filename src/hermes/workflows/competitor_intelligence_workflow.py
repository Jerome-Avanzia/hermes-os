"""Competitor Intelligence Workflow.

Execution flow:
  Founder → CEO → Business Strategy Consultant → Competitor Intelligence SOP
         → WebResearcher (httpx) → evidence collection → synthesis → report

Architecture position:
  This workflow is a pure orchestrator. It:
    1. Resolves the competitor's web domain.
    2. Fetches public pages via WebResearcher (injectable for tests).
    3. Builds a research prompt with retrieved evidence.
    4. Calls Conductor.stream_chat() with the strategy-consultant profile.
    5. Returns a CompetitorIntelligenceReport.

  It never calls adapters directly, never writes files, never stores state.
  The report text is the deliverable — storage/routing is the caller's responsibility.

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

if TYPE_CHECKING:
    from hermes.conductor import Conductor

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FetchResult:
    """Result of a single HTTP fetch attempt."""

    url: str
    http_status: int | None  # None = connection error / timeout
    success: bool
    title: str
    content: str  # visible text, truncated


@dataclass(frozen=True)
class CompetitorIntelligenceRequest:
    """Input to the Competitor Intelligence workflow."""

    competitor_name: str
    question: str = ""  # optional business question
    workspace_id: str = ""


@dataclass(frozen=True)
class CompetitorIntelligenceReport:
    """Output of the Competitor Intelligence workflow."""

    competitor_name: str
    question: str
    report_text: str
    pages_fetched: int
    pages_successful: int
    advisory_label: str = "FOUNDER-INTERNAL — ADVISORY ONLY"
    source_log: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# WebResearcher — injectable for testing
# ---------------------------------------------------------------------------


class WebResearcher:
    """Fetches public web pages for evidence collection.

    Dependency-injectable: tests supply a stub that returns pre-built FetchResult
    objects without making real HTTP calls.
    """

    _STRIP_TAGS = re.compile(r"<[^>]+>")
    _COLLAPSE_WS = re.compile(r"\s+")

    def __init__(self, timeout: float = 10.0) -> None:
        self._timeout = timeout

    def fetch(self, url: str) -> FetchResult:
        """Fetch a URL and return a FetchResult.  Never raises."""
        try:
            import httpx

            resp = httpx.get(
                url,
                follow_redirects=True,
                timeout=self._timeout,
                headers={"User-Agent": "Hermes-Intelligence/1.0"},
            )
            content = self._extract_text(resp.content)
            title = self._extract_title(resp.text)
            return FetchResult(
                url=url,
                http_status=resp.status_code,
                success=resp.status_code == 200,
                title=title,
                content=content[:3000],
            )
        except Exception as exc:
            logger.debug("WebResearcher.fetch failed url=%r: %s", url, exc)
            return FetchResult(
                url=url,
                http_status=None,
                success=False,
                title="",
                content="",
            )

    def fetch_competitor_profile(self, competitor_name: str) -> list[FetchResult]:
        """Fetch the standard page set for a competitor."""
        domain = _infer_domain(competitor_name)
        urls = [
            f"https://{domain}/",
            f"https://{domain}/pricing",
            f"https://{domain}/about",
            f"https://{domain}/blog",
            f"https://{domain}/customers",
        ]
        results = []
        for url in urls:
            result = self.fetch(url)
            logger.debug(
                "WebResearcher: url=%r status=%s success=%s",
                url,
                result.http_status,
                result.success,
            )
            results.append(result)
        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @classmethod
    def _extract_text(cls, html_bytes: bytes) -> str:
        try:
            text = html_bytes.decode("utf-8", errors="replace")
        except Exception:
            return ""
        text = cls._STRIP_TAGS.sub(" ", text)
        text = cls._COLLAPSE_WS.sub(" ", text)
        return text.strip()

    @classmethod
    def _extract_title(cls, html: str) -> str:
        m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        return ""


# ---------------------------------------------------------------------------
# Domain inference
# ---------------------------------------------------------------------------


def _infer_domain(competitor_name: str) -> str:
    """Infer the primary web domain from a competitor name.

    Handles:
      - "Acme (acme.com)"  → "acme.com"
      - "acme.com"         → "acme.com"
      - "Acme Corp"        → "acmecorp.com"
    """
    # parenthetical domain: "Name (domain.tld)"
    m = re.search(r"\(([a-z0-9.-]+\.[a-z]{2,})\)", competitor_name, re.IGNORECASE)
    if m:
        return m.group(1).lower()

    # bare domain: contains a dot with a known TLD suffix
    stripped = competitor_name.strip()
    if re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", stripped, re.IGNORECASE):
        return stripped.lower()

    # plain name: slugify and append .com
    slug = re.sub(r"[^a-z0-9]+", "", competitor_name.lower())
    return f"{slug}.com"


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------


_SYNTHESIS_PROFILE = "strategy-consultant"


class CompetitorIntelligenceWorkflow:
    """Orchestrates a Competitor Intelligence Report.

    Construction::

        workflow = CompetitorIntelligenceWorkflow(
            conductor=conductor,
            researcher=WebResearcher(),   # or a stub for tests
        )
        report = workflow.execute(CompetitorIntelligenceRequest(
            competitor_name="Acme",
            question="How do they price for SMBs?",
        ))
    """

    def __init__(
        self,
        conductor: "Conductor",
        researcher: WebResearcher | None = None,
    ) -> None:
        self._conductor = conductor
        self._researcher = researcher or WebResearcher()

    def execute(self, request: CompetitorIntelligenceRequest) -> CompetitorIntelligenceReport:
        """Run the full competitor intelligence pipeline."""
        name = request.competitor_name
        question = request.question or "General competitive landscape"

        logger.info(
            "CompetitorIntelligenceWorkflow: starting competitor=%r question=%r",
            name,
            question,
        )

        # Step 1 — Fetch evidence
        pages = self._researcher.fetch_competitor_profile(name)
        successful = [p for p in pages if p.success]
        failed = [p for p in pages if not p.success]

        logger.info(
            "CompetitorIntelligenceWorkflow: fetched %d pages (%d ok, %d failed)",
            len(pages),
            len(successful),
            len(failed),
        )

        # Step 2 — Build evidence block for the prompt
        evidence_block = _build_evidence_block(pages)

        # Step 3 — Build synthesis prompt
        prompt = _build_synthesis_prompt(name, question, evidence_block, pages)

        # Step 4 — Stream synthesis from strategy-consultant profile
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
            "CompetitorIntelligenceWorkflow: synthesis complete length=%d",
            len(report_text),
        )

        return CompetitorIntelligenceReport(
            competitor_name=name,
            question=question,
            report_text=report_text,
            pages_fetched=len(pages),
            pages_successful=len(successful),
            source_log=source_log,
        )


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------


def _build_evidence_block(pages: list[FetchResult]) -> str:
    """Format fetched pages into an evidence block for the synthesis prompt."""
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
    competitor_name: str,
    question: str,
    evidence_block: str,
    pages: list[FetchResult],
) -> str:
    successful_count = sum(1 for p in pages if p.success)
    failed_urls = [p.url for p in pages if not p.success]
    failed_str = ", ".join(failed_urls) if failed_urls else "none"

    return f"""You are producing a Competitor Intelligence Report for internal Founder use.

COMPETITOR: {competitor_name}
QUESTION TO ADDRESS: {question}

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
3. Never invent pricing, headcount, revenue, funding, or customer names.
4. If a page fetch failed, mark all claims that would depend on it as [UNKNOWN].
5. Every report must open with the advisory label: FOUNDER-INTERNAL — ADVISORY ONLY

REPORT STRUCTURE (follow exactly):

COMPETITOR INTELLIGENCE REPORT
Advisory label: FOUNDER-INTERNAL — ADVISORY ONLY

COMPETITOR: {competitor_name}
QUESTION ADDRESSED: {question}

1. COMPANY OVERVIEW
2. PRODUCT / SERVICE OFFERING
3. MARKET POSITIONING
4. PRICING
5. TARGET CUSTOMER SEGMENTS
6. RECENT MOVES
7. NEWS & EXTERNAL SIGNALS
8. STRATEGIC IMPLICATIONS

EVIDENCE SUMMARY
  Pages fetched: {len(pages)}
  Pages successful: {successful_count}
  Pages failed: {failed_str}

SOURCE LOG
  (list each URL with HTTP status and title)

Produce the complete report now. Do not summarise these instructions. Do not add commentary after the report.
"""


# ---------------------------------------------------------------------------
# FounderJobRouter integration — module-level factory (class is unchanged)
# ---------------------------------------------------------------------------


def _extract_competitor_name(objective: str) -> str:
    """Extract the competitor name from a plain-text Founder objective.

    Handles common phrasings:
      "Research competitor Acme"      → "Acme"
      "Competitor analysis for Acme"  → "Acme"
      "What does Acme Corp do?"       → "Acme Corp"
      "acme.com"                      → "acme.com"

    Falls back to the full objective string so domain inference always has
    something to work with.
    """
    patterns = [
        # "research competitor Acme" / "analyse competitor Acme Corp"
        r"(?:research|analyze|analyse|profile)\s+(?:competitor\s+)?([A-Za-z0-9][^\n,?!]{1,50})",
        # "competitor analysis for Acme" / "competitive intelligence on Acme"
        r"(?:competitor(?:\s+analysis)?|competitive\s+intelligence)\s+(?:for|on|of)\s+([A-Za-z0-9][^\n,?!]{1,50})",
        # "what does Acme do" / "tell me about Acme"
        r"(?:what\s+does|about|profile\s+(?:of|for)?)\s+([A-Z][A-Za-z0-9\s\(\)\.]{1,40})",
    ]
    for pattern in patterns:
        m = re.search(pattern, objective, re.IGNORECASE)
        if m:
            return m.group(1).strip().rstrip(".,?! ")
    return objective  # fallback: pass the full string to domain inference


def execute_from_objective(
    objective: str,
    workspace_id: str,
    conductor: "Conductor",
) -> str:
    """Factory adapter invoked by FounderJobRouter for competitor.intelligence capability.

    Extracts the competitor name from a free-text Founder objective, constructs
    a CompetitorIntelligenceRequest, runs the workflow, and returns the report text.

    This function is the boundary between the routing layer and the workflow.
    CompetitorIntelligenceWorkflow itself is not modified.
    """
    competitor_name = _extract_competitor_name(objective)
    logger.info(
        "execute_from_objective: extracted competitor_name=%r from objective=%r",
        competitor_name, objective,
    )
    request = CompetitorIntelligenceRequest(
        competitor_name=competitor_name,
        workspace_id=workspace_id,
    )
    workflow = CompetitorIntelligenceWorkflow(conductor=conductor, researcher=WebResearcher())
    report = workflow.execute(request)
    return report.report_text
