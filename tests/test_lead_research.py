"""Tests for Lead / Company Research Workflow.

Coverage:
  - _extract_company_name(): all extraction patterns + fallback
  - LeadResearchWorkflow.execute(): happy path, partial failures, all-fail
  - execute_from_objective(): adapter integration
  - skill.yaml: workflow_executor and keywords present
  - CapabilityRegistry: lead.research is discoverable and has an executor
  - FounderJobRouter: routes lead-research objective via /chat
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from hermes.workflows.competitor_intelligence_workflow import FetchResult
from hermes.workflows.lead_research_workflow import (
    LeadResearchReport,
    LeadResearchRequest,
    LeadResearchWorkflow,
    _extract_company_name,
    execute_from_objective,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SKILLS_ROOT = Path(__file__).parent.parent / "skills"


def _ok_page(url: str, title: str = "Title", content: str = "Some content") -> FetchResult:
    return FetchResult(url=url, http_status=200, success=True, title=title, content=content)


def _fail_page(url: str) -> FetchResult:
    return FetchResult(url=url, http_status=None, success=False, title="", content="")


def _url_researcher(url_map: dict[str, FetchResult]) -> MagicMock:
    """Return a WebResearcher stub that maps URL → FetchResult (unknown → fail)."""
    stub = MagicMock()
    stub.fetch.side_effect = lambda url: url_map.get(url, _fail_page(url))
    return stub


def _stub_researcher(pages: list[FetchResult]) -> MagicMock:
    """Return a WebResearcher stub that yields FetchResult objects in sequence."""
    stub = MagicMock()
    stub.fetch.side_effect = list(pages)
    return stub


WIKI_ACME = "https://en.wikipedia.org/wiki/Acme"
WIKI_LINEAR = "https://en.wikipedia.org/wiki/Linear"


def _mock_conductor(response: str = "LEAD INTELLIGENCE REPORT\nAdvisory label: FOUNDER-INTERNAL — ADVISORY ONLY\n\nContent here.") -> MagicMock:
    conductor = MagicMock()
    conductor.stream_chat.return_value = iter([response])
    return conductor


# ---------------------------------------------------------------------------
# _extract_company_name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "objective, expected",
    [
        # Primary acceptance-test phrasing
        ("Research Acme as a potential AVANZIA client.", "Acme"),
        ("Research Notion as a potential client", "Notion"),
        # Multi-word names
        ("Research Acme Corp as a potential client", "Acme Corp"),
        ("Research Linear App as a potential AVANZIA client", "Linear App"),
        # Bare domain
        ("Research acme.com as a lead", "acme.com"),
        # Other trigger verbs
        ("Qualify Acme as a prospect", "Acme"),
        ("Profile Stripe as a potential customer", "Stripe"),
        ("Investigate HubSpot as a potential client", "HubSpot"),
        # Pattern 2: "company research on X"
        ("company research on Intercom", "Intercom"),
        ("account research for Stripe", "Stripe"),
        # Pattern 3: "tell me about X"
        ("tell me about Notion", "Notion"),
        # Fallback
        ("Some random unrecognised prompt", "Some random unrecognised prompt"),
    ],
)
def test_extract_company_name(objective: str, expected: str):
    assert _extract_company_name(objective) == expected


# ---------------------------------------------------------------------------
# LeadResearchWorkflow.execute — happy path (Wikipedia always included)
# ---------------------------------------------------------------------------


def test_execute_returns_report():
    stub = _url_researcher({
        "https://acme.com/": _ok_page("https://acme.com/", "Acme", "We help teams."),
        "https://acme.com/about": _ok_page("https://acme.com/about", "About", "Founded 2018."),
        "https://acme.com/pricing": _ok_page("https://acme.com/pricing", "Pricing", "Free tier."),
        "https://acme.com/team": _ok_page("https://acme.com/team", "Team", "CEO: Jane Smith"),
        "https://acme.com/blog": _ok_page("https://acme.com/blog", "Blog", "News."),
        WIKI_ACME: _ok_page(WIKI_ACME, "Acme - Wikipedia", "Acme is a company."),
    })
    report = LeadResearchWorkflow(
        conductor=_mock_conductor(), researcher=stub
    ).execute(LeadResearchRequest(company_name="Acme"))

    assert isinstance(report, LeadResearchReport)
    assert report.company_name == "Acme"
    assert report.pages_fetched == 6       # 5 primary + Wikipedia
    assert report.pages_successful == 6
    assert report.advisory_label == "FOUNDER-INTERNAL — ADVISORY ONLY"


def test_execute_always_fetches_wikipedia():
    """Wikipedia is always included regardless of whether primary pages succeed."""
    stub = _url_researcher({
        "https://acme.com/": _ok_page("https://acme.com/", "Acme", "content"),
        WIKI_ACME: _ok_page(WIKI_ACME, "Acme - Wikipedia", "wiki content"),
    })
    LeadResearchWorkflow(
        conductor=_mock_conductor(), researcher=stub
    ).execute(LeadResearchRequest(company_name="Acme"))

    fetched = [c[0][0] for c in stub.fetch.call_args_list]
    assert any("wikipedia.org" in url for url in fetched)


def test_execute_wikipedia_url_uses_company_name():
    stub = _url_researcher({
        "https://acme.com/": _ok_page("https://acme.com/", "Acme", "content"),
        "https://en.wikipedia.org/wiki/Acme_Corp": _ok_page(
            "https://en.wikipedia.org/wiki/Acme_Corp", "Acme Corp - Wikipedia", "wiki"
        ),
    })
    LeadResearchWorkflow(
        conductor=_mock_conductor(), researcher=stub
    ).execute(LeadResearchRequest(company_name="Acme Corp"))

    fetched = [c[0][0] for c in stub.fetch.call_args_list]
    assert "https://en.wikipedia.org/wiki/Acme_Corp" in fetched


def test_execute_fetches_primary_and_wikipedia_url_set():
    stub = _url_researcher({
        "https://acme.com/": _ok_page("https://acme.com/", "Acme", "content"),
        WIKI_ACME: _ok_page(WIKI_ACME, "Wikipedia", "content"),
    })
    LeadResearchWorkflow(
        conductor=_mock_conductor(), researcher=stub
    ).execute(LeadResearchRequest(company_name="Acme"))

    fetched = [c[0][0] for c in stub.fetch.call_args_list]
    assert "https://acme.com/" in fetched
    assert "https://acme.com/about" in fetched
    assert "https://acme.com/pricing" in fetched
    assert "https://acme.com/team" in fetched
    assert "https://acme.com/blog" in fetched
    assert WIKI_ACME in fetched


def test_execute_uses_sales_consultant_profile():
    stub = _url_researcher({
        "https://acme.com/": _ok_page("https://acme.com/", "Acme", "content"),
        WIKI_ACME: _ok_page(WIKI_ACME, "Wikipedia", "content"),
    })
    conductor = _mock_conductor()
    LeadResearchWorkflow(conductor=conductor, researcher=stub).execute(
        LeadResearchRequest(company_name="Acme")
    )
    assert conductor.stream_chat.call_args[1].get("profile_id") == "sales-consultant"


def test_execute_with_question_passes_question_to_prompt():
    stub = _url_researcher({
        "https://acme.com/": _ok_page("https://acme.com/", "Acme", "content"),
        WIKI_ACME: _ok_page(WIKI_ACME, "Wikipedia", "content"),
    })
    conductor = _mock_conductor()
    LeadResearchWorkflow(conductor=conductor, researcher=stub).execute(
        LeadResearchRequest(company_name="Acme", question="Are they a fit for AVANZIA?")
    )
    prompt = conductor.stream_chat.call_args[0][0][0].content
    assert "Are they a fit for AVANZIA?" in prompt


def test_execute_partial_failures_counted():
    stub = _url_researcher({
        "https://acme.com/": _ok_page("https://acme.com/", "Home", "content"),
        "https://acme.com/about": _ok_page("https://acme.com/about", "About", "content"),
        "https://acme.com/blog": _ok_page("https://acme.com/blog", "Blog", "content"),
        WIKI_ACME: _ok_page(WIKI_ACME, "Wikipedia", "content"),
        # pricing and team: unknown → fail
    })
    report = LeadResearchWorkflow(
        conductor=_mock_conductor(), researcher=stub
    ).execute(LeadResearchRequest(company_name="Acme"))

    assert report.pages_fetched == 6       # 5 primary + Wikipedia
    assert report.pages_successful == 4   # home + about + blog + wiki


def test_execute_default_question_used_when_empty():
    stub = _url_researcher({"https://acme.com/": _ok_page("https://acme.com/", "A", "b")})
    report = LeadResearchWorkflow(
        conductor=_mock_conductor(), researcher=stub
    ).execute(LeadResearchRequest(company_name="Acme"))
    assert report.question == "General lead qualification"


def test_execute_source_log_contains_wikipedia():
    stub = _url_researcher({
        "https://acme.com/": _ok_page("https://acme.com/", "Home", "content"),
        WIKI_ACME: _ok_page(WIKI_ACME, "Acme - Wikipedia", "wiki content"),
    })
    report = LeadResearchWorkflow(
        conductor=_mock_conductor(), researcher=stub
    ).execute(LeadResearchRequest(company_name="Acme"))
    log_str = " ".join(report.source_log)
    assert "wikipedia.org" in log_str


# ---------------------------------------------------------------------------
# LeadResearchWorkflow — alternative TLD fallback
# ---------------------------------------------------------------------------


def test_tld_fallback_not_triggered_when_primary_succeeds():
    """When at least one primary page succeeds, no TLD fallback is attempted."""
    stub = _url_researcher({
        "https://acme.com/": _ok_page("https://acme.com/", "Acme", "content"),
        WIKI_ACME: _ok_page(WIKI_ACME, "Wikipedia", "wiki"),
    })
    LeadResearchWorkflow(
        conductor=_mock_conductor(), researcher=stub
    ).execute(LeadResearchRequest(company_name="Acme"))

    fetched = [c[0][0] for c in stub.fetch.call_args_list]
    assert not any(".io" in url or ".app" in url or ".co/" in url for url in fetched)


def test_tld_fallback_triggered_when_primary_all_fail():
    """When all .com pages fail, alternative TLDs are probed."""
    stub = _url_researcher({
        # All .com pages fail (not in map → _url_researcher returns fail_page)
        "https://acme.io/": _ok_page("https://acme.io/", "Acme IO", "content"),
        "https://acme.io/about": _ok_page("https://acme.io/about", "About", "content"),
        "https://acme.io/pricing": _ok_page("https://acme.io/pricing", "Pricing", "content"),
        "https://acme.io/team": _ok_page("https://acme.io/team", "Team", "content"),
        "https://acme.io/blog": _ok_page("https://acme.io/blog", "Blog", "content"),
        WIKI_ACME: _ok_page(WIKI_ACME, "Wikipedia", "wiki"),
    })
    report = LeadResearchWorkflow(
        conductor=_mock_conductor(), researcher=stub
    ).execute(LeadResearchRequest(company_name="Acme"))

    fetched = [c[0][0] for c in stub.fetch.call_args_list]
    assert any(".io" in url for url in fetched), "Expected .io fallback attempt"
    assert report.pages_successful >= 1


def test_tld_fallback_uses_app_when_io_fails():
    """When .io probe fails, .app is tried next."""
    stub = _url_researcher({
        # .io fails (not in map), .app succeeds
        "https://acme.app/": _ok_page("https://acme.app/", "Acme App", "content"),
        "https://acme.app/about": _ok_page("https://acme.app/about", "About", "content"),
        WIKI_ACME: _ok_page(WIKI_ACME, "Wikipedia", "wiki"),
    })
    report = LeadResearchWorkflow(
        conductor=_mock_conductor(), researcher=stub
    ).execute(LeadResearchRequest(company_name="Acme"))

    fetched = [c[0][0] for c in stub.fetch.call_args_list]
    assert any(".app" in url for url in fetched)
    assert report.pages_successful >= 1


def test_tld_fallback_stops_after_first_success():
    """Once a TLD probe succeeds, no further TLDs are tried."""
    stub = _url_researcher({
        "https://acme.io/": _ok_page("https://acme.io/", "Acme", "content"),
        WIKI_ACME: _ok_page(WIKI_ACME, "Wikipedia", "wiki"),
    })
    LeadResearchWorkflow(
        conductor=_mock_conductor(), researcher=stub
    ).execute(LeadResearchRequest(company_name="Acme"))

    fetched = [c[0][0] for c in stub.fetch.call_args_list]
    # .io succeeded; .app and .co must not have been tried
    assert not any(".app" in url for url in fetched)
    assert not any(".co/" in url for url in fetched)


def test_tld_fallback_pages_counted_in_report():
    """Pages fetched from the alt domain are counted in the report."""
    stub = _url_researcher({
        # All .com fail; .io succeeds with 2 pages
        "https://acme.io/": _ok_page("https://acme.io/", "Acme", "content"),
        "https://acme.io/about": _ok_page("https://acme.io/about", "About", "content"),
        WIKI_ACME: _ok_page(WIKI_ACME, "Wikipedia", "wiki"),
    })
    report = LeadResearchWorkflow(
        conductor=_mock_conductor(), researcher=stub
    ).execute(LeadResearchRequest(company_name="Acme"))

    # 5 primary fails + .io home (success) + 4 .io pages (2 ok, 2 fail) + Wikipedia = 11 total
    assert report.pages_fetched >= 6
    assert report.pages_successful >= 3   # .io home + .io/about + Wikipedia


def test_all_sources_fail_still_returns_report():
    """When every source (primary, alt TLDs, Wikipedia) fails, synthesis still runs."""
    # Nothing in map → everything fails
    stub = _url_researcher({})
    report = LeadResearchWorkflow(
        conductor=_mock_conductor(), researcher=stub
    ).execute(LeadResearchRequest(company_name="Acme"))

    assert report.pages_successful == 0
    assert report.report_text  # synthesis still produces output


# ---------------------------------------------------------------------------
# execute_from_objective
# ---------------------------------------------------------------------------


def test_execute_from_objective_extracts_name_and_runs():
    conductor = _mock_conductor()
    with patch(
        "hermes.workflows.lead_research_workflow.LeadResearchWorkflow"
    ) as MockWorkflow:
        mock_instance = MagicMock()
        mock_instance.execute.return_value = LeadResearchReport(
            company_name="Acme",
            question="General lead qualification",
            report_text="Report text",
            pages_fetched=5,
            pages_successful=3,
        )
        MockWorkflow.return_value = mock_instance

        result = execute_from_objective(
            objective="Research Acme as a potential AVANZIA client.",
            workspace_id="AVANZIA",
            conductor=conductor,
        )

    assert result == "Report text"
    request_arg = mock_instance.execute.call_args[0][0]
    assert request_arg.company_name == "Acme"
    assert request_arg.workspace_id == "AVANZIA"


# ---------------------------------------------------------------------------
# Skill manifest validation
# ---------------------------------------------------------------------------


def test_skill_yaml_has_workflow_executor():
    import yaml
    skill_path = SKILLS_ROOT / "lead-research" / "skill.yaml"
    assert skill_path.exists(), f"Missing: {skill_path}"
    manifest = yaml.safe_load(skill_path.read_text())
    assert manifest.get("workflow_executor") == \
        "hermes.workflows.lead_research_workflow.execute_from_objective"


def test_skill_yaml_has_lead_keywords():
    import yaml
    skill_path = SKILLS_ROOT / "lead-research" / "skill.yaml"
    manifest = yaml.safe_load(skill_path.read_text())
    keywords = [k.lower() for k in manifest.get("keywords", [])]
    assert "potential client" in keywords or any("client" in k for k in keywords)
    assert any("lead" in k for k in keywords)


# ---------------------------------------------------------------------------
# CapabilityRegistry integration
# ---------------------------------------------------------------------------


def test_capability_registry_discovers_lead_research():
    from hermes.kernel.capability_registry import CapabilityRegistry
    reg = CapabilityRegistry(skills_root=SKILLS_ROOT)
    cap = reg.get("lead.research")
    assert cap is not None, "lead.research capability not found in registry"
    assert cap.workflow_executor == \
        "hermes.workflows.lead_research_workflow.execute_from_objective"


def test_capability_registry_matches_lead_objective():
    from hermes.kernel.capability_registry import CapabilityRegistry
    from hermes.models.task import Task
    reg = CapabilityRegistry(skills_root=SKILLS_ROOT)
    task = Task(
        id="test",
        business="AVANZIA",
        request="Research Acme as a potential AVANZIA client.",
    )
    matches = reg.match(task)
    cap_ids = [c.id for c in matches]
    assert "lead.research" in cap_ids


def test_capability_registry_matches_prospect_keyword():
    from hermes.kernel.capability_registry import CapabilityRegistry
    from hermes.models.task import Task
    reg = CapabilityRegistry(skills_root=SKILLS_ROOT)
    task = Task(id="test", business="AVANZIA", request="Qualify Stripe as a prospect")
    matches = reg.match(task)
    assert any(c.id == "lead.research" for c in matches)


# ---------------------------------------------------------------------------
# FounderJobRouter routes lead objectives from /chat
# ---------------------------------------------------------------------------


def test_founder_job_router_routes_lead_objective():
    """FounderJobRouter.find_executable_capability returns lead.research for a lead objective."""
    from hermes.kernel.capability_registry import CapabilityRegistry
    from hermes.kernel.founder_job_router import FounderJobRouter

    conductor = MagicMock()
    reg = CapabilityRegistry(skills_root=SKILLS_ROOT)
    router = FounderJobRouter(conductor=conductor, capability_registry=reg)

    cap = router.find_executable_capability(
        "Research Acme as a potential AVANZIA client.",
        workspace_id="AVANZIA",
    )
    assert cap is not None
    assert cap.id == "lead.research"


def test_founder_job_router_does_not_route_board_update_to_lead():
    """Non-lead objectives do not match lead.research."""
    from hermes.kernel.capability_registry import CapabilityRegistry
    from hermes.kernel.founder_job_router import FounderJobRouter

    conductor = MagicMock()
    reg = CapabilityRegistry(skills_root=SKILLS_ROOT)
    router = FounderJobRouter(conductor=conductor, capability_registry=reg)

    cap = router.find_executable_capability(
        "Write a board update for this quarter",
        workspace_id="AVANZIA",
    )
    # May return None or a non-lead-research capability
    if cap is not None:
        assert cap.id != "lead.research"
